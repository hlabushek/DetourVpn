package org.example.vpnpoc;

import org.junit.Test;
import static org.junit.Assert.*;

public class TunnelWatchdogTest {
    @Test public void idleOrOfflineTunnelIsNeverRestarted() {
        var w = new TunnelWatchdog();
        assertFalse(w.shouldRestart(1_000_000, -1, true)); // not running
        w.tunnelUp(0);
        assertFalse(w.shouldRestart(500_000, 400_000, false)); // no usable network
        w.tunnelDown();
        assertFalse(w.shouldRestart(900_000, 900_000, true));
    }

    @Test public void staleHandshakeRestartsWithGrowingBackoffAndFreshHandshakeResets() {
        var w = new TunnelWatchdog();
        w.tunnelUp(0);
        assertFalse(w.shouldRestart(170_000, 170_000, true));
        assertFalse(w.shouldRestart(179_999, -1, true));
        assertTrue(w.shouldRestart(180_000, 180_000, true));
        w.restarted(180_000);
        assertFalse(w.shouldRestart(250_000, 250_000, true)); // within the 2 min gap
        assertTrue(w.shouldRestart(420_000, 400_000, true)); // gap 240 s after one attempt
        w.restarted(420_000);
        assertFalse(w.shouldRestart(700_000, 500_000, true));
        assertEquals(480_000, w.staleGapMs());
        assertFalse(w.shouldRestart(700_000, 10_000, true)); // fresh handshake: healthy
        assertEquals(120_000, w.staleGapMs());
        for (int i = 0; i < 6; i++) w.restarted(0);
        assertEquals(TunnelWatchdog.MAX_GAP_MS, w.staleGapMs());
    }

    /** ADR-146: a new usable network is acted on after 0.5 s, unless it already has a handshake. */
    @Test public void networkChangeRestartsHalfASecondLaterUnlessAlreadyHandshaked() {
        var w = new TunnelWatchdog();
        w.tunnelUp(0);
        w.networkChanged(100_000, 0);
        assertFalse(w.shouldRestart(100_400, 30_000, true)); // still settling
        assertEquals(100, w.pendingNetworkDelayMs(100_400));
        assertTrue(w.shouldRestart(100_500, 30_000, true)); // handshake predates the change
        w.restarted(100_500);
        assertFalse(w.shouldRestart(101_000, 31_000, true)); // the restart itself is settling

        w.networkChanged(200_000, 0);
        assertFalse(w.shouldRestart(200_600, 300, true)); // handshake already on new network
        assertEquals(-1, w.pendingNetworkDelayMs(200_600));

        w.networkChanged(210_000, 0);
        w.restarted(212_000);
        assertFalse(w.shouldRestart(214_000, 50_000, true)); // within 15 s of last restart
    }

    /**
     * ADR-151: after a restart in place the next watchdog check is 15.5 s away, but a handshake
     * after the restart already proves the path; the displayed state must not wait for that check.
     */
    @Test public void aHandshakeAfterTheRestartEndsTheProofWaitBeforeTheNextCheck() {
        var w = new TunnelWatchdog();
        w.tunnelUp(0);
        w.networkChanged(100_000, 0);
        assertTrue(w.awaitingNetworkProof(100_100, 30_000)); // settling, old handshake
        assertTrue(w.shouldRestart(100_500, 30_000, true));
        w.restarted(100_500);
        assertTrue(w.pendingNetworkDelayMs(102_000) >= 0); // trigger still pending (gap)
        assertTrue(w.awaitingNetworkProof(101_000, 31_000)); // no handshake since the restart
        assertTrue(w.awaitingNetworkProof(101_500, -1)); // no handshake at all
        // Handshake 1 s after the restart, seen 2 s after it: proven, although still pending.
        assertFalse(w.awaitingNetworkProof(102_500, 1_000));
        assertTrue(w.pendingNetworkDelayMs(102_500) >= 0);
        // Negative: a handshake from before the restart proves nothing.
        assertTrue(w.awaitingNetworkProof(102_500, 3_000));
        // Read-only: the trigger is still cleared only by decide().
        assertFalse(w.shouldRestart(116_000, 14_000, true));
        assertEquals(-1, w.pendingNetworkDelayMs(116_000));
        assertFalse(w.awaitingNetworkProof(116_000, -1)); // nothing pending any more
    }

    @Test public void networkChangeWhileDownIsIgnored() {
        var w = new TunnelWatchdog();
        w.networkChanged(10_000, -1);
        assertFalse(w.running());
        w.tunnelUp(20_000);
        assertFalse(w.shouldRestart(30_000, 5_000, true));
    }

    /** ADR-146: a change before the first watch tick was dropped; it now counts. */
    @Test public void changeBeforeTheFirstTickIsNotDropped() {
        var w = new TunnelWatchdog();
        assertFalse(w.running());
        w.networkChanged(5_000, 1_000); // tunnel up since 1 s, no tick yet
        assertTrue(w.running());
        assertEquals(500, w.pendingNetworkDelayMs(5_000));
        assertTrue(w.shouldRestart(5_500, -1, true));
    }

