package org.example.vpnpoc;

/**
 * Task 7 / RCA section 9: remembers, in the VPN process, whether the tunnel completed a handshake
 * since the last connect. Dart can sleep through a whole window (native renewal, ADR-097), so
 * only the native side can say this reliably. Kept after the tunnel stops, so a diagnostic report
 * made after a failure still knows; cleared only by the next connect. Thread-safe and pure.
 */
final class HandshakeMemory {
    private boolean seen;

    /** A new connection starts: nothing seen yet. */
    synchronized void reset() {
        seen = false;
    }

    /**
     * One reading of the engine's last-handshake time (Unix seconds; 0 or less = none yet).
     * A time in the future beyond clock skew is ignored as a bogus reading.
     */
    synchronized void observe(long lastHandshakeSeconds, long nowSeconds) {
        if (lastHandshakeSeconds > 0 && lastHandshakeSeconds <= nowSeconds + 60) seen = true;
    }

    synchronized boolean seen() {
        return seen;
    }
}
