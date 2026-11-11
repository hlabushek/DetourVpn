package org.example.vpnpoc;

import org.junit.Test;
import static org.junit.Assert.*;

public class TunnelWatchdogTest {
    @Test public void idleOrOfflineTunnelIsNeverRestarted() {
        var w = new TunnelWatchdog();
        assertFalse(w.shouldRestart(1_000_000, -1, true)); // not running
        w.tunnelUp(0);
        assertFalse(w.shouldRestart(500_000, 400_000, false)); // no validated network
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

    @Test public void networkChangeRestartsOnceAfterSettlingUnlessAlreadyHandshaked() {
        var w = new TunnelWatchdog();
        w.tunnelUp(0);
        w.networkChanged(100_000);
        assertFalse(w.shouldRestart(101_000, 30_000, true)); // still settling
        assertTrue(w.shouldRestart(103_000, 30_000, true)); // handshake predates the change
        w.restarted(103_000);
        assertFalse(w.shouldRestart(104_000, 31_000, true)); // the restart itself is settling

        w.networkChanged(200_000);
        assertFalse(w.shouldRestart(204_000, 2_000, true)); // handshake already on new network

        w.networkChanged(210_000);
        w.restarted(212_000);
        assertFalse(w.shouldRestart(214_000, 50_000, true)); // within 15 s of last restart
    }

    @Test public void networkChangeWhileDownIsIgnored() {
        var w = new TunnelWatchdog();
        w.networkChanged(10_000);
        w.tunnelUp(20_000);
        assertFalse(w.shouldRestart(30_000, 5_000, true));
    }

    /** ADR-122: a change too soon after a restart waits for the gap instead of being dropped. */
    @Test public void quickSecondChangeIsPostponedNotDropped() {
        var w = new TunnelWatchdog();
        w.tunnelUp(0);
        w.networkChanged(100_000);
        assertTrue(w.shouldRestart(103_000, 50_000, true));
        w.restarted(103_000);
        w.networkChanged(105_000); // Wi-Fi -> mobile right after
        assertFalse(w.shouldRestart(108_500, 55_000, true)); // gap to 118 s
        assertEquals(9_500, w.pendingNetworkDelayMs(108_500));
        assertTrue(w.shouldRestart(118_000, 65_000, true)); // still no handshake since 105 s
    }

    /** ADR-122: no validated network yet keeps the change pending. */
    @Test public void changeWaitsForAValidatedNetwork() {
        var w = new TunnelWatchdog();
        w.tunnelUp(0);
        w.networkChanged(100_000);
        assertFalse(w.shouldRestart(104_000, 40_000, false));
        assertFalse(w.shouldRestart(110_000, 46_000, false));
        assertTrue(w.shouldRestart(112_000, 48_000, true));
    }

    /** ADR-122: a restart without a handshake is retried twice, then left to the stall rule. */
    @Test public void networkRestartIsRetriedAtMostTwiceAndNeverRaisesTheBackoff() {
        var w = new TunnelWatchdog();
        w.tunnelUp(0);
        w.networkChanged(100_000);
        assertTrue(w.shouldRestart(103_000, 50_000, true));
        w.restarted(103_000);
        assertEquals(15_000, w.pendingNetworkDelayMs(103_000));
        assertTrue(w.shouldRestart(118_000, 65_000, true)); // retry 1
        w.restarted(118_000);
        assertTrue(w.shouldRestart(133_000, 80_000, true)); // retry 2
        w.restarted(133_000);
        assertEquals(-1, w.pendingNetworkDelayMs(133_000));
        assertFalse(w.shouldRestart(150_000, 97_000, true)); // done; stall rule needs 180 s
        assertEquals(TunnelWatchdog.STALE_GAP_MS, w.staleGapMs());
    }

    /** ADR-122: a handshake after the restart ends the retries. */
    @Test public void handshakeAfterARestartEndsTheRetries() {
        var w = new TunnelWatchdog();
        w.tunnelUp(0);
        w.networkChanged(100_000);
        assertTrue(w.shouldRestart(103_000, 50_000, true));
        w.restarted(103_000);
        assertFalse(w.shouldRestart(118_000, 5_000, true)); // handshake 5 s ago, after the restart
        assertEquals(-1, w.pendingNetworkDelayMs(118_000));
    }
}
