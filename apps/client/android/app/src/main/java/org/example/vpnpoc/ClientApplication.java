package org.example.vpnpoc;

import android.app.Application;
import android.net.VpnService;
import android.net.ConnectivityManager;
import android.net.NetworkCapabilities;
import android.os.SystemClock;
import android.os.Handler;
import android.os.PowerManager;
import android.os.Looper;
import io.flutter.FlutterInjector;
import io.flutter.embedding.engine.FlutterEngine;
import io.flutter.embedding.engine.dart.DartExecutor;
import io.flutter.plugin.common.MethodCall;
import io.flutter.plugin.common.MethodChannel;
import io.flutter.plugins.GeneratedPluginRegistrant;
import java.net.URI;
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
    private final Handler mainThread = new Handler(Looper.getMainLooper());
    private volatile VpnService recoveryService;
    private ProtectedApiRelay protectedRelay;
    private FlutterEngine recoveryEngine;
    long deadline;
    String reason = "";
    // Window renewals are driven from Dart timers. Without this the CPU may sleep with the screen
    // off and a 90 s window then expires. The lock has a timeout and is refreshed on each renewal,
    // so a dead process can never leak it.
    private PowerManager.WakeLock cpuLock;
    private static final long CPU_LOCK_MS = 180_000L;

    private void holdCpu() {
        try {
            if (cpuLock == null) {
                PowerManager manager = (PowerManager) getSystemService(POWER_SERVICE);
                cpuLock = manager.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "detourvpn:window");
                cpuLock.setReferenceCounted(false);
            }
            cpuLock.acquire(CPU_LOCK_MS);
        } catch (RuntimeException ignored) {
            // Best effort: the tunnel still works, only background renewal is less reliable.
        }
    }

    private void releaseCpu() {
        try {
            if (cpuLock != null && cpuLock.isHeld()) cpuLock.release();
        } catch (RuntimeException ignored) {
            // Already released by the timeout.
        }
    }
    @Override public void onCreate() {
        super.onCreate();
        GoBackend.setAlwaysOnCallback(new GoBackend.AlwaysOnCallback() {
            @Override public void alwaysOnTriggered(GoBackend.VpnService service) {
                startAlwaysOnRecovery(service);
            }
            @Override public void alwaysOnStopped(GoBackend.VpnService service) {
                stopAlwaysOnService(service);
            }
        });
        worker.execute(() -> {
            String initStage = "vault";
            try {
                vault = new SecureVault(this);
                initStage = "device_key";
                key = vault.device();
                initStage = "awg_backend";
                provider = new AwgProviderV1(this, this);
                reason = "";
            } catch (Exception | LinkageError failure) {
                reason = "native_unavailable";
                android.util.Log.e("DetourVpnNativeInit", "stage=" + initStage
                        + " failure=" + failure.getClass().getSimpleName());
            }
        });
        worker.scheduleWithFixedDelay(() -> {
            if (deadline > 0 && SystemClock.elapsedRealtime() >= deadline) {
                try { stop(); reason = "lease_expired"; }
                catch (Exception ignored) { reason = "native_disconnect_failed"; }
            }
        }, 250, 250, TimeUnit.MILLISECONDS);
    }

    void startAlwaysOnRecovery(VpnService service) {
        worker.execute(() -> {
            if (recoveryEngine != null) {
                if (recoveryService != service && service != null) service.stopSelf();
                return;
            }
            recoveryService = service;
            try {
                if (service == null || VpnService.prepare(this) != null || vault == null
                        || provider == null) {
                    throw new IllegalStateException("recovery_unavailable");
                }
                String saved = vault.read("session");
                if (saved == null) throw new IllegalStateException("session_missing");
                var session = new org.json.JSONObject(saved);
                URI origin = URI.create(session.getString("origin"));
                protectedRelay = ProtectedApiRelay.forApiOrigin(service, origin);
                recoveryService = service;

                FlutterEngine engine = new FlutterEngine(this);
                GeneratedPluginRegistrant.registerWith(engine);
                new MethodChannel(engine.getDartExecutor().getBinaryMessenger(),
                        "project.vpn/tunnel.v1").setMethodCallHandler(this::handleRecoveryCall);
                recoveryEngine = engine;
                String bundle = FlutterInjector.instance().flutterLoader().findAppBundlePath();
                engine.getDartExecutor().executeDartEntrypoint(
                        new DartExecutor.DartEntrypoint(bundle, "alwaysOnRecoveryMain"));
            } catch (Exception | LinkageError failure) {
                stopAlwaysOnRecovery();
            }
        });
    }

    private void handleRecoveryCall(MethodCall call, MethodChannel.Result result) {
        if (call.method.equals("bootstrapFailed")) {
            result.success(null);
            worker.execute(this::stopAlwaysOnRecovery);
            return;
        }
        if (call.method.equals("bootstrapComplete")) {
            result.success(null);
            return;
        }
        if (!java.util.Set.of("protectedRelayRead", "sessionRead", "sessionWrite", "nodeRead",
                "publicKey", "prove", "permission", "connectWindow", "advanceWindow",
                "disconnect", "snapshot").contains(call.method)) {
            result.notImplemented();
            return;
        }
        final long generation = call.method.equals("disconnect")
                ? this.generation.incrementAndGet() : this.generation.get();
        final long submittedAt = SystemClock.elapsedRealtime();
        worker.execute(() -> {
            try {
                Object value = null;
                switch (call.method) {
                    case "protectedRelayRead":
                        if (protectedRelay == null || recoveryService == null)
                            throw new IllegalStateException();
                        var endpoint = new HashMap<String, Object>();
                        endpoint.put("port", protectedRelay.port());
                        endpoint.put("token", protectedRelay.token());
                        value = endpoint;
                        break;
                    case "sessionRead": {
                        String saved = vault.read("session");
                        value = saved == null ? null : sessionMap(new org.json.JSONObject(saved));
                        break;
                    }
                    case "sessionWrite": {
                        Map<String, Object> record = call.argument("value");
                        vault.write("session", record == null ? null : new org.json.JSONObject(record).toString());
                        break;
                    }
                    case "nodeRead":
                        value = getSharedPreferences("connection", MODE_PRIVATE)
                                .getString("selected_node", null);
                        break;
                    case "permission":
                        value = VpnService.prepare(this) == null;
                        break;
                    case "publicKey":
                        value = key.getPublicKey().toBase64();
                        break;
                    case "prove":
                        value = KeyConfirmation.answer(key, call.argument("ephemeralKey"),
                                call.argument("transcript"));
                        break;
                    case "snapshot":
                        value = snapshot();
                        break;
                    case "disconnect":
                        stop();
                        break;
                    case "advanceWindow":
                        if (generation != this.generation.get() || !provider.isUp())
                            throw new IllegalStateException();
                        window.advance(call.argument("connection"), call.argument("runtime"),
                                ((Number) call.argument("generation")).longValue(),
                                submittedAt + ((Number) call.argument("remainingMs")).longValue(),
                                SystemClock.elapsedRealtime());
                        deadline = window.deadline;
                        holdCpu();
                        break;
                    case "connectWindow":
                        Number duration = call.argument("remainingMs");
                        if (duration == null || duration.longValue() <= 0 || duration.longValue() > 90000
                                || generation != this.generation.get() || provider.isUp()
                                || VpnService.prepare(this) != null)
                            throw new IllegalStateException();
                        long end = submittedAt + duration.longValue();
                        var config = routedConfig(call.argument("config"));
                        window.start(call.argument("connection"), call.argument("runtime"),
                                ((Number) call.argument("generation")).longValue(), end,
                                SystemClock.elapsedRealtime());
                        deadline = end;
                        try {
                            provider.connect(config);
                            if (generation != this.generation.get()
                                    || SystemClock.elapsedRealtime() >= deadline) {
                                stop();
                                throw new IllegalStateException();
                            }
                            reason = "";
                            holdCpu();
                        } catch (Exception failure) {
                            stop();
                            throw failure;
                        }
                        break;
                    default:
                        throw new IllegalArgumentException();
                }
                Object answer = value;
                mainThread.post(() -> result.success(answer));
            } catch (Exception | LinkageError failure) {
                String code = call.method.equals("connectWindow")
                        ? nativeStartFailureCode(failure) : "native_operation_failed";
                mainThread.post(() -> result.error(code, "Операция VPN не выполнена", null));
            }
        });
    }

    static String nativeStartFailureCode(Throwable failure) {
        if (failure instanceof SecurityException) return "native_permission_error";
        if (failure instanceof IllegalArgumentException) return "native_config_invalid";
        if (failure instanceof LinkageError) return "native_engine_unavailable";
        if (failure instanceof java.io.IOException) return "native_io_error";
        if (failure instanceof IllegalStateException) return "native_service_state_invalid";
        return "native_start_failed";
    }

    private void stopAlwaysOnRecovery() {
        try { stop(); } catch (Exception ignored) { reason = "native_disconnect_failed"; }
        ProtectedApiRelay relay = protectedRelay;
        protectedRelay = null;
        if (relay != null) relay.close();
        VpnService service = recoveryService;
        recoveryService = null;
        FlutterEngine engine = recoveryEngine;
        recoveryEngine = null;
        if (engine != null) mainThread.post(engine::destroy);
        if (service != null) service.stopSelf();
    }

    void stopAlwaysOnService(VpnService service) {
        worker.execute(() -> {
            if (service == null || recoveryService != service) return;
            generation.incrementAndGet();
            deadline = 0;
            window.clear();
            recoveryService = null;
            ProtectedApiRelay relay = protectedRelay;
            protectedRelay = null;
            if (relay != null) relay.close();
            FlutterEngine engine = recoveryEngine;
            recoveryEngine = null;
            if (engine != null) mainThread.post(engine::destroy);
        });
    }

    private static Map<String, Object> sessionMap(org.json.JSONObject record) throws Exception {
        var value = new HashMap<String, Object>();
        for (String field : java.util.List.of("origin", "refresh_token"))
            value.put(field, record.getString(field));
        return value;
    }

    private java.util.ArrayList<String> selectedPackages() {
        var preferences = getSharedPreferences("routing", MODE_PRIVATE);
        var selected = preferences.getStringSet("selected", null);
        if (selected == null) selected = preferences.getStringSet("excluded", java.util.Collections.emptySet());
        return new java.util.ArrayList<>(selected);
    }

    /** Package routing for the GoBackend engine. Domain rules are unsupported and fail closed. */
    org.amnezia.awg.config.Config routedConfig(String text) throws Exception {
        var preferences = getSharedPreferences("routing", MODE_PRIVATE);
        if (!preferences.getStringSet("domains", java.util.Collections.emptySet()).isEmpty()
                && !provider.capabilities().contains("domain-routing.v1"))
            throw new IllegalStateException();
        String stored = preferences.getString("mode", "exclude");
        var packages = ClientProfile.packagesForMode(stored, selectedPackages(), getPackageName());
        return ClientProfile.parse(text, key, packages, ClientProfile.routingModeFor(stored));
    }

    void stop() throws Exception {
        releaseCpu();
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
        try {
            long transfer = up && provider.capabilities().contains("traffic-stats-advisory.v1")
                    ? provider.transferBytes() : -1;
            if (transfer >= 0) value.put("trafficBytes", transfer);
            long received = up && provider.capabilities().contains("traffic-stats-advisory.v1")
                    ? provider.receivedBytes() : -1;
            if (received >= 0) value.put("receivedBytes", received);
        } catch (RuntimeException | LinkageError ignored) { /* Advisory unavailable. */ }
        value.put("reason", reason);
        return value;
    }
    @Override public String getName() { return "i013"; }
    @Override public void onStateChange(State state) { /* Status is read from serialized backend. */ }
}
