package org.example.vpnpoc;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import java.io.InputStream;
import java.io.OutputStream;
import java.net.InetAddress;
import java.net.ServerSocket;
import java.net.Socket;
import java.net.URI;
import java.nio.charset.StandardCharsets;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicReference;
import java.util.concurrent.atomic.AtomicBoolean;
import org.junit.Test;

public final class ProtectedApiRelayTest {
    @Test public void apiOriginMustBeHttpsRootOnStandardTlsPort() {
        assertEquals("api.example.test", ProtectedApiRelay.validateApiOrigin(
                URI.create("https://api.example.test")));
        for (String rejected : new String[] {
                "http://api.example.test", "https://user@api.example.test",
                "https://api.example.test:8443", "https://api.example.test/path",
                "https://api.example.test/?token=secret", "https://api.example.test/#fragment"
        }) {
            try {
                ProtectedApiRelay.validateApiOrigin(URI.create(rejected));
                throw new AssertionError("accepted invalid API origin");
            } catch (IllegalArgumentException expected) {
                // Origin constraints fail closed before any socket is created.
            }
        }
    }

    @Test public void authenticatedConnectionIsProtectedBeforeItReachesFixedOrigin() throws Exception {
        try (ServerSocket origin = new ServerSocket(0, 4, InetAddress.getByName("127.0.0.1"))) {
            AtomicBoolean protectedSocket = new AtomicBoolean();
            AtomicReference<Throwable> originFailure = new AtomicReference<>();
            CountDownLatch originAccepted = new CountDownLatch(1);
            Thread originThread = new Thread(() -> {
                try (Socket peer = origin.accept()) {
                    originAccepted.countDown();
                    InputStream input = peer.getInputStream();
                    byte[] request = input.readNBytes(4);
                    assertEquals("ping", new String(request, StandardCharsets.US_ASCII));
                    OutputStream output = peer.getOutputStream();
                    output.write("pong".getBytes(StandardCharsets.US_ASCII));
                    output.flush();
                } catch (Exception failure) {
                    originFailure.set(failure);
                }
            });
            originThread.start();

            try (ProtectedApiRelay relay = new ProtectedApiRelay(
                    "127.0.0.1", origin.getLocalPort(), socket -> {
                        protectedSocket.set(true);
                        return true;
                    });
                    Socket client = new Socket("127.0.0.1", relay.port())) {
                client.setSoTimeout(3000);
                client.getOutputStream().write(("DETOUR-RELAY/1 " + relay.token() + "\r\n")
                        .getBytes(StandardCharsets.US_ASCII));
                client.getOutputStream().write("ping".getBytes(StandardCharsets.US_ASCII));
                client.getOutputStream().flush();
                client.shutdownOutput();

                byte[] response = client.getInputStream().readNBytes(4);
                assertEquals("pong", new String(response, StandardCharsets.US_ASCII));
                assertTrue(originAccepted.await(1, TimeUnit.SECONDS));
                assertTrue(protectedSocket.get());
            }
            originThread.join(1000);
            assertFalse(originThread.isAlive());
            if (originFailure.get() != null) throw new AssertionError(originFailure.get());
        }
    }

    @Test public void invalidTokenNeverOpensTheConfiguredUpstreamSocket() throws Exception {
        try (ServerSocket origin = new ServerSocket(0, 4, InetAddress.getByName("127.0.0.1"));
                ProtectedApiRelay relay = new ProtectedApiRelay(
                        "127.0.0.1", origin.getLocalPort(), socket -> true);
                Socket client = new Socket("127.0.0.1", relay.port())) {
            client.setSoTimeout(3000);
            client.getOutputStream().write(("DETOUR-RELAY/1 " + "0".repeat(64) + "\r\n")
                    .getBytes(StandardCharsets.US_ASCII));
            client.getOutputStream().flush();
            assertEquals(-1, client.getInputStream().read());
            origin.setSoTimeout(200);
            try (Socket ignored = origin.accept()) {
                throw new AssertionError("unauthenticated relay opened upstream");
            } catch (java.net.SocketTimeoutException expected) {
                // Rejection occurs before the fixed upstream is contacted.
            }
        }
    }
}
