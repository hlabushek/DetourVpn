package org.example.vpnpoc;

/**
 * Pure ADR-099 policy: when to recreate a running tunnel in place. No I/O. Inputs are
 * elapsed-realtime milliseconds and the handshake age (-1 when no handshake happened yet).
 *
 * <ul>
 *   <li>After the default network changed, restart once the change settled, unless a handshake
 *       already happened on the new network.</li>
 *   <li>With a validated network but no handshake for {@link #STALE_MS}, restart, backing off
 *       from 2 to 10 minutes while it keeps failing. A fresh handshake resets the backoff.</li>
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

    private long upSince = -1;
    private long lastRestart = Long.MIN_VALUE / 2;
    private long networkChangedAt = -1;
    private int attempts;

    void tunnelUp(long now) {
        upSince = now;
        attempts = 0;
        networkChangedAt = -1;
    }

    void tunnelDown() {
        upSince = -1;
        networkChangedAt = -1;
    }

    boolean running() { return upSince >= 0; }

    void networkChanged(long now) {
        if (upSince >= 0) networkChangedAt = now;
    }

    long staleGapMs() {
        return Math.min(MAX_GAP_MS, STALE_GAP_MS << Math.min(attempts, 3));
    }

    /** Returns true if the caller should restart now; the caller then reports {@link #restarted}. */
    boolean shouldRestart(long now, long handshakeAgeMs, boolean validatedNetwork) {
        if (upSince < 0 || !validatedNetwork) return false;
        if (handshakeAgeMs >= 0 && handshakeAgeMs <= FRESH_MS) attempts = 0;
        if (networkChangedAt >= 0 && now - networkChangedAt >= SETTLE_MS) {
            long sinceChange = now - networkChangedAt;
            networkChangedAt = -1;
            boolean handshakeAfterChange = handshakeAgeMs >= 0 && handshakeAgeMs < sinceChange;
            if (!handshakeAfterChange && now - lastRestart >= NETWORK_GAP_MS) return true;
        }
        boolean stale = handshakeAgeMs < 0 ? now - upSince >= STALE_MS : handshakeAgeMs >= STALE_MS;
        return stale && now - upSince >= STALE_MS && now - lastRestart >= staleGapMs();
    }

    void restarted(long now) {
        lastRestart = now;
        attempts = Math.min(attempts + 1, 8);
    }
}
