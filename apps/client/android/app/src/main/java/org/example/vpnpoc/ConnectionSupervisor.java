package org.example.vpnpoc;

/** Pure serialized policy. Times are monotonic; no Android, I/O or secret state. */
final class ConnectionSupervisor {
    enum State { DISCONNECTED, CONNECTING, CONNECTED, DEGRADED, WAITING_FOR_NETWORK,
        RECONNECTING, ERROR }
    State state = State.DISCONNECTED;
    boolean desired;
    long generation;
    long networkGeneration;
    long healthyAt = -1;
    long stableSince = -1;
    long retryAt;
    int attempts;
    String failure = "";
    private String network;

    void connect(long now) {
        generation++;
        desired = true;
        healthyAt = stableSince = -1;
        attempts = 0;
        retryAt = now;
        failure = "";
        state = network == null ? State.WAITING_FOR_NETWORK : State.CONNECTING;
    }

    void stop(String reason) {
        generation++;
        desired = false;
        healthyAt = stableSince = -1;
        failure = reason;
        state = State.DISCONNECTED;
    }

    boolean network(String identity, long now) {
        if (java.util.Objects.equals(network, identity)) return false;
        network = identity;
        networkGeneration++;
        healthyAt = stableSince = -1;
        if (desired) {
            state = identity == null ? State.WAITING_FOR_NETWORK : State.RECONNECTING;
            failure = identity == null ? "network_unavailable" : "";
            // Coalesce handovers, but never delay reporting total loss.
            retryAt = now + 750;
        }
        return true;
    }

    boolean accepts(long session, long link) {
        return desired && generation == session && networkGeneration == link;
    }

    boolean due(long now) { return desired && network != null && now >= retryAt; }

    void health(long session, long link, boolean success, long now, double jitter) {
        if (!accepts(session, link)) return;
        if (success) {
            healthyAt = now;
            if (stableSince < 0) stableSince = now;
            if (now - stableSince >= 120_000) attempts = 0;
            state = State.CONNECTED;
            failure = "";
            retryAt = now + 30_000;
        } else {
            healthyAt = stableSince = -1;
            state = State.DEGRADED;
            failure = "tunnel_health_unconfirmed";
            retryAt = now + backoff(attempts++, jitter);
        }
    }

    void terminal(String reason) {
        stop(reason);
        state = State.ERROR;
    }

    void unconfirmed(long now) {
        healthyAt = stableSince = -1;
        state = State.DEGRADED;
        failure = "awaiting_authenticated_data";
        retryAt = now + 30_000;
    }

    boolean healthy(long now) {
        return desired && state == State.CONNECTED && healthyAt >= 0
                && now >= healthyAt && now - healthyAt <= 60_000;
    }

    static long backoff(int attempt, double jitter) {
        long base = Math.min(60_000, 1000L << Math.min(6, Math.max(0, attempt)));
        return Math.min(60_000, Math.max(500, (long) (base * (0.75 + Math.max(0, Math.min(1, jitter)) * 0.5))));
    }
}
