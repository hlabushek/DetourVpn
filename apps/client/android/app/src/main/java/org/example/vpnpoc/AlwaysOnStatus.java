package org.example.vpnpoc;

/**
 * ADR-139/141: the Always-on VPN setting as the app can know it. Pure, JVM-tested.
 *
 * <p>Android exposes the setting only on a {@code VpnService} instance ({@code isAlwaysOn()},
 * {@code isLockdownEnabled()}, API 29+), and both answer {@code true} only while that app's VPN is
 * established: the platform looks up the caller's running VPN first and returns {@code false} when
 * there is none (AOSP {@code VpnManagerService.isCallerCurrentAlwaysOnVpnApp} via
 * {@code getVpnIfOwner}). A read on an Always-on start (before {@code establish()}) or in
 * {@code onDestroy} (after the tunnel is down) therefore says "off" although Always-on is set.
 * ADR-141: such a read is not a reading; only a read while the tunnel is up counts. Before any
 * valid read the value is {@link #UNKNOWN} and the UI shows nothing.
 */
final class AlwaysOnStatus {
    static final String UNKNOWN = "unknown";
    static final String OFF = "off";
    static final String ON = "on";
    /** Always-on with "block connections without VPN": native renewal is refused (ADR-121/136). */
    static final String LOCKDOWN = "lockdown";
    /**
     * ADR-141: preference key for the last valid reading. Builds up to 0.1.0-beta.33 stored false
     * "off" readings under {@link #LEGACY_PREF_KEY}; that value is dropped, never shown.
     */
    static final String PREF_KEY = "always_on_v2";
    static final String LEGACY_PREF_KEY = "always_on";

    private AlwaysOnStatus() {}

    static String of(boolean apiAvailable, boolean alwaysOn, boolean lockdown) {
        if (!apiAvailable) return UNKNOWN;
        if (!alwaysOn) return OFF;
        return lockdown ? LOCKDOWN : ON;
    }

    /**
     * ADR-141: one read of the platform answers, or {@code null} when the read proves nothing
     * because this app's VPN was not established at that moment (keep the previous value).
     */
    static String reading(boolean apiAvailable, boolean vpnEstablished, boolean alwaysOn,
            boolean lockdown) {
        if (!apiAvailable) return UNKNOWN;
        if (!vpnEstablished) return null;
        return of(true, alwaysOn, lockdown);
    }

    /**
     * ADR-149 (R6): the Lockdown flag used to choose the native renewal transport, from the same
     * reading as the display value. A {@code null} reading (taken before {@code establish()} or
     * after the tunnel went down) proves nothing and keeps the previous flag; it must never reset
     * Lockdown to false, which would make native renewal pick a direct path the OS blocks.
     */
    static boolean lockdownAfterRead(String reading, boolean previous) {
        if (reading == null) return previous;
        return LOCKDOWN.equals(reading);
    }

    /** A persisted value from an older or damaged store is treated as unknown. */
    static String sanitize(String stored) {
        if (OFF.equals(stored) || ON.equals(stored) || LOCKDOWN.equals(stored)) return stored;
        return UNKNOWN;
    }
}
