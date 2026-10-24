package org.example.vpnpoc;

/** Pure monotonic v3 fencing. Replays cannot reset a deadline or revive a stopped VPN. */
final class WindowDeadline {
    String connection;
    String runtime;
    long generation;
    long deadline;
    void start(String id, String epoch, long version, long end, long now) {
        if (connection != null || version != 1 || end <= now || end - now > 90000)
            throw new IllegalStateException();
        connection = id; runtime = epoch; generation = version; deadline = end;
    }
    void advance(String id, String epoch, long version, long end, long now) {
        if (connection == null || !connection.equals(id) || !runtime.equals(epoch)
                || now >= deadline || end <= now || end - now > 90000)
            throw new IllegalStateException();
        if (version == generation) return;
        if (version != generation + 1 || end <= deadline) throw new IllegalStateException();
        generation = version; deadline = end;
    }
    void clear() { connection = null; runtime = null; generation = 0; deadline = 0; }
}
