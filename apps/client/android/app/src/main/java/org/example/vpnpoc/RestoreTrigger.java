package org.example.vpnpoc;

/**
 * ADR-098/149: whether a system broadcast may restore the VPN. Pure, JVM-tested.
 *
 * <p>Two broadcasts restore: {@code BOOT_COMPLETED} (ADR-098; delivered only after the first
 * unlock, as the app is not direct-boot aware, ADR-149 6.2(a)) and {@code MY_PACKAGE_REPLACED}
 * (ADR-149 6.3; the app was updated and its process, with the tunnel, was killed). Both are
 * protected broadcasts that only the system can send. Both use the same gate: the user left the
 * VPN connected (no manual disconnect) and VPN consent is still this app's. Any other action is
 * ignored, so the receiver can never start a VPN the user did not want.
 */
final class RestoreTrigger {
    static final String BOOT_COMPLETED = "android.intent.action.BOOT_COMPLETED";
    static final String MY_PACKAGE_REPLACED = "android.intent.action.MY_PACKAGE_REPLACED";

    enum Decision {
        /** Not a restore broadcast: do nothing. */
        IGNORE,
        /** The user did not leave the VPN connected: do nothing. */
        NOT_WANTED,
        /** Consent was revoked while the app was off: forget the intent, start nothing. */
        CLEAR_INTENT,
        /** Start the restore path (the same one as an Always-on start). */
        RESTORE
    }

    private RestoreTrigger() {}

    static boolean restoreAction(String action) {
        return BOOT_COMPLETED.equals(action) || MY_PACKAGE_REPLACED.equals(action);
    }

    /**
     * @param wantsConnected the user's persisted intent ({@code ClientApplication.wantsConnected})
     * @param consentGranted {@code VpnService.prepare()} returned {@code null}
     */
    static Decision decide(String action, boolean wantsConnected, boolean consentGranted) {
        if (!restoreAction(action)) return Decision.IGNORE;
        if (!wantsConnected) return Decision.NOT_WANTED;
        if (!consentGranted) return Decision.CLEAR_INTENT;
        return Decision.RESTORE;
    }
}
