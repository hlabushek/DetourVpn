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
}
