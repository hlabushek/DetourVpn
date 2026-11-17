package org.example.vpnpoc;

/**
 * ADR-149 (6.1): which VPN service instance native may use as its protector. Pure, JVM-tested.
 *
 * <p>GoBackend reports each instance of this app's own {@code VpnService} when it is created (any
 * start: UI connect, Always-on, sticky restart, boot or update restore) and again when it is
 * destroyed. The newest live instance is the protector. A stop of an older instance (a late
 * {@code onDestroy}) never clears a newer one. The type is generic so that this rule has no
 * Android types; the caller uses it with {@code android.net.VpnService}.
 *
 * <p>Security: this only selects which of the app's own service instances is remembered. What a
 * protector may be used for is unchanged (ADR-140: native renewal sockets to the API origin under
 * Lockdown; ADR-146: setUnderlyingNetworks; ADR-141: the Always-on read).
 */
final class ServiceProtector {
    private ServiceProtector() {}

    /** A new instance replaces the current one; a missing instance keeps it. */
    static <T> T created(T current, T created) {
        return created != null ? created : current;
    }

    /** Clears the protector only when the instance that stopped is the current one. */
    static <T> T stopped(T current, T stopped) {
        return current == stopped ? null : current;
    }
}
