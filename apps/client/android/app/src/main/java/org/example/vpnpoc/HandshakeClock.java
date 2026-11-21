package org.example.vpnpoc;

/**
 * ADR-160: turns the engine's last-handshake time into a monotonic age. Pure and thread-safe.
 *
 * <p>The WireGuard UAPI value {@code last_handshake_time_sec} (read by {@code GoBackend} and
 * {@code TunnelProviderV1.lastHandshakeSeconds()}) is Unix wall-clock time. Subtracting it from
 * {@code System.currentTimeMillis()} on every read made a wall-clock step (manual change, NITZ or
 * NTP correction, time-zone bug) look like an old handshake (forward step: stale, the watchdog
 * restarts) or like no handshake (backward step). Here the wall clock is used once, at the first
 * observation of each new handshake value: its age at that moment is anchored to
 * {@code SystemClock.elapsedRealtime()}, and every later age is a monotonic delta from that anchor.
 *
 * <p>A handshake value that is in the future at its first observation (the wall clock went back
 * between the handshake and our first read) has an unknown age and reports -1, as before; it
 * resolves at the next handshake (WireGuard rekeys every 120 s while traffic flows). The ADR-151
 * rule is unchanged: callers still compare this age with the monotonic age of the last network
 * change, so a handshake older than the change proves nothing.
 */
final class HandshakeClock {
    private static final long UNKNOWN = Long.MIN_VALUE;

    private long seenSeconds;
    /** {@code elapsedRealtime} at which the seen handshake happened (estimated once), or UNKNOWN. */
    private long handshakeAtElapsedMs = UNKNOWN;

    /**
     * @param handshakeSeconds the engine's value (Unix seconds; 0 or less = no handshake yet)
     * @param wallNowMs {@code System.currentTimeMillis()} now (used only for a new value)
     * @param elapsedNowMs {@code SystemClock.elapsedRealtime()} now
     * @return the handshake age in milliseconds, or -1 when none/unknown
     */
    synchronized long ageMs(long handshakeSeconds, long wallNowMs, long elapsedNowMs) {
        if (handshakeSeconds <= 0) {
            seenSeconds = 0;
            handshakeAtElapsedMs = UNKNOWN;
            return -1;
        }
        if (handshakeSeconds != seenSeconds) {
            seenSeconds = handshakeSeconds;
            long ageSeconds = wallNowMs / 1000 - handshakeSeconds;
            handshakeAtElapsedMs = ageSeconds < 0 ? UNKNOWN : elapsedNowMs - ageSeconds * 1000;
        }
        if (handshakeAtElapsedMs == UNKNOWN) return -1;
        return Math.max(0, elapsedNowMs - handshakeAtElapsedMs);
    }

    /** As {@link #ageMs}, in whole seconds (-1 when none/unknown). */
    long ageSeconds(long handshakeSeconds, long wallNowMs, long elapsedNowMs) {
        long age = ageMs(handshakeSeconds, wallNowMs, elapsedNowMs);
        return age < 0 ? -1 : age / 1000;
    }
}
