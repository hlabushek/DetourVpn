package org.example.vpnpoc;

import android.content.Context;
import org.amnezia.awg.backend.GoBackend;
import org.amnezia.awg.backend.Tunnel;
import org.amnezia.awg.config.Config;
import java.util.Set;

/** All calls must use the application worker; backend lifecycle cleanup is synchronized. */
final class AwgProviderV1 implements TunnelProviderV1 {
    private final GoBackend backend;
    private final Tunnel tunnel;
    AwgProviderV1(Context context, Tunnel tunnel) {
        this.tunnel = tunnel;
        backend = new GoBackend(context);
        if (!backend.getVersion().equals("v3.1.20260814")) throw new IllegalStateException();
    }
    @Override public Set<String> capabilities() {
        return Set.of("awg3", "userspace", "ipv4-full-route", "ephemeral-device-key",
                "traffic-stats-advisory.v1");
    }
    @Override public void connect(Config config) throws Exception {
        if (!capabilities().contains("awg3")) throw new IllegalStateException();
        try { backend.setState(tunnel, Tunnel.State.UP, config); }
        catch (Exception failure) { disconnect(); throw failure; }
    }
    @Override public void disconnect() throws Exception {
        // GoBackend stops its VpnService after tearing down an active tunnel.
        // Calling Context.stopService here as well races that lifecycle against a
        // fast reconnect and can leave the next UP request using a dying service.
        backend.setState(tunnel, Tunnel.State.DOWN, null);
    }
    @Override public boolean isUp() { return backend.getState(tunnel) == Tunnel.State.UP; }
    @Override public long lastHandshakeSeconds() { return backend.getLastHandshake(tunnel); }
    @Override public long transferBytes() {
        if (!isUp()) return -1;
        var stats = backend.getStatistics(tunnel);
        if (stats.peers().length != 1) return -1;
        long rx = stats.totalRx(), tx = stats.totalTx();
        if (rx < 0 || tx < 0) return -1;
        try { return Math.addExact(rx, tx); }
        catch (ArithmeticException ignored) { return -1; }
    }
    @Override public long receivedBytes() {
        if (!isUp()) return -1;
        var stats = backend.getStatistics(tunnel);
        if (stats.peers().length != 1) return -1;
        long rx = stats.totalRx();
        return rx >= 0 ? rx : -1;
    }
}
