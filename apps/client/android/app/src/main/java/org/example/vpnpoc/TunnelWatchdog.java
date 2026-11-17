package org.example.vpnpoc;

/**
 * Pure ADR-099/ADR-122/ADR-146 policy: when to recreate a running tunnel in place, and when to
 * give up on it and ask for a new lease. No I/O. Inputs are elapsed-realtime milliseconds and the
 * handshake age (-1 when no handshake happened yet).
 *
 * <ul>
 *   <li>ADR-146: after the default network changed, restart once the new network held for
 *       {@link #SETTLE_MS} (0.5 s), unless a handshake already happened on it. A change that
 *       cannot act yet (too soon after the last restart, or no usable network) stays pending
 *       instead of being dropped (ADR-122). A change reported before the first watch tick counts.</li>
 *   <li>ADR-146: a handshake newer than the change (or the restart it caused) is the proof of
 *       recovery. Without it, at most {@link #NETWORK_RESTARTS} restarts run per change; if the
 *       last one also gets no handshake, {@link Action#NEW_LEASE} asks the caller to stop the
 *       tunnel so recovery starts a new window.</li>
 *   <li>With a usable network but no handshake for {@link #STALE_MS}, restart, backing off
 *       from 2 to 10 minutes while it keeps failing. Only these stall restarts raise the backoff;
 *       a fresh handshake or a network change resets it.</li>
 * </ul>
 */
final class TunnelWatchdog {
    /** Keepalive 25 s plus WireGuard rekey every 120 s: 180 s without a handshake is a stall. */
    static final long STALE_MS = 180_000;
    static final long FRESH_MS = 60_000;
    /** ADR-146: a new usable network is acted on after this (was 3 s). */
    static final long SETTLE_MS = 500;
    static final long NETWORK_GAP_MS = 15_000;
    static final long STALE_GAP_MS = 120_000;
    static final long MAX_GAP_MS = 600_000;
    /** ADR-146: restarts per network change without a handshake, then a new lease. */
    static final int NETWORK_RESTARTS = 2;

    enum Action { NONE, RESTART, NEW_LEASE }

    private long upSince = -1;
    private long lastRestart = Long.MIN_VALUE / 2;
    /** Pending network trigger: the change (or the restart it caused) to verify; -1 when none. */
    private long networkChangedAt = -1;
    private int networkRestarts;
    private boolean lastWasNetwork;
    private int attempts;

    void tunnelUp(long now) {
        upSince = now;
        attempts = 0;
        networkChangedAt = -1;
        networkRestarts = 0;
    }

    void tunnelDown() {
        upSince = -1;
        networkChangedAt = -1;
        networkRestarts = 0;
    }

    boolean running() { return upSince >= 0; }

    /**
     * The default network changed. {@code tunnelUpSince} is when the running tunnel came up, or
     * -1 when no tunnel runs. ADR-146: a change before the first watch tick is no longer dropped;
     * it starts tracking the tunnel. A change also resets the stall backoff (a new path).
     */
    void networkChanged(long now, long tunnelUpSince) {
        if (tunnelUpSince < 0) return;
        if (upSince < 0) upSince = Math.min(tunnelUpSince, now);
        networkChangedAt = now;
        networkRestarts = 0;
        attempts = 0;
    }

    long staleGapMs() {
        return Math.min(MAX_GAP_MS, STALE_GAP_MS << Math.min(attempts, 3));
    }

    /** Milliseconds until a pending network trigger may act, or -1 when none is pending. */
    long pendingNetworkDelayMs(long now) {
        if (upSince < 0 || networkChangedAt < 0) return -1;
        long settle = networkChangedAt + SETTLE_MS - now;
        long gap = lastRestart + NETWORK_GAP_MS - now;
        return Math.max(0, Math.max(settle, gap));
    }

    /** Returns true if the caller should restart now (see {@link #decide}). */
    boolean shouldRestart(long now, long handshakeAgeMs, boolean usableNetwork) {
        return decide(now, handshakeAgeMs, usableNetwork) == Action.RESTART;
    }

    /**
     * What the caller should do now. After {@link Action#RESTART} it reports {@link #restarted};
     * after {@link Action#NEW_LEASE} it stops the tunnel and reports {@link #tunnelDown}.
     */
    Action decide(long now, long handshakeAgeMs, boolean usableNetwork) {
        return decide(now, handshakeAgeMs, usableNetwork, true);
    }

    /**
     * ADR-148: as {@link #decide(long, long, boolean)}; with {@code staleRestarts} false the
     * stale-handshake restart is left to the active probe ({@link DeadPeerWatch}). Network-change
     * restarts and the new-lease step are unchanged.
     */
    Action decide(long now, long handshakeAgeMs, boolean usableNetwork, boolean staleRestarts) {
        if (upSince < 0 || !usableNetwork) return Action.NONE; // a pending change waits
        if (handshakeAgeMs >= 0 && handshakeAgeMs <= FRESH_MS) attempts = 0;
        if (networkChangedAt >= 0 && now - networkChangedAt >= SETTLE_MS) {
            long sinceChange = now - networkChangedAt;
            boolean handshakeAfterChange = handshakeAgeMs >= 0 && handshakeAgeMs < sinceChange;
            if (handshakeAfterChange) {
                networkChangedAt = -1;
                networkRestarts = 0;
            } else if (now - lastRestart >= NETWORK_GAP_MS) {
                if (networkRestarts >= NETWORK_RESTARTS) {
                    networkChangedAt = -1;
                    networkRestarts = 0;
                    return Action.NEW_LEASE;
                }
                lastWasNetwork = true;
                return Action.RESTART;
            }
            // Otherwise keep it pending until the gap since the last restart has passed.
        }
        boolean stale = handshakeAgeMs < 0 ? now - upSince >= STALE_MS : handshakeAgeMs >= STALE_MS;
        if (staleRestarts && stale && now - upSince >= STALE_MS
                && now - lastRestart >= staleGapMs()) {
            lastWasNetwork = false;
            return Action.RESTART;
        }
        return Action.NONE;
    }

    /**
     * ADR-148: the tunnel was restarted in place by the probe ladder. It counts for the gap
     * before a network-change restart, not for the stale backoff.
     */
    void restartedByProbe(long now) {
        lastRestart = now;
        lastWasNetwork = false;
    }

    void restarted(long now) {
        lastRestart = now;
        if (lastWasNetwork) {
            // Verify this restart: if no handshake follows it, restart again or ask for a lease.
            networkRestarts++;
            networkChangedAt = now;
        } else {
            attempts = Math.min(attempts + 1, 8);
        }
        lastWasNetwork = false;
    }
}
