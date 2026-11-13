package org.example.vpnpoc;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertThrows;
import static org.junit.Assert.assertTrue;

import java.io.ByteArrayInputStream;
import java.io.IOException;
import java.net.InetAddress;
import java.net.InetSocketAddress;
import java.net.ServerSocket;
import java.net.Socket;
import java.nio.charset.StandardCharsets;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicInteger;
import org.junit.Test;

/** ADR-140: HTTP framing and protect-before-connect for the protected native renewal path. */
public class ProtectedHttpsTest {
    private static final String TOKEN = "abcdefghijklmnopqrstuvwxyz012345_-AB";
    private static final String PATH =
            "/api/v1/window-tunnels/123e4567-e89b-12d3-a456-426614174000/native-renew";

    private static ProtectedHttps.Result parse(String wire) throws IOException {
        return ProtectedHttps.parse(new ByteArrayInputStream(wire.getBytes(StandardCharsets.ISO_8859_1)));
    }

    @Test public void requestCarriesOnlyTheExpectedHeadersAndBody() {
        byte[] body = "{\"a\":1}".getBytes(StandardCharsets.UTF_8);
        String wire = new String(ProtectedHttps.request("POST", "api.example.test", PATH, TOKEN, body),
                StandardCharsets.US_ASCII);
        assertEquals("POST " + PATH + " HTTP/1.1\r\n"
                + "Host: api.example.test\r\n"
                + "Accept: application/json\r\n"
                + "X-Window-Renew-Token: " + TOKEN + "\r\n"
                + "Connection: close\r\n"
                + "Content-Type: application/json\r\n"
                + "Content-Length: 7\r\n\r\n{\"a\":1}", wire);
        String get = new String(ProtectedHttps.request("GET", "api.example.test", PATH, TOKEN, null),
                StandardCharsets.US_ASCII);
        assertFalse(get.contains("Content-Length"));
        assertTrue(get.endsWith("Connection: close\r\n\r\n"));
    }

    @Test public void requestRefusesHeaderInjectionAndOtherMethods() {
        assertThrows(IllegalArgumentException.class,
                () -> ProtectedHttps.request("POST", "api.example.test\r\nX: y", PATH, TOKEN, null));
        assertThrows(IllegalArgumentException.class,
                () -> ProtectedHttps.request("POST", "api.example.test", PATH + "\r\nX: y", TOKEN, null));
        assertThrows(IllegalArgumentException.class,
                () -> ProtectedHttps.request("POST", "api.example.test", PATH, TOKEN + "\r\n", null));
        assertThrows(IllegalArgumentException.class,
                () -> ProtectedHttps.request("POST", "api.example.test", PATH + "?q=1", TOKEN, null));
        assertThrows(IllegalArgumentException.class,
                () -> ProtectedHttps.request("DELETE", "api.example.test", PATH, TOKEN, null));
        assertThrows(IllegalArgumentException.class,
                () -> ProtectedHttps.request("GET", "api.example.test", PATH, "short", null));
    }

    @Test public void parsesContentLengthBody() throws IOException {
        var result = parse("HTTP/1.1 202 Accepted\r\nContent-Type: application/json\r\n"
                + "Content-Length: 13\r\n\r\n{\"ok\":\"yes\"}\nEXTRA");
        assertEquals(202, result.status);
        assertEquals("{\"ok\":\"yes\"}\n", result.body);
    }

    @Test public void parsesChunkedBodyAndSkipsInterimResponses() throws IOException {
        var result = parse("HTTP/1.1 100 Continue\r\n\r\n"
                + "HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n"
                + "4;ext=1\r\n{\"a\"\r\n3\r\n:1}\r\n0\r\n\r\n");
        assertEquals(200, result.status);
        assertEquals("{\"a\":1}", result.body);
    }

    @Test public void parsesCloseDelimitedBody() throws IOException {
        var result = parse("HTTP/1.0 409 Conflict\r\n\r\n{\"code\":\"x\"}");
        assertEquals(409, result.status);
        assertEquals("{\"code\":\"x\"}", result.body);
    }

    @Test public void refusesMalformedOrOversizedResponses() {
        assertThrows(IOException.class, () -> parse("HTTP/2 200 OK\r\n\r\n"));
        assertThrows(IOException.class, () -> parse("HTTP/1.1 200 OK\n\n"));
        assertThrows(IOException.class, () -> parse("HTTP/1.1 200 OK\r\nContent-Length: 10\r\n\r\nshort"));
        assertThrows(IOException.class,
                () -> parse("HTTP/1.1 200 OK\r\nContent-Length: 1\r\nContent-Length: 1\r\n\r\nx"));
        assertThrows(IOException.class,
                () -> parse("HTTP/1.1 200 OK\r\nContent-Length: 1\r\nTransfer-Encoding: chunked\r\n\r\n0\r\n\r\n"));
        assertThrows(IOException.class,
                () -> parse("HTTP/1.1 200 OK\r\nTransfer-Encoding: gzip\r\n\r\n"));
        assertThrows(IOException.class, () -> parse("HTTP/1.1 200 OK\r\nContent-Length: "
                + (ProtectedHttps.MAX_BODY_BYTES + 1) + "\r\n\r\n"));
        assertThrows(IOException.class,
                () -> parse("HTTP/1.1 200 OK\r\nX: " + "a".repeat(ProtectedHttps.MAX_HEADER_BYTES) + "\r\n\r\n"));
        assertThrows(IOException.class, () -> parse("HTTP/1.1 200 OK\r\n\r\n"
                + "b".repeat(ProtectedHttps.MAX_BODY_BYTES + 1)));
        assertThrows(IOException.class,
                () -> parse("HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n4\r\nabcdXX0\r\n\r\n"));
    }

    @Test public void aRefusedProtectNeverConnects() throws Exception {
        try (ServerSocket server = new ServerSocket(0, 1, InetAddress.getLoopbackAddress())) {
            server.setSoTimeout(300);
            AtomicInteger asked = new AtomicInteger();
            IOException failure = assertThrows(IOException.class, () -> ProtectedHttps.open(
                    socket -> { asked.incrementAndGet(); return false; },
                    "127.0.0.1", server.getLocalPort(), 1000));
            assertEquals("socket_protection_failed", failure.getMessage());
            assertEquals(1, asked.get());
            assertThrows("nothing reached the server", java.net.SocketTimeoutException.class, server::accept);
            assertThrows(IOException.class,
                    () -> ProtectedHttps.open(null, "127.0.0.1", server.getLocalPort(), 1000));
        }
    }

    @Test public void protectRunsOnTheUnconnectedSocketBeforeConnect() throws Exception {
        try (ServerSocket server = new ServerSocket(0, 1, InetAddress.getLoopbackAddress())) {
            AtomicBoolean unconnectedWhenProtected = new AtomicBoolean();
            try (Socket socket = ProtectedHttps.open(s -> {
                unconnectedWhenProtected.set(!s.isConnected());
                return true;
            }, "127.0.0.1", server.getLocalPort(), 1000)) {
                assertTrue(unconnectedWhenProtected.get());
                assertTrue(socket.isConnected());
                assertEquals(new InetSocketAddress("127.0.0.1", server.getLocalPort()),
                        socket.getRemoteSocketAddress());
                server.accept().close();
            }
        }
    }
}
