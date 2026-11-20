package org.example.vpnpoc;

/**
 * ADR-124: the one authoritative record of what the user wants and what may happen next. Pure
 * (no Android types, no clock, no I/O) so every rule is unit-tested on the JVM. Owned by
 * {@link ClientApplication} and touched only on its worker thread; Flutter observes it through
 * {@code snapshot}.
 *
 * <ul>
 *   <li>User intent ({@link #desired()}) changes only on an explicit user connect or disconnect, or
 *       a terminal failure. Screen-off, backgrounding, a lost network, a closed Activity or an
 *       engine cleanup never change it.</li>
 *   <li>Every intent change bumps {@link #intentGeneration()}. Work started for an older generation
 *       (a recovery start, a restore engine's result) must be dropped by the caller.</li>
 *   <li>Native recovery is only a trigger: when the tunnel is down for a recoverable reason, no
 *       Dart controller is attached (UI or restore engine), the physical network is usable and the
 *       backoff has passed, it starts the headless restore engine, which asks the API for a new
 *       window. Native never extends or reuses a window by itself.</li>
 * </ul>
 */
final class ConnectionSupervisor {
    /** First retry delay; doubles per failed attempt up to {@link #MAX_DELAY_MS}. */
    static final long BASE_DELAY_MS = 30_000;
    /** ADR-140: the VPN stays wanted for hours, so a retry is never further away than this. */
    static final long MAX_DELAY_MS = 5 * 60_000;
    /** Up this long without trouble: the retry history is forgotten. */
    static final long STABLE_MS = 10 * 60_000;
    /**
     * ADR-134: Android refused a background service start. The intent stays; the next allowed trigger
     * (network, app open, boot or the alarm) tries again, but not more often than this.
     */
    static final String BACKGROUND_REFUSED = "background_start_refused";
    static final long REFUSED_RETRY_MS = 60_000;
    /** ADR-140: a returning network retries at once, but a flapping one not more often than this. */
    static final long NETWORK_RETURN_GAP_MS = 10_000;
    /**
     * ADR-139 reason code, kept so a state reported by an older build is still named. ADR-140: no
     * attempt budget ends a wanted connection any more, so nothing here produces it.
     */
    static final String EXHAUSTED = "recovery_exhausted";

    enum Action { NONE, START_RECOVERY, WAIT }

    /** One decision; {@link #delayMs} is set for {@link Action#WAIT}. */
    static final class Decision {
        final Action action;
        final long delayMs;
        final String why;
        Decision(Action action, long delayMs, String why) {
            this.action = action; this.delayMs = delayMs; this.why = why;
        }
    }

    private boolean desired;
    private long intentGeneration;
    private int attempts;
    private long lastAttemptAt = Long.MIN_VALUE / 2;
    private long upSince = -1;
    private String lastStop = "";
    private double nextJitter = 0.5;
    /** ADR-140: recovery was held for a missing network; its return skips the backoff once. */
    private boolean heldForNetwork;

    boolean desired() { return desired; }
    long intentGeneration() { return intentGeneration; }
    int attempts() { return attempts; }
    String lastStop() { return lastStop; }

    /** ADR-134: the stop category the UI may show (Russian text lives in Flutter). */
    String stopCategory() { return category(lastStop); }

    /** Persisted intent read at process start (never treated as a fresh user action). */
    void restore(boolean persistedDesired) { desired = persistedDesired; }

    /** The user connected (a tunnel came up from the UI). */
    long userConnected() {
        desired = true;
        attempts = 0;
        lastStop = "";
        return ++intentGeneration;
    }

    /** Explicit user disconnect: cancels every pending recovery for good. */
    long userDisconnected() {
        desired = false;
        attempts = 0;
        upSince = -1;
        lastStop = "user_stop";
        return ++intentGeneration;
    }

    void tunnelUp(long now) {
        attemptOpen = false;
        if (upSince < 0) upSince = now;
    }

    /**
     * ADR-142: who owns reconnecting. A Dart controller owns it only while it can actually run:
     * the UI engine while the Activity is started (visible), or a headless restore engine. A UI
     * engine whose Activity is merely stopped (screen off, Home) sleeps in Doze, so native owns
     * recovery then; the UI reconciles and adopts when it is started again (ADR-121).
     */
    static boolean dartOwnsRecovery(boolean uiForeground, boolean restoreEngineAlive) {
        return uiForeground || restoreEngineAlive;
    }

