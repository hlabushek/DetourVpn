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
        assertFalse(w.shouldRestart(104_000, 31_000, true)); // consumed

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
}
