package org.example.vpnpoc;

import android.app.Application;
import android.content.Intent;
import android.net.ConnectivityManager;
import android.net.NetworkCapabilities;
import android.os.SystemClock;
import java.util.HashMap;
import java.util.Map;
import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicLong;
import org.amnezia.awg.backend.Tunnel;
import org.amnezia.awg.backend.GoBackend;
import org.amnezia.awg.crypto.KeyPair;

/** Application-scoped native owner. Private key never crosses the channel. */
public final class ClientApplication extends Application implements Tunnel {
    final ScheduledExecutorService worker = Executors.newSingleThreadScheduledExecutor();
    final AtomicLong generation = new AtomicLong();
    KeyPair key;
    SecureVault vault;
    final WindowDeadline window = new WindowDeadline();
    TunnelProviderV1 provider;
    long deadline;
    String reason = "";
    @Override public void onCreate() {
        super.onCreate();
        worker.execute(() -> {
            try {
                // Process restart never resumes a peer without its owner/lease.
                stopService(new Intent(this, GoBackend.VpnService.class));
                vault = new SecureVault(this);
                key = vault.device();
                provider = new AwgProviderV1(this, this);
            } catch (Exception | LinkageError ignored) { reason = "native_unavailable"; }
        });
        worker.scheduleWithFixedDelay(() -> {
            if (deadline > 0 && SystemClock.elapsedRealtime() >= deadline) {
                try { stop(); reason = "lease_expired"; }
                catch (Exception ignored) { reason = "native_disconnect_failed"; }
            }
        }, 250, 250, TimeUnit.MILLISECONDS);
    }
    void stop() throws Exception {
        if (provider != null) provider.disconnect();
        deadline = 0;
        window.clear();
    }
    Map<String, Object> snapshot() {
        if (provider == null) throw new IllegalStateException();
        boolean up = provider.isUp();
        long handshake = up ? provider.lastHandshakeSeconds() : 0;
        long now = System.currentTimeMillis() / 1000;
        boolean network = false;
        ConnectivityManager manager = (ConnectivityManager) getSystemService(CONNECTIVITY_SERVICE);
        for (var candidate : manager.getAllNetworks()) {
            NetworkCapabilities caps = manager.getNetworkCapabilities(candidate);
            if (caps != null && caps.hasCapability(NetworkCapabilities.NET_CAPABILITY_NOT_VPN)
                    && caps.hasCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET)
                    && caps.hasCapability(NetworkCapabilities.NET_CAPABILITY_VALIDATED)) network = true;
        }
        Map<String, Object> value = new HashMap<>();
        value.put("up", up); value.put("network", network);
        value.put("handshakeAge", handshake > 0 && handshake <= now ? now - handshake : -1L);
        value.put("remainingMs", Math.max(0, deadline - SystemClock.elapsedRealtime()));
        value.put("reason", reason);
        return value;
    }
    @Override public String getName() { return "i013"; }
    @Override public void onStateChange(State state) { /* Status is read from serialized backend. */ }
}
