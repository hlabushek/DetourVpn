package org.example.vpnpoc;

import android.content.ComponentName;
import android.content.Context;
import android.content.Intent;
import android.content.ServiceConnection;
import android.os.Build;
import android.os.IBinder;
import org.amnezia.awg.config.Config;
import java.util.ArrayList;
import java.util.List;
import java.util.Set;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;

/** libbox-backed AWG provider. Calls are serialized by ClientApplication.worker. */
final class LibboxProviderV1 implements TunnelProviderV1 {
    private final Context context;
    private LibboxVpnService.LocalBinder binder;
    private ServiceConnection connection;
    private boolean bound;

    LibboxProviderV1(Context context) { this.context = context.getApplicationContext(); }

    @Override public Set<String> capabilities() {
        return Set.of("awg3", "userspace", "ipv4-full-route", "ephemeral-device-key",
                "traffic-stats-advisory.v1", "domain-routing.v1", "package-routing.v1");
    }

    @Override public void connect(Config config) throws Exception {
        var preferences = context.getSharedPreferences("routing", Context.MODE_PRIVATE);
        String mode = preferences.getString("mode", "exclude");
        var packages = new ArrayList<>(preferences.getStringSet("selected", Set.of()));
        var domains = new ArrayList<>(preferences.getStringSet("domains", Set.of()));
        String engineConfig = LibboxConfigFactory.create(config, mode, packages, domains,
                context.getPackageName());
        Intent intent = new Intent(context, LibboxVpnService.class).setAction(LibboxVpnService.ACTION_START);
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) context.startForegroundService(intent);
        else context.startService(intent);
        bind(intent);
        try {
            binder.connect(engineConfig);
        } catch (Exception failure) {
            disconnect();
            throw failure;
        }
    }

    private void bind(Intent intent) throws Exception {
        if (binder != null && bound) return;
        CountDownLatch connected = new CountDownLatch(1);
        connection = new ServiceConnection() {
            @Override public void onServiceConnected(ComponentName name, IBinder service) {
                binder = (LibboxVpnService.LocalBinder) service;
                connected.countDown();
            }
            @Override public void onServiceDisconnected(ComponentName name) { binder = null; }
        };
        bound = context.bindService(intent, connection, Context.BIND_AUTO_CREATE);
        if (!bound || !connected.await(15, TimeUnit.SECONDS) || binder == null) {
            if (bound) context.unbindService(connection);
            bound = false;
            connection = null;
            throw new IllegalStateException("vpn_service_unavailable");
        }
    }

    @Override public void disconnect() {
        LibboxVpnService.LocalBinder active = binder;
        if (active != null) active.disconnect();
        binder = null;
        if (bound && connection != null) {
            try { context.unbindService(connection); } catch (IllegalArgumentException ignored) { }
        }
        bound = false;
        connection = null;
        context.stopService(new Intent(context, LibboxVpnService.class));
    }

    @Override public boolean isUp() {
        LibboxVpnService.LocalBinder active = binder;
        return active != null && active.isUp();
    }

    @Override public long lastHandshakeSeconds() {
        try {
            org.json.JSONObject stats = stats();
            return stats.getLong("last_handshake_time_sec");
        } catch (Exception unavailable) { return 0; }
    }

    @Override public long transferBytes() {
        try {
            org.json.JSONObject stats = stats();
            if (stats.getInt("peer_count") != 1) return -1;
            return Math.addExact(stats.getLong("rx_bytes"), stats.getLong("tx_bytes"));
        } catch (Exception unavailable) { return -1; }
    }

    @Override public long receivedBytes() {
        try {
            org.json.JSONObject stats = stats();
            return stats.getInt("peer_count") == 1 ? stats.getLong("rx_bytes") : -1;
        } catch (Exception unavailable) { return -1; }
    }

    private org.json.JSONObject stats() throws Exception {
        LibboxVpnService.LocalBinder active = binder;
        if (active == null || !active.isUp()) throw new IllegalStateException("vpn_not_active");
        return new org.json.JSONObject(active.peerStats());
    }
}
