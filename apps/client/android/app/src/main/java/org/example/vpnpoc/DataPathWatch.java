package org.example.vpnpoc;

/**
 * ADR-133: data-path evidence from the native byte counters. Pure (no Android types, no clock, no
 * I/O), fed with elapsed-realtime milliseconds on every snapshot.
 *
 * <ul>
 *   <li>A handshake proves the peer answered the control channel. Inbound bytes growing proves the
 *       return path, which is what the user needs.</li>
 *   <li>{@link #flowing} is true while inbound bytes progressed within {@link #FLOW_MS}.</li>
 *   <li>{@link #stalled} is true when, since the last inbound progress (or since measuring began),
 *       at least {@link #FLOW_MS} passed and at least {@link #STALL_OUTBOUND_BYTES} were sent.
 *       Idle phones (no outbound growth) are never stalled. Keepalives and handshakes stay far below
 *       the outbound threshold.</li>
 *   <li>Unknown counters (negative) change nothing. A counter reset starts a fresh baseline.</li>
 * </ul>
 * A stall only lowers the reported transport to "degraded". It never restarts or reconnects by
 * itself: {@link TunnelWatchdog} and {@link ConnectionSupervisor} take no input from this class.
 */
final class DataPathWatch {
    /** Inbound progress older than this no longer counts as flowing. */
    static final long FLOW_MS = 120_000;
    /** Outbound bytes since the last inbound progress that make silence a stall. */
    static final long STALL_OUTBOUND_BYTES = 8192;

    private long lastReceived = -1;
    private long lastOutbound = -1;
    /** Elapsed time of the last inbound progress; -1 if none since the baseline. */
    private long inboundAt = -1;
    /** Elapsed time the current baseline started (first observation or reset). */
    private long baseAt = -1;
    private long outboundSince;

    void reset() {
        lastReceived = -1;
        lastOutbound = -1;
        inboundAt = -1;
        baseAt = -1;
        outboundSince = 0;
    }

    /**
     * @param received inbound tunnel bytes, or -1 if unknown
     * @param transfer inbound plus outbound tunnel bytes, or -1 if unknown
     */
    void observe(long received, long transfer, long now) {
        if (received < 0 || transfer < 0 || transfer < received) return;
        long outbound = transfer - received;
        if (lastReceived < 0 || received < lastReceived || outbound < lastOutbound) {
            lastReceived = received;
            lastOutbound = outbound;
            baseAt = now;
            inboundAt = -1;
            outboundSince = 0;
            return;
        }
        if (received > lastReceived) {
            inboundAt = now;
            outboundSince = 0;
        } else {
            outboundSince += outbound - lastOutbound;
        }
        lastReceived = received;
        lastOutbound = outbound;
    }

    boolean flowing(long now) {
        return inboundAt >= 0 && now - inboundAt <= FLOW_MS;
    }

    boolean stalled(long now) {
        if (baseAt < 0) return false;
        long since = inboundAt >= 0 ? inboundAt : baseAt;
        return now - since >= FLOW_MS && outboundSince >= STALL_OUTBOUND_BYTES;
    }
}
