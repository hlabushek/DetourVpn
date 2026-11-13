package org.example.vpnpoc;

/**
 * ADR-139: the Always-on VPN setting as the app can know it. Pure, JVM-tested.
 *
 * <p>Android exposes the setting only on a running {@code VpnService} instance
 * ({@code isAlwaysOn()}, {@code isLockdownEnabled()}, API 29+). Native sees an instance when the
 * service starts by Always-on, sticky restart or restore, and when it stops, so the value is the
 * last one read; before any read it is {@link #UNKNOWN}. The UI never claims more than this.
 */
final class AlwaysOnStatus {
    static final String UNKNOWN = "unknown";
    static final String OFF = "off";
    static final String ON = "on";
    /** Always-on with "block connections without VPN": native renewal is refused (ADR-121/136). */
    static final String LOCKDOWN = "lockdown";

    private AlwaysOnStatus() {}

    static String of(boolean apiAvailable, boolean alwaysOn, boolean lockdown) {
        if (!apiAvailable) return UNKNOWN;
        if (!alwaysOn) return OFF;
        return lockdown ? LOCKDOWN : ON;
    }

    /** A persisted value from an older or damaged store is treated as unknown. */
    static String sanitize(String stored) {
        if (OFF.equals(stored) || ON.equals(stored) || LOCKDOWN.equals(stored)) return stored;
        return UNKNOWN;
    }
}
