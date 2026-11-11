package org.example.vpnpoc;

/**
 * Pure ADR-099/ADR-122 policy: when to recreate a running tunnel in place. No I/O. Inputs are
 * elapsed-realtime milliseconds and the handshake age (-1 when no handshake happened yet).
 *
 * <ul>
 *   <li>After the default network changed, restart once the change settled, unless a handshake
 *       already happened on the new network. A change that cannot act yet (too soon after the last
 *       restart, or no validated network) stays pending instead of being dropped (ADR-122).</li>
 *   <li>If no handshake follows a network-change restart, try again, at most
 *       {@link #NETWORK_RETRIES} more times per change.</li>
 *   <li>With a validated network but no handshake for {@link #STALE_MS}, restart, backing off
 *       from 2 to 10 minutes while it keeps failing. Only these stall restarts raise the backoff;
 *       a fresh handshake resets it.</li>
 * </ul>
 */
final class TunnelWatchdog {
    /** Keepalive 25 s plus WireGuard rekey every 120 s: 180 s without a handshake is a stall. */
    static final long STALE_MS = 180_000;
    static final long FRESH_MS = 60_000;
    static final long SETTLE_MS = 3_000;
    static final long NETWORK_GAP_MS = 15_000;
    static final long STALE_GAP_MS = 120_000;
    static final long MAX_GAP_MS = 600_000;
    /** Extra restarts after a network-change restart that got no handshake (ADR-122). */
    static final int NETWORK_RETRIES = 2;

    private long upSince = -1;
    private long lastRestart = Long.MIN_VALUE / 2;
    /** Pending network trigger: the change (or the restart it caused) to verify; -1 when none. */
    private long networkChangedAt = -1;
    private int networkRetries;
    private boolean lastWasNetwork;
    private int attempts;

    void tunnelUp(long now) {
        upSince = now;
        attempts = 0;
        networkChangedAt = -1;
        networkRetries = 0;
    }

    void tunnelDown() {
        upSince = -1;
        networkChangedAt = -1;
        networkRetries = 0;
    }

    boolean running() { return upSince >= 0; }

    void networkChanged(long now) {
        if (upSince < 0) return;
        networkChangedAt = now;
        networkRetries = 0;
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

    /** Returns true if the caller should restart now; the caller then reports {@link #restarted}. */
    boolean shouldRestart(long now, long handshakeAgeMs, boolean validatedNetwork) {
        if (upSince < 0 || !validatedNetwork) return false; // a pending change waits
        if (handshakeAgeMs >= 0 && handshakeAgeMs <= FRESH_MS) attempts = 0;
        if (networkChangedAt >= 0 && now - networkChangedAt >= SETTLE_MS) {
            long sinceChange = now - networkChangedAt;
            boolean handshakeAfterChange = handshakeAgeMs >= 0 && handshakeAgeMs < sinceChange;
            if (handshakeAfterChange) {
                networkChangedAt = -1;
                networkRetries = 0;
            } else if (now - lastRestart >= NETWORK_GAP_MS) {
                lastWasNetwork = true;
                return true;
            }
            // Otherwise keep it pending until the gap since the last restart has passed.
        }
        boolean stale = handshakeAgeMs < 0 ? now - upSince >= STALE_MS : handshakeAgeMs >= STALE_MS;
        if (stale && now - upSince >= STALE_MS && now - lastRestart >= staleGapMs()) {
            lastWasNetwork = false;
            return true;
        }
        return false;
    }

    void restarted(long now) {
        lastRestart = now;
        if (lastWasNetwork) {
            // Verify this restart: if no handshake follows it, the same rule restarts again.
            if (networkRetries < NETWORK_RETRIES) {
                networkRetries++;
                networkChangedAt = now;
            } else {
                networkChangedAt = -1;
                networkRetries = 0;
            }
        } else {
            attempts = Math.min(attempts + 1, 8);
        }
        lastWasNetwork = false;
    }
}
