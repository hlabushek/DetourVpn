package org.example.vpnpoc;

import android.net.VpnService;
import java.io.BufferedInputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.InetAddress;
import java.net.InetSocketAddress;
import java.net.ServerSocket;
import java.net.Socket;
import java.net.URI;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.SecureRandom;
import java.util.Set;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.RejectedExecutionException;
import java.util.concurrent.Semaphore;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicBoolean;

/** Origin-pinned opaque TCP relay. TLS remains end-to-end in Dart HttpClient. */
final class ProtectedApiRelay implements AutoCloseable {
    interface SocketProtector {
        boolean protect(Socket socket) throws IOException;
    }

    private static final int MAX_CONNECTIONS = 8;
    private static final int CONNECT_TIMEOUT_MS = 5000;
    private static final int PREFACE_TIMEOUT_MS = 3000;
    private static final int MAX_PREFACE_BYTES = 96;

    private final String host;
    private final int port;
    private final SocketProtector protector;
    private final byte[] token = new byte[32];
    private final ServerSocket server;
    private final ExecutorService handlers = Executors.newFixedThreadPool(MAX_CONNECTIONS);
    private final ExecutorService copies = Executors.newFixedThreadPool(MAX_CONNECTIONS * 2);
    private final Semaphore permits = new Semaphore(MAX_CONNECTIONS);
    private final Set<Socket> sockets = java.util.concurrent.ConcurrentHashMap.newKeySet();
    private final AtomicBoolean closed = new AtomicBoolean();
    private final Thread acceptThread;

    static ProtectedApiRelay forApiOrigin(VpnService service, URI origin) throws IOException {
        if (service == null) {
            throw new IllegalArgumentException("invalid_protected_api_origin");
        }
        return new ProtectedApiRelay(validateApiOrigin(origin), 443, service::protect);
    }

    static String validateApiOrigin(URI origin) {
        if (origin == null || !"https".equalsIgnoreCase(origin.getScheme())
                || origin.getHost() == null || origin.getHost().isBlank()
                || origin.getUserInfo() != null || origin.getRawQuery() != null
                || origin.getRawFragment() != null
                || (origin.getRawPath() != null && !origin.getRawPath().isEmpty()
                    && !"/".equals(origin.getRawPath()))
                || (origin.getPort() != -1 && origin.getPort() != 443)) {
            throw new IllegalArgumentException("invalid_protected_api_origin");
        }
        return origin.getHost();
    }

    /** Package-private seam for JVM tests; production origins use forApiOrigin. */
    ProtectedApiRelay(String host, int port, SocketProtector protector) throws IOException {
        if (host == null || host.isBlank() || port < 1 || port > 65535 || protector == null)
            throw new IllegalArgumentException();
        this.host = host;
        this.port = port;
        this.protector = protector;
        new SecureRandom().nextBytes(token);
        server = new ServerSocket();
        server.setReuseAddress(false);
        server.bind(new InetSocketAddress(InetAddress.getByAddress(new byte[] {127, 0, 0, 1}), 0), MAX_CONNECTIONS);
        acceptThread = new Thread(this::acceptLoop, "vpn-protected-api-relay");
        acceptThread.setDaemon(true);
        acceptThread.start();
    }

    int port() { return server.getLocalPort(); }
    String token() { return hex(token); }

    private void acceptLoop() {
        while (!closed.get()) {
            try {
                Socket local = server.accept();
                if (!permits.tryAcquire()) {
                    closeQuietly(local);
                    continue;
                }
                sockets.add(local);
                try {
                    handlers.execute(() -> relay(local));
                } catch (RejectedExecutionException rejected) {
                    sockets.remove(local);
                    permits.release();
                    closeQuietly(local);
                }
            } catch (IOException failure) {
                if (!closed.get()) close();
            }
        }
    }

    private void relay(Socket local) {
        Socket upstream = null;
        try {
            local.setSoTimeout(PREFACE_TIMEOUT_MS);
            if (!validPreface(local.getInputStream())) return;
            upstream = new Socket();
            if (!protector.protect(upstream)) return;
            upstream.connect(new InetSocketAddress(host, port), CONNECT_TIMEOUT_MS);
            upstream.setSoTimeout(0);
            sockets.add(upstream);
            pipeBothWays(local, upstream);
        } catch (IOException ignored) {
            // Connection failures are surfaced to the HTTPS client; no payloads are logged.
        } finally {
            if (upstream != null) {
                sockets.remove(upstream);
                closeQuietly(upstream);
            }
            sockets.remove(local);
            closeQuietly(local);
            permits.release();
        }
    }

    private boolean validPreface(InputStream stream) throws IOException {
        byte[] received = new byte[MAX_PREFACE_BYTES];
        int length = 0;
        while (length < received.length) {
            int next = stream.read();
            if (next < 0) return false;
            received[length++] = (byte) next;
            if (next == '\n') break;
        }
        if (length == received.length || received[length - 1] != '\n') return false;
        byte[] expected = ("DETOUR-RELAY/1 " + token() + "\r\n").getBytes(StandardCharsets.US_ASCII);
        return MessageDigest.isEqual(expected, java.util.Arrays.copyOf(received, length));
    }

    private void pipeBothWays(Socket left, Socket right) throws IOException {
        CountDownLatch completed = new CountDownLatch(2);
        copies.execute(() -> copy(left, right, completed));
        copies.execute(() -> copy(right, left, completed));
        try {
            completed.await();
        } catch (InterruptedException interrupted) {
            Thread.currentThread().interrupt();
            throw new IOException("relay_interrupted");
        }
    }

    private static void copy(Socket source, Socket target, CountDownLatch completed) {
        try {
            byte[] buffer = new byte[8192];
            InputStream input = new BufferedInputStream(source.getInputStream());
            OutputStream output = target.getOutputStream();
            int count;
            while ((count = input.read(buffer)) != -1) {
                output.write(buffer, 0, count);
                output.flush();
            }
            try { target.shutdownOutput(); } catch (IOException ignored) { }
        } catch (IOException ignored) {
            // Closing the paired sockets in relay() unblocks the other copy direction.
        } finally {
            completed.countDown();
        }
    }

    private static String hex(byte[] bytes) {
        StringBuilder value = new StringBuilder(bytes.length * 2);
        for (byte current : bytes) value.append(String.format(java.util.Locale.ROOT, "%02x", current & 0xff));
        return value.toString();
    }

    private static void closeQuietly(Socket socket) {
        try { socket.close(); } catch (IOException ignored) { }
    }

    @Override public void close() {
        if (!closed.compareAndSet(false, true)) return;
        try { server.close(); } catch (IOException ignored) { }
        for (Socket socket : sockets) closeQuietly(socket);
        handlers.shutdownNow();
        copies.shutdownNow();
        try { handlers.awaitTermination(1, TimeUnit.SECONDS); } catch (InterruptedException ignored) {
            Thread.currentThread().interrupt();
        }
        try { copies.awaitTermination(1, TimeUnit.SECONDS); } catch (InterruptedException ignored) {
            Thread.currentThread().interrupt();
        }
    }
}
