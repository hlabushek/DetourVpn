package org.example.vpnpoc;

import org.junit.Test;
import static org.junit.Assert.*;

/** Deterministic policy tests; no real Android network or tunnel is simulated as proof. */
public class ConnectionSupervisorTest {
    private ConnectionSupervisor online() {
        var p = new ConnectionSupervisor();
        p.network("wifi", 0);
        p.connect(0);
        return p;
    }
    @Test public void startupIsNotHealth() {
        var p = online();
        assertFalse(p.healthy(0));
        p.health(p.generation, p.networkGeneration, true, 10, .5);
        assertTrue(p.healthy(10));
        assertFalse(p.healthy(60_011));
    }
    @Test public void offlineRetainsIntentWithoutRetries() {
        var p = online();
        p.network(null, 1);
        assertTrue(p.desired);
        assertEquals(ConnectionSupervisor.State.WAITING_FOR_NETWORK, p.state);
        assertFalse(p.due(Long.MAX_VALUE));
        p.network("wifi-in-airplane-mode", 10);
        assertFalse(p.due(759));
        assertTrue(p.due(760));
    }
    @Test public void userStopInvalidatesInFlightSuccess() {
        var p = online();
        long session = p.generation, link = p.networkGeneration;
        p.stop("user_disconnected");
        p.health(session, link, true, 50, .5);
        assertFalse(p.desired);
        assertEquals(ConnectionSupervisor.State.DISCONNECTED, p.state);
        assertFalse(p.healthy(50));
    }
    @Test public void oldNetworkCannotConfirmNewNetwork() {
        var p = online();
        long link = p.networkGeneration;
        p.network("mobile", 10);
        p.health(p.generation, link, true, 20, .5);
        assertFalse(p.healthy(20));
        assertEquals(ConnectionSupervisor.State.RECONNECTING, p.state);
    }
    @Test public void oldSessionCannotConfirmNewSession() {
        var p = online();
        long session = p.generation;
        p.connect(10);
        p.health(session, p.networkGeneration, true, 20, .5);
        assertFalse(p.healthy(20));
    }
    @Test public void duplicateCapabilitiesDoNotRestart() {
        var p = online();
        long link = p.networkGeneration;
        assertFalse(p.network("wifi", 50));
        assertEquals(link, p.networkGeneration);
    }
    @Test public void failureImmediatelyRemovesHealth() {
        var p = online();
        p.health(p.generation, p.networkGeneration, true, 0, .5);
        p.health(p.generation, p.networkGeneration, false, 30_000, .5);
        assertFalse(p.healthy(30_000));
        assertEquals(ConnectionSupervisor.State.DEGRADED, p.state);
    }
    @Test public void successOnlyResetsBackoffAfterStablePeriod() {
        var p = online();
        p.health(p.generation, p.networkGeneration, false, 0, .5);
        p.health(p.generation, p.networkGeneration, true, 1000, .5);
        assertEquals(1, p.attempts);
        p.health(p.generation, p.networkGeneration, true, 121_000, .5);
        assertEquals(0, p.attempts);
    }
    @Test public void backoffIsBoundedAndJittered() {
        assertEquals(750, ConnectionSupervisor.backoff(0, 0));
        assertEquals(1250, ConnectionSupervisor.backoff(0, 1));
        for (int attempt = 0; attempt < 1000; attempt++) {
            assertTrue(ConnectionSupervisor.backoff(attempt, .9) <= 60_000);
            assertTrue(ConnectionSupervisor.backoff(attempt, .1) >= 500);
        }
    }
    @Test public void repeatedStopIsSafeAndCannotRevive() {
        var p = online();
        p.stop("revoked"); p.stop("revoked");
        p.network("mobile", 10);
        assertFalse(p.due(100_000));
        assertFalse(p.desired);
    }
    @Test public void rapidHandoverAcceptsOnlyLatest() {
        var p = online();
        for (int i = 0; i < 100; i++) {
            long old = p.networkGeneration;
            p.network("network-" + i, i);
            p.health(p.generation, old, true, i, .5);
            assertFalse(p.healthy(i));
        }
        p.health(p.generation, p.networkGeneration, true, 1000, .5);
        assertTrue(p.healthy(1000));
    }
    @Test public void terminalFailureNeverRetries() {
        var p = online();
        p.terminal("permission_revoked");
        assertFalse(p.due(Long.MAX_VALUE));
        assertEquals(ConnectionSupervisor.State.ERROR, p.state);
    }
    @Test public void idleTunnelDoesNotEscalateRetries() {
        var p = online();
        for (int i = 0; i < 100; i++) p.unconfirmed(i * 30_000L);
        assertEquals(0, p.attempts);
        assertEquals("awaiting_authenticated_data", p.failure);
        assertFalse(p.healthy(3_000_000));
        assertTrue(p.desired);
    }
}
