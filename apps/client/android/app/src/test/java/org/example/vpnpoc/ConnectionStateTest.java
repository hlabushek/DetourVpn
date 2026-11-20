package org.example.vpnpoc;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertNotEquals;

import org.junit.Test;

public class ConnectionStateTest {
    private static ConnectionState.Facts up() {
        var f = new ConnectionState.Facts();
        f.desired = true;
        f.tunnelUp = true;
        f.networkUsable = true;
        f.windowAgeMs = 600_000;
        return f;
    }

    private static String of(ConnectionState.Facts f) {
        return ConnectionState.of(f).toString();
    }

    @Test public void anIdleTunnelBetweenRekeysIsConnected() {
        // The old Flutter rule (60 s) showed "reconnecting" here although the tunnel worked.
        var f = up();
        f.handshakeAgeSeconds = 150;
        assertEquals("connected:verified", of(f));
        f.handshakeAgeSeconds = 180;
        assertEquals("connected:verified", of(f));
    }

    /** ADR-151: the fast re-check runs only while a tunnel is up, not connected, for 30 s. */
    @Test public void theFastRecheckStopsWhenConnectedDownOrAfterThirtySeconds() {
        var connecting = new ConnectionState(ConnectionState.Kind.CONNECTING, "awaiting_handshake");
        var recovering = new ConnectionState(ConnectionState.Kind.RECOVERING, "network_changed");
        var connected = new ConnectionState(ConnectionState.Kind.CONNECTED, "verified");
        org.junit.Assert.assertTrue(ConnectionState.recheckSoon(connecting, true, 0));
        org.junit.Assert.assertTrue(ConnectionState.recheckSoon(recovering, true, 29_999));
        org.junit.Assert.assertFalse(ConnectionState.recheckSoon(recovering, true, 30_000));
        org.junit.Assert.assertFalse(ConnectionState.recheckSoon(connected, true, 1_000));
        org.junit.Assert.assertFalse(ConnectionState.recheckSoon(recovering, false, 1_000));
        org.junit.Assert.assertFalse(ConnectionState.recheckSoon(null, true, 1_000));
        org.junit.Assert.assertFalse(ConnectionState.recheckSoon(connecting, true, -1));
    }

    /** ADR-151: once the restart's handshake is known, the pending trigger no longer shows. */
    @Test public void aProvenRestartShowsConnectedWhileTheTriggerIsStillPending() {
        var f = up();
        f.networkChangeAgeMs = 3_000; // the change; the restart followed 0.5 s later
        f.handshakeAgeSeconds = 1; // after the restart
        f.restartPending = false; // awaitingNetworkProof: proven
        assertEquals("connected:verified", of(f));
        f.restartPending = true; // still unproven
        assertEquals("recovering:network_changed", of(f));
    }

    @Test public void aHandshakeOlderThanTheRejectTimeIsStale() {
        var f = up();
        f.handshakeAgeSeconds = 181;
        assertEquals("recovering:handshake_stale", of(f));
    }

    @Test public void inboundDataAloneProvesTheTunnelWhenTheNetworkDidNotJustChange() {
        var f = up();
        f.handshakeAgeSeconds = 400;
        f.inboundFlowing = true;
        assertEquals("connected:verified", of(f));
        f.networkChangeAgeMs = 10_000;
        assertEquals("inbound bytes from before a switch prove nothing",
                "recovering:network_changed", of(f));
    }

    @Test public void aHandshakeFromBeforeANetworkChangeIsNotProof() {
        var f = up();
        f.handshakeAgeSeconds = 30;
        f.networkChangeAgeMs = 5_000;
        assertEquals("recovering:network_changed", of(f));
        f.handshakeAgeSeconds = 2;
        assertEquals("connected:verified", of(f));
    }

    @Test public void pendingRestartNoNetworkAndStallAreRecovering() {
        var f = up();
        f.handshakeAgeSeconds = 10;
        f.restartPending = true;
        assertEquals("recovering:network_changed", of(f));
        f.restartPending = false;
        f.networkUsable = false;
        assertEquals("recovering:waiting_for_network", of(f));
        f.networkUsable = true;
        f.dataStalled = true;
        assertEquals("recovering:data_stalled", of(f));
    }

    /** ADR-146 (4.2): a running tunnel is marked recovering only once the loss is confirmed. */
    @Test public void anUnconfirmedLossKeepsAWorkingTunnelConnected() {
        var f = up();
        f.handshakeAgeSeconds = 20;
        f.networkUsable = false;
        f.networkLossPending = true;
        assertEquals("connected:verified", of(f));
        f.networkLossPending = false;
        assertEquals("recovering:waiting_for_network", of(f));
        var down = new ConnectionState.Facts();
        down.desired = true;
        down.networkLossPending = true;
        assertEquals("a down tunnel waits for the network at once",
                "recovering:waiting_for_network", ConnectionState.of(down).toString());
    }

    @Test public void aNewWindowWaitsForItsFirstHandshakeThenReportsIt() {
        var f = up();
        f.windowAgeMs = 5_000;
        assertEquals("connecting:awaiting_handshake", of(f));
        f.windowAgeMs = ConnectionState.AWAIT_HANDSHAKE_MS;
        assertEquals("recovering:no_handshake", of(f));
    }

    @Test public void downButWantedIsRecoveringWithTheCause() {
        var f = new ConnectionState.Facts();
        f.desired = true;
        f.networkUsable = true;
        f.lastStop = "lease_expired";
        assertEquals("recovering:window_expired", of(f));
        f.restoring = true;
        assertEquals("recovering:restoring", of(f));
        f.restoring = false;
        f.networkUsable = false;
        assertEquals("recovering:waiting_for_network", of(f));
        f.networkUsable = true;
        f.lastStop = "";
        assertEquals("recovering:reconnecting", of(f));
    }

    @Test public void notWantedIsDisconnectedUnlessATerminalFailureEndedIt() {
        var f = new ConnectionState.Facts();
        assertEquals("disconnected:none", of(f));
        f.lastStop = "user_stop";
        assertEquals("disconnected:none", of(f));
        f.lastStop = "vpn_revoked";
        assertEquals("failed:permission_revoked", of(f));
        f.lastStop = "quota_exhausted";
        assertEquals("failed:quota_exhausted", of(f));
    }

    @Test public void equalityIsKindAndReason() {
        assertEquals(new ConnectionState(ConnectionState.Kind.CONNECTED, "verified"),
                new ConnectionState(ConnectionState.Kind.CONNECTED, "verified"));
        assertNotEquals(new ConnectionState(ConnectionState.Kind.RECOVERING, "a"),
                new ConnectionState(ConnectionState.Kind.RECOVERING, "b"));
    }

    /** ADR-148 (5.3): a captive portal asks the user to sign in, up or down. */
    @Test public void aCaptivePortalShowsTheSignInHint() {
        var f = up();
        f.handshakeAgeSeconds = 30;
        f.networkUsable = false;
        f.captivePortal = true;
        assertEquals("recovering:captive_portal", of(f));
        f.tunnelUp = false;
        assertEquals("recovering:captive_portal", of(f));
        f.captivePortal = false;
        assertEquals("recovering:waiting_for_network", of(f));
    }

    /** ADR-148: the probe ladder's stop is a transient, recovering reason, never a failure. */
    @Test public void aDeadPeerStopIsRecovering() {
        var f = new ConnectionState.Facts();
        f.desired = true;
        f.networkUsable = true;
        f.lastStop = "dead_peer";
        assertEquals("recovering:dead_peer", of(f));
        f.lastStop = "dead_peer_failover";
        assertEquals("recovering:dead_peer", of(f));
    }
}
