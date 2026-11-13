package org.example.vpnpoc;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import org.junit.Test;

public class ConnectionSupervisorTest {
    private static ConnectionSupervisor connected() {
        var s = new ConnectionSupervisor();
        s.userConnected();
        s.evaluate(0, true, false, true, true);
        return s;
    }

    @Test public void normalConnectAndDisconnect() {
        var s = connected();
        assertTrue(s.desired());
        assertEquals(ConnectionSupervisor.Action.NONE, s.evaluate(1000, true, false, true, true).action);
        long before = s.intentGeneration();
        s.userDisconnected();
        assertFalse(s.desired());
        assertTrue("disconnect bumps the generation", s.intentGeneration() > before);
        s.tunnelDown("user_stop");
        assertEquals("not_desired", s.evaluate(2000, false, false, false, true).why);
    }

    @Test public void backgroundingOrClosingTheUiDoesNotChangeIntent() {
        var s = connected();
        // UI engine detached (Home, Recents swipe, Activity destroyed): no intent call is made.
        assertTrue(s.desired());
        assertEquals(ConnectionSupervisor.Action.NONE, s.evaluate(5000, true, false, false, true).action);
    }

    @Test public void windowExpiryWithoutUiStartsOneRecoveryThenBacksOff() {
        var s = connected();
        assertFalse("expiry is recoverable", s.tunnelDown("lease_expired"));
        var first = s.evaluate(10_000, false, false, false, true);
        assertEquals(ConnectionSupervisor.Action.START_RECOVERY, first.action);
        s.attemptStarted(10_000, 0.5);
        s.recoveryFailed("api_unavailable");
        var next = s.evaluate(11_000, false, false, false, true);
        assertEquals(ConnectionSupervisor.Action.WAIT, next.action);
        assertEquals(29_000, next.delayMs); // 30 s base, jitter 0.5 = x1.0, minus 1 s elapsed
    }

    @Test public void backoffIsBoundedWithJitter() {
        var s = connected();
        s.tunnelDown("native_restart_failed");
        for (int i = 0; i < 20; i++) s.attemptStarted(0, 0.999);
        assertTrue(s.backoffMs() <= ConnectionSupervisor.MAX_DELAY_MS * 5 / 4);
        assertTrue(s.backoffMs() >= ConnectionSupervisor.MAX_DELAY_MS * 3 / 4);
        var low = connected();
        low.tunnelDown("lease_expired");
        low.attemptStarted(0, 0);
        assertEquals(22_500, low.backoffMs());
    }

    @Test public void noRetriesWhileOfflineAndPromptRetryWhenNetworkReturns() {
        var s = connected();
        s.tunnelDown("lease_expired");
        s.attemptStarted(0, 0.5);
        s.recoveryFailed("network_unavailable");
        var offline = s.evaluate(10_000_000, false, false, false, false);
        assertEquals(ConnectionSupervisor.Action.NONE, offline.action);
        assertEquals("waiting_for_network", offline.why);
        assertEquals(ConnectionSupervisor.Action.START_RECOVERY,
                s.evaluate(10_000_001, false, false, false, true).action);
    }

    @Test public void attachedDartControllerIsTheOnlyRecoveryOwner() {
        var s = connected();
        s.tunnelDown("lease_expired");
        assertEquals("dart_owner", s.evaluate(1, false, false, true, true).why);
        assertEquals("connecting", s.evaluate(1, false, true, false, true).why);
    }

    @Test public void explicitDisconnectDuringRecoveryCancelsItForGood() {
        var s = connected();
        s.tunnelDown("lease_expired");
        s.attemptStarted(0, 0.5);
        long started = s.intentGeneration();
        s.userDisconnected();
        assertTrue("a running attempt sees a newer generation", s.intentGeneration() != started);
        s.recoveryFailed("api_unavailable"); // late result of the cancelled attempt
        for (long t = 0; t < 10 * ConnectionSupervisor.MAX_DELAY_MS; t += 60_000)
            assertEquals(ConnectionSupervisor.Action.NONE, s.evaluate(t, false, false, false, true).action);
    }

    @Test public void terminalFailuresEndIntentAndNeverRetry() {
        for (String code : new String[] {"vpn_revoked", "quota_exhausted", "session_expired",
                "unauthorized", "native_config_invalid", "device_revoked"}) {
            var s = connected();
            assertTrue(code, s.tunnelDown(code));
            assertFalse(code, s.desired());
            assertEquals(code, ConnectionSupervisor.Action.NONE,
                    s.evaluate(1_000_000, false, false, false, true).action);
        }
    }

    @Test public void stablePeriodResetsRetryHistory() {
        var s = connected();
        s.tunnelDown("native_restart_failed");
        s.attemptStarted(0, 0.5);
        s.attemptStarted(0, 0.5);
        assertEquals(2, s.attempts());
        s.evaluate(100_000, true, false, false, true);
        s.evaluate(100_000 + ConnectionSupervisor.STABLE_MS - 1, true, false, false, true);
        assertEquals(2, s.attempts());
        s.evaluate(100_000 + ConnectionSupervisor.STABLE_MS, true, false, false, true);
        assertEquals(0, s.attempts());
    }

    @Test public void persistedIntentIsRestoredWithoutAnAttempt() {
        var s = new ConnectionSupervisor();
        s.restore(true);
        assertTrue(s.desired());
        assertEquals(0, s.attempts());
    }

    @Test public void transportStateIsDerivedFromFactsOnly() {
        assertEquals("disconnected", ConnectionSupervisor.transport(false, false, false, true, false, false));
        assertEquals("connecting", ConnectionSupervisor.transport(true, false, true, true, false, false));
        assertEquals("connected", ConnectionSupervisor.transport(true, true, false, true, true, false));
        assertEquals("idle tunnel without fresh handshake is degraded, not failed",
                "degraded", ConnectionSupervisor.transport(true, true, false, true, false, false));
        assertEquals("reconnecting", ConnectionSupervisor.transport(true, true, false, true, true, true));
        assertEquals("waiting_for_network", ConnectionSupervisor.transport(true, true, false, false, true, false));
        assertEquals("waiting_for_network", ConnectionSupervisor.transport(true, false, false, false, false, false));
        assertEquals("reconnecting", ConnectionSupervisor.transport(true, false, false, true, false, false));
    }

    @Test public void evidenceNeverOverclaims() {
        assertEquals("none", ConnectionSupervisor.evidence(false, 5, true, true));
        assertEquals("core_running", ConnectionSupervisor.evidence(true, -1, false, false));
        assertEquals("a handshake before the network change proves nothing",
                "core_running", ConnectionSupervisor.evidence(true, 10, false, false));
        assertEquals("peer_handshake", ConnectionSupervisor.evidence(true, 10, true, false));
        assertEquals("peer_and_inbound_data", ConnectionSupervisor.evidence(true, 10, true, true));
        assertEquals("inbound_data", ConnectionSupervisor.evidence(true, 900, true, true));
    }

    @Test public void unknownReasonsAreTransient() {
        assertEquals("transient", ConnectionSupervisor.category("something_new"));
        assertFalse(ConnectionSupervisor.terminal("something_new"));
        assertFalse(ConnectionSupervisor.terminal(""));
    }
}
