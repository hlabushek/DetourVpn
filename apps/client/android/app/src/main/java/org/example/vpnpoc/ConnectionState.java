package org.example.vpnpoc;

import java.util.Objects;

/**
 * ADR-144: the one connection state the app shows, computed natively from facts only. Pure (no
 * Android types, no clock, no I/O) so every rule is unit-tested on the JVM. Flutter renders it;
 * it no longer guesses the state from its own polling (that guess used a 60 s handshake rule
 * that flipped an idle, working tunnel to "reconnecting").
 *
 * <p>States match TECHNICAL_SPEC.md (disconnected / connecting / connected / reconnecting /
 * failed); {@link Kind#RECOVERING} is "reconnecting". Every state carries a reason code (no data).
 */
final class ConnectionState {
    enum Kind {
        DISCONNECTED("disconnected"),
        CONNECTING("connecting"),
        CONNECTED("connected"),
        RECOVERING("recovering"),
        FAILED("failed");

        final String wire;

        Kind(String wire) { this.wire = wire; }
    }

    /**
     * A handshake at most this old proves the peer answers. WireGuard rekeys about every 120 s
     * while packets flow (PersistentKeepalive = 25 keeps them flowing), and rejects a session
     * after 180 s; the same bound as {@link ConnectionSupervisor#transport} and ADR-128.
     */
    static final long HANDSHAKE_FRESH_SECONDS = 180;
    /** A new window may take this long for its first handshake before it counts as a problem. */
    static final long AWAIT_HANDSHAKE_MS = 30_000;
    /** Inbound bytes from just before a network change prove nothing about the new path. */
    static final long NETWORK_CHANGE_RECENT_MS = 60_000;
    /**
     * ADR-151: after a window start, a restart in place or a network change, the state is
     * re-read this often (instead of only on the 15 s watch tick), so the first handshake shows
     * «Подключено» within about a second.
     */
    static final long RECHECK_MS = 1_000;
    /** ADR-151: how long after such a trigger the fast re-check runs at most. */
    static final long RECHECK_FOR_MS = 30_000;

    /**
     * ADR-151: whether to re-read the state again in {@link #RECHECK_MS}: only while a tunnel is
     * up, not yet shown as connected, and the trigger is less than {@link #RECHECK_FOR_MS} old.
     */
    static boolean recheckSoon(ConnectionState state, boolean tunnelUp, long sinceTriggerMs) {
        return state != null && tunnelUp && sinceTriggerMs >= 0 && sinceTriggerMs < RECHECK_FOR_MS
                && state.kind != Kind.CONNECTED;
    }

    /** Facts read on the worker thread. Plain fields; built once per evaluation. */
    static final class Facts {
        boolean desired;
        boolean tunnelUp;
        boolean networkUsable;
        /**
         * ADR-146: the network was lost less than {@link PhysicalNetwork#LOSS_CONFIRM_MS} ago. A
         * running tunnel is not marked recovering for that alone until the loss is confirmed.
         */
        boolean networkLossPending;
        /** Seconds since the last handshake, or -1 if none in this window. */
        long handshakeAgeSeconds = -1;
        /** Milliseconds since the default network changed, or -1 if it never changed. */
        long networkChangeAgeMs = -1;
        /**
         * The watchdog is still verifying the path after a network change, and no handshake newer
         * than that change (or the restart it caused) is known ({@link
         * TunnelWatchdog#awaitingNetworkProof}, ADR-151).
         */
        boolean restartPending;
        /** Inbound bytes progressed recently ({@link DataPathWatch#flowing}). */
        boolean inboundFlowing;
        /** Outbound bytes without inbound progress ({@link DataPathWatch#stalled}). */
        boolean dataStalled;
        /** Milliseconds since this window came up, or -1 if unknown. */
        long windowAgeMs = -1;
        /** A restore engine is bringing a tunnel up, or a native recovery start is in flight. */
        boolean restoring;
        /** The last stop reason recorded by the supervisor ("" if none). */
        String lastStop = "";
        /**
         * ADR-148 (5.3): the default network is a captive portal that is not signed in
         * ({@link PhysicalNetwork#captivePortalBlocking}). Such a network is never usable.
         */
        boolean captivePortal;
    }

    final Kind kind;
    final String reason;

    ConnectionState(Kind kind, String reason) {
        this.kind = kind;
        this.reason = reason == null ? "" : reason;
    }

    static ConnectionState of(Facts f) {
        if (f.tunnelUp) return whileUp(f);
        if (f.desired) {
            if (f.restoring) return new ConnectionState(Kind.RECOVERING, "restoring");
            if (!f.networkUsable) return noNetwork(f);
            String category = ConnectionSupervisor.category(f.lastStop);
            return new ConnectionState(Kind.RECOVERING,
                    "none".equals(category) ? "reconnecting" : category);
        }
        String category = ConnectionSupervisor.category(f.lastStop);
        if (ConnectionSupervisor.terminal(f.lastStop) && !"user_stop".equals(category))
            return new ConnectionState(Kind.FAILED, category);
        return new ConnectionState(Kind.DISCONNECTED, "none");
    }

    /** ADR-148 (5.3): a captive portal asks the user to sign in; otherwise wait for a network. */
    private static ConnectionState noNetwork(Facts f) {
        return new ConnectionState(Kind.RECOVERING,
                f.captivePortal ? "captive_portal" : "waiting_for_network");
    }

    private static ConnectionState whileUp(Facts f) {
        if (!f.networkUsable && !f.networkLossPending) return noNetwork(f);
        if (f.restartPending) return new ConnectionState(Kind.RECOVERING, "network_changed");
        if (f.dataStalled) return new ConnectionState(Kind.RECOVERING, "data_stalled");
        boolean changed = f.networkChangeAgeMs >= 0;
        boolean afterChange = f.handshakeAgeSeconds >= 0
                && (!changed || f.handshakeAgeSeconds * 1000 < f.networkChangeAgeMs);
        boolean fresh = afterChange && f.handshakeAgeSeconds <= HANDSHAKE_FRESH_SECONDS;
        boolean recentChange = changed && f.networkChangeAgeMs < NETWORK_CHANGE_RECENT_MS;
        if (fresh || (f.inboundFlowing && !recentChange))
            return new ConnectionState(Kind.CONNECTED, "verified");
        if (f.handshakeAgeSeconds < 0 && f.windowAgeMs >= 0 && f.windowAgeMs < AWAIT_HANDSHAKE_MS)
            return new ConnectionState(Kind.CONNECTING, "awaiting_handshake");
        if (f.handshakeAgeSeconds < 0) return new ConnectionState(Kind.RECOVERING, "no_handshake");
        return new ConnectionState(Kind.RECOVERING,
                afterChange ? "handshake_stale" : "network_changed");
    }

    @Override public boolean equals(Object other) {
        if (!(other instanceof ConnectionState)) return false;
        ConnectionState that = (ConnectionState) other;
        return kind == that.kind && reason.equals(that.reason);
    }

    @Override public int hashCode() { return Objects.hash(kind, reason); }

    @Override public String toString() { return kind.wire + ":" + reason; }
}
