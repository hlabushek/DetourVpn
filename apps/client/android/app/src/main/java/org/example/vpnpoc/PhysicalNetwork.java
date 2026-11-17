package org.example.vpnpoc;

import java.util.Collections;
import java.util.Set;
import java.util.TreeSet;

/**
 * ADR-127: pure view of the app's default physical network (the app's own UID is outside the VPN,
 * so its default network is the one that carries the encrypted transport). No Android types, so
 * the decisions are unit-tested on the JVM. Touched only on the application worker thread.
 *
 * <p>"Usable" means a default network with INTERNET that is not behind a captive portal. Android's
 * VALIDATED flag is kept separately as stronger evidence: some networks never validate (the
 * system's connectivity check is blocked) yet carry the tunnel fine, and requiring it left the
 * tunnel without any recovery on such networks.
 *
 * <p>Every change that can move the transport path (another network, return after a loss, a new
 * local address on the same network) bumps {@link #generation()}.
 */
final class PhysicalNetwork {
    private String id;
    private boolean lost = true;
    private boolean internet;
    private boolean captivePortal;
    private boolean validated;
    private Set<String> addresses = Collections.emptySet();
    private long generation;
    /**
     * ADR-146: a loss is confirmed only after it lasted this long; a quick handover (Wi-Fi to
     * mobile, a short radio gap) must not mark a working tunnel as recovering.
     */
    static final long LOSS_CONFIRM_MS = 8_000;
    private static final long NO_LOSS = Long.MIN_VALUE;
    /** Elapsed time of the unconfirmed loss of the current network, or {@link #NO_LOSS}. */
    private long lostAt = NO_LOSS;

    long generation() { return generation; }

    boolean usable() { return !lost && id != null && internet && !captivePortal; }

    boolean validated() { return usable() && validated; }

    boolean captivePortal() { return !lost && id != null && captivePortal; }

    /**
     * A default network became available. Returns true when the transport path may have changed:
     * a different network than before, or the same one back after a loss.
     */
    boolean available(String network, boolean hasInternet, boolean captive, boolean isValidated) {
        boolean changed = id != null && (lost || !id.equals(network));
        boolean first = id == null;
        if (changed || first || lost) addresses = Collections.emptySet();
        id = network;
        lost = false;
        lostAt = NO_LOSS; // ADR-146: a new onAvailable cancels a pending loss.
        internet = hasInternet;
        captivePortal = captive;
        validated = isValidated;
        if (changed) generation++;
        return changed;
    }

    /** Capabilities of the current default network. Returns true if it just became usable. */
    boolean capabilities(String network, boolean hasInternet, boolean captive, boolean isValidated) {
        if (lost || id == null || !id.equals(network)) return false;
        boolean before = usable();
        internet = hasInternet;
        captivePortal = captive;
        validated = isValidated;
        return !before && usable();
    }

    /**
     * Local addresses of the current default network. Returns true (and bumps the generation)
     * when a known address set changed: same network, new IP, so the NAT path is new.
     */
    boolean linkAddresses(String network, Set<String> current) {
        if (lost || id == null || !id.equals(network) || current == null) return false;
        Set<String> next = Collections.unmodifiableSet(new TreeSet<>(current));
        boolean changed = !addresses.isEmpty() && !next.isEmpty() && !addresses.equals(next);
        if (!next.isEmpty()) addresses = next;
        if (changed) generation++;
        return changed;
    }

    /** The default network went away. Only the current one counts; late losses are ignored. */
    void lost(String network) { lost(network, NO_LOSS); }

    /**
     * ADR-146: the current default network went away at {@code now}. It is unusable at once (no
     * recovery attempt or restart runs without a network), but {@link #lossPending} stays true
     * for {@link #LOSS_CONFIRM_MS}. Returns true if this was the current network.
     */
    boolean lost(String network, long now) {
        if (id == null || !id.equals(network)) return false;
        if (!lost) lostAt = now;
        lost = true;
        return true;
    }

    /** ADR-146: the network was lost less than {@link #LOSS_CONFIRM_MS} ago and has not returned. */
    boolean lossPending(long now) {
        return lost && lostAt != NO_LOSS && now - lostAt < LOSS_CONFIRM_MS;
    }
}
