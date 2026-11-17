package org.example.vpnpoc;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import org.junit.Test;

/** ADR-147 (Phase 3): the pure Doze-survival rules. */
public class DozeSurvivalTest {
    private static final String C = "11111111-1111-4111-8111-111111111111";
    private static final String R = "22222222-2222-4222-8222-222222222222";
    private static final String W = "33333333-3333-4333-8333-333333333333";

    @Test public void exactAlarmsNeedThePermissionFromAndroid12() {
        assertTrue("no permission exists before API 31", DozeSurvival.exactAlarmAllowed(30, false));
        assertTrue(DozeSurvival.exactAlarmAllowed(31, true));
        assertFalse("denied or revoked: inexact fallback", DozeSurvival.exactAlarmAllowed(34, false));
    }

    @Test public void theExactAlarmRenewsLaterThanTheInexactFallback() {
        var plan = new NativeRenewal(C, R, W, 1, 1048576, 900);
        assertEquals("ADR-142 fallback: 2 min after the grant", 780_000, plan.renewBeforeMs(false));
        assertEquals("exact: 10 min before expiry", 600_000, plan.renewBeforeMs(true));
        assertEquals(plan.renewBeforeMs(), plan.renewBeforeMs(false));
        assertFalse(plan.due(700_000, -1, true));
        assertTrue("the fallback renews earlier", plan.due(700_000, -1, false));
        assertTrue(plan.due(600_000, -1, true));
        assertEquals(100_000, plan.idleDelayMs(700_000, true));
        assertEquals(1_000, plan.idleDelayMs(700_000, false));
    }

    @Test public void shortWindowsKeepTheirMarginWhateverTheAlarm() {
        var ninety = new NativeRenewal(C, R, W, 1, 1048576, 90);
        assertEquals(55_000, ninety.renewBeforeMs(true));
        assertEquals(55_000, ninety.renewBeforeMs(false));
        var five = new NativeRenewal(C, R, W, 1, 1048576, 300);
        assertEquals("never later than the fallback", 180_000, five.renewBeforeMs(true));
    }

    @Test public void theCompanionRunsOnlyInARecoveryGap() {
        assertTrue(DozeSurvival.companionWanted(true, false, false));
        assertFalse("the VPN service is up", DozeSurvival.companionWanted(true, true, false));
        assertFalse("not wanted", DozeSurvival.companionWanted(false, false, false));
        assertFalse("terminal stop", DozeSurvival.companionWanted(true, false, true));
    }

    @Test public void theCompanionIsNotStartedWhereItsTypeWouldBeRefused() {
        assertTrue(DozeSurvival.companionMayStart(33, false));
        assertTrue(DozeSurvival.companionMayStart(34, true));
        assertFalse(DozeSurvival.companionMayStart(34, false));
    }

    @Test public void theBackstopSignalsOnlyWhenWantedDownAndIdle() {
        assertTrue(DozeSurvival.backstopSignal(true, false, false));
        assertFalse(DozeSurvival.backstopSignal(false, false, false));
        assertFalse(DozeSurvival.backstopSignal(true, true, false));
        assertFalse("an attempt is in flight", DozeSurvival.backstopSignal(true, false, true));
        assertEquals(15 * 60_000L, DozeSurvival.BACKSTOP_PERIOD_MS);
        assertTrue("bounded wake lock", DozeSurvival.BACKSTOP_LOCK_MS <= 60_000L);
    }

    @Test public void aBackstopSignalStartsRecoveryThroughTheSupervisorRules() {
        var supervisor = new ConnectionSupervisor();
        supervisor.restore(true);
        supervisor.tunnelDown("lease_expired", 0);
        long now = 10 * 60_000;
        assertTrue(DozeSurvival.backstopSignal(supervisor.desired(), false,
                supervisor.attemptInFlight(now)));
        var decision = supervisor.freshSignal(now, false, false, true);
        assertEquals(ConnectionSupervisor.Action.START_RECOVERY, decision.action);
        supervisor.attemptStarted(now, 0.5);
        assertFalse("nothing more while the attempt is in flight",
                DozeSurvival.backstopSignal(supervisor.desired(), false,
                        supervisor.attemptInFlight(now + 1_000)));
    }
}
