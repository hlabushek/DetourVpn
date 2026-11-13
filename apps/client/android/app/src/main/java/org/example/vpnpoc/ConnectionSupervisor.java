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
    static final long MAX_DELAY_MS = 30 * 60_000;
    /** Up this long without trouble: the retry history is forgotten. */
    static final long STABLE_MS = 10 * 60_000;

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

    boolean desired() { return desired; }
    long intentGeneration() { return intentGeneration; }
    int attempts() { return attempts; }
    String lastStop() { return lastStop; }

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
        lastStop = "user_stop";
        return ++intentGeneration;
    }

    void tunnelUp(long now) {
        if (upSince < 0) upSince = now;
    }

    /**
     * The tunnel stopped. Returns true if this ended the user's intent (terminal reason): the
     * caller then persists {@code desired = false}.
     */
    boolean tunnelDown(String reason) {
        upSince = -1;
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

    void attemptStarted(long now, double jitterForNext) {
        attempts = Math.min(attempts + 1, 16);
        lastAttemptAt = now;
        nextJitter = Math.max(0, Math.min(jitterForNext, 0.999));
    }

    /** Delay after {@link #attempts} failed attempts: 30 s, 1, 2, 4 ... 30 min, with ±25 % jitter. */
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
            tunnelUp(now);
            if (now - upSince >= STABLE_MS) attempts = 0;
            return new Decision(Action.NONE, 0, "up");
        }
        if (!desired) return new Decision(Action.NONE, 0, "not_desired");
        if (connecting) return new Decision(Action.NONE, 0, "connecting");
        if (dartOwner) return new Decision(Action.NONE, 0, "dart_owner");
        if (terminal(lastStop)) return new Decision(Action.NONE, 0, "terminal");
        if (!networkUsable) return new Decision(Action.NONE, 0, "waiting_for_network");
        long wait = lastAttemptAt + backoffMs() - now;
        if (wait > 0) return new Decision(Action.WAIT, wait, "backoff");
        return new Decision(Action.START_RECOVERY, 0, "recover");
    }

    /**
     * Stop reasons after which reconnecting cannot help without the user: lost VPN consent,
     * rejected credentials, exhausted quota, an invalid profile or device, a user stop.
     */
    static boolean terminal(String reason) {
        switch (category(reason)) {
            case "permission_revoked":
            case "auth_rejected":
            case "quota_exhausted":
            case "invalid_config":
            case "user_stop":
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
                return "tun_failure";
            case "network_unavailable":
                return "network_unavailable";
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
        if (up) {
            if (!networkUsable) return "waiting_for_network";
            if (restartPending) return "reconnecting";
            return handshakeFresh ? "connected" : "degraded";
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
