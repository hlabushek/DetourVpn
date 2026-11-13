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

    /** ADR-133: a handshake-only tunnel with no inbound data is degraded, never connected. */
    @Test public void stalledDataPathIsDegradedAndNeverRequestsRecovery() {
        assertEquals("connected", ConnectionSupervisor.transport(true, true, false, true, true, false, false));
        assertEquals("degraded", ConnectionSupervisor.transport(true, true, false, true, true, false, true));
        var s = connected();
        // The tunnel is up even while the data path is stalled: the supervisor takes no input for it.
        assertEquals(ConnectionSupervisor.Action.NONE, s.evaluate(1000, true, false, false, true).action);
        assertTrue("degraded keeps the user's intent", s.desired());
    }

    /** ADR-138 (H) + ADR-124: Wi-Fi to cellular, lost network, and return, as supervisor decisions. */
    @Test public void wifiToCellularAndBackAreRecoveryDecisions() {
        var s = connected();
        // Wi-Fi tunnel up, the default network changes to cellular: still usable, no action.
        assertEquals(ConnectionSupervisor.Action.NONE, s.evaluate(1000, true, false, false, true).action);
        // Wi-Fi dies before cellular is up: the tunnel is gone and nothing usable exists.
        s.tunnelDown("network_unavailable");
        assertEquals("waiting_for_network", ConnectionSupervisor.transport(true, false, false, false, false, false));
        assertEquals("waiting_for_network", s.evaluate(2000, false, false, false, false).why);
        // Cellular becomes usable: recovery starts at once (no earlier attempt, no backoff).
        var recover = s.evaluate(3000, false, false, false, true);
        assertEquals(ConnectionSupervisor.Action.START_RECOVERY, recover.action);
        s.attemptStarted(3000, 0.5);
        // The restore fails once on cellular: the next look waits for the backoff, not a storm.
        s.recoveryFailed("server_unreachable");
        assertEquals(ConnectionSupervisor.Action.WAIT, s.evaluate(4000, false, false, false, true).action);
        // Once the backoff has passed, cellular is still the usable network: try again.
        assertEquals(ConnectionSupervisor.Action.START_RECOVERY,
                s.evaluate(3000 + ConnectionSupervisor.BASE_DELAY_MS * 2, false, false, false, true).action);
    }

    /** ADR-134 (C): a refused background start keeps the intent and retries on the next trigger. */
    @Test public void refusedBackgroundStartKeepsIntentAndRetriesOnTheNextTrigger() {
        var s = connected();
        s.tunnelDown("lease_expired");
        assertEquals(ConnectionSupervisor.Action.START_RECOVERY,
                s.evaluate(10_000, false, false, false, true).action);
        s.attemptStarted(10_000, 0.5);
        s.startRefused(10_000);
        assertTrue("the user's intent is kept", s.desired());
        assertEquals("background_start_refused", s.stopCategory());
        assertEquals("a refusal is not a failed attempt, so no backoff grows", 0, s.attempts());
        // A trigger inside the minute waits; it does not hammer the system.
        var soon = s.evaluate(20_000, false, false, false, true);
        assertEquals(ConnectionSupervisor.Action.WAIT, soon.action);
        assertEquals(50_000, soon.delayMs);
        // The next allowed trigger after the minute starts recovery.
        assertEquals(ConnectionSupervisor.Action.START_RECOVERY,
                s.evaluate(70_000, false, false, false, true).action);
        // A start that is accepted clears the category; the next failure reports its own reason.
        s.attemptStarted(70_000, 0.5);
        assertEquals("none", s.stopCategory());
    }

    @Test public void backgroundRefusalIsTransientNotTerminal() {
        assertFalse(ConnectionSupervisor.terminal(ConnectionSupervisor.BACKGROUND_REFUSED));
        assertEquals("background_start_refused",
                ConnectionSupervisor.category(ConnectionSupervisor.BACKGROUND_REFUSED));
    }

    /** ADR-134 (D): after a process kill the persisted intent alone drives the next trigger. */
    @Test public void processRestartReconcilesPersistedIntentOnTheNextTrigger() {
        var recover = new ConnectionSupervisor();
        recover.restore(true);
        // Boot, network or app-open trigger with no Dart owner and a usable network.
        assertEquals(ConnectionSupervisor.Action.START_RECOVERY,
                recover.evaluate(5_000, false, false, false, true).action);

        var adopt = new ConnectionSupervisor();
        adopt.restore(true);
        // The service survived the process: the running tunnel is adopted, nothing is restarted.
        assertEquals(ConnectionSupervisor.Action.NONE, adopt.evaluate(5_000, true, false, false, true).action);

        var offline = new ConnectionSupervisor();
        offline.restore(true);
        assertEquals("waiting_for_network", offline.evaluate(5_000, false, false, false, false).why);

        var owned = new ConnectionSupervisor();
        owned.restore(true);
        assertEquals("a Dart owner is never competed with", "dart_owner",
                owned.evaluate(5_000, false, false, true, true).why);

        var off = new ConnectionSupervisor();
        off.restore(false);
        assertEquals("no persisted intent, nothing starts",
                ConnectionSupervisor.Action.NONE, off.evaluate(5_000, false, false, false, true).action);
    }

    @Test public void unknownReasonsAreTransient() {
        assertEquals("transient", ConnectionSupervisor.category("something_new"));
        assertFalse(ConnectionSupervisor.terminal("something_new"));
        assertFalse(ConnectionSupervisor.terminal(""));
    }

    /**
     * ADR-140: a wanted connection is never given up for a transient cause. Hours of failures keep
     * retrying, never further apart than the 5 min cap, and the intent stays.
     */
    @Test public void transientFailuresRetryForHoursWithoutATerminalState() {
        var s = connected();
        s.tunnelDown("lease_expired");
        long now = 0;
        int starts = 0;
        long generation = s.intentGeneration();
        while (now < 4 * 60 * 60_000L) {
            var d = s.evaluate(now, false, false, false, true);
            if (d.action == ConnectionSupervisor.Action.WAIT) {
                assertTrue("never more than the cap plus jitter",
                        d.delayMs <= ConnectionSupervisor.MAX_DELAY_MS * 5 / 4);
                now += d.delayMs;
                continue;
            }
            assertEquals(ConnectionSupervisor.Action.START_RECOVERY, d.action);
            s.attemptStarted(now, 0.999);
            starts++;
            assertFalse(s.recoveryFailed("api_unavailable"));
        }
        assertTrue("about one attempt per cap interval over four hours", starts >= 40);
        assertTrue(s.desired());
        assertEquals(generation, s.intentGeneration());
        assertFalse(ConnectionSupervisor.terminal("api_unavailable"));
        assertEquals(5 * 60_000L, ConnectionSupervisor.MAX_DELAY_MS);
    }

    @Test public void offlineAttemptsAreNotCountedAndTheReturnRetriesAtOnce() {
        var s = connected();
        s.tunnelDown("lease_expired");
        for (int i = 0; i < 10; i++) {
            s.attemptStarted(0, 0.5);
            s.recoveryFailed("network_unavailable");
        }
        int attempts = s.attempts();
        for (long t = 1_000; t < 60 * 60_000L; t += 60_000)
            assertEquals("waiting_for_network", s.evaluate(t, false, false, false, false).why);
        assertEquals("waiting does not spend attempts", attempts, s.attempts());
        var back = s.evaluate(60 * 60_000L, false, false, false, true);
        assertEquals(ConnectionSupervisor.Action.START_RECOVERY, back.action);
        assertEquals("network_returned", back.why);
        assertTrue(s.desired());
    }

    @Test public void aFlappingNetworkDoesNotStartAttemptsBackToBack() {
        var s = connected();
        s.tunnelDown("lease_expired");
        s.attemptStarted(100_000, 0.5);
        s.recoveryFailed("network_unavailable");
        s.evaluate(101_000, false, false, false, false);
        var back = s.evaluate(102_000, false, false, false, true);
        assertEquals(ConnectionSupervisor.Action.WAIT, back.action);
        assertEquals(ConnectionSupervisor.NETWORK_RETURN_GAP_MS - 2_000, back.delayMs);
        assertEquals(ConnectionSupervisor.Action.START_RECOVERY,
                s.evaluate(110_000, false, false, false, true).action);
        s.attemptStarted(110_000, 0.5);
        // Without another network loss the normal backoff applies again.
        assertEquals(ConnectionSupervisor.Action.WAIT,
                s.evaluate(111_000, false, false, false, true).action);
    }

    @Test public void attachedDartOwnerKeepsNativeOut() {
        var s = connected();
        s.tunnelDown("lease_expired");
        for (int i = 0; i < 20; i++) s.attemptStarted(0, 0.5);
        assertEquals("dart_owner", s.evaluate(1, false, false, true, true).why);
        assertTrue(s.desired());
    }

    @Test public void legacyExhaustedReasonStaysNamedAndTerminal() {
        assertEquals("recovery_exhausted", ConnectionSupervisor.category("recovery_exhausted"));
        assertTrue(ConnectionSupervisor.terminal("recovery_exhausted"));
        var s = connected();
        assertTrue(s.tunnelDown("recovery_exhausted"));
        assertFalse(s.desired());
        s.userConnected();
        assertTrue(s.desired());
        assertEquals(0, s.attempts());
        s.tunnelDown("lease_expired");
        assertEquals(ConnectionSupervisor.Action.START_RECOVERY,
                s.evaluate(2, false, false, false, true).action);
    }
}