    /**
     * ADR-144: a headless restore engine owns recovery only while it can still act: while it is
     * bringing its first tunnel up (bounded by {@link #RESTORE_BOOTSTRAP_MS}) or while its tunnel
     * is up. Once its tunnel is gone its Dart timers sleep in Doze, so it is ended and native owns
     * recovery again (before, an engine that lost its tunnel blocked native recovery forever).
     */
    static boolean restoreEngineOwnsRecovery(boolean engineAlive, boolean bootstrapping,
            boolean tunnelUp) {
        return engineAlive && (bootstrapping || tunnelUp);
    }

    /** ADR-144: a restore engine that has not reported its first result by then has failed. */
    static final long RESTORE_BOOTSTRAP_MS = 180_000;

    /**
     * ADR-144: after process start, native waits this long for the UI to say whether it is in the
     * foreground before it starts recovery itself, so a cold app start and native recovery never
     * race for the same connection. The first owner signal ends the wait early.
     */
    static final long STARTUP_SETTLE_MS = 3_000;

    /** ADR-144: whether native may already decide recovery at {@code now}. */
    static boolean startupSettled(boolean ownerSignalSeen, long processStart, long now) {
        return ownerSignalSeen || now - processStart >= STARTUP_SETTLE_MS;
    }

    /** ADR-155: a failed native init (vault, device key, engine) is tried again at most this often. */
    static final long NATIVE_INIT_RETRY_MS = 2_000;

    /**
     * ADR-155: whether a failed native init may run again at {@code now}. {@code failedAt} is the
     * elapsed time of the last failure, or -1 when the init has not failed (or not run yet: the
     * onCreate task always runs first on the same worker).
     */
    static boolean nativeInitRetryDue(long failedAt, long now) {
        return failedAt >= 0 && now - failedAt >= NATIVE_INIT_RETRY_MS;
    }

    /**
     * ADR-142: the tunnel stopped at {@code now}. A tunnel that had been up for {@link #STABLE_MS}
     * starts a fresh streak, so the first recovery runs at once instead of waiting for an old
     * backoff (and for another Doze-deferred alarm).
     */
    boolean tunnelDown(String reason, long now) {
        if (upSince >= 0 && now - upSince >= STABLE_MS) {
            attempts = 0;
            lastAttemptAt = Long.MIN_VALUE / 2;
        }
        return tunnelDown(reason);
    }

    /**
     * The tunnel stopped. Returns true if this ended the user's intent (terminal reason): the
     * caller then persists {@code desired = false}.
     */
    boolean tunnelDown(String reason) {
        upSince = -1;
        attemptOpen = false;
        lastStop = reason == null ? "" : reason;
        if (desired && terminal(lastStop)) {
            desired = false;
            intentGeneration++;
            return true;
        }
        return false;
    }

    /** A restore attempt ended without a tunnel. Same terminal rule as {@link #tunnelDown}. */
    boolean recoveryFailed(String code) { return tunnelDown(code == null ? "recovery_failed" : code); }

    /** ADR-143: at most one attempt started by fresh signals per this gap. */
    static final long FRESH_GAP_MS = 30_000;
    /** ADR-143: a wake signal is evaluated this long after it arrives (an unlock may open the UI). */
    static final long WAKE_SETTLE_MS = 2_000;
    /** ADR-143: an attempt that has not reported back is treated as in flight this long. */
    static final long ATTEMPT_IN_FLIGHT_MS = 60_000;
    private long lastFreshAt = Long.MIN_VALUE / 2;
    private boolean attemptOpen;

    /** ADR-143: a recovery attempt was started and has not reported back yet. */
    boolean attemptInFlight(long now) {
        return attemptOpen && now - lastAttemptAt < ATTEMPT_IN_FLIGHT_MS;
    }

