package org.example.vpnpoc;

import android.net.ConnectivityManager;
import android.net.Network;
import android.net.NetworkCapabilities;
import android.net.NetworkRequest;
import android.net.LinkProperties;
import android.os.SystemClock;
import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledFuture;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicBoolean;
import org.amnezia.awg.config.Config;

/** All mutable policy/resource state belongs to ClientApplication.worker. */
final class AndroidConnectionRuntime {
    final ConnectionSupervisor policy = new ConnectionSupervisor();
    private final ClientApplication app;
    private final ConnectivityManager connectivity;
    private final AtomicBoolean refreshQueued = new AtomicBoolean();
    private ConnectivityManager.NetworkCallback callback;
    private ScheduledFuture<?> scheduled;
    private Config config;
    private Network selected;

    AndroidConnectionRuntime(ClientApplication app) {
        this.app = app;
        connectivity = app.getSystemService(ConnectivityManager.class);
    }

    void start(Config authorized) {
        if (callback != null) {
            connectivity.unregisterNetworkCallback(callback);
            callback = null;
        }
        stop("");
        if (!app.provider.capabilities().contains("underlying-network-binding.v1"))
            throw new IllegalArgumentException("network_binding_unavailable");
        config = authorized;
        policy.connect(now());
        callback = new ConnectivityManager.NetworkCallback() {
            @Override public void onAvailable(Network n) { requestNetworkRefresh(); }
            @Override public void onLost(Network n) { requestNetworkRefresh(); }
            @Override public void onCapabilitiesChanged(Network n, NetworkCapabilities c) { requestNetworkRefresh(); }
            @Override public void onLinkPropertiesChanged(Network n, LinkProperties p) { requestNetworkRefresh(); }
        };
        try {
            connectivity.registerNetworkCallback(new NetworkRequest.Builder()
                    .addCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET)
                    .addCapability(NetworkCapabilities.NET_CAPABILITY_NOT_VPN).build(), callback);
            refreshNetwork();
        } catch (RuntimeException failure) {
            stop("network_monitor_unavailable");
            throw failure;
        }
    }

    private void requestNetworkRefresh() {
        if (!refreshQueued.compareAndSet(false, true)) return;
        app.worker.execute(() -> {
            refreshQueued.set(false);
            if (app.desiredConnected) refreshNetwork();
        });
    }

    private boolean usable(Network network) {
        if (network == null) return false;
        var caps = connectivity.getNetworkCapabilities(network);
        return caps != null && caps.hasCapability(NetworkCapabilities.NET_CAPABILITY_NOT_VPN)
                && caps.hasCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET)
                && caps.hasCapability(NetworkCapabilities.NET_CAPABILITY_VALIDATED)
                && !caps.hasCapability(NetworkCapabilities.NET_CAPABILITY_CAPTIVE_PORTAL);
    }

    private void refreshNetwork() {
        Network next = connectivity.getActiveNetwork();
        if (!usable(next)) next = usable(selected) ? selected : null;
        if (next == null) for (Network candidate : connectivity.getAllNetworks()) {
            if (usable(candidate)) { next = candidate; break; }
        }
        selected = next;
        // LinkProperties equality includes routes, DNS and addresses; never log this value.
        var link = next == null ? null : connectivity.getLinkProperties(next);
        String identity = next == null ? null : next.toString() + ":" + (link == null ? "" : link.toString());
        if (policy.network(identity, now())) {
            app.provider.suspendTransport();
            app.publishConnectionState();
            schedule(next == null ? -1 : 750);
        } else if (scheduled == null && next != null) schedule(0);
    }

    private void schedule(long delay) {
        if (scheduled != null) scheduled.cancel(false);
        scheduled = delay < 0 ? null : app.worker.schedule(this::check, delay, TimeUnit.MILLISECONDS);
    }

    private void check() {
        scheduled = null;
        if (!app.desiredConnected || selected == null) return;
        app.requestSessionPulse();
        if (config == null) { schedule(30_000); return; }
        if (now() >= app.deadline || app.deadline <= 0) {
            app.expireAuthorization();
            return;
        }
        if (android.net.VpnService.prepare(app) != null) {
            app.systemStopped("vpn_permission_revoked");
            return;
        }
        if (policy.state == ConnectionSupervisor.State.CONNECTED && !policy.healthy(now())) {
            policy.state = ConnectionSupervisor.State.DEGRADED;
            app.publishConnectionState();
        }
        if (!policy.due(now())) { schedule(Math.max(1, policy.retryAt - now())); return; }
        try {
            if (!app.provider.isUp()) {
                // Only repair within the same unexpired authorization. System destruction
                // and revoke cancel this controller before scheduling another attempt.
                long command = app.generation.get();
                policy.state = ConnectionSupervisor.State.RECONNECTING;
                app.publishConnectionState();
                app.provider.connect(config);
                app.provider.setAuthorizationDeadline(app.deadline);
                if (command != app.generation.get() || now() >= app.deadline) {
                    app.stop();
                    return;
                }
            }
            app.provider.bindNetwork(selected, policy.networkGeneration);
        } catch (IllegalArgumentException | SecurityException terminal) {
            app.systemStopped("native_config_invalid");
            return;
        } catch (Exception failure) {
            complete(policy.generation, policy.networkGeneration, false);
            return;
        }
        long dataAge = app.provider.deliveredDataAge();
        boolean healthy = TunnelHealthProbe.confirmed(dataAge);
        if (healthy) {
            policy.health(policy.generation, policy.networkGeneration, true, now(), .5);
            policy.healthyAt = now() - dataAge;
        }
        else policy.unconfirmed(now()); // Idle is unknown, not a transport fault.
        app.publishConnectionState();
        schedule(30_000);
    }

    private void complete(long session, long network, boolean healthy) {
        policy.health(session, network, healthy, now(), Math.random());
        app.publishConnectionState();
        schedule(Math.max(1, policy.retryAt - now()));
    }

    void stop(String reason) {
        policy.stop(reason);
        schedule(-1);
        if (app.desiredConnected && callback != null) {
            config = null;
            schedule(selected == null ? -1 : 2000);
            return;
        }
        if (callback != null) {
            try { connectivity.unregisterNetworkCallback(callback); }
            catch (IllegalArgumentException ignored) { }
            callback = null;
        }
        selected = null;
        config = null;
        policy.network(null, now());
    }

    private static long now() { return SystemClock.elapsedRealtime(); }
}
