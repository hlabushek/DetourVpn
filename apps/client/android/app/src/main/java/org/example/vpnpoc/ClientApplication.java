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
    /**
     * ADR-142: the UI Activity is started (between onStart and onStop). Only then can its Dart
     * controller run timers; a stopped Activity sleeps in Doze, so native owns recovery.
     */
    volatile boolean uiForeground;
    /** ADR-125: last native recovery note for diagnostics (a code, no data). */
    private String recoveryNote = "";
    /**
     * ADR-133/148: data-path evidence and the active-probe ladder; worker thread only. Built in
     * onCreate from the manifest config (probe off until a project-owned health URL is set).
     */
    private DeadPeerWatch deadPeer = new DeadPeerWatch(false, false);
    /** ADR-148: probe URL and failover opt-in (both off by default). */
    private DeadPeerProbe probeConfig = DeadPeerProbe.off();
    /** ADR-148: probe I/O, off the worker thread (each probe is bounded to 5 s). */
    private final java.util.concurrent.ExecutorService probeIo = Executors.newCachedThreadPool();
    /**
     * ADR-148: the probe ladder asked for failover (owner opt-in only). Read once, and cleared,
     * by the restore engine before its next window; cleared by any window start or a user
     * disconnect.
     */
    private volatile boolean failoverRequested;
    /** ADR-135: tunnel check period while a window is up (was 15 s for the whole process). */
    static final long WATCH_MS = 15_000;
    private java.util.concurrent.ScheduledFuture<?> watchTick;
    static final String RECOVER = "org.example.vpnpoc.action.RECOVER";
    /** ADR-126: one-shot deadline check; replaces the 250 ms polling loop. */
    private java.util.concurrent.ScheduledFuture<?> deadlineCheck;
    /** ADR-127: the default physical network; worker thread only. */
    private final PhysicalNetwork physical = new PhysicalNetwork();
    /** ADR-146: the network-callback decisions (pure, tested); worker thread only. */
    private final NetworkCallbacks networkCallbacks = new NetworkCallbacks(physical);
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
    /**
     * ADR-121/149: Lockdown is on; plain native HTTPS cannot get out then. ADR-149 (R6): set only
     * from a read while this app's VPN is established ({@link AlwaysOnStatus#lockdownAfterRead}),
     * never from a read before establish(), which Android always answers with false.
     */
    volatile boolean lockdown;
    /**
     * ADR-140/149: the VPN service instance of this app (any start: UI connect, Always-on, sticky
     * restart, restore), kept until that instance stops ({@link ServiceProtector}). Used only to
     * protect() native renewal sockets to the API origin under Lockdown (ADR-140), for
     * setUnderlyingNetworks (ADR-146) and for the Always-on read (ADR-141). Never handed to Dart,
     * and never used to exempt any other socket or app from the VPN.
     */
    volatile VpnService protector;
    /** ADR-139: last Always-on setting read from a service instance ({@link AlwaysOnStatus}). */
    private volatile String alwaysOn = AlwaysOnStatus.UNKNOWN;
    static final String WAKE_RESTORE = "org.example.vpnpoc.action.WAKE_RESTORE";
    private static final long RESTORE_WAKE_LOCK_MS = 45_000L;
    long deadline;
    String reason = "";
    /**
     * The native step that failed first in this window: a stage code from the diagnostic
     * allowlist, never data. Kept until a window starts again, so a report can name the step
     * when Dart is not running. Read by snapshot(). Written from several threads (benign race).
     */
    private volatile String failureStage = "";
    // Task 7: whether a handshake happened since the last connect, seen natively (Dart may sleep).
    final HandshakeMemory handshakes = new HandshakeMemory();
    // Window renewals are driven from Dart timers. Without this the CPU may sleep with the screen
    // off and a 90 s window then expires. The lock has a timeout and is refreshed on each renewal,
    // so a dead process can never leak it.
    private PowerManager.WakeLock cpuLock;
    private static final long CPU_LOCK_MS = 180_000L;

    /** ADR-144 (Phase 0): lifecycle events for the diagnostic report; codes only, memory only. */
    final ConnectionEventLog events = new ConnectionEventLog();
    /** ADR-144: process start (elapsed realtime) and whether the UI has said who owns recovery. */
    private long processStart;
    private volatile boolean ownerSignalSeen;
    /**
     * ADR-144: a restore engine is bringing its first tunnel up (from start until bootstrapComplete
     * or bootstrapFailed, at most {@link ConnectionSupervisor#RESTORE_BOOTSTRAP_MS}). Worker thread.
     */
    private boolean restoreBootstrapping;
    private java.util.concurrent.ScheduledFuture<?> bootstrapTimeout;
    /** ADR-144: elapsed realtime the current window's tunnel came up, -1 if none. Worker thread. */
    private long windowUpAt = -1;
    /** ADR-144: the UI engine's state stream (main thread only) and the last state sent to it. */
    private io.flutter.plugin.common.EventChannel.EventSink stateSink;
    private String lastPublished = "";
    private String lastLoggedState = "";
    /** ADR-146: the current default physical network (null when lost). Worker thread only. */
    private android.net.Network defaultNetwork;
    /** ADR-146: the VPN's underlying network must be set again (new path or new interface). */
    private boolean underlyingStale;
    /** ADR-146: confirms a network loss after {@link PhysicalNetwork#LOSS_CONFIRM_MS}. */
    private java.util.concurrent.ScheduledFuture<?> lossCheck;

    /** ADR-144: one event-log line. Any thread. */
    void log(String event, String detail) {
        events.record(SystemClock.elapsedRealtime(), event, detail);
    }

    void log(String event) { log(event, null); }

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
        processStart = SystemClock.elapsedRealtime();
        log("process_start");
        loadProbeConfig();
        GoBackend.setAlwaysOnCallback(new GoBackend.AlwaysOnCallback() {
            // ADR-141: no Always-on read here. Before establish() and in onDestroy Android answers
            // isAlwaysOn() == false whatever the setting is; snapshot() reads it while up.
            @Override public void alwaysOnTriggered(GoBackend.VpnService service) {
                protector = ServiceProtector.created(protector, service);
                startAlwaysOnRecovery(service);
            }
            // ADR-149 (6.1): also the instance a UI connect started, so a UI-started tunnel can
            // renew natively under Lockdown and get setUnderlyingNetworks. Main thread, like
            // alwaysOnStopped, so the two never reorder.
            @Override public void serviceCreated(GoBackend.VpnService service) {
                protector = ServiceProtector.created(protector, service);
            }
            @Override public void alwaysOnStopped(GoBackend.VpnService service) {
                protector = ServiceProtector.stopped(protector, service);
                stopAlwaysOnService(service);
            }
        });
        var connectionPrefs = getSharedPreferences("connection", MODE_PRIVATE);
        alwaysOn = AlwaysOnStatus.sanitize(connectionPrefs.getString(AlwaysOnStatus.PREF_KEY, null));
        // ADR-141: beta.33 and older stored false "off" readings; drop them.
        if (connectionPrefs.contains(AlwaysOnStatus.LEGACY_PREF_KEY))
            connectionPrefs.edit().remove(AlwaysOnStatus.LEGACY_PREF_KEY).apply();
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
                failedAt("native_init");
                android.util.Log.e("DetourVpnNativeInit", "stage=" + initStage
                        + " failure=" + failure.getClass().getSimpleName());
            }
        });
        watchNetwork();
        watchWake();
        // ADR-135: the 15 s tunnel check is armed by setDeadline and stops when no window is up.
        // ADR-144: native decides recovery only once the UI said whether it is open, or after a
        // short bound, so a cold app start and a native restore never race (R5).
        worker.schedule(this::evaluateRecovery, ConnectionSupervisor.STARTUP_SETTLE_MS + 50,
                TimeUnit.MILLISECONDS);
    }

    /**
     * ADR-148, main thread in onCreate, before any worker task: the probe URL and the failover
     * opt-in from the manifest meta-data. Missing or invalid values keep both off. The URL is
     * never logged.
     */
    private void loadProbeConfig() {
        DeadPeerProbe config = DeadPeerProbe.off();
        try {
            var info = getPackageManager().getApplicationInfo(getPackageName(),
                    android.content.pm.PackageManager.GET_META_DATA);
            android.os.Bundle meta = info.metaData;
            if (meta != null)
                config = DeadPeerProbe.fromConfig(meta.get(DeadPeerProbe.META_URL),
                        meta.get(DeadPeerProbe.META_FAILOVER));
        } catch (Exception | LinkageError unavailable) {
            // Probe off.
        }
        probeConfig = config;
        deadPeer = new DeadPeerWatch(config.enabled(), config.failoverOptIn);
        log("probe_config", (config.enabled() ? "on" : "off")
                + (config.failoverOptIn ? "_failover" : ""));
    }

    /** ADR-144, any thread. */
    boolean startupSettled() {
        return ConnectionSupervisor.startupSettled(ownerSignalSeen, processStart,
                SystemClock.elapsedRealtime());
    }

    /** ADR-144, worker thread: the tunnel of the current window is running. */
    private boolean tunnelUpNow() {
        return provider != null && provider.isUp() && deadline > 0;
    }

    /** ADR-142/144, worker thread: a Dart controller that can still act owns recovery. */
    private boolean dartOwner() {
        return ConnectionSupervisor.dartOwnsRecovery(uiForeground,
                ConnectionSupervisor.restoreEngineOwnsRecovery(recoveryEngine != null,
                        restoreBootstrapping, tunnelUpNow()));
    }

    /**
     * ADR-144, worker thread. A restore engine whose tunnel is gone after its bootstrap cannot run
     * in Doze; end it so native owns recovery (fix for an engine that blocked recovery forever).
     */
    private void endIdleRestoreEngine() {
        if (recoveryEngine == null || restoreBootstrapping || tunnelUpNow()) return;
        log("restore_ended", "tunnel_lost");
        stopAlwaysOnRecovery();
    }

    /** ADR-144, worker thread: clears the restore bootstrap state and its timeout. */
    private void bootstrapEnded() {
        restoreBootstrapping = false;
        if (bootstrapTimeout != null) bootstrapTimeout.cancel(false);
        bootstrapTimeout = null;
    }

    /** ADR-144, worker thread, before provider.connect: forget the previous window's evidence. */
    void prepareWindow() {
        handshakes.reset();
        deadPeer.windowStarted(); // ADR-148: the probe ladder survives a new lease.
        windowUpAt = -1;
    }

    /**
     * ADR-144, worker thread: the same bookkeeping for a window started by the UI and by the
     * restore engine (R8; before, the UI path skipped the CPU lock and the handshake reset).
     */
    void beginWindow() {
        long now = SystemClock.elapsedRealtime();
        reason = "";
        failureStage = "";
        windowUpAt = now;
        supervisor.tunnelUp(now);
        holdCpu();
        scheduleRestoreWake();
        log("window_up");
        failoverRequested = false; // ADR-148: a restore engine read it before this window.
        underlyingStale = true; // ADR-146: establish() starts with no underlying network set.
        applyUnderlying();
        // ADR-149 (R6): read Lockdown now that establish() is done, before Dart can ask for
        // native renewal on this window (same worker queue), instead of on the next 15 s tick.
        observeAlwaysOn(protector, tunnelUpNow());
        updateDozeAids(); // ADR-147: the VPN service is up; the companion stops.
        publishState();
        recheckStateSoon(); // ADR-151: show the first handshake within about a second.
    }

    /** ADR-144, worker thread: a renewed window was applied (UI or restore engine path). */
    void windowAdvanced() {
        failureStage = "";
        holdCpu();
        scheduleRestoreWake();
        log("window_advanced");
        publishState();
    }

    /** ADR-144, worker thread: the one native connection state, from facts only. */
    ConnectionState connectionState() {
        long now = SystemClock.elapsedRealtime();
        var facts = new ConnectionState.Facts();
        facts.desired = supervisor.desired();
        facts.tunnelUp = tunnelUpNow();
        facts.networkUsable = physical.usable();
        facts.networkLossPending = physical.lossPending(now);
        if (facts.tunnelUp) {
            try {
                long handshake = provider.lastHandshakeSeconds();
                long wall = System.currentTimeMillis() / 1000;
                facts.handshakeAgeSeconds = handshake > 0 && handshake <= wall ? wall - handshake : -1;
            } catch (RuntimeException | LinkageError unavailable) {
                facts.handshakeAgeSeconds = -1;
            }
        }
        facts.networkChangeAgeMs = lastNetworkChangeAt < 0 ? -1 : now - lastNetworkChangeAt;
        // ADR-151: a handshake after the change (or its restart) ends "network_changed" at once.
        facts.restartPending = watchdog.awaitingNetworkProof(now,
                facts.handshakeAgeSeconds < 0 ? -1 : facts.handshakeAgeSeconds * 1000);
        facts.inboundFlowing = facts.tunnelUp && deadPeer.flowing(now);
        facts.dataStalled = facts.tunnelUp && deadPeer.stalled(now);
        facts.captivePortal = physical.captivePortalBlocking(); // ADR-148 (5.3)
        facts.windowAgeMs = windowUpAt < 0 ? -1 : now - windowUpAt;
        facts.restoring = (recoveryEngine != null && restoreBootstrapping)
                || supervisor.attemptInFlight(now);
        facts.lastStop = supervisor.lastStop();
        return ConnectionState.of(facts);
    }

    /** ADR-144, worker thread: the state event sent to Flutter (codes and fenced ids only). */
    private Map<String, Object> stateEvent(ConnectionState state) {
        Map<String, Object> value = new HashMap<>();
        value.put("state", state.kind.wire);
        value.put("reason", state.reason);
        value.put("desired", supervisor.desired());
        value.put("restoreActive", recoveryEngine != null);
        value.put("nativeRenewal", renewer.active());
        boolean up = tunnelUpNow();
        value.put("up", up);
        if (up && window.connection != null) {
            value.put("connection", window.connection);
            value.put("windowGeneration", window.generation);
        }
        return value;
    }

    /**
     * ADR-144, worker thread. Pushes the state to the UI engine when it changed (or when
     * {@code force}). Cheap; called after every event that can change it.
     */
    ConnectionState publishState(boolean force) {
        if (provider == null) return null;
        ConnectionState state;
        Map<String, Object> event;
        try {
            state = connectionState();
            event = stateEvent(state);
        } catch (RuntimeException unavailable) {
            return null; // A state bug must never affect the tunnel.
        }
        String key = state + "|" + event.get("connection") + "|" + event.get("windowGeneration")
                + "|" + event.get("desired") + "|" + event.get("restoreActive")
                + "|" + event.get("nativeRenewal");
        boolean changed = !key.equals(lastPublished);
        if (!changed && !force) return state;
        if (!state.toString().equals(lastLoggedState)) {
            lastLoggedState = state.toString();
            log("state", state.kind.wire + "_" + state.reason);
        }
        lastPublished = key;
        mainThread.post(() -> {
            var sink = stateSink;
            if (sink == null) return;
            try { sink.success(event); } catch (RuntimeException ignored) { /* Engine gone. */ }
        });
        return state;
    }

    void publishState() { publishState(false); }

    /** ADR-151: the fast state re-check and when its trigger happened (-1: none). Worker thread. */
    private java.util.concurrent.ScheduledFuture<?> stateRecheck;
    private long stateRecheckFrom = -1;

    /**
     * ADR-151, worker thread: re-read and publish the state every {@link
     * ConnectionState#RECHECK_MS} for up to {@link ConnectionState#RECHECK_FOR_MS} after a window
     * start, a restart in place or a network change, until it is connected. Before, nothing
     * looked at the handshake between 15 s watch ticks, so «Восстановление связи» or
     * «Подключение» stayed on screen long after the handshake. Display only: it restarts,
     * stops or escalates nothing.
     */
    private void recheckStateSoon() {
        stateRecheckFrom = SystemClock.elapsedRealtime();
        if (stateRecheck == null || stateRecheck.isDone())
            stateRecheck = worker.schedule(this::stateRecheckTick, ConnectionState.RECHECK_MS,
                    TimeUnit.MILLISECONDS);
    }

    private void stateRecheckTick() {
        stateRecheck = null;
        try {
            if (stateRecheckFrom < 0) return;
            ConnectionState state = publishState(false);
            long since = SystemClock.elapsedRealtime() - stateRecheckFrom;
            if (ConnectionState.recheckSoon(state, tunnelUpNow(), since)) {
                stateRecheck = worker.schedule(this::stateRecheckTick, ConnectionState.RECHECK_MS,
                        TimeUnit.MILLISECONDS);
            } else {
                stateRecheckFrom = -1;
            }
        } catch (RuntimeException ignored) {
            stateRecheckFrom = -1; // A state bug must never affect the tunnel.
        }
    }

    /** ADR-144, main thread: the UI engine subscribed to (sink) or left (null) the state stream. */
    void stateListener(io.flutter.plugin.common.EventChannel.EventSink sink) {
        stateSink = sink;
        if (sink != null) worker.execute(() -> publishState(true));
    }

    /** ADR-143: pending settle of a wake signal and a coalesced re-signal; worker thread only. */
    private java.util.concurrent.ScheduledFuture<?> pendingWake;
    private java.util.concurrent.ScheduledFuture<?> freshRetry;

    /**
     * ADR-143: Doze exit, light-idle exit (API 33+), screen on and unlock are fresh wake signals.
     * Registered-only broadcasts, so a runtime receiver in this process; not exported. It reaches
     * the app only while the process runs and is not frozen (see ADR-143); the recovery alarm
     * stays the backstop. The receiver starts nothing itself.
     */
    private void watchWake() {
        try {
            android.content.IntentFilter filter = new android.content.IntentFilter();
            filter.addAction(android.content.Intent.ACTION_SCREEN_ON);
            filter.addAction(android.content.Intent.ACTION_USER_PRESENT);
            filter.addAction(PowerManager.ACTION_DEVICE_IDLE_MODE_CHANGED);
            if (android.os.Build.VERSION.SDK_INT >= 33)
                filter.addAction(PowerManager.ACTION_DEVICE_LIGHT_IDLE_MODE_CHANGED);
            android.content.BroadcastReceiver receiver = new android.content.BroadcastReceiver() {
                @Override public void onReceive(android.content.Context context,
                        android.content.Intent intent) {
                    onWakeBroadcast(intent == null ? null : intent.getAction());
                }
            };
            if (android.os.Build.VERSION.SDK_INT >= 33) {
                registerReceiver(receiver, filter, RECEIVER_NOT_EXPORTED);
            } else {
                registerReceiver(receiver, filter);
            }
        } catch (RuntimeException unavailable) {
            // Alarms and network callbacks still drive recovery.
        }
    }

    private void onWakeBroadcast(String action) {
        if (action == null) return;
        boolean light = false;
        boolean lightNow = false;
        boolean deepNow = false;
        try {
            PowerManager power = (PowerManager) getSystemService(POWER_SERVICE);
            deepNow = power.isDeviceIdleMode();
            if (android.os.Build.VERSION.SDK_INT >= 33) {
                light = PowerManager.ACTION_DEVICE_LIGHT_IDLE_MODE_CHANGED.equals(action);
                lightNow = power.isDeviceLightIdleMode();
            }
        } catch (RuntimeException unavailable) {
            return;
        }
        if (!ConnectionSupervisor.wakeSignal(
                android.content.Intent.ACTION_SCREEN_ON.equals(action),
                android.content.Intent.ACTION_USER_PRESENT.equals(action),
                PowerManager.ACTION_DEVICE_IDLE_MODE_CHANGED.equals(action), deepNow,
                light, lightNow)) return;
        // Keep the CPU up across the settle; released by its timeout.
        try {
            PowerManager power = (PowerManager) getSystemService(POWER_SERVICE);
            PowerManager.WakeLock lock = power.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK,
                    "detourvpn:wake_signal");
            lock.setReferenceCounted(false);
            lock.acquire(ConnectionSupervisor.WAKE_SETTLE_MS + 3_000);
        } catch (RuntimeException ignored) { /* Best effort. */ }
        worker.execute(() -> {
            if (pendingWake != null) pendingWake.cancel(false); // coalesce a burst of wake events
            pendingWake = worker.schedule(() -> freshSignal("wake"),
                    ConnectionSupervisor.WAKE_SETTLE_MS, TimeUnit.MILLISECONDS);
        });
    }

    /**
     * ADR-143, worker thread. A fresh wake or network signal: when native owns recovery (ADR-142)
     * and nothing runs, clear the backoff and recover at once; at most one such attempt per 30 s.
     */
    void freshSignal(String kind) {
        enforceDeadline();
        if (!startupSettled()) return; // ADR-144: the settle task evaluates shortly.
        endIdleRestoreEngine();
        long now = SystemClock.elapsedRealtime();
        boolean up = tunnelUpNow();
        var decision = supervisor.freshSignal(now, up, dartOwner(), physical.usable());
        switch (decision.action) {
            case START_RECOVERY:
                log("fresh_signal", kind);
                evaluateRecovery();
                if ("restore_started".equals(recoveryNote)) recoveryNote = "fresh_" + kind;
                break;
            case WAIT:
                if (freshRetry != null) freshRetry.cancel(false);
                freshRetry = worker.schedule(() -> freshSignal(kind), decision.delayMs + 50,
                        TimeUnit.MILLISECONDS);
                break;
            default:
                // Codes only. "up" and "not_desired" are the normal case and keep the last note.
                if (!"up".equals(decision.why) && !"not_desired".equals(decision.why))
                    recoveryNote = "fresh_ignored_" + decision.why;
        }
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
            try { stop(); reason = "lease_expired"; failedAt("native_deadline"); }
            catch (Exception ignored) { reason = "native_disconnect_failed"; failedAt("native_disconnect"); }
            stopped(reason);
        }
    }

    /**
     * Names the native step that failed. The first failure of a window stays the named step, so a
     * failed renewal is not replaced by the deadline it leads to.
     */
    void failedAt(String stage) {
        if (failureStage.isEmpty()) failureStage = stage;
    }

    /** Worker thread. Sets the native deadline and (re)arms its one-shot check. */
    void setDeadline(long end) {
        deadline = end;
        if (deadlineCheck != null) deadlineCheck.cancel(false);
        deadlineCheck = null;
        if (end <= 0) return;
        // ADR-142: start of the up period. ADR-144: only once the tunnel really runs; a connect
        // sets the deadline first and beginWindow() marks the start after it succeeded.
        if (provider != null && provider.isUp()) supervisor.tunnelUp(SystemClock.elapsedRealtime());
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
        log("stopped", why);
        windowUpAt = -1;
        // ADR-142: a stop after a stable window starts recovery at once (same wake-up).
        if (supervisor.tunnelDown(why, SystemClock.elapsedRealtime()))
            setWantsConnected(this, false);
        endIdleRestoreEngine(); // ADR-144: its tunnel is gone; native owns recovery now.
        evaluateRecovery();
    }

    /**
     * ADR-139/141: read the Always-on setting from a service instance (the only place Android
     * exposes it) and remember it, so the UI can show it. Worker thread. The caller passes whether
     * this app's VPN is established right now; a read without it is discarded (see
     * {@link AlwaysOnStatus}). Never throws; never changes recovery decisions.
     */
    private void observeAlwaysOn(VpnService service, boolean vpnEstablished) {
        if (service == null) return;
        String value;
        try {
            boolean api = android.os.Build.VERSION.SDK_INT >= 29;
            value = AlwaysOnStatus.reading(api, vpnEstablished,
                    api && vpnEstablished && service.isAlwaysOn(),
                    api && vpnEstablished && service.isLockdownEnabled());
        } catch (RuntimeException unavailable) {
            return;
        }
        // ADR-149 (R6): the renewal transport follows the same rule as the display value.
        lockdown = AlwaysOnStatus.lockdownAfterRead(value, lockdown);
        if (value == null || value.equals(alwaysOn)) return;
        alwaysOn = value;
        try {
            getSharedPreferences("connection", MODE_PRIVATE).edit()
                    .putString(AlwaysOnStatus.PREF_KEY, value).apply();
        } catch (RuntimeException ignored) { /* Display only. */ }
    }

    /** ADR-124/125, worker thread. Native recovery runs only when no Dart controller owns it. */
    void evaluateRecovery() {
        // ADR-144: until the UI said whether it is open (bounded), decide nothing (R5).
        if (!startupSettled()) return;
        endIdleRestoreEngine();
        long now = SystemClock.elapsedRealtime();
        boolean up = tunnelUpNow();
        // ADR-142: a stopped Activity's Dart controller cannot run in Doze; native owns then.
        var decision = supervisor.evaluate(now, up, false, dartOwner(), physical.usable());
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
        updateDozeAids();
        publishState();
    }

    /** ADR-147: the companion service was asked to start and not yet confirmed/stopped. */
    private boolean companionRequested;
    /** ADR-147: the companion is in the foreground (set from its main-thread lifecycle). */
    private volatile boolean companionRunning;
    private long companionRefusedAt = Long.MIN_VALUE / 2;
    /** ADR-147: the periodic backstop job is known to be scheduled in this process. */
    private boolean backstopScheduled;

    /**
     * ADR-147 (3.3, 3.4), worker thread: keep the companion foreground service only during a
     * recovery gap, and the backstop job only while the VPN is wanted. Cheap; called after every
     * recovery decision, window start and user disconnect.
     */
    private void updateDozeAids() {
        boolean desired = supervisor.desired();
        if (desired && !backstopScheduled) {
            BackstopJobService.ensureScheduled(this);
            backstopScheduled = true;
        } else if (!desired && backstopScheduled) {
            BackstopJobService.cancel(this);
            backstopScheduled = false;
        }
        boolean wanted = DozeSurvival.companionWanted(desired, tunnelUpNow(),
                ConnectionSupervisor.terminal(supervisor.lastStop()));
        long now = SystemClock.elapsedRealtime();
        if (wanted && !companionRequested && !companionRunning) {
            if (now - companionRefusedAt < ConnectionSupervisor.REFUSED_RETRY_MS) return;
            if (!DozeSurvival.companionMayStart(android.os.Build.VERSION.SDK_INT,
                    exactAlarmAllowed())) {
                companionRefusedAt = now;
                log("companion_skipped", "fgs_type");
                return;
            }
            try {
                var start = new android.content.Intent(this, RecoveryCompanionService.class);
                if (android.os.Build.VERSION.SDK_INT >= 26) startForegroundService(start);
                else startService(start);
                companionRequested = true;
                log("companion_start");
            } catch (RuntimeException refused) {
                // Android 12+ background-start limits: alarms and the backstop still recover.
                companionRefusedAt = now;
                log("companion_refused", "background_start");
            }
        } else if (!wanted && (companionRequested || companionRunning)) {
            companionRequested = false;
            try {
                stopService(new android.content.Intent(this, RecoveryCompanionService.class));
                log("companion_stop");
            } catch (RuntimeException ignored) { /* Already gone. */ }
        }
    }

    /** ADR-147: same rule as the renewal alarm (SCHEDULE_EXACT_ALARM). Never throws. */
    private boolean exactAlarmAllowed() {
        try {
            int sdk = android.os.Build.VERSION.SDK_INT;
            return DozeSurvival.exactAlarmAllowed(sdk,
                    sdk >= DozeSurvival.EXACT_ALARM_PERMISSION_SDK && alarms().canScheduleExactAlarms());
        } catch (RuntimeException unavailable) {
            return false;
        }
    }

    /** ADR-147, main thread: the companion reached the foreground. */
    void companionStarted() {
        companionRunning = true;
        // A tunnel may have come up while it was starting; re-check on the worker.
        worker.execute(this::updateDozeAids);
    }

    /** ADR-147, main thread: the companion could not enter the foreground. */
    void companionRefused(String why) {
        worker.execute(() -> {
            companionRequested = false;
            companionRefusedAt = SystemClock.elapsedRealtime();
            log("companion_refused", why);
        });
    }

    /** ADR-147, main thread: the companion was destroyed. */
    void companionStopped() {
        companionRunning = false;
        worker.execute(() -> companionRequested = false);
    }

    /**
     * ADR-147 (3.4): the periodic backstop job ran (JobScheduler). Bounded wake lock; it only
     * sends a fresh signal when the VPN is wanted, no tunnel is up and nothing is in flight.
     * {@code done} finishes the job.
     */
    void backstop(Runnable done) {
        PowerManager.WakeLock lock = null;
        try {
            PowerManager power = (PowerManager) getSystemService(POWER_SERVICE);
            lock = power.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "detourvpn:backstop");
            lock.setReferenceCounted(false);
            lock.acquire(DozeSurvival.BACKSTOP_LOCK_MS);
        } catch (RuntimeException ignored) { /* Best effort. */ }
        final PowerManager.WakeLock held = lock;
        // A job that started the process waits out the startup settle (ADR-144), as alarms do.
        long settle = startupSettled() ? 0
                : Math.max(0, processStart + ConnectionSupervisor.STARTUP_SETTLE_MS
                        - SystemClock.elapsedRealtime()) + 50;
        worker.schedule(() -> {
            try {
                log("backstop");
                enforceDeadline();
                long now = SystemClock.elapsedRealtime();
                if (DozeSurvival.backstopSignal(supervisor.desired(), tunnelUpNow(),
                        supervisor.attemptInFlight(now))) {
                    freshSignal("backstop");
                } else if (!supervisor.desired()) {
                    // The VPN is no longer wanted (also a job left from an earlier process).
                    BackstopJobService.cancel(this);
                    backstopScheduled = false;
                }
            } catch (RuntimeException ignored) {
                // A backstop bug must never affect the tunnel.
            } finally {
                try { if (held != null && held.isHeld()) held.release(); }
                catch (RuntimeException ignored) { /* Released by its timeout. */ }
                done.run();
            }
        }, settle, TimeUnit.MILLISECONDS);
    }

    private void startRecovery(long now) {
        log("recovery_start", supervisor.stopCategory());
        if (VpnService.prepare(this) != null) {
            reason = "vpn_revoked";
            failedAt("android_vpn_permission");
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
            log("recovery_refused");
            failedAt("native_restore");
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
        log("alarm", "recover");
        // ADR-144: an alarm that starts the process waits out the startup settle (the broadcast
        // stays open meanwhile, so the process is not frozen before it decides).
        long settle = startupSettled() ? 0
                : Math.max(0, processStart + ConnectionSupervisor.STARTUP_SETTLE_MS
                        - SystemClock.elapsedRealtime()) + 50;
        worker.schedule(() -> {
            try {
                enforceDeadline();
                evaluateRecovery();
            } finally {
                done.run();
            }
        }, settle, TimeUnit.MILLISECONDS);
    }

    /** ADR-125: the UI engine attached or detached. Detaching is not a disconnect. */
    void uiEngine(boolean attached) {
        uiAttached = attached;
        if (!attached) uiForeground = false;
        log("ui_engine", attached ? "attached" : "detached");
        worker.execute(this::evaluateRecovery);
    }

    /** ADR-142: Activity onStart (true) / onStop (false). Re-evaluates who owns recovery. */
    void uiForeground(boolean started) {
        uiForeground = started && uiAttached;
        ownerSignalSeen = true; // ADR-144: the owner is known; native may decide from now on.
        log("owner", uiForeground ? "ui" : "native");
        worker.execute(this::evaluateRecovery);
    }

    /** ADR-124: explicit user disconnect (UI channel `setIntent false`). Worker thread. */
    void userDisconnect() {
        generation.incrementAndGet();
        failureStage = "";
        supervisor.userDisconnected();
        setWantsConnected(this, false);
        cancelRecoveryAlarm();
        log("user_disconnect");
        deadPeer.reset(); // ADR-148: the next connect starts a fresh probe ladder.
        failoverRequested = false;
        if (recoveryEngine != null) stopAlwaysOnRecovery();
        updateDozeAids(); // ADR-147: no companion and no backstop once the user disconnected.
        publishState();
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
                    worker.execute(() -> applyNetwork(network, networkCallbacks.available(
                            network.toString(), known == null || hasInternet(known),
                            known != null && captive(known), known != null && validated(known))));
                }
                @Override public void onCapabilitiesChanged(android.net.Network network,
                        NetworkCapabilities caps) {
                    boolean internet = hasInternet(caps);
                    boolean portal = captive(caps);
                    boolean valid = validated(caps);
                    worker.execute(() -> applyNetwork(network, networkCallbacks.capabilities(
                            network.toString(), internet, portal, valid)));
                }
                @Override public void onLinkPropertiesChanged(android.net.Network network,
                        android.net.LinkProperties properties) {
                    var addresses = new java.util.TreeSet<String>();
                    for (var address : properties.getLinkAddresses())
                        addresses.add(address.getAddress().getHostAddress());
                    worker.execute(() -> applyNetwork(network,
                            networkCallbacks.linkAddresses(network.toString(), addresses)));
                }
                @Override public void onLost(android.net.Network network) {
                    worker.execute(() -> applyNetwork(network, networkCallbacks.lost(
                            network.toString(), SystemClock.elapsedRealtime())));
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

    /**
     * ADR-146, worker thread: applies one default-network callback decision
     * ({@link NetworkCallbacks}, unit-tested) with the Android objects held here.
     */
    private void applyNetwork(android.net.Network network, NetworkCallbacks.Outcome out) {
        if (out.log != null) log(out.log, out.logDetail);
        if (out.cancelLossTimer && lossCheck != null) lossCheck.cancel(false);
        if (out.cancelLossTimer) lossCheck = null;
        if (out.underlyingStale) underlyingStale = true;
        if (out.setDefault) defaultNetwork = network;
        if (out.clearDefault) defaultNetwork = null;
        if (out.armLossTimer)
            lossCheck = worker.schedule(this::networkLossConfirmed,
                    NetworkCallbacks.LOSS_TIMER_MS, TimeUnit.MILLISECONDS);
        if (out.pathChanged) networkPathChanged();
        if (out.settleWatch) scheduleWatch(TunnelWatchdog.SETTLE_MS);
        if (out.evaluateRecovery) evaluateRecovery();
        if (out.freshSignal) freshSignal("network");
        if (out.publishState) publishState();
    }

    /** ADR-146, worker thread: the loss lasted {@link PhysicalNetwork#LOSS_CONFIRM_MS}. */
    private void networkLossConfirmed() {
        lossCheck = null;
        if (!networkCallbacks.lossTimerFired()) return;
        log("network_loss_confirmed");
        publishState();
    }

    /**
     * Worker thread. The encrypted transport's path may have moved: verify it once the new network
     * held for {@link TunnelWatchdog#SETTLE_MS} (ADR-146: 0.5 s; the watch then restarts the socket
     * and sets the underlying network at once instead of on the next 15 s tick).
     */
    private void networkPathChanged() {
        long now = SystemClock.elapsedRealtime();
        lastNetworkChangeAt = now;
        underlyingStale = true;
        deadPeer.networkChanged(); // ADR-148: earlier probe failures say nothing about this path.
        // ADR-146: no first-tick gate; a change right after connect is verified too.
        watchdog.networkChanged(now, tunnelUpNow() ? (windowUpAt >= 0 ? windowUpAt : now) : -1);
        scheduleWatch(TunnelWatchdog.SETTLE_MS + 50);
        publishState();
        if (tunnelUpNow()) recheckStateSoon(); // ADR-151
    }

    /**
     * ADR-146, worker thread. Tells Android which physical network carries the tunnel, so metering
     * and validation of the VPN network follow the real path. Must run after every establish()
     * (window start, restart in place): a new interface starts with no underlying network. Needs
     * the service instance ({@link #protector}); since ADR-149 native holds it for UI-started
     * tunnels too. If it is still missing the call is skipped and Android keeps its default
     * behaviour (tracking the default network). Only codes are logged, never a network id.
     * Never throws.
     */
    private void applyUnderlying() {
        if (!underlyingStale) return;
        android.net.Network network = defaultNetwork;
        VpnService service = protector;
        if (!tunnelUpNow() || network == null || !physical.usable()) return; // stays stale
        underlyingStale = false;
        if (service == null) { log("underlying_skipped", "no_service"); return; }
        try {
            service.setUnderlyingNetworks(new android.net.Network[] {network});
            log("underlying_set");
        } catch (RuntimeException failed) {
            log("underlying_failed"); // Display only; the tunnel's own socket is unaffected.
        }
    }

    /** Worker thread. Recreates a stalled tunnel in place; fail-closed if that fails. */
    private void watchTunnel() {
        try {
            enforceDeadline();
            long now = SystemClock.elapsedRealtime();
            boolean up = provider != null && deadline > 0 && provider.isUp();
            if (!up) { watchdog.tunnelDown(); watchTick = null; publishState(); return; }
            armWatch(WATCH_MS);
            if (!watchdog.running()) watchdog.tunnelUp(now);
            long handshake = provider.lastHandshakeSeconds();
            long wall = System.currentTimeMillis() / 1000;
            handshakes.observe(handshake, wall);
            observeAlwaysOn(protector, true); // ADR-141: meaningful only while up.
            long age = handshake > 0 && handshake <= wall ? (wall - handshake) * 1000 : -1;
            boolean usable = physical.usable();
            // ADR-148: while the probe ladder owns stall recovery, the watchdog keeps only its
            // network-change restarts.
            var action = watchdog.decide(now, age, usable, !deadPeer.ownsStallRecovery(now));
            if (action == TunnelWatchdog.Action.NEW_LEASE) {
                // ADR-146: two restarts after a network change got no handshake; the window is
                // given up so recovery (UI or native) starts a new one. Same path as a failed
                // restart; the code is the existing "no handshake on this path" one.
                log("watchdog_new_lease", "network_unrecovered");
                giveUpWindow("handshake_not_established");
                return;
            }
            if (action == TunnelWatchdog.Action.RESTART) {
                watchdog.restarted(now);
                log("watchdog_restart", age < 0 ? "no_handshake" : "stale_or_network");
                restartTunnel();
                return;
            }
            // ADR-148 (5.1): the data path is fed here, on the native tick, and nowhere else.
            long[] counters = trafficCounters();
            var probe = deadPeer.tick(now, counters[1], counters[0], age,
                    windowUpAt < 0 ? -1 : now - windowUpAt, usable);
            if (probe == DeadPeerWatch.Action.PROBE) startProbe(deadPeer.probeId());
            else if (probe != DeadPeerWatch.Action.NONE) escalate(probe); // a lost result
            if (!tunnelUpNow()) return;
            applyUnderlying(); // ADR-146: a confirmed change that needed no restart.
            schedulePendingWatch(now);
            publishState();
        } catch (RuntimeException ignored) {
            // A watchdog bug must never take the tunnel down by itself.
        }
    }

    /**
     * Worker thread: restart the running tunnel in place (watchdog or probe ladder); fail closed
     * if that fails.
     */
    private void restartTunnel() {
        try {
            // ADR-146: restart first, then set the underlying network (establish() may clear it).
            // TODO: verify on a real device that establish() clears the underlying network and
            // that this order is correct (ADR-146).
            provider.restartInPlace();
            tunnelRestarts++;
            underlyingStale = true; // ADR-146: the new interface has no underlying network.
            applyUnderlying();
            schedulePendingWatch(SystemClock.elapsedRealtime());
            publishState();
            recheckStateSoon(); // ADR-151: the restart's handshake ends "network_changed" at once.
        } catch (Exception | LinkageError failed) {
            giveUpWindow("native_restart_failed");
        }
    }

    /**
     * Worker thread: stop this window with {@code why} so recovery (UI or native) starts a new
     * one. The same path for the watchdog's new-lease step, a failed restart and the probe ladder.
     */
    private void giveUpWindow(String why) {
        try { stop(); } catch (Exception ignored) { /* Already down. */ }
        reason = why;
        failedAt("native_watchdog_restart");
        watchdog.tunnelDown();
        stopped(why);
    }

    /** ADR-133/148, worker thread: [transfer, received] advisory byte counters, -1 if unknown. */
    private long[] trafficCounters() {
        long transfer = -1;
        long received = -1;
        try {
            if (provider.capabilities().contains("traffic-stats-advisory.v1")) {
                transfer = provider.transferBytes();
                received = provider.receivedBytes();
            }
        } catch (RuntimeException | LinkageError ignored) { /* Advisory unavailable. */ }
        return new long[] {transfer, received};
    }

    /**
     * ADR-148 (5.2), worker thread: one probe through the tunnel to the configured project-owned
     * health URL. Its result comes back on the worker thread. Codes only in the log.
     */
    private void startProbe(long id) {
        String url = probeConfig.url;
        if (url == null) return;
        log("probe_start");
        TunnelProbeRunner.run(this, url, probeIo, worker,
                (result, code) -> worker.execute(() -> probeFinished(id, result, code)));
    }

    /** ADR-148, worker thread: the probe's result and the escalation step it leads to. */
    private void probeFinished(long id, DeadPeerWatch.ProbeResult result, String code) {
        try {
            log("probe_" + result.name().toLowerCase(java.util.Locale.ROOT), code);
            var action = deadPeer.probeResult(SystemClock.elapsedRealtime(), id, result);
            if (!tunnelUpNow()) return; // the window ended meanwhile; nothing to escalate
            if (action != DeadPeerWatch.Action.NONE) {
                escalate(action);
                return;
            }
            // One failure: the second probe follows shortly, not on the next 15 s tick.
            if (result == DeadPeerWatch.ProbeResult.FAIL
                    && deadPeer.step() < DeadPeerWatch.STEP_EXHAUSTED
                    && (pendingCheck == null || pendingCheck.isDone()))
                scheduleWatch(DeadPeerWatch.PROBE_RETRY_MS + 50);
            publishState();
        } catch (RuntimeException ignored) {
            // A probe bug must never take the tunnel down by itself.
        }
    }

    /** ADR-148, worker thread: one escalation step of the probe ladder. */
    private void escalate(DeadPeerWatch.Action action) {
        switch (action) {
            case RESTART:
                log("probe_restart");
                watchdog.restartedByProbe(SystemClock.elapsedRealtime());
                restartTunnel();
                break;
            case NEW_LEASE:
                log("probe_new_lease");
                giveUpWindow("dead_peer");
                break;
            case FAILOVER:
                // Reached only with the owner's opt-in (DeadPeerWatch). The restore engine picks
                // another available node for its next window; the user's saved node is kept.
                log("probe_failover");
                failoverRequested = true;
                giveUpWindow("dead_peer_failover");
                break;
            default:
                break;
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
                // ADR-149 (R6): no Lockdown read here. Before establish() Android answers false
                // whatever the setting is; beginWindow() reads it once the tunnel is up.
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
                // ADR-144: the engine owns recovery while it brings its first tunnel up, bounded.
                restoreBootstrapping = true;
                if (bootstrapTimeout != null) bootstrapTimeout.cancel(false);
                bootstrapTimeout = worker.schedule(() -> restoreBootstrapTimedOut(engine),
                        ConnectionSupervisor.RESTORE_BOOTSTRAP_MS, TimeUnit.MILLISECONDS);
                log("restore_engine_start");
                String bundle = FlutterInjector.instance().flutterLoader().findAppBundlePath();
                engine.getDartExecutor().executeDartEntrypoint(
                        new DartExecutor.DartEntrypoint(bundle, "alwaysOnRecoveryMain"));
            } catch (Exception | LinkageError failure) {
                failedAt("native_restore");
                log("restore_engine_failed", "start");
                stopAlwaysOnRecovery();
                evaluateRecovery(); // ADR-144: before, nothing re-evaluated after this failure.
            }
        });
    }

    /**
     * ADR-144, worker thread. A restore engine that has not reported its first result in time is
     * treated like a failed bootstrap, so a hung engine can never hold recovery forever.
     */
    private void restoreBootstrapTimedOut(FlutterEngine engine) {
        if (recoveryEngine != engine || !restoreBootstrapping) return;
        log("restore_engine_failed", "timeout");
        failedAt("native_restore");
        bootstrapEnded();
        stopAlwaysOnRecovery();
        if (supervisor.recoveryFailed("recovery_timeout")) setWantsConnected(this, false);
        evaluateRecovery();
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
                log("restore_engine_failed", code);
                bootstrapEnded();
                failedAt("native_restore");
                stopAlwaysOnRecovery();
                if (supervisor.recoveryFailed(code)) setWantsConnected(this, false);
                evaluateRecovery();
            });
            return;
        }
        if (call.method.equals("bootstrapComplete")) {
            result.success(null);
            worker.execute(() -> {
                bootstrapEnded(); // ADR-144: from now on it owns recovery only while its tunnel is up.
                log("restore_engine_up");
                endIdleRestoreEngine();
                evaluateRecovery();
            });
            return;
        }
        // ADR-140: the restore engine may hand renewal to native (protected sockets under
        // Lockdown), so renewal no longer waits for Dart timers in Doze.
        if (!java.util.Set.of("startNativeRenewal", "protectedRelayRead", "sessionRead", "sessionWrite", "nodeRead",
                "failoverRead", "publicKey", "prove", "permission", "connectWindow", "advanceWindow",
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
                    case "startNativeRenewal":
                        if (generation != this.generation.get() || provider == null
                                || !provider.isUp() || deadline <= 0)
                            throw new IllegalStateException();
                        if (!RenewalOwner.nativeMayStart(true, recoveryEngine != null, lockdown,
                                protector != null)) {
                            value = false; // Dart restore keeps renewing.
                            break;
                        }
                        renewer.start(call.arguments(), generation);
                        value = true;
                        break;
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
                    case "failoverRead":
                        // ADR-148: one-shot; set only by the opted-in probe ladder.
                        value = failoverRequested && deadPeer.failoverOptIn();
                        failoverRequested = false;
                        if (Boolean.TRUE.equals(value)) log("failover_read");
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
                    case "disconnect": {
                        boolean wasUp = tunnelUpNow();
                        stop();
                        // ADR-144: after its bootstrap, the restore engine dropping its tunnel
                        // (a failed renewal, a lost lease) ends that engine; native owns recovery
                        // from here, since the engine's Dart timers sleep in Doze.
                        if (wasUp && !restoreBootstrapping) {
                            reason = "restore_stopped";
                            stopped(reason);
                        }
                        break;
                    }
                    case "advanceWindow":
                        if (generation != this.generation.get() || !provider.isUp())
                            throw new IllegalStateException();
                        window.advance(call.argument("connection"), call.argument("runtime"),
                                ((Number) call.argument("generation")).longValue(),
                                submittedAt + ((Number) call.argument("remainingMs")).longValue(),
                                SystemClock.elapsedRealtime());
                        setDeadline(window.deadline);
                        windowAdvanced();
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
                        prepareWindow();
                        try {
                            provider.connect(config);
                            if (generation != this.generation.get()
                                    || SystemClock.elapsedRealtime() >= deadline) {
                                stop();
                                throw new IllegalStateException();
                            }
                            beginWindow();
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
        bootstrapEnded();
        log("restore_adopted_by_ui");
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
        bootstrapEnded();
        cancelRestoreWake();
        try { stop(); } catch (Exception ignored) { reason = "native_disconnect_failed"; failedAt("native_disconnect"); }
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
            boolean revoked = VpnService.prepare(this) != null;
            if (revoked) {
                setWantsConnected(this, false);
                renewer.stop();
                reason = "vpn_revoked";
                failedAt("android_vpn_permission");
                supervisor.tunnelDown(reason);
                log("vpn_revoked");
            }
            // ADR-144 (R7): the service went away while a window was still held and nobody here
            // stopped it (every own stop clears the deadline first, on this thread). Before, a
            // UI-started tunnel that died this way waited for its deadline (up to 15 min).
            boolean diedWithWindow = deadline > 0 && (provider == null || !provider.isUp());
            boolean restoreService = service != null && recoveryService == service;
            if (!restoreService && !diedWithWindow) return;
            cancelRestoreWake();
            generation.incrementAndGet();
            renewer.stop();
            releaseCpu();
            setDeadline(0);
            window.clear();
            if (restoreService) {
                bootstrapEnded();
                recoveryChannel = null;
                recoveryService = null;
                ProtectedApiRelay relay = protectedRelay;
                protectedRelay = null;
                if (relay != null) relay.close();
                FlutterEngine engine = recoveryEngine;
                recoveryEngine = null;
                if (engine != null) mainThread.post(engine::destroy);
            }
            if (revoked) {
                stopped(reason);
            } else if (diedWithWindow) {
                reason = "service_died";
                failedAt("native_service");
                stopped(reason);
            } else {
                evaluateRecovery();
            }
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
        java.util.concurrent.Future<long[]> view = worker.submit(() -> {
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
        });
        try {
            return view.get(5, TimeUnit.SECONDS);
        } catch (java.util.concurrent.TimeoutException busy) {
            // ADR-152: the worker is busy; drop the stale read and let the renewer look again.
            view.cancel(false);
            throw busy;
        }
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
                failureStage = "";
                log("native_renewed");
                publishState();
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
        windowUpAt = -1;
        publishState();
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
        // ADR-133: data-path evidence from the same advisory counters. ADR-148 (5.1): sampled only
        // on the native watch tick; a snapshot only reads it.
        long nowElapsed = SystemClock.elapsedRealtime();
        boolean flowing = up && deadPeer.flowing(nowElapsed);
        boolean stalled = up && deadPeer.stalled(nowElapsed);
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
                watchdog.awaitingNetworkProof(SystemClock.elapsedRealtime(),
                        handshakeAge < 0 ? -1 : handshakeAge * 1000), stalled));
        value.put("evidence", ConnectionSupervisor.evidence(tunnelUp, handshakeAge, afterChange, flowing));
        value.put("failureCategory", ConnectionSupervisor.category(tunnelUp ? "" : reason));
        // ADR-144: the authoritative connection state (the same one the state stream pushes).
        ConnectionState state = connectionState();
        value.put("state", state.kind.wire);
        value.put("stateReason", state.reason);
        // ADR-134: why native is not running a tunnel although the user still wants it.
        value.put("stopCategory", tunnelUp ? "none" : supervisor.stopCategory());
        value.put("recoveryAttempts", (long) supervisor.attempts());
        value.put("recoveryNote", recoveryNote);
        value.put("alwaysOn", alwaysOn); // ADR-139: on | off | lockdown | unknown.
        value.put("uiAttached", uiAttached);
        value.put("uiForeground", uiForeground); // ADR-142: diagnostics only.
        // Additive: the native step that failed in this window ("" = none). Older Dart ignores it.
        value.put("failureStage", failureStage);
        return value;
    }
    /** ADR-124: the native supervisor line for the diagnostic report. Worker thread. */
    String diagnosticLine() {
        Map<String, Object> state = snapshot();
        String device = "";
        try {
            android.os.PowerManager power = (android.os.PowerManager) getSystemService(POWER_SERVICE);
            if (power != null) device = DiagnosticReportText.deviceLine(power.isInteractive(),
                    power.isDeviceIdleMode());
        } catch (RuntimeException unavailable) { /* Report without the device line. */ }
        return DiagnosticReportText.nativeLine((String) state.get("transport"),
                (String) state.get("evidence"), (String) state.get("failureCategory"),
                supervisor.desired(), physical.validated(), supervisor.attempts(), tunnelRestarts)
                + device + eventLogText();
    }

    /** ADR-144: event-log section of the diagnostic report (codes and ages only). */
    String eventLogText() {
        long now = SystemClock.elapsedRealtime();
        String state = "";
        try { state = connectionState().toString(); } catch (RuntimeException ignored) { }
        return "\nСостояние: " + (state.isEmpty() ? "unknown" : state)
                + "\nЖурнал подключения (последние события):"
                + events.format(now, EVENT_REPORT_LINES);
    }

    static final int EVENT_REPORT_LINES = 80;

    @Override public String getName() { return "i013"; }
    @Override public void onStateChange(State state) { /* Status is read from serialized backend. */ }
}
