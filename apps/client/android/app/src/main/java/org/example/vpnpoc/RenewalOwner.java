package org.example.vpnpoc;

/**
 * ADR-136/140: at most one renewal owner for a window. Pure policy, no Android types.
 *
 * <ul>
 *   <li>Dart UI controller: renews while it is attached, from its own timers.</li>
 *   <li>Dart restore engine (ADR-121): renews from its timers, woken by the restore alarm.</li>
 *   <li>Native renewer (ADR-097): renews without Dart. Started by the UI (after it adopted the
 *       tunnel) or, since ADR-140, by the restore engine itself.</li>
 * </ul>
 * While native renews, every attached Dart controller only observes. A refusal leaves the caller
 * (Dart) as the single owner.
 */
final class RenewalOwner {
    private RenewalOwner() {}

    /** ADR-140: how the native renewer reaches the API. */
    enum Transport {
        /** Plain platform HTTPS; the app's own package is outside its VPN (ClientProfile). */
        DIRECT,
        /** {@link ProtectedHttps} over a {@code VpnService.protect()}ed socket (Lockdown). */
        PROTECTED,
        /** No path that can leave a Lockdown VPN: native renewal is refused. */
        NONE
    }

    /**
     * Without Lockdown the app's sockets already use the physical network. Under Lockdown only a
     * protected socket can leave, which needs a known VpnService instance (Always-on start, sticky
     * restart, restore). Device behaviour under Lockdown is not verified (ADR-140).
     */
    static Transport transport(boolean lockdown, boolean protectorKnown) {
        if (!lockdown) return Transport.DIRECT;
        return protectorKnown ? Transport.PROTECTED : Transport.NONE;
    }

    /**
     * @param fromRestoreEngine the restore engine itself asks (ADR-140); the UI channel may not
     *     start native renewal while a restore engine it has not adopted is alive (ADR-136).
     */
    static boolean nativeMayStart(boolean fromRestoreEngine, boolean restoreEngineAlive,
            boolean lockdown, boolean protectorKnown) {
        if (!fromRestoreEngine && restoreEngineAlive) return false;
        return transport(lockdown, protectorKnown) != Transport.NONE;
    }

    /** A Dart controller (UI or restore) renews only while native does not. */
    static boolean dartRenews(boolean dartAttached, boolean nativeRenewing) {
        return dartAttached && !nativeRenewing;
    }

    /**
     * The owners that renew right now. More than one is a bug, so the caller must refuse the
     * second owner; {@link #conflict} reports that.
     */
    static int owners(boolean nativeRenewing, boolean dartUiRenewing, boolean restoreRenewing) {
        int count = 0;
        if (nativeRenewing) count++;
        if (dartUiRenewing) count++;
        if (restoreRenewing) count++;
        return count;
    }

    static boolean conflict(boolean nativeRenewing, boolean dartUiRenewing, boolean restoreRenewing) {
        return owners(nativeRenewing, dartUiRenewing, restoreRenewing) > 1;
    }
}
