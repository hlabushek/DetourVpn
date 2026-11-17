package org.example.vpnpoc;

/**
 * ADR-146: the decisions behind {@code ClientApplication}'s default-network callbacks, without
 * Android types, so they are unit-tested on the JVM. The application keeps the Android objects
 * (the {@code Network} for {@code setUnderlyingNetworks}, the scheduled future of the loss timer)
 * and applies each {@link Outcome}. Networks are identified by an opaque string id that is never
 * logged. Worker thread only.
 */
final class NetworkCallbacks {
    /** Delay of the loss-confirmation task: just after {@link PhysicalNetwork#LOSS_CONFIRM_MS}. */
    static final long LOSS_TIMER_MS = PhysicalNetwork.LOSS_CONFIRM_MS + 50;
    private static final long NONE = Long.MIN_VALUE;

    /** What the application does after one callback. Plain fields; all false by default. */
    static final class Outcome {
        /** Event-log code for this callback, or null for none. */
        String log;
        String logDetail;
        /** The transport path moved: run {@code networkPathChanged()}. */
        boolean pathChanged;
        /** Check the tunnel after {@link TunnelWatchdog#SETTLE_MS} (no path change). */
        boolean settleWatch;
        boolean evaluateRecovery;
        /** Send {@code freshSignal("network")} (ADR-143, 30 s limit applies there). */
        boolean freshSignal;
        /** The VPN's underlying network must be set again. */
        boolean underlyingStale;
        /** This network is now the default one (keep its Android object). */
        boolean setDefault;
        /** The default network is gone (drop its Android object). */
        boolean clearDefault;
        /** Arm (or re-arm) the loss timer for {@link #LOSS_TIMER_MS}. */
        boolean armLossTimer;
        /** Cancel a pending loss timer. */
        boolean cancelLossTimer;
        boolean publishState;
    }

    final PhysicalNetwork physical;
    private String defaultId;
    /** Elapsed time the armed loss timer fires, or {@link #NONE}. */
    private long lossTimerDueAt = NONE;

    NetworkCallbacks(PhysicalNetwork physical) { this.physical = physical; }

    /** {@code onAvailable}: a default network appeared (capabilities may be unknown yet). */
    Outcome available(String id, boolean internet, boolean captive, boolean validated) {
        Outcome out = new Outcome();
        boolean wasUsable = physical.usable();
        boolean changed = physical.available(id, internet, captive, validated);
        out.log = "network_available";
        out.logDetail = changed ? "changed" : "same";
        // ADR-146: a new onAvailable cancels a pending loss confirmation.
        out.cancelLossTimer = true;
        lossTimerDueAt = NONE;
        out.underlyingStale = !id.equals(defaultId);
        out.setDefault = true;
        defaultId = id;
        out.pathChanged = changed;
        out.settleWatch = !changed;
        out.evaluateRecovery = true;
        // ADR-143/146: a network that became usable resets the backoff (30 s limit).
        out.freshSignal = changed || (!wasUsable && physical.usable());
        return out;
    }

    /** {@code onCapabilitiesChanged} of a network. */
    Outcome capabilities(String id, boolean internet, boolean captive, boolean validated) {
        Outcome out = new Outcome();
        boolean portalBefore = physical.captivePortalBlocking();
        boolean becameUsable = physical.capabilities(id, internet, captive, validated);
        boolean portalNow = physical.captivePortalBlocking();
        if (portalNow && !portalBefore) {
            // ADR-148 (5.3): show «sign in to the network»; no restart or attempt runs (unusable).
            out.log = "network_captive_portal";
            out.publishState = true;
            return out;
        }
        if (!becameUsable) {
            out.publishState = portalBefore && !portalNow; // the portal hint goes away
            return out;
        }
        out.log = "network_usable";
        out.settleWatch = true;
        out.evaluateRecovery = true;
        out.freshSignal = true; // ADR-143: it just became usable.
        return out;
    }

    /** {@code onLinkPropertiesChanged}: the local addresses of a network. */
    Outcome linkAddresses(String id, java.util.Set<String> addresses) {
        Outcome out = new Outcome();
        if (!physical.linkAddresses(id, addresses)) return out;
        out.log = "network_address_changed";
        out.pathChanged = true;
        return out;
    }

    /** {@code onLost} at elapsed time {@code now}. A late loss of an older network does nothing. */
    Outcome lost(String id, long now) {
        Outcome out = new Outcome();
        if (!physical.lost(id, now)) return out;
        out.log = "network_lost";
        if (id.equals(defaultId)) {
            defaultId = null;
            out.clearDefault = true;
        }
        // ADR-146: no attempt runs while offline (physical.usable() is false), but the tunnel is
        // marked recovering only if no network returns within 8 s.
        out.cancelLossTimer = lossTimerDueAt != NONE;
        out.armLossTimer = true;
        lossTimerDueAt = now + LOSS_TIMER_MS;
        out.publishState = true;
        return out;
    }

    /** Whether an armed loss timer would have fired by {@code now} (for tests and checks). */
    boolean lossTimerDue(long now) { return lossTimerDueAt != NONE && now >= lossTimerDueAt; }

    boolean lossTimerArmed() { return lossTimerDueAt != NONE; }

    /**
     * The loss timer fired. Returns true when the loss is confirmed (no usable network returned):
     * log {@code network_loss_confirmed} and publish the state.
     */
    boolean lossTimerFired() {
        lossTimerDueAt = NONE;
        return !physical.usable();
    }
}