    /** ADR-146: a network change resets the stall backoff (a new path deserves a fresh start). */
    @Test public void networkChangeResetsTheStallBackoff() {
        var w = new TunnelWatchdog();
        w.tunnelUp(0);
        assertTrue(w.shouldRestart(180_000, 180_000, true));
        w.restarted(180_000);
        assertEquals(240_000, w.staleGapMs());
        w.networkChanged(200_000, 0);
        assertEquals(TunnelWatchdog.STALE_GAP_MS, w.staleGapMs());
    }

    /** ADR-122: a change too soon after a restart waits for the gap instead of being dropped. */
    @Test public void quickSecondChangeIsPostponedNotDropped() {
        var w = new TunnelWatchdog();
        w.tunnelUp(0);
        w.networkChanged(100_000, 0);
        assertTrue(w.shouldRestart(100_500, 50_000, true));
        w.restarted(100_500);
        w.networkChanged(105_000, 0); // Wi-Fi -> mobile right after
        assertFalse(w.shouldRestart(108_500, 55_000, true)); // gap to 115.5 s
        assertEquals(7_000, w.pendingNetworkDelayMs(108_500));
        assertTrue(w.shouldRestart(115_500, 65_000, true)); // still no handshake since 105 s
    }

    /** ADR-122: no usable network yet keeps the change pending. */
    @Test public void changeWaitsForAUsableNetwork() {
        var w = new TunnelWatchdog();
        w.tunnelUp(0);
        w.networkChanged(100_000, 0);
        assertFalse(w.shouldRestart(104_000, 40_000, false));
        assertFalse(w.shouldRestart(110_000, 46_000, false));
        assertTrue(w.shouldRestart(112_000, 48_000, true));
    }

    /**
     * ADR-146 (4.4): without a handshake after the change, two restarts run, then a new lease is
     * asked for. Network restarts never raise the stall backoff.
     */
    @Test public void twoRestartsWithoutAHandshakeThenANewLease() {
        var w = new TunnelWatchdog();
        w.tunnelUp(0);
        w.networkChanged(100_000, 0);
        assertEquals(TunnelWatchdog.Action.RESTART, w.decide(100_500, 50_000, true));
        w.restarted(100_500);
        assertEquals(15_000, w.pendingNetworkDelayMs(100_500));
        assertEquals(TunnelWatchdog.Action.NONE, w.decide(110_000, 60_000, true)); // gap
        assertEquals(TunnelWatchdog.Action.RESTART, w.decide(115_500, 65_000, true)); // restart 2
        w.restarted(115_500);
        assertEquals(TunnelWatchdog.Action.NONE, w.decide(120_000, 70_000, false)); // offline
        assertEquals(TunnelWatchdog.Action.NEW_LEASE, w.decide(130_500, 80_000, true));
        w.tunnelDown();
        assertEquals(-1, w.pendingNetworkDelayMs(130_500));
        assertEquals(TunnelWatchdog.STALE_GAP_MS, w.staleGapMs());
    }

    /** ADR-146: a new change during verification starts a new budget of two restarts. */
    @Test public void aNewChangeStartsANewRestartBudget() {
        var w = new TunnelWatchdog();
        w.tunnelUp(0);
        w.networkChanged(100_000, 0);
        assertTrue(w.shouldRestart(100_500, -1, true));
        w.restarted(100_500);
        assertTrue(w.shouldRestart(115_500, -1, true));
        w.restarted(115_500);
        w.networkChanged(120_000, 0);
        assertEquals(TunnelWatchdog.Action.RESTART, w.decide(130_500, -1, true));
    }

    /** ADR-122/146: a handshake after the restart is the proof; it ends the retries. */
    @Test public void handshakeAfterARestartEndsTheRetries() {
        var w = new TunnelWatchdog();
        w.tunnelUp(0);
        w.networkChanged(100_000, 0);
        assertTrue(w.shouldRestart(100_500, 50_000, true));
        w.restarted(100_500);
        assertEquals(TunnelWatchdog.Action.NONE, w.decide(115_500, 5_000, true)); // 5 s ago
        assertEquals(-1, w.pendingNetworkDelayMs(115_500));
    }

    /** ADR-148: while the probe ladder owns stalls, only network-change restarts remain. */
    @Test public void staleRestartsCanBeLeftToTheProbe() {
        var w = new TunnelWatchdog();
        w.tunnelUp(0);
        assertEquals(TunnelWatchdog.Action.NONE, w.decide(400_000, 400_000, true, false));
        assertEquals(TunnelWatchdog.Action.RESTART, w.decide(400_000, 400_000, true, true));
        w.networkChanged(500_000, 0);
        assertEquals("network restarts stay", TunnelWatchdog.Action.RESTART,
                w.decide(500_600, 400_000, true, false));
    }

    @Test public void aProbeRestartDelaysANetworkRestartButNotTheStaleBackoff() {
        var w = new TunnelWatchdog();
        w.tunnelUp(0);
        w.restartedByProbe(400_000);
        assertEquals(TunnelWatchdog.STALE_GAP_MS, w.staleGapMs());
        w.networkChanged(401_000, 0);
        assertEquals(TunnelWatchdog.Action.NONE, w.decide(401_600, 400_000, true, false));
        assertEquals(TunnelWatchdog.Action.RESTART,
                w.decide(400_000 + TunnelWatchdog.NETWORK_GAP_MS, 400_000, true, false));
    }
}
