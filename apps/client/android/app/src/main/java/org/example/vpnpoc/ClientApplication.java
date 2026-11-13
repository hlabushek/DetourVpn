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
    /** ADR-124: user intent, stop reasons and the native recovery trigger; worker thread only. */
    final ConnectionSupervisor supervisor = new ConnectionSupervisor();
    /** ADR-125: a UI Flutter engine is attached and its controller owns reconnects. */
    volatile boolean uiAttached;
    /** ADR-125: last native recovery note for diagnostics (a code, no data). */
    private String recoveryNote = "";
    /** ADR-133: data-path evidence; touched only on the worker thread. */
    private final DataPathWatch dataPath = new DataPathWatch();
    /** ADR-135: tunnel check period while a window is up (was 15 s for the whole process). */
    static final long WATCH_MS = 15_000;
    private java.util.concurrent.ScheduledFuture<?> watchTick;
    static final String RECOVER = "org.example.vpnpoc.action.RECOVER";
    /** ADR-126: one-shot deadline check; replaces the 250 ms polling loop. */
    private java.util.concurrent.ScheduledFuture<?> deadlineCheck;
    /** ADR-127: the default physical network; worker thread only. */
    private final PhysicalNetwork physical = new PhysicalNetwork();
    /** ADR-122: elapsed-realtime of the last default-network change, -1 if none; worker thread. */
    private long lastNetworkChangeAt = -1;
    private java.util.concurrent.ScheduledFuture<?> pendingCheck;
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
        worker.execute(() -> supervisor.restore(wantsConnected(this)));
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
        // ADR-135: the 15 s tunnel check is armed by setDeadline and stops when no window is up.
    }

    /**
     * ADR-126, worker thread. Fail closed once the monotonic deadline has passed. Called from the
     * one-shot deadline task and from every event that runs after the CPU slept (watchdog tick,
     * network callbacks, alarms, channel calls), so a delayed timer can never keep a window alive:
     * elapsed-realtime counts deep sleep, and the node removes the peer at not_after anyway.
     */
    void enforceDeadline() {
        if (deadline > 0 && SystemClock.elapsedRealtime() >= deadline) {
            observeHandshake();
            try { stop(); reason = "lease_expired"; }
            catch (Exception ignored) { reason = "native_disconnect_failed"; }
            stopped(reason);
        }
    }

    /** Worker thread. Sets the native deadline and (re)arms its one-shot check. */
    void setDeadline(long end) {
        deadline = end;
        if (deadlineCheck != null) deadlineCheck.cancel(false);
        deadlineCheck = null;
        if (end <= 0) return;
        long delay = Math.max(0, end - SystemClock.elapsedRealtime()) + 50;
        deadlineCheck = worker.schedule(this::enforceDeadline, delay, TimeUnit.MILLISECONDS);
        armWatch(WATCH_MS);
    }

    /** ADR-135: the one pending tunnel check; re-armed only while a window is up. Worker thread. */
    private void armWatch(long delayMs) {
        if (watchTick != null) watchTick.cancel(false);
        watchTick = worker.schedule(this::watchTunnel, delayMs, TimeUnit.MILLISECONDS);
    }

    /**
     * ADR-124, worker thread. The tunnel stopped without a user disconnect. A terminal reason
     * ends the user's intent; otherwise the supervisor decides whether native starts recovery.
     */
    void stopped(String why) {
        if (supervisor.tunnelDown(why)) setWantsConnected(this, false);
        evaluateRecovery();
    }

    /** ADR-124/125, worker thread. Native recovery runs only when no Dart controller owns it. */
    void evaluateRecovery() {
        long now = SystemClock.elapsedRealtime();
        boolean up = provider != null && provider.isUp() && deadline > 0;
        var decision = supervisor.evaluate(now, up, false,
                uiAttached || recoveryEngine != null, physical.usable());
        switch (decision.action) {
            case START_RECOVERY:
                startRecovery(now);
                break;
            case WAIT:
                scheduleRecoveryAlarm(decision.delayMs);
                break;
            default:
                cancelRecoveryAlarm();
        }
    }

    private void startRecovery(long now) {
        if (VpnService.prepare(this) != null) {
            reason = "vpn_revoked";
            if (supervisor.tunnelDown(reason)) setWantsConnected(this, false);
            cancelRecoveryAlarm();
            return;
        }
        supervisor.attemptStarted(now, java.util.concurrent.ThreadLocalRandom.current().nextDouble());
        try {
            android.content.Intent restore = new android.content.Intent(this, GoBackend.VpnService.class)
                    .setAction(GoBackend.VpnService.RESTORE_ACTION);
            if (android.os.Build.VERSION.SDK_INT >= 26) startForegroundService(restore);
            else startService(restore);
            recoveryNote = "restore_started";
        } catch (RuntimeException refused) {
            // ADR-134: Android 12+ background-start limits. The intent stays; the next trigger
            // (network, app open, boot or the alarm below) tries again, at most once a minute.
            recoveryNote = "background_start_refused";
            supervisor.startRefused(now);
            scheduleRecoveryAlarm(ConnectionSupervisor.REFUSED_RETRY_MS);
            return;
        }
        // If the restore never reports back (process trouble), look again after the backoff.
        scheduleRecoveryAlarm(Math.max(supervisor.backoffMs(), 60_000));
    }

    private void scheduleRecoveryAlarm(long delayMs) {
        try {
            alarms().setAndAllowWhileIdle(android.app.AlarmManager.ELAPSED_REALTIME_WAKEUP,
                    SystemClock.elapsedRealtime() + Math.max(1_000, delayMs), recoveryIntent());
        } catch (RuntimeException ignored) {
            // Network and UI triggers still re-evaluate.
        }
    }

    private void cancelRecoveryAlarm() {
        try { alarms().cancel(recoveryIntent()); } catch (RuntimeException ignored) { }
    }

    private android.app.PendingIntent recoveryIntent() {
        android.content.Intent intent = new android.content.Intent(this, RenewAlarmReceiver.class)
                .setAction(RECOVER);
        return android.app.PendingIntent.getBroadcast(this, 2, intent,
                android.app.PendingIntent.FLAG_UPDATE_CURRENT
                        | android.app.PendingIntent.FLAG_IMMUTABLE);
    }

    /** Alarm receiver entry point (ADR-125). */
    void wakeRecovery(Runnable done) {
        worker.execute(() -> {
            try {
                enforceDeadline();
                evaluateRecovery();
            } finally {
                done.run();
            }
        });
    }

    /** ADR-125: the UI engine attached or detached. Detaching is not a disconnect. */
    void uiEngine(boolean attached) {
        uiAttached = attached;
        worker.execute(this::evaluateRecovery);
    }

    /** ADR-124: explicit user disconnect (UI channel `setIntent false`). Worker thread. */
    void userDisconnect() {
        generation.incrementAndGet();
        supervisor.userDisconnected();
        setWantsConnected(this, false);
        cancelRecoveryAlarm();
        if (recoveryEngine != null) stopAlwaysOnRecovery();
    }

    /** Worker thread. One reading for {@link #handshakes}; never throws. */
    private void observeHandshake() {
        try {
            if (provider != null && provider.isUp())
                handshakes.observe(provider.lastHandshakeSeconds(), System.currentTimeMillis() / 1000);
        } catch (RuntimeException | LinkageError ignored) { /* Diagnostics only. */ }
    }

    /**
     * ADR-127: default physical network changes (Wi-Fi <-> mobile, loss and return, a new local
     * address on the same network) feed the watchdog; a network becoming usable re-checks at once.
     * The app's own UID is outside the VPN, so this callback sees the physical network.
     */
    private void watchNetwork() {
        try {
            ConnectivityManager manager = (ConnectivityManager) getSystemService(CONNECTIVITY_SERVICE);
            manager.registerDefaultNetworkCallback(new ConnectivityManager.NetworkCallback() {
                @Override public void onAvailable(android.net.Network network) {
                    NetworkCapabilities caps = null;
                    try { caps = manager.getNetworkCapabilities(network); }
                    catch (RuntimeException ignored) { /* Capabilities callback follows. */ }
                    final NetworkCapabilities known = caps;
                    worker.execute(() -> {
                        boolean changed = physical.available(network.toString(),
                                known == null || hasInternet(known), known != null && captive(known),
                                known != null && validated(known));
                        if (changed) networkPathChanged();
                        else scheduleWatch(500);
                        evaluateRecovery();
                    });
                }
                @Override public void onCapabilitiesChanged(android.net.Network network,
                        NetworkCapabilities caps) {
                    boolean internet = hasInternet(caps);
                    boolean portal = captive(caps);
                    boolean valid = validated(caps);
                    worker.execute(() -> {
                        if (physical.capabilities(network.toString(), internet, portal, valid)) {
                            scheduleWatch(500);
                            evaluateRecovery();
                        }
                    });
                }
                @Override public void onLinkPropertiesChanged(android.net.Network network,
                        android.net.LinkProperties properties) {
                    var addresses = new java.util.TreeSet<String>();
                    for (var address : properties.getLinkAddresses())
                        addresses.add(address.getAddress().getHostAddress());
                    worker.execute(() -> {
                        if (physical.linkAddresses(network.toString(), addresses))
                            networkPathChanged();
                    });
                }
                @Override public void onLost(android.net.Network network) {
                    worker.execute(() -> physical.lost(network.toString()));
                }
            });
        } catch (RuntimeException unavailable) {
            // Stall detection below still works without change notifications.
        }
    }

    private static boolean hasInternet(NetworkCapabilities caps) {
        return caps.hasCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET);
    }

    private static boolean captive(NetworkCapabilities caps) {
        return caps.hasCapability(NetworkCapabilities.NET_CAPABILITY_CAPTIVE_PORTAL);
    }

    private static boolean validated(NetworkCapabilities caps) {
        return caps.hasCapability(NetworkCapabilities.NET_CAPABILITY_VALIDATED);
    }

    /** Worker thread. The encrypted transport's path may have moved: verify it after settling. */
    private void networkPathChanged() {
        lastNetworkChangeAt = SystemClock.elapsedRealtime();
        watchdog.networkChanged(lastNetworkChangeAt);
        scheduleWatch(TunnelWatchdog.SETTLE_MS + 500);
    }

    /** Worker thread. Recreates a stalled tunnel in place; fail-closed if that fails. */
    private void watchTunnel() {
        try {
            enforceDeadline();
            long now = SystemClock.elapsedRealtime();
            boolean up = provider != null && deadline > 0 && provider.isUp();
            if (!up) { watchdog.tunnelDown(); watchTick = null; return; }
            armWatch(WATCH_MS);
            if (!watchdog.running()) watchdog.tunnelUp(now);
            long handshake = provider.lastHandshakeSeconds();
            long wall = System.currentTimeMillis() / 1000;
            handshakes.observe(handshake, wall);
            long age = handshake > 0 && handshake <= wall ? (wall - handshake) * 1000 : -1;
            if (!watchdog.shouldRestart(now, age, physical.usable())) {
                schedulePendingWatch(now);
                return;
            }
            watchdog.restarted(now);
            try {
                provider.restartInPlace();
                tunnelRestarts++;
                schedulePendingWatch(SystemClock.elapsedRealtime());
            } catch (Exception | LinkageError failed) {
                try { stop(); } catch (Exception ignored) { /* Already down. */ }
                reason = "native_restart_failed";
                watchdog.tunnelDown();
                stopped(reason);
            }
        } catch (RuntimeException ignored) {
            // A watchdog bug must never take the tunnel down by itself.
        }
    }

    /** ADR-122: check again when a pending network trigger may act (not only on the 15 s tick). */
    private void schedulePendingWatch(long now) {
        long delay = watchdog.pendingNetworkDelayMs(now);
        if (delay >= 0) scheduleWatch(Math.max(delay + 500, 2_000));
    }

    /** Worker thread. One extra watchdog check; replaces any earlier one still waiting. */
    private void scheduleWatch(long delayMs) {
        if (pendingCheck != null) pendingCheck.cancel(false);
        pendingCheck = worker.schedule(this::watchTunnel, delayMs, TimeUnit.MILLISECONDS);
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
            // ADR-125: the restore engine says why; a terminal code ends the user's intent.
            String failed = call.arguments instanceof Map ? call.argument("code") : null;
            String code = failed != null && failed.matches("[a-z_]{1,64}") ? failed : "recovery_failed";
            result.success(null);
            worker.execute(() -> {
                stopAlwaysOnRecovery();
                if (supervisor.recoveryFailed(code)) setWantsConnected(this, false);
                evaluateRecovery();
            });
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
                        setDeadline(window.deadline);
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
                        setDeadline(end);
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
                supervisor.tunnelDown(reason);
            }
            if (service == null || recoveryService != service) return;
            cancelRestoreWake();
            recoveryChannel = null;
            generation.incrementAndGet();
            setDeadline(0);
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
            enforceDeadline();
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
                setDeadline(window.deadline);
                return null;
            }).get(5, TimeUnit.SECONDS);
        } catch (java.util.concurrent.ExecutionException failure) {
            throw new IllegalStateException();
        }
    }

    /** ADR-136, worker thread: true while a headless restore engine is alive (it owns renewal). */
    boolean restoreEngineAlive() { return recoveryEngine != null; }

    void stop() throws Exception {
        renewer.stop();
        cancelRestoreWake();
        releaseCpu();
        if (provider != null) provider.disconnect();
        setDeadline(0);
        window.clear();
    }
    Map<String, Object> snapshot() {
        if (provider == null) throw new IllegalStateException();
        enforceDeadline();
        boolean up = provider.isUp();
        long handshake = up ? provider.lastHandshakeSeconds() : 0;
        long now = System.currentTimeMillis() / 1000;
        handshakes.observe(handshake, now);
        // ADR-127: a usable physical network (INTERNET, no captive portal); validation separate.
        boolean network = physical.usable();
        Map<String, Object> value = new HashMap<>();
        value.put("up", up); value.put("network", network);
        value.put("networkValidated", physical.validated());
        value.put("handshakeAge", handshake > 0 && handshake <= now ? now - handshake : -1L);
        value.put("remainingMs", Math.max(0, deadline - SystemClock.elapsedRealtime()));
        long transfer = -1;
        long received = -1;
        try {
            if (up && provider.capabilities().contains("traffic-stats-advisory.v1")) {
                transfer = provider.transferBytes();
                received = provider.receivedBytes();
            }
        } catch (RuntimeException | LinkageError ignored) { /* Advisory unavailable. */ }
        if (transfer >= 0) value.put("trafficBytes", transfer);
        if (received >= 0) value.put("receivedBytes", received);
        // ADR-133: data-path evidence from the same advisory counters. Pure policy; no effect on
        // recovery (see DataPathWatch).
        long nowElapsed = SystemClock.elapsedRealtime();
        if (up) dataPath.observe(received, transfer, nowElapsed);
        else dataPath.reset();
        boolean flowing = up && dataPath.flowing(nowElapsed);
        boolean stalled = up && dataPath.stalled(nowElapsed);
        value.put("reason", reason);
        value.put("nativeRenewal", renewer.active());
        value.put("handshakeSeen", handshakes.seen());
        value.put("restoreActive", recoveryEngine != null);
        // ADR-122: a handshake older than the last network change does not prove the new path.
        value.put("networkChangeAgeMs", lastNetworkChangeAt < 0 ? -1L
                : SystemClock.elapsedRealtime() - lastNetworkChangeAt);
        // ADR-117: the fenced window identity, so a restarted UI can adopt this tunnel. Values the
        // app already received from the API; no key or token.
        if (up && deadline > 0 && window.connection != null) {
            value.put("connection", window.connection);
            value.put("windowGeneration", window.generation);
        }
        value.put("tunnelRestarts", (long) tunnelRestarts);
        // ADR-124/128: additive, authoritative native state. Codes only; no endpoint or key.
        long changeAge = lastNetworkChangeAt < 0 ? -1 : SystemClock.elapsedRealtime() - lastNetworkChangeAt;
        long handshakeAge = handshake > 0 && handshake <= now ? now - handshake : -1;
        boolean afterChange = handshakeAge >= 0 && (changeAge < 0 || handshakeAge * 1000 < changeAge);
        boolean tunnelUp = up && deadline > 0;
        value.put("desiredConnected", supervisor.desired());
        value.put("transport", ConnectionSupervisor.transport(supervisor.desired(), tunnelUp,
                false, network, afterChange && handshakeAge <= 180,
                watchdog.pendingNetworkDelayMs(SystemClock.elapsedRealtime()) >= 0, stalled));
        value.put("evidence", ConnectionSupervisor.evidence(tunnelUp, handshakeAge, afterChange, flowing));
        value.put("failureCategory", ConnectionSupervisor.category(tunnelUp ? "" : reason));
        // ADR-134: why native is not running a tunnel although the user still wants it.
        value.put("stopCategory", tunnelUp ? "none" : supervisor.stopCategory());
        value.put("recoveryAttempts", (long) supervisor.attempts());
        value.put("recoveryNote", recoveryNote);
        value.put("uiAttached", uiAttached);
        return value;
    }
    /** ADR-124: the native supervisor line for the diagnostic report. Worker thread. */
    String diagnosticLine() {
        Map<String, Object> state = snapshot();
        return DiagnosticReportText.nativeLine((String) state.get("transport"),
                (String) state.get("evidence"), (String) state.get("failureCategory"),
                supervisor.desired(), physical.validated(), supervisor.attempts(), tunnelRestarts);
    }

    @Override public String getName() { return "i013"; }
    @Override public void onStateChange(State state) { /* Status is read from serialized backend. */ }
}
