package org.example.vpnpoc;

/**
 * ADR-147 (connection-survivability Phase 3): the pure rules for surviving Doze overnight. No
 * Android types, so each rule is unit-tested on the JVM; {@code ClientApplication},
 * {@code NativeRenewer}, {@code RecoveryCompanionService} and {@code BackstopJobService} apply them.
 */
final class DozeSurvival {
    private DozeSurvival() {}

    /** Android 12 (API 31) is the first release where exact alarms need a permission. */
    static final int EXACT_ALARM_PERMISSION_SDK = 31;
    /** Android 14 (API 34) checks the declared foreground-service type at startForeground. */
    static final int FGS_TYPE_SDK = 34;
    /** ADR-147 (3.4): the periodic backstop job; 15 min is JobScheduler's minimum period. */
    static final long BACKSTOP_PERIOD_MS = 15 * 60_000L;
    /** ADR-147 (3.5): the backstop's own wake lock; released as soon as its check ran. */
    static final long BACKSTOP_LOCK_MS = 10_000L;

    /**
     * 3.2: whether the renewal alarm may be exact. Before API 31 exact alarms need no permission;
     * from API 31 {@code AlarmManager.canScheduleExactAlarms()} decides (SCHEDULE_EXACT_ALARM can
     * be denied or revoked by the user, and Android 14 denies it by default for new installs).
     */
    static boolean exactAlarmAllowed(int sdk, boolean canScheduleExact) {
        return sdk < EXACT_ALARM_PERMISSION_SDK || canScheduleExact;
    }

    /**
     * 3.3: the companion foreground service runs only in a recovery gap: the user wants the VPN,
     * the tunnel is not up, and the stop was not terminal (a terminal stop already cleared the
     * intent; checked again here so a stale flag never keeps a notification).
     */
    static boolean companionWanted(boolean desired, boolean tunnelUp, boolean terminalStop) {
        return desired && !tunnelUp && !terminalStop;
    }

    /**
     * 3.3: whether a start of the companion may be tried. From API 34 it is started with the
     * {@code systemExempted} type, which Android only grants to some apps (among them apps that
     * hold the exact-alarm permission); without it startForeground would throw after the service
     * was started, so it is not tried. ASSUMPTION, not verified on a device (ADR-147).
     */
    static boolean companionMayStart(int sdk, boolean exactAllowed) {
        return sdk < FGS_TYPE_SDK || exactAllowed;
    }

    /**
     * ADR-157: the companion may be stopped only once it is in the foreground. A service started
     * with {@code startForegroundService} that is stopped before it called {@code startForeground}
     * is reported by Android as a crash of the app process ("did not then call
     * startForeground"), which would also end the VPN service in the same process. A requested
     * but not yet running companion is stopped by the check that follows its start instead.
     */
    static boolean companionMayStop(boolean running) {
        return running;
    }

    /**
     * 3.4: a backstop run sends {@code freshSignal("backstop")} only when the VPN is wanted, no
     * tunnel is up and no recovery attempt is in flight. All other cases do nothing.
     */
    static boolean backstopSignal(boolean desired, boolean tunnelUp, boolean attemptInFlight) {
        return desired && !tunnelUp && !attemptInFlight;
    }
}
