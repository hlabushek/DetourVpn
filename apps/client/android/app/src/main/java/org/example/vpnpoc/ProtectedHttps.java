package org.example.vpnpoc;

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.InetSocketAddress;
import java.net.Socket;
import java.nio.charset.StandardCharsets;
import java.util.Locale;
import javax.net.ssl.HttpsURLConnection;
import javax.net.ssl.SSLPeerUnverifiedException;
import javax.net.ssl.SSLSocket;
import javax.net.ssl.SSLSocketFactory;

/**
 * ADR-140: one HTTPS exchange with the API origin over a socket that is {@code protect()}ed before
 * it connects, so it leaves through the physical network under Lockdown. TLS, certificate trust and
 * host-name checks are the platform's ({@link SSLSocketFactory#getDefault()}, the default
 * {@link javax.net.ssl.HostnameVerifier}); only the HTTP/1.1 framing lives here. The framing is pure
 * and unit-tested on the JVM. No Android types.
 */
final class ProtectedHttps {
    static final int MAX_BODY_BYTES = 65536;
    static final int MAX_HEADER_BYTES = 8192;

    private ProtectedHttps() {}

    static final class Result {
        final int status;
        final String body;
        Result(int status, String body) { this.status = status; this.body = body; }
    }

    /** One request with {@code Connection: close}; the caller validated host and path. */
    static Result exchange(ProtectedApiRelay.SocketProtector protector, String host, String method,
            String path, String renewToken, byte[] body, int timeoutMs) throws IOException {
        Socket raw = open(protector, host, 443, timeoutMs);
        SSLSocket tls = null;
        try {
            tls = (SSLSocket) ((SSLSocketFactory) SSLSocketFactory.getDefault())
                    .createSocket(raw, host, 443, true);
            tls.setSoTimeout(timeoutMs);
            tls.startHandshake();
            if (!HttpsURLConnection.getDefaultHostnameVerifier().verify(host, tls.getSession()))
                throw new SSLPeerUnverifiedException("host_mismatch");
            OutputStream out = tls.getOutputStream();
            out.write(request(method, host, path, renewToken, body));
            out.flush();
            return parse(tls.getInputStream());
        } finally {
            try {
                if (tls != null) tls.close(); else raw.close();
            } catch (IOException ignored) { /* Closing only. */ }
        }
    }

    /** Protects the plain socket first; a refused protect never connects (fails closed). */
    static Socket open(ProtectedApiRelay.SocketProtector protector, String host, int port,
            int timeoutMs) throws IOException {
        Socket raw = new Socket();
        try {
            if (protector == null || !protector.protect(raw))
                throw new IOException("socket_protection_failed");
            raw.connect(new InetSocketAddress(host, port), timeoutMs);
            raw.setSoTimeout(timeoutMs);
            return raw;
        } catch (IOException | RuntimeException failure) {
            try { raw.close(); } catch (IOException ignored) { /* Closing only. */ }
            throw failure;
        }
    }

    static byte[] request(String method, String host, String path, String renewToken, byte[] body) {
        if (!("GET".equals(method) || "POST".equals(method))
                || host == null || !host.matches("[A-Za-z0-9.-]{1,253}")
                || path == null || !path.matches("/[A-Za-z0-9._~/-]{0,255}")
                || renewToken == null || !renewToken.matches("[A-Za-z0-9_-]{32,128}"))
            throw new IllegalArgumentException();
        StringBuilder head = new StringBuilder(256)
                .append(method).append(' ').append(path).append(" HTTP/1.1\r\n")
                .append("Host: ").append(host).append("\r\n")
                .append("Accept: application/json\r\n")
                .append("X-Window-Renew-Token: ").append(renewToken).append("\r\n")
                .append("Connection: close\r\n");
        if (body != null) {
            head.append("Content-Type: application/json\r\n")
                    .append("Content-Length: ").append(body.length).append("\r\n");
        }
        head.append("\r\n");
        byte[] start = head.toString().getBytes(StandardCharsets.US_ASCII);
        if (body == null) return start;
        byte[] all = new byte[start.length + body.length];
        System.arraycopy(start, 0, all, 0, start.length);
        System.arraycopy(body, 0, all, start.length, body.length);
        return all;
    }

    /** Status line, headers, then a Content-Length, chunked or close-delimited body. */
    static Result parse(InputStream in) throws IOException {
        int[] headerBudget = {MAX_HEADER_BYTES};
        int status;
        long length = -1;
        boolean chunked = false;
        while (true) {
            String line = line(in, headerBudget);
            if (!line.matches("HTTP/1\\.[01] [1-5][0-9]{2}( .*)?"))
                throw new IOException("bad_status_line");
            status = Integer.parseInt(line.substring(9, 12));
            length = -1;
            chunked = false;
            for (String header; !(header = line(in, headerBudget)).isEmpty(); ) {
                int colon = header.indexOf(':');
                if (colon <= 0) throw new IOException("bad_header");
                String name = header.substring(0, colon).trim().toLowerCase(Locale.ROOT);
                String value = header.substring(colon + 1).trim();
                if (name.equals("content-length")) {
                    if (!value.matches("[0-9]{1,9}") || length >= 0) throw new IOException("bad_length");
                    length = Long.parseLong(value);
                } else if (name.equals("transfer-encoding")) {
                    if (!value.equalsIgnoreCase("chunked")) throw new IOException("bad_encoding");
                    chunked = true;
                }
            }
            if (status >= 200) break; // 1xx interim responses carry no body.
        }
        if (chunked && length >= 0) throw new IOException("bad_length");
        ByteArrayOutputStream body = new ByteArrayOutputStream();
        if (chunked) {
            while (true) {
                String size = line(in, headerBudget);
                int semicolon = size.indexOf(';');
                if (semicolon >= 0) size = size.substring(0, semicolon);
                size = size.trim();
                if (!size.matches("[0-9A-Fa-f]{1,6}")) throw new IOException("bad_chunk");
                int n = Integer.parseInt(size, 16);
                if (n == 0) break;
                copy(in, body, n);
                if (in.read() != '\r' || in.read() != '\n') throw new IOException("bad_chunk");
            }
        } else if (length >= 0) {
            if (length > MAX_BODY_BYTES) throw new IOException("body_too_large");
            copy(in, body, (int) length);
        } else {
            byte[] buffer = new byte[4096];
            for (int n; (n = in.read(buffer)) > 0; ) {
                if (body.size() + n > MAX_BODY_BYTES) throw new IOException("body_too_large");
                body.write(buffer, 0, n);
            }
        }
        return new Result(status, body.toString("UTF-8"));
    }

    private static void copy(InputStream in, ByteArrayOutputStream out, int n) throws IOException {
        if (out.size() + (long) n > MAX_BODY_BYTES) throw new IOException("body_too_large");
        byte[] buffer = new byte[Math.min(n, 4096)];
        for (int left = n; left > 0; ) {
            int read = in.read(buffer, 0, Math.min(left, buffer.length));
            if (read < 0) throw new IOException("truncated");
            out.write(buffer, 0, read);
            left -= read;
        }
    }

    /** One CRLF-terminated line within the shared header budget. */
    private static String line(InputStream in, int[] budget) throws IOException {
        StringBuilder line = new StringBuilder();
        while (true) {
            int b = in.read();
            if (b < 0) throw new IOException("truncated");
            if (--budget[0] < 0) throw new IOException("headers_too_large");
            if (b == '\n') {
                int end = line.length();
                if (end == 0 || line.charAt(end - 1) != '\r') throw new IOException("bad_line");
                return line.substring(0, end - 1);
            }
            line.append((char) b);
        }
    }
}
