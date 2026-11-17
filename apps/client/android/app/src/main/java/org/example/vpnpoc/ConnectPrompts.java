package org.example.vpnpoc;

/**
 * ADR-145 (2.1): which one-time prompts the connect consent step shows after the VPN consent.
 * Pure logic, so the rule is unit-tested without Android.
 */
final class ConnectPrompts {
    private ConnectPrompts() { }

    /** Android 13 (API 33) added the runtime notification permission. */
    static final int NOTIFICATIONS_API = 33;

    /** Ask for POST_NOTIFICATIONS once, on Android 13+, while it is not granted. */
    static boolean askNotifications(int sdk, boolean granted, boolean askedBefore) {
        return sdk >= NOTIFICATIONS_API && !granted && !askedBefore;
    }

    /**
     * Explain and ask for the battery exemption once per install, at the first connect, while the
     * app is not exempt. {@code earlierPrompts} counts the older post-connect prompts,
     * so a phone that was already asked is not asked again.
     */
    static boolean explainBattery(boolean exempt, int earlierPrompts) {
        return !exempt && earlierPrompts <= 0;
    }
}
