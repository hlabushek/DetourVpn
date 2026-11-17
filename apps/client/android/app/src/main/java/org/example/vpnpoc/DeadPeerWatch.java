package org.example.vpnpoc;

/**
 * ADR-148 (Phase 5): dead-peer and silent-drop detection. Pure (no Android types, no clock, no
 * I/O), so every rule is unit-tested on the JVM. Touched only on the application worker thread.
 *
 * <ul>
 *   <li>5.1: owns the {@link DataPathWatch}. It is fed only by {@link #tick}, which the native
 *       watch tick calls; {@link #flowing} and {@link #stalled} only read it (a snapshot no longer
 *       adds samples).</li>
 *   <li>5.2: a stall is suspected when outbound bytes got no inbound answer for
 *       {@link DataPathWatch#SUSPECT_MS} (60 s), or when there was no handshake for
 *       {@link #STALE_HANDSHAKE_MS} (180 s). Then {@link Action#PROBE}: the caller sends one small
 *       request through the tunnel (no user data, {@link #PROBE_TIMEOUT_MS}) and reports it with
 *       {@link #probeResult}. A pass means the tunnel is healthy and resets the ladder. Two
 *       failures in a row escalate one step: {@link Action#RESTART} (in place), then
 *       {@link Action#NEW_LEASE}, then {@link Action#FAILOVER} only when the owner opted in; after
 *       that the ladder is exhausted.</li>
 *   <li>A probe that cannot run at all ({@link ProbeResult#UNAVAILABLE}: no probe URL configured,
 *       no VPN network, the socket could not be bound to it) is not a failure; it never
 *       escalates.</li>
 *   <li>While the probe owns stall recovery ({@link #ownsStallRecovery}), the watchdog's own
 *       stale-handshake restarts are off; when the probe is disabled, unavailable or exhausted,
 *       the watchdog behaves as before (ADR-099/146).</li>
 *   <li>No probe runs without a usable network (offline or behind a captive portal).</li>
 * </ul>
 */
final class DeadPeerWatch {
    /** The same bound as {@link TunnelWatchdog#STALE_MS}: no handshake for this long is a stall. */
    static final long STALE_HANDSHAKE_MS = TunnelWatchdog.STALE_MS;
    /** One probe may take at most this long (connect, TLS and response). */
    static final long PROBE_TIMEOUT_MS = 5_000;
    /** The second probe follows a failed first one after this. */
    static final long PROBE_RETRY_MS = 5_000;
    /** Failed probes in a row that escalate one step. */
    static final int FAILS_TO_ESCALATE = 2;
    /** After an escalation step, the new tunnel gets this long before it is probed again. */
    static final long STEP_SETTLE_MS = 30_000;
    /** A passed probe proves the path for this long; no probe runs meanwhile. */
    static final long PASS_HOLD_MS = 120_000;
    /** Probe period once the ladder is exhausted, and the pause after an unavailable probe. */
    static final long QUIET_PROBE_MS = 600_000;
    /** A result that never arrives (lost callback) counts as a failure after this. */
    static final long LOST_RESULT_MS = 3 * PROBE_TIMEOUT_MS;

    enum Action { NONE, PROBE, RESTART, NEW_LEASE, FAILOVER }

    enum ProbeResult { PASS, FAIL, UNAVAILABLE }

    /** Ladder positions: the last step taken. */
    static final int STEP_NONE = 0;
    static final int STEP_RESTARTED = 1;
    static final int STEP_NEW_LEASE = 2;
    static final int STEP_FAILED_OVER = 3;
    static final int STEP_EXHAUSTED = 4;

    private static final long NEVER = Long.MIN_VALUE / 2;

    private final boolean probeEnabled;
    private final boolean failoverOptIn;
    private final DataPathWatch dataPath = new DataPathWatch();
    private int step = STEP_NONE;
    private int fails;
    private boolean inFlight;
    /** Identifies the probe in flight; a result for an older one is ignored. */
    private long probeId;
    private long probeStartedAt = NEVER;
    private long lastProbeAt = NEVER;
    private long lastPassAt = NEVER;
    private long lastEscalationAt = NEVER;
    private long unavailableUntil = NEVER;

    /**
     * @param probeEnabled a valid project-owned health URL is configured ({@link DeadPeerProbe})
     * @param failoverOptIn the owner's opt-in for node failover (default off)
     */
    DeadPeerWatch(boolean probeEnabled, boolean failoverOptIn) {
        this.probeEnabled = probeEnabled;
        this.failoverOptIn = failoverOptIn;
    }

    boolean probeEnabled() { return probeEnabled; }

    boolean failoverOptIn() { return failoverOptIn; }

    int step() { return step; }

    /** A probe was asked for and its result is not in yet. */
    boolean probeInFlight() { return inFlight; }

    /** The id of the probe last asked for (pass it back to {@link #probeResult}). */
    long probeId() { return probeId; }