    /**
     * ADR-143: the device woke (Doze exit, screen on, unlock) or the default network changed or
     * became usable. When native owns recovery (ADR-142) and nothing is running or in flight, the
     * backoff is cleared so the next {@link #evaluate} starts one attempt at once. Rate-limited to
     * one per {@link #FRESH_GAP_MS}; a signal inside the gap returns WAIT with the time left, so the
     * caller can re-signal then (coalesced). Returns NONE with the reason when it does nothing.
     */
    Decision freshSignal(long now, boolean tunnelUp, boolean dartOwner, boolean networkUsable) {
        if (tunnelUp) return new Decision(Action.NONE, 0, "up");
        if (!desired) return new Decision(Action.NONE, 0, "not_desired");
        if (terminal(lastStop)) return new Decision(Action.NONE, 0, "terminal");
        if (dartOwner) return new Decision(Action.NONE, 0, "owner");
        if (!networkUsable) return new Decision(Action.NONE, 0, "offline");
        if (attemptInFlight(now)) return new Decision(Action.NONE, 0, "in_flight");
        long gap = lastFreshAt + FRESH_GAP_MS - now;
        if (gap > 0) return new Decision(Action.WAIT, gap, "gap");
        lastFreshAt = now;
        attempts = 0;
        lastAttemptAt = Long.MIN_VALUE / 2; // also clears a held background_start_refused wait
        heldForNetwork = false;
        return new Decision(Action.START_RECOVERY, 0, "fresh");
    }

    /**
     * ADR-143: whether a broadcast is a wake signal. Screen on and unlock always are; an idle-mode
     * change is one only when the device has left that idle mode (entering idle is not).
     */
    static boolean wakeSignal(boolean screenOn, boolean userPresent, boolean deepIdleChanged,
            boolean deepIdleNow, boolean lightIdleChanged, boolean lightIdleNow) {
        if (screenOn || userPresent) return true;
        if (deepIdleChanged && !deepIdleNow) return true;
        return lightIdleChanged && !lightIdleNow;
    }

    void attemptStarted(long now, double jitterForNext) {
        attemptOpen = true;
        attempts = Math.min(attempts + 1, 16);
        lastAttemptAt = now;
        nextJitter = Math.max(0, Math.min(jitterForNext, 0.999));
        if (BACKGROUND_REFUSED.equals(lastStop)) lastStop = "";
    }

    /**
     * ADR-134: the background start was refused before anything ran. Not a failed recovery, so the
     * backoff does not grow; the intent is kept and the stop category says why nothing is running.
     */
    void startRefused(long now) {
        attemptOpen = false;
        attempts = Math.max(0, attempts - 1);
        lastAttemptAt = now;
        lastStop = BACKGROUND_REFUSED;
    }

    /** Delay after {@link #attempts} failed attempts: 30 s, 1, 2, 4, 5 min, with ±25 % jitter. */
    long backoffMs() {
        if (attempts == 0) return 0;
        long base = Math.min(MAX_DELAY_MS, BASE_DELAY_MS << Math.min(attempts - 1, 10));
        return (long) (base * (0.75 + nextJitter / 2));
    }

    /**
     * What native should do now. Inputs are facts read on the worker thread.
     *
     * @param dartOwner a Dart controller (UI engine or restore engine) is attached and owns
     *     reconnects; native must not start a competing one.
     */
    Decision evaluate(long now, boolean tunnelUp, boolean connecting, boolean dartOwner,
            boolean networkUsable) {
        if (tunnelUp) {
            heldForNetwork = false;
            tunnelUp(now);
            if (now - upSince >= STABLE_MS) attempts = 0;
            return new Decision(Action.NONE, 0, "up");
        }
        if (!desired) return new Decision(Action.NONE, 0, "not_desired");
        if (connecting) return new Decision(Action.NONE, 0, "connecting");
        if (dartOwner) return new Decision(Action.NONE, 0, "dart_owner");
        if (terminal(lastStop)) return new Decision(Action.NONE, 0, "terminal");
        // ADR-140: no attempt budget. Without a network nothing is attempted or counted; the
        // network callback re-evaluates, and its return retries at once instead of after backoff.
        if (!networkUsable) {
            heldForNetwork = true;
            return new Decision(Action.NONE, 0, "waiting_for_network");
        }
        if (heldForNetwork && !BACKGROUND_REFUSED.equals(lastStop)) {
            long flap = lastAttemptAt + NETWORK_RETURN_GAP_MS - now;
            if (flap > 0) return new Decision(Action.WAIT, flap, "network_returned");
            heldForNetwork = false;
            // ADR-146: a returning network is a new path; failures before it say nothing about
            // it, so the backoff starts over (the 10 s flap gap above still bounds the pace).
            attempts = 0;
            return new Decision(Action.START_RECOVERY, 0, "network_returned");
        }
        heldForNetwork = false;
        long gap = BACKGROUND_REFUSED.equals(lastStop) ? REFUSED_RETRY_MS : backoffMs();
        long wait = lastAttemptAt + gap - now;
        if (wait > 0) return new Decision(Action.WAIT, wait, "backoff");
        return new Decision(Action.START_RECOVERY, 0, "recover");
    }

