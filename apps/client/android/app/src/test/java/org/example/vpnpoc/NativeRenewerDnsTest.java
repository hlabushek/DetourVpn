package org.example.vpnpoc;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertSame;
import static org.junit.Assert.assertThrows;
import static org.junit.Assert.assertTrue;

import java.io.IOException;
import java.net.InetAddress;
import java.net.SocketTimeoutException;
import java.net.UnknownHostException;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import org.junit.Test;

/** A-05: the native renewal call deadline also bounds the DNS lookup. TEST ONLY fake resolvers. */
public class NativeRenewerDnsTest {
    private static InetAddress loopback() throws UnknownHostException {
        return InetAddress.getByAddress(new byte[] {127, 0, 0, 1});
    }

    @Test public void returnsTheAnswerOfAFastLookup() throws Exception {
        InetAddress[] answer = {loopback()};
        assertSame(answer, NativeRenewer.resolveWithin("api.example.test", 1000, host -> answer));
    }

    @Test public void aHangingLookupEndsAtTheBound() throws Exception {
        CountDownLatch never = new CountDownLatch(1);
        long started = System.nanoTime();
        assertThrows(SocketTimeoutException.class, () -> NativeRenewer.resolveWithin(
                "api.example.test", 300, host -> { never.await(30, TimeUnit.SECONDS); return null; }));
        long tookMs = TimeUnit.NANOSECONDS.toMillis(System.nanoTime() - started);
        assertTrue("took " + tookMs, tookMs >= 250 && tookMs < 5000);
    }

    @Test public void lookupFailuresStayTransientIoErrors() {
        UnknownHostException unknown = new UnknownHostException("x");
        IOException thrown = assertThrows(IOException.class, () -> NativeRenewer.resolveWithin(
                "api.example.test", 1000, host -> { throw unknown; }));
        assertSame(unknown, thrown);
        IOException other = assertThrows(IOException.class, () -> NativeRenewer.resolveWithin(
                "api.example.test", 1000, host -> { throw new IllegalStateException(); }));
        assertEquals("dns_failed", other.getMessage());
        assertThrows(UnknownHostException.class, () -> NativeRenewer.resolveWithin(
                "api.example.test", 1000, host -> new InetAddress[0]));
        assertThrows(SocketTimeoutException.class, () -> NativeRenewer.resolveWithin(
                "api.example.test", 0, host -> new InetAddress[] {loopback()}));
    }
}