    /** A new window's tunnel is about to start: forget its data-path evidence, keep the ladder. */
    void windowStarted() {
        dataPath.reset();
        dropProbe();
    }

    /** The user disconnected, or the path changed: start over (ladder included). */
    void reset() {
        dataPath.reset();
        dropProbe();
        step = STEP_NONE;
        lastProbeAt = NEVER;
        lastPassAt = NEVER;
        lastEscalationAt = NEVER;
        unavailableUntil = NEVER;
    }

    /** ADR-146: a new network path; earlier probe failures say nothing about it. */
    void networkChanged() {
        dropProbe();
        step = STEP_NONE;
        lastEscalationAt = NEVER;
        unavailableUntil = NEVER;
    }

    private void dropProbe() {
        inFlight = false;
        fails = 0;
        probeId++;
    }

    /** Read only: inbound bytes progressed recently. */
    boolean flowing(long now) { return dataPath.flowing(now); }

    /** Read only: outbound bytes got no inbound answer for {@link DataPathWatch#FLOW_MS}. */
    boolean stalled(long now) { return dataPath.stalled(now); }

    /**
     * Whether the watchdog's stale-handshake restarts are handed to this ladder right now. False
     * when no probe is configured, after an unavailable probe (for {@link #QUIET_PROBE_MS}) and
     * once the ladder is exhausted.
     */
    boolean ownsStallRecovery(long now) {
        return probeEnabled && step < STEP_EXHAUSTED && now >= unavailableUntil;
    }

    /** A stall is suspected (see the class comment). Read only. */
    boolean suspect(long now, long handshakeAgeMs, long windowAgeMs) {
        if (dataPath.suspect(now)) return true;
        return handshakeAgeMs >= 0 ? handshakeAgeMs >= STALE_HANDSHAKE_MS
                : windowAgeMs >= STALE_HANDSHAKE_MS;
    }

    /**
     * One native watch tick while the tunnel is up. Feeds the data path (the only place that
     * does), then decides whether to probe now.
     *
     * @param received inbound tunnel bytes, or -1 if unknown
     * @param transfer inbound plus outbound tunnel bytes, or -1 if unknown
     * @param handshakeAgeMs age of the last handshake, or -1 if none in this window
     * @param windowAgeMs time since this window's tunnel came up, or -1 if unknown
     * @param networkUsable a default network with INTERNET and no captive portal
     */
    Action tick(long now, long received, long transfer, long handshakeAgeMs, long windowAgeMs,
            boolean networkUsable) {
        dataPath.observe(received, transfer, now);
        if (!probeEnabled) return Action.NONE;
        if (inFlight) {
            if (now - probeStartedAt < LOST_RESULT_MS) return Action.NONE;
            return probeResult(now, probeId, ProbeResult.FAIL); // the result never came
        }
        if (!networkUsable) return Action.NONE;
        if (!suspect(now, handshakeAgeMs, windowAgeMs)) return Action.NONE;
        if (now - lastPassAt < PASS_HOLD_MS) return Action.NONE;
        if (now < unavailableUntil) return Action.NONE;
        if (now - lastEscalationAt < STEP_SETTLE_MS) return Action.NONE;
        long gap = step >= STEP_EXHAUSTED ? QUIET_PROBE_MS : fails > 0 ? PROBE_RETRY_MS : 0;
        if (now - lastProbeAt < gap) return Action.NONE;
        inFlight = true;
        probeId++;
        probeStartedAt = now;
        lastProbeAt = now;
        return Action.PROBE;
    }

    /**
     * The result of probe {@code id}. Returns the escalation step to take now, or
     * {@link Action#NONE}. A result for an older probe (another window, a network change) is
     * ignored.
     */
    Action probeResult(long now, long id, ProbeResult result) {
        if (!inFlight || id != probeId) return Action.NONE;
        inFlight = false;
        switch (result) {
            case PASS:
                fails = 0;
                step = STEP_NONE;
                lastPassAt = now;
                return Action.NONE;
            case UNAVAILABLE:
                fails = 0;
                unavailableUntil = now + QUIET_PROBE_MS;
                return Action.NONE;
            default:
                break;
        }
        if (step >= STEP_EXHAUSTED) return Action.NONE; // the watchdog has it again
        if (++fails < FAILS_TO_ESCALATE) return Action.NONE;
        fails = 0;
        lastEscalationAt = now;
        switch (step) {
            case STEP_NONE:
                step = STEP_RESTARTED;
                return Action.RESTART;
            case STEP_RESTARTED:
                step = STEP_NEW_LEASE;
                return Action.NEW_LEASE;
            case STEP_NEW_LEASE:
                if (failoverOptIn) {
                    step = STEP_FAILED_OVER;
                    return Action.FAILOVER;
                }
                step = STEP_EXHAUSTED;
                return Action.NONE;
            default:
                step = STEP_EXHAUSTED;
                return Action.NONE;
        }
    }
}
