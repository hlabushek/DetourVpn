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
    final NativeRenewer renewer = new NativeRenewer(this);
    /** ADR-099; touched only on the worker thread. */
    private final TunnelWatchdog watchdog = new TunnelWatchdog();
    private android.net.Network defaultNetwork;
    private boolean defaultNetworkLost;
    int tunnelRestarts;
    TunnelProviderV1 provider;
    private final Handler mainThread = new Handler(Looper.getMainLooper());
    private volatile VpnService recoveryService;
    private ProtectedApiRelay protectedRelay;
    private volatile FlutterEngine recoveryEngine;
    /** ADR-121: the restore engine's channel, used to wake its Dart controller. */
    private volatile MethodChannel recoveryChannel;
    /** ADR-121: Lockdown seen on an Always-on start; native HTTPS cannot get out then. */
    volatile boolean lockdown;
    static final String WAKE_RESTORE = "org.example.vpnpoc.action.WAKE_RESTORE";
    private static final long RESTORE_WAKE_LOCK_MS = 45_000L;
    long deadline;
    String reason = "";
    // Task 7: whether a handshake happened since the last connect, seen natively (Dart may sleep).
    final HandshakeMemory handshakes = new HandshakeMemory();
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
        watchNetwork();
        worker.scheduleWithFixedDelay(this::watchTunnel, 15, 15, TimeUnit.SECONDS);
        worker.scheduleWithFixedDelay(() -> {
            if (deadline > 0 && SystemClock.elapsedRealtime() >= deadline) {
                observeHandshake();
                try { stop(); reason = "lease_expired"; }
                catch (Exception ignored) { reason = "native_disconnect_failed"; }
            }
        }, 250, 250, TimeUnit.MILLISECONDS);
    }

    /** Worker thread. One reading for {@link #handshakes}; never throws. */
    private void observeHandshake() {
        try {
            if (provider != null && provider.isUp())
                handshakes.observe(provider.lastHandshakeSeconds(), System.currentTimeMillis() / 1000);
        } catch (RuntimeException | LinkageError ignored) { /* Diagnostics only. */ }
    }

    /** Default-network changes (Wi-Fi <-> mobile, reconnects) feed the watchdog. */
    private void watchNetwork() {
        try {
            ConnectivityManager manager = (ConnectivityManager) getSystemService(CONNECTIVITY_SERVICE);
            manager.registerDefaultNetworkCallback(new ConnectivityManager.NetworkCallback() {
                @Override public void onAvailable(android.net.Network network) {
                    worker.execute(() -> {
                        boolean changed = defaultNetworkLost
                                || (defaultNetwork != null && !defaultNetwork.equals(network));
                        defaultNetwork = network;
                        defaultNetworkLost = false;
                        if (changed) {
                            watchdog.networkChanged(SystemClock.elapsedRealtime());
                            worker.schedule(ClientApplication.this::watchTunnel,
                                    TunnelWatchdog.SETTLE_MS + 500, TimeUnit.MILLISECONDS);
                        }
                    });
                }
                @Override public void onLost(android.net.Network network) {
                    worker.execute(() -> {
                        if (network.equals(defaultNetwork)) defaultNetworkLost = true;
                    });
                }
            });
        } catch (RuntimeException unavailable) {
            // Stall detection below still works without change notifications.
        }
    }

    /** Worker thread. Recreates a stalled tunnel in place; fail-closed if that fails. */
    private void watchTunnel() {
        try {
            long now = SystemClock.elapsedRealtime();
            boolean up = provider != null && deadline > 0 && provider.isUp();
            if (!up) { watchdog.tunnelDown(); return; }
            if (!watchdog.running()) watchdog.tunnelUp(now);
            long handshake = provider.lastHandshakeSeconds();
            long wall = System.currentTimeMillis() / 1000;
            handshakes.observe(handshake, wall);
            long age = handshake > 0 && handshake <= wall ? (wall - handshake) * 1000 : -1;
            if (!watchdog.shouldRestart(now, age, hasValidatedNetwork())) return;
            watchdog.restarted(now);
            try {
                provider.restartInPlace();
                tunnelRestarts++;
            } catch (Exception | LinkageError failed) {
                try { stop(); } catch (Exception ignored) { /* Already down. */ }
                reason = "native_restart_failed";
                watchdog.tunnelDown();
            }
        } catch (RuntimeException ignored) {
            // A watchdog bug must never take the tunnel down by itself.
        }
    }

    private boolean hasValidatedNetwork() {
        ConnectivityManager manager = (ConnectivityManager) getSystemService(CONNECTIVITY_SERVICE);
        for (var candidate : manager.getAllNetworks()) {
            NetworkCapabilities caps = manager.getNetworkCapabilities(candidate);
            if (caps != null && caps.hasCapability(NetworkCapabilities.NET_CAPABILITY_NOT_VPN)
                    && caps.hasCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET)
                    && caps.hasCapability(NetworkCapabilities.NET_CAPABILITY_VALIDATED)) return true;
        }
        return false;
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
                if (android.os.Build.VERSION.SDK_INT >= 29) lockdown = service.isLockdownEnabled();
                String saved = vault.read("session");
                if (saved == null) throw new IllegalStateException("session_missing");
                var session = new org.json.JSONObject(saved);
                URI origin = URI.create(session.getString("origin"));
                protectedRelay = ProtectedApiRelay.forApiOrigin(service, origin);
                recoveryService = service;

                FlutterEngine engine = new FlutterEngine(this);
                GeneratedPluginRegistrant.registerWith(engine);
                MethodChannel channel = new MethodChannel(
                        engine.getDartExecutor().getBinaryMessenger(), "project.vpn/tunnel.v1");
                channel.setMethodCallHandler(this::handleRecoveryCall);
                recoveryChannel = channel;
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
        // ADR-121: after a hand-over to the UI, the old engine may still be finishing a call; it
        // must not be able to stop or move the tunnel the UI now owns.
        if (recoveryEngine == null) {
            result.error("restore_detached", "Операция VPN не выполнена", null);
            return;
        }
        if (call.method.equals("bootstrapFailed")) {
            result.success(null);
            worker.execute(this::stopAlwaysOnRecovery);
            return;
        }
        if (call.method.equals("bootstrapComplete")) {
            result.success(null);
            return;
        }
        // ADR-117: the restore path keeps Dart-driven renewal (Lockdown would need protect()).
        // Answer "not started" instead of notImplemented, which Dart cannot treat as a refusal.
        if (call.method.equals("startNativeRenewal")) {
            result.success(false);
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
                        scheduleRestoreWake();
                        break;
                    case "connectWindow":
                        Number duration = call.argument("remainingMs");
                        if (duration == null || duration.longValue() <= 0 || duration.longValue() > WindowDeadline.MAX_MS
                                || generation != this.generation.get() || provider.isUp()
                                || VpnService.prepare(this) != null)
                            throw new IllegalStateException();
                        long end = submittedAt + duration.longValue();
                        var config = routedConfig(call.argument("config"));
                        window.start(call.argument("connection"), call.argument("runtime"),
                                ((Number) call.argument("generation")).longValue(), end,
                                SystemClock.elapsedRealtime());
                        deadline = end;
                        handshakes.reset();
                        try {
                            provider.connect(config);
                            if (generation != this.generation.get()
                                    || SystemClock.elapsedRealtime() >= deadline) {
                                stop();
                                throw new IllegalStateException();
                            }
                            reason = "";
                            holdCpu();
                            scheduleRestoreWake();
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

    /**
     * ADR-121: wake the restore engine's Dart controller while the phone dozes, so it can renew.
     * Worker thread. Allow-while-idle alarm, no data, receiver not exported.
     */
    private void scheduleRestoreWake() {
        if (recoveryEngine == null || deadline <= 0) { cancelRestoreWake(); return; }
        long delay = NativeRenewal.restoreWakeDelayMs(deadline - SystemClock.elapsedRealtime());
        if (delay < 0) { cancelRestoreWake(); return; }
        try {
            alarms().setAndAllowWhileIdle(android.app.AlarmManager.ELAPSED_REALTIME_WAKEUP,
                    SystemClock.elapsedRealtime() + delay, restoreWakeIntent());
        } catch (RuntimeException ignored) {
            // Best effort; Dart timers still run whenever the CPU is awake.
        }
    }

    private void cancelRestoreWake() {
        try { alarms().cancel(restoreWakeIntent()); } catch (RuntimeException ignored) { }
    }

    private android.app.AlarmManager alarms() {
        return (android.app.AlarmManager) getSystemService(ALARM_SERVICE);
    }

    private android.app.PendingIntent restoreWakeIntent() {
        android.content.Intent intent = new android.content.Intent(this, RenewAlarmReceiver.class)
                .setAction(WAKE_RESTORE);
        return android.app.PendingIntent.getBroadcast(this, 1, intent,
                android.app.PendingIntent.FLAG_UPDATE_CURRENT
                        | android.app.PendingIntent.FLAG_IMMUTABLE);
    }

    /** Alarm receiver entry point (ADR-121). */
    void wakeRestore(Runnable done) {
        try {
            PowerManager power = (PowerManager) getSystemService(POWER_SERVICE);
            PowerManager.WakeLock lock =
                    power.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "detourvpn:restore");
            lock.setReferenceCounted(false);
            lock.acquire(RESTORE_WAKE_LOCK_MS); // Released by its timeout.
        } catch (RuntimeException ignored) {
            // Best effort.
        }
        done.run();
        worker.execute(() -> {
            MethodChannel channel = recoveryChannel;
            if (recoveryEngine == null || channel == null) return;
            mainThread.post(() -> {
                try { channel.invokeMethod("wake", null); }
                catch (RuntimeException ignored) { /* Engine already gone. */ }
            });
            scheduleRestoreWake();
        });
    }

    /**
     * ADR-121: the UI takes over a restored tunnel. Ends the restore engine and its relay but
     * keeps the tunnel and the VPN service. Worker thread. True if the tunnel is still up.
     */
    boolean detachRestoreForUi() {
        cancelRestoreWake();
        FlutterEngine engine = recoveryEngine;
        if (engine == null) return false;
        recoveryEngine = null;
        recoveryChannel = null;
        recoveryService = null;
        ProtectedApiRelay relay = protectedRelay;
        protectedRelay = null;
        if (relay != null) relay.close();
        mainThread.post(engine::destroy);
        return provider != null && provider.isUp() && deadline > 0;
    }

    private void stopAlwaysOnRecovery() {
        cancelRestoreWake();
        try { stop(); } catch (Exception ignored) { reason = "native_disconnect_failed"; }
        ProtectedApiRelay relay = protectedRelay;
        protectedRelay = null;
        if (relay != null) relay.close();
        VpnService service = recoveryService;
        recoveryService = null;
        FlutterEngine engine = recoveryEngine;
        recoveryEngine = null;
        recoveryChannel = null;
        if (engine != null) mainThread.post(engine::destroy);
        if (service != null) service.stopSelf();
    }

    /** User intent to stay connected across reboots and process restarts (ADR-098). */
    static boolean wantsConnected(android.content.Context context) {
        return context.getSharedPreferences("connection", MODE_PRIVATE)
                .getBoolean("resume_connected", false);
    }

    static void setWantsConnected(android.content.Context context, boolean value) {
        context.getSharedPreferences("connection", MODE_PRIVATE).edit()
                .putBoolean("resume_connected", value).apply();
    }

    void stopAlwaysOnService(VpnService service) {
        worker.execute(() -> {
            // Lost VPN consent (another VPN app took over, or the user revoked it in Settings)
            // is a wanted stop: never restore it on the next boot or restart.
            if (VpnService.prepare(this) != null) {
                setWantsConnected(this, false);
                renewer.stop();
                reason = "vpn_revoked";
            }
            if (service == null || recoveryService != service) return;
            cancelRestoreWake();
            recoveryChannel = null;
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
        // User DNS choice (ADR-106). Unset = the profile's own DNS, as before. Applied at connect time.
        String dns = DnsChoice.storedOrNull(preferences.getString("dns", null));
        return ClientProfile.parse(DnsChoice.applyToConfigText(text, dns), key, packages,
                ClientProfile.routingModeFor(stored));
    }

    /**
     * [remainingMs, advisory transfer bytes or -1] for the native renewer, read on the worker
     * thread that owns the tunnel state. Throws once the tunnel this renewal belongs to is gone.
     */
    long[] renewalView(long appGeneration) throws Exception {
        return worker.submit(() -> {
            if (generation.get() != appGeneration || provider == null || !provider.isUp()
                    || deadline <= 0)
                throw new IllegalStateException();
            long traffic = -1;
            try {
                if (provider.capabilities().contains("traffic-stats-advisory.v1"))
                    traffic = provider.transferBytes();
            } catch (RuntimeException | LinkageError ignored) { /* Advisory only. */ }
            return new long[] {Math.max(0, deadline - SystemClock.elapsedRealtime()), traffic};
        }).get(5, TimeUnit.SECONDS);
    }

    /** Applies a natively renewed window under the same fencing as advanceWindow. */
    void advanceNative(long appGeneration, String connection, String runtime, long version,
            long end) throws Exception {
        try {
            worker.submit(() -> {
                if (generation.get() != appGeneration || provider == null || !provider.isUp())
                    throw new IllegalStateException();
                window.advance(connection, runtime, version, end, SystemClock.elapsedRealtime());
                deadline = window.deadline;
                return null;
            }).get(5, TimeUnit.SECONDS);
        } catch (java.util.concurrent.ExecutionException failure) {
            throw new IllegalStateException();
        }
    }

    void stop() throws Exception {
        renewer.stop();
        cancelRestoreWake();
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
        handshakes.observe(handshake, now);
        boolean network = hasValidatedNetwork();
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
        value.put("nativeRenewal", renewer.active());
        value.put("handshakeSeen", handshakes.seen());
        value.put("restoreActive", recoveryEngine != null);
        // ADR-117: the fenced window identity, so a restarted UI can adopt this tunnel. Values the
        // app already received from the API; no key or token.
        if (up && deadline > 0 && window.connection != null) {
            value.put("connection", window.connection);
            value.put("windowGeneration", window.generation);
        }
        value.put("tunnelRestarts", (long) tunnelRestarts);
        return value;
    }
    @Override public String getName() { return "i013"; }
    @Override public void onStateChange(State state) { /* Status is read from serialized backend. */ }
}