    /**
     * Stop reasons after which reconnecting cannot help without the user: lost VPN consent,
     * rejected credentials, exhausted quota, an invalid profile or device, or a user stop.
     * ADR-140: a legacy {@link #EXHAUSTED} stays terminal so an older build's end state is kept.
     */
    static boolean terminal(String reason) {
        switch (category(reason)) {
            case "permission_revoked":
            case "auth_rejected":
            case "quota_exhausted":
            case "invalid_config":
            case "user_stop":
            case EXHAUSTED:
                return true;
            default:
                return false;
        }
    }

    /** Stable failure category for diagnostics and the policy above. Unknown = transient. */
    static String category(String reason) {
        if (reason == null || reason.isEmpty()) return "none";
        switch (reason) {
            case "user_stop":
                return "user_stop";
            case "vpn_revoked":
            case "permission_denied":
            case "native_permission_error":
                return "permission_revoked";
            case "unauthorized":
            case "session_expired":
            case "session_missing":
            case "session_device_mismatch":
            case "device_conflict":
            case "device_revoked":
                return "auth_rejected";
            case "quota_exhausted":
            case "insufficient_quota":
            case "quota_insufficient":
            case "funding_exhausted":
                return "quota_exhausted";
            case "native_config_invalid":
            case "invalid_origin":
            case "window_node_required":
                return "invalid_config";
            case "lease_expired":
            case "window_not_active":
            case "window_not_renewable":
                return "window_expired";
            case "native_restart_failed":
            case "native_start_failed":
            case "native_engine_unavailable":
            case "native_unavailable":
                return "native_core_failure";
            case "native_io_error":
            case "native_service_state_invalid":
            case "service_died": // ADR-144: the VPN service stopped without a revoke or a stop.
                return "tun_failure";
            case "dead_peer": // ADR-148: the probe ladder asked for a new lease (or failover).
            case "dead_peer_failover":
                return "dead_peer";
            case "network_unavailable":
                return "network_unavailable";
            case BACKGROUND_REFUSED:
                return BACKGROUND_REFUSED;
            case EXHAUSTED:
                return EXHAUSTED;
            case "api_unavailable":
            case "window_node_unavailable":
            case "window_activation_refused":
                return "server_unreachable";
            default:
                return "transient";
        }
    }

    /**
     * Transport state shown to Flutter, from facts only.
     *
     * @param handshakeFresh a peer handshake newer than the last network change and not older than
     *     about three keepalive/rekey periods (the WireGuard peer answered on this path).
     */
    static String transport(boolean desired, boolean up, boolean connecting,
            boolean networkUsable, boolean handshakeFresh, boolean restartPending) {
        return transport(desired, up, connecting, networkUsable, handshakeFresh, restartPending, false);
    }

    /**
     * ADR-133: {@code dataStalled} (outbound bytes without inbound progress, see
     * {@link DataPathWatch}) lowers an otherwise connected tunnel to "degraded". It is a display
     * state only; nothing in the recovery policy reads it.
     */
    static String transport(boolean desired, boolean up, boolean connecting,
            boolean networkUsable, boolean handshakeFresh, boolean restartPending, boolean dataStalled) {
        if (up) {
            if (!networkUsable) return "waiting_for_network";
            if (restartPending) return "reconnecting";
            return handshakeFresh && !dataStalled ? "connected" : "degraded";
        }
        if (connecting) return "connecting";
        if (!desired) return "disconnected";
        return networkUsable ? "reconnecting" : "waiting_for_network";
    }

    /**
     * ADR-128 evidence label. Strongest first; never claims end-to-end reachability, which the
     * engine cannot measure.
     */
    static String evidence(boolean up, long handshakeAgeSeconds, boolean handshakeAfterChange,
            boolean inboundRecent) {
        if (!up) return "none";
        if (handshakeAgeSeconds >= 0 && handshakeAgeSeconds <= 180 && handshakeAfterChange)
            return inboundRecent ? "peer_and_inbound_data" : "peer_handshake";
        return inboundRecent ? "inbound_data" : "core_running";
    }
}
