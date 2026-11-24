package org.example.vpnpoc;

import android.app.Application;
import android.net.VpnService;
import android.net.ConnectivityManager;
import android.net.NetworkCapabilities;
import android.os.SystemClock;
import android.os.Handler;
import android.os.Looper;
import io.flutter.FlutterInjector;
import io.flutter.embedding.engine.FlutterEngine;
import io.flutter.embedding.engine.dart.DartExecutor;
import io.flutter.plugin.common.MethodCall;
import io.flutter.plugin.common.MethodChannel;
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
    volatile TunnelProviderV1 provider;
    private final Handler mainThread = new Handler(Looper.getMainLooper());
    private volatile VpnService recoveryService;
    private ProtectedApiRelay protectedRelay;
    private volatile FlutterEngine recoveryEngine;
    final ClientBridge bridge = new ClientBridge(this);
    private volatile FlutterEngine uiEngine;
    private AndroidConnectionRuntime runtime;
    private volatile GoBackend.VpnService activeService;
    volatile boolean desiredConnected;
    private java.util.concurrent.ScheduledFuture<?> deadlineTask;
    long deadline;
    private java.util.concurrent.ScheduledFuture<?> pulseTask;
    volatile boolean nativeTimedOut;

    void scheduleSessionPulse(long delayMs) {
        if (pulseTask != null) pulseTask.cancel(false);
        pulseTask = worker.schedule(this::requestSessionPulse, Math.max(1000, Math.min(30_000, delayMs)), TimeUnit.MILLISECONDS);
    }
    void requestSessionPulse() {
        mainThread.post(() -> {
            FlutterEngine engine = uiEngine != null ? uiEngine : recoveryEngine;
            if (engine != null) new MethodChannel(engine.getDartExecutor().getBinaryMessenger(),
                    "project.vpn/tunnel.v1").invokeMethod("sessionPulse", null);
        });
    }
    MethodChannel.Result boundedResult(MethodChannel.Result result) {
        return new BoundedMethodResult(result, mainThread, () -> {
            nativeTimedOut = true;
            cancelDesiredConnection();
        });
    }

    // Main-thread only. One engine and one token/renewal owner across Activity recreation.
    FlutterEngine uiEngine() {
        if (recoveryEngine != null) { uiEngine = recoveryEngine; return uiEngine; }
        if (uiEngine == null) {
            uiEngine = new FlutterEngine(this);
            new MethodChannel(uiEngine.getDartExecutor().getBinaryMessenger(), "project.vpn/tunnel.v1")
                .setMethodCallHandler(bridge::handle);
            uiEngine.getDartExecutor().executeDartEntrypoint(DartExecutor.DartEntrypoint.createDefault());
        }
        return uiEngine;
    }

    void startAuthorized(org.amnezia.awg.config.Config config) throws Exception {
        provider.connect(config);
        provider.setAuthorizationDeadline(deadline);
        if (runtime == null) runtime = new AndroidConnectionRuntime(this);
        runtime.start(config);
        armDeadline();
    }

    void armDeadline() {
        if (deadline > 0 && provider != null && provider.isUp())
            provider.setAuthorizationDeadline(deadline);
        if (deadlineTask != null) deadlineTask.cancel(false);
        deadlineTask = deadline <= 0 ? null : worker.schedule(this::expireAuthorization,
            Math.max(0, deadline - SystemClock.elapsedRealtime()), TimeUnit.MILLISECONDS);
    }

    void expireAuthorization() {
        if (deadline <= 0 || SystemClock.elapsedRealtime() < deadline) return;
        generation.incrementAndGet();
        try { stop(); reason = "lease_expired"; requestSessionPulse(); }
        catch (Exception ignored) { reason = "native_disconnect_failed"; }
    }

    void setDesiredConnected(boolean desired) {
        setDesiredConnected(desired, generation.get());
    }

    void setDesiredConnected(boolean desired, long expectedGeneration) {
        boolean saved;
        synchronized (intentLock) {
            if (desired && expectedGeneration != generation.get())
                throw new IllegalStateException("session_cancelled");
            desiredConnected = desired;
            saved = getSharedPreferences("connection", MODE_PRIVATE).edit()
                    .putBoolean("desired_connected", desired).commit();
            if (!saved) desiredConnected = false;
        }
        if (!desiredConnected) {
            // A storage error must never prevent a local disconnect.
            try { stop(); }
            catch (Exception failure) { throw new IllegalStateException("native_disconnect_failed"); }
            if (activeService != null) activeService.stopSelf();
        }
        if (!saved) throw new IllegalStateException("intent_storage_failed");
        if (desired && activeService == null && VpnService.prepare(this) == null) {
            android.content.Intent intent = new android.content.Intent(this, GoBackend.VpnService.class);
            if (android.os.Build.VERSION.SDK_INT >= 26) startForegroundService(intent);
            else startService(intent);
        }
    }

    void cancelNative() {
        generation.incrementAndGet();
        TunnelProviderV1 active = provider;
        if (active != null) active.cancelImmediately();
    }

    private final Object intentLock = new Object();
    void cancelDesiredConnection() {
        cancelNative();
        synchronized (intentLock) {
            desiredConnected = false;
            // Save independently of the native worker, which may be blocked in JNI.
            getSharedPreferences("connection", MODE_PRIVATE).edit()
                    .putBoolean("desired_connected", false).apply();
        }
    }

    void systemStopped(String why) {
        cancelNative();
        reason = why;
        try { setDesiredConnected(false); } catch (RuntimeException ignored) { }
        try { stop(); } catch (Exception ignored) { }
        publishConnectionState();
    }

    void publishConnectionState() {
        GoBackend.VpnService service = activeService;
        if (service == null) return;
        String state = runtime == null ? "CONNECTING" : runtime.policy.state.name();
        if ("CONNECTED".equals(state) && !runtime.policy.healthy(SystemClock.elapsedRealtime()))
            state = "DEGRADED";
        if (desiredConnected && "DISCONNECTED".equals(state)) state = "RECONNECTING";
        String label = switch (state) {
            case "CONNECTED" -> "Соединение проверено";
            case "WAITING_FOR_NETWORK" -> "Ожидается сеть";
            case "DISCONNECTED", "ERROR" -> "VPN отключён";
            default -> "Соединение восстанавливается";
        };
        final long notificationGeneration = generation.get();
        mainThread.post(() -> {
            if (activeService != service || notificationGeneration != generation.get()) return;
            var stop = new android.content.Intent(this, GoBackend.VpnService.class)
                .setAction("org.example.vpnpoc.DISCONNECT");
            var builder = (android.os.Build.VERSION.SDK_INT >= 26
                ? new android.app.Notification.Builder(this, "i011")
                : new android.app.Notification.Builder(this))
                .setSmallIcon(org.example.vpnclient.R.drawable.ic_vpn)
                .setContentTitle("DetourVpn").setContentText(label).setOngoing(true)
                .addAction(android.R.drawable.ic_menu_close_clear_cancel, "Отключить",
                    android.app.PendingIntent.getService(this, 11, stop,
                        android.app.PendingIntent.FLAG_IMMUTABLE | android.app.PendingIntent.FLAG_UPDATE_CURRENT));
            service.startForeground(11, builder.build());
        });
    }
    String reason = "";
    @Override public void onCreate() {
        super.onCreate();
        desiredConnected = getSharedPreferences("connection", MODE_PRIVATE)
                .getBoolean("desired_connected", false);
        GoBackend.setAlwaysOnCallback(new GoBackend.AlwaysOnCallback() {
            @Override public void alwaysOnTriggered(GoBackend.VpnService service) {
                startAlwaysOnRecovery(service);
            }
            @Override public void alwaysOnStopped(GoBackend.VpnService service) {
                worker.execute(() -> {
                    if (activeService == service) {
                        activeService = null;
                        if (runtime != null && runtime.policy.desired) systemStopped("system_stopped");
                    }
                });
                stopAlwaysOnService(service);
            }
            @Override public boolean keepAliveRequested() { return desiredConnected; }
            @Override public void serviceCreated(GoBackend.VpnService service) {
                worker.execute(() -> { activeService = service; publishConnectionState(); });
            }
            @Override public void disconnectRequested(GoBackend.VpnService service) {
                cancelDesiredConnection();
                worker.execute(() -> systemStopped("user_disconnected"));
            }
            @Override public void permissionRevoked(GoBackend.VpnService service) {
                cancelDesiredConnection();
                worker.execute(() -> systemStopped("vpn_permission_revoked"));
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

                mainThread.post(() -> {
                    if (recoveryEngine != null) return;
                    if (uiEngine != null) {
                        new MethodChannel(uiEngine.getDartExecutor().getBinaryMessenger(),
                                "project.vpn/tunnel.v1").invokeMethod("restoreConnection", null);
                        return;
                    }
                    try {
                        FlutterEngine engine = new FlutterEngine(this);
                        new MethodChannel(engine.getDartExecutor().getBinaryMessenger(),
                                "project.vpn/tunnel.v1").setMethodCallHandler(this::handleRecoveryCall);
                        recoveryEngine = engine;
                        String bundle = FlutterInjector.instance().flutterLoader().findAppBundlePath();
                        engine.getDartExecutor().executeDartEntrypoint(
                                new DartExecutor.DartEntrypoint(bundle, "alwaysOnRecoveryMain"));
                    } catch (RuntimeException | LinkageError failure) {
                        worker.execute(this::stopAlwaysOnRecovery);
                    }
                });
            } catch (Exception | LinkageError failure) {
                stopAlwaysOnRecovery();
            }
        });
    }

    void handleRecoveryCall(MethodCall call, MethodChannel.Result result) {
        if (call.method.equals("bootstrapFailed")) {
            result.success(null);
            worker.execute(this::stopAlwaysOnRecovery);
            return;
        }
        if (call.method.equals("bootstrapComplete")) {
            result.success(null);
            return;
        }
        if (!call.method.equals("protectedRelayRead")) {
            bridge.handle(call, result); return;
        }
        worker.execute(() -> {
            try {
                Object value = null;
                switch (call.method) {
                    case "protectedRelayRead":
                        if (protectedRelay == null && activeService != null) {
                            String saved = vault.read("session");
                            if (saved == null) throw new IllegalStateException();
                            URI origin = URI.create(new org.json.JSONObject(saved).getString("origin"));
                            protectedRelay = ProtectedApiRelay.forApiOrigin(activeService, origin);
                            recoveryService = activeService;
                        }
                        if (protectedRelay == null || recoveryService == null)
                            throw new IllegalStateException();
                        var endpoint = new HashMap<String, Object>();
                        endpoint.put("port", protectedRelay.port());
                        endpoint.put("token", protectedRelay.token());
                        value = endpoint;
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
        setDesiredConnected(false);
        try { stop(); } catch (Exception ignored) { reason = "native_disconnect_failed"; }
        ProtectedApiRelay relay = protectedRelay;
        protectedRelay = null;
        if (relay != null) relay.close();
        VpnService service = recoveryService;
        recoveryService = null;
        FlutterEngine engine = recoveryEngine;
        recoveryEngine = null;
        if (engine != null && engine != uiEngine) mainThread.post(engine::destroy);
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
            if (engine != null && engine != uiEngine) mainThread.post(engine::destroy);
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
        if (provider != null) provider.cancelImmediately();
        if (runtime != null) runtime.stop(reason);
        deadline = 0;
        armDeadline();
        window.clear();
        if (provider != null) provider.disconnect();
        publishConnectionState();
    }
    Map<String, Object> snapshot() {
        if (provider == null) throw new IllegalStateException();
        expireAuthorization();
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
        if (runtime != null) {
            value.put("healthConfirmed", runtime.policy.healthy(SystemClock.elapsedRealtime()));
            value.put("connectionState", runtime.policy.state.name());
            value.put("desiredConnected", desiredConnected);
            value.put("sessionGeneration", runtime.policy.generation);
            value.put("networkGeneration", runtime.policy.networkGeneration);
        }
        return value;
    }
    @Override public String getName() { return "i013"; }
    @Override public void onStateChange(State state) { /* Status is read from serialized backend. */ }
}
