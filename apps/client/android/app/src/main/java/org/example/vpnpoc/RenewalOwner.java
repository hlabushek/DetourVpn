package org.example.vpnpoc;

/**
 * ADR-136: at most one renewal owner for a window. Pure policy, no Android types.
 *
 * <ul>
 *   <li>Dart UI controller: renews while it is attached, from its own timers.</li>
 *   <li>Dart restore engine (ADR-121): renews from its timers, woken by the restore alarm.</li>
 *   <li>Native renewer (ADR-097): renews without Dart, only after the UI adopted the tunnel.</li>
 * </ul>
 * Native renewal may start only when no restore engine is alive and Lockdown is off (ADR-121: under
 * Lockdown native HTTPS cannot reach the API). A refusal leaves Dart as the single owner.
 */
final class RenewalOwner {
    private RenewalOwner() {}

    static boolean nativeMayStart(boolean restoreEngineAlive, boolean lockdown) {
        return !restoreEngineAlive && !lockdown;
    }

    /**
     * The owners that may renew right now. More than one is a bug, so the caller must refuse the
     * second owner; {@link #conflict} reports that.
     */
    static int owners(boolean nativeRenewing, boolean dartUiAttached, boolean restoreEngineAlive) {
        int count = 0;
        if (nativeRenewing) count++;
        if (dartUiAttached) count++;
        if (restoreEngineAlive) count++;
        return count;
    }

    static boolean conflict(boolean nativeRenewing, boolean dartUiAttached, boolean restoreEngineAlive) {
        return owners(nativeRenewing, dartUiAttached, restoreEngineAlive) > 1;
    }
}
