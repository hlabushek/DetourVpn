package org.example.vpnpoc;

import android.content.Context;
import android.content.Intent;
import org.amnezia.awg.backend.GoBackend;
import org.amnezia.awg.backend.Tunnel;
import org.amnezia.awg.config.Config;
import java.util.Set;

/** All calls must use the application worker; backend lifecycle cleanup is synchronized. */
final class AwgProviderV1 implements TunnelProviderV1 {
    private final GoBackend backend;
    private final Context context;
    private final Tunnel tunnel;
    AwgProviderV1(Context context, Tunnel tunnel) {
        this.context = context;
        this.tunnel = tunnel;
        backend = new GoBackend(context);
        if (!backend.getVersion().equals("v3.1.20260814")) throw new IllegalStateException();
    }
    @Override public Set<String> capabilities() {
        return Set.of("awg3", "userspace", "ipv4-full-route", "ephemeral-device-key");
    }
    @Override public void connect(Config config) throws Exception {
        if (!capabilities().contains("awg3")) throw new IllegalStateException();
        try { backend.setState(tunnel, Tunnel.State.UP, config); }
        catch (Exception failure) { disconnect(); throw failure; }
    }
    @Override public void disconnect() throws Exception {
        boolean wasUp = backend.getState(tunnel) == Tunnel.State.UP;
        try { backend.setState(tunnel, Tunnel.State.DOWN, null); }
        finally {
            // During Android system bootstrap the idle VPN service owns the protected API relay.
            // Keep that service alive while no tunnel exists; active tunnels still stop normally.
            if (wasUp) context.stopService(new Intent(context, GoBackend.VpnService.class));
        }
    }
    @Override public boolean isUp() { return backend.getState(tunnel) == Tunnel.State.UP; }
    @Override public long lastHandshakeSeconds() { return backend.getLastHandshake(tunnel); }
}
