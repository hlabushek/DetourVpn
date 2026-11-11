package org.example.vpnpoc;

import org.junit.Test;
import static org.junit.Assert.*;

public class NativeRenewalTest {
    static final String C = "11111111-1111-1111-1111-111111111111";
    static final String R = "22222222-2222-2222-2222-222222222222";
    static final String W = "33333333-3333-3333-3333-333333333333";

    static NativeRenewal plan(int seconds) {
        return new NativeRenewal(C, R, W, 1, 16777216, seconds);
    }

    @Test public void longWindowsRenewAfterAThirdAndShortOnesKeepLegacyThreshold() {
        assertEquals(600000, plan(900).renewBeforeMs());
        assertEquals(55000, plan(90).renewBeforeMs());
        NativeRenewal p = plan(900);
        assertFalse(p.due(600001, -1));
        assertTrue(p.due(600000, -1));
        assertEquals(1000, p.idleDelayMs(600500));
        assertEquals(300000, p.idleDelayMs(900000));
    }

    @Test public void halfTheBudgetTriggersRenewalAndCounterResetRebaselines() {
        NativeRenewal p = plan(900);
        assertFalse(p.due(890000, 1000));
        assertFalse(p.due(890000, 1000 + 8388607));
        assertTrue(p.due(890000, 1000 + 8388608));
        assertFalse(p.due(890000, 10)); // reset counter starts a fresh baseline
    }

    @Test public void intentKeyIsStableAcrossRetries() {
        NativeRenewal p = plan(900);
        String key = p.beginIntent(1000);
        assertEquals(key, p.beginIntent(5000));
        assertEquals(1000, p.intentAtElapsed);
        assertTrue(p.due(899000, -1));
    }

    @Test public void backoffGrowsIsCappedAndNeverPassesTheSafetyMargin() {
        NativeRenewal p = plan(900);
        assertEquals(1000, p.backoffMs(500000, 0));
        assertEquals(2000, p.backoffMs(500000, 0));
        for (int i = 0; i < 20; i++) p.backoffMs(500000, 0.999);
        assertTrue(p.backoffMs(500000, 0.999) <= NativeRenewal.MAX_BACKOFF_MS);
        assertEquals(4000, p.backoffMs(10000, 0.5));
        assertEquals(500, p.backoffMs(6100, 0.5));
    }

    @Test public void adoptsOnlyTheNextWindowOfThisIntent() {
        NativeRenewal p = plan(900);
        long wall = NativeRenewal.parseUtcMillis("2026-11-10T21:00:00Z");
        String key = p.beginIntent(100000);
        long end = p.adopt(C, R, key, 2, "2026-11-10T21:00:00.000000Z",
                "2026-11-10T21:15:00Z", 101000, wall);
        assertEquals(100000 + 900000 - 6000, end);
        assertThrows(IllegalStateException.class,
                () -> p.adopt(C, R, key, 3, "2026-11-10T21:00:00Z", "2026-11-10T21:15:00Z", 101000, wall));
        assertThrows(IllegalStateException.class,
                () -> p.adopt(C, W, key, 2, "2026-11-10T21:00:00Z", "2026-11-10T21:15:00Z", 101000, wall));
        assertThrows(IllegalStateException.class,
                () -> p.adopt(C, R, W, 2, "2026-11-10T21:00:00Z", "2026-11-10T21:15:00Z", 101000, wall));
        // Longer than requested, or a device clock more than 5 minutes off: refused.
        assertThrows(IllegalStateException.class,
                () -> p.adopt(C, R, key, 2, "2026-11-10T21:00:00Z", "2026-11-10T21:15:01Z", 101000, wall));
        assertThrows(IllegalStateException.class,
                () -> p.adopt(C, R, key, 2, "2026-11-10T20:00:00Z", "2026-11-10T20:10:00Z", 101000, wall));
        assertThrows(IllegalStateException.class,
                () -> p.adopt(C, R, key, 2, "2026-11-10T21:05:01Z", "2026-11-10T21:15:00Z", 101000, wall));
        // ADR-123: a clock a few seconds or minutes off still renews; the deadline is monotonic.
        assertEquals(100000 + 595000 - 6000, p.adopt(C, R, key, 2, "2026-11-10T21:00:05Z",
                "2026-11-10T21:10:00Z", 101000, wall));
        assertEquals(100000 + 900000 - 6000, p.adopt(C, R, key, 2, "2026-11-10T20:56:00Z",
                "2026-11-10T21:11:00Z", 101000, wall));
        p.adopted(2, key, 42);
        assertEquals(2, p.generation);
        assertNull(p.intentKey);
        assertEquals(0, p.failures);
    }

    @Test public void parsesOnlyUtcTimestamps() {
        assertEquals(1789074000190L, NativeRenewal.parseUtcMillis("2026-09-10T21:00:00.190912Z"));
        assertEquals(1789074000000L, NativeRenewal.parseUtcMillis("2026-09-10T21:00:00+00:00"));
        assertThrows(IllegalStateException.class,
                () -> NativeRenewal.parseUtcMillis("2026-09-10T21:00:00+03:00"));
        assertThrows(IllegalStateException.class,
                () -> NativeRenewal.parseUtcMillis("2026-02-30T21:00:00Z"));
        assertThrows(IllegalStateException.class, () -> NativeRenewal.parseUtcMillis(null));
    }

    @Test public void rejectsInvalidConfiguration() {
        assertThrows(IllegalArgumentException.class,
                () -> new NativeRenewal("x", R, W, 1, 16777216, 900));
        assertThrows(IllegalArgumentException.class,
                () -> new NativeRenewal(C, R, W, 1, 1024, 900));
        assertThrows(IllegalArgumentException.class,
                () -> new NativeRenewal(C, R, W, 1, 16777216, 901));
    }

    /** ADR-118: retries inside one wake-up, bounded by count, time and the window margin. */
    @Test public void burstRetriesStayInsideTheWakeUp() {
        assertEquals(2000, NativeRenewal.burstDelayMs(1, 0, 600000));
        assertEquals(4000, NativeRenewal.burstDelayMs(2, 5000, 600000));
        assertEquals(8000, NativeRenewal.burstDelayMs(3, 15000, 600000));
        // Fourth attempt was the last one.
        assertEquals(-1, NativeRenewal.burstDelayMs(4, 20000, 600000));
        // No attempt may start at or after the 30 s budget.
        assertEquals(-1, NativeRenewal.burstDelayMs(1, 28000, 600000));
        assertEquals(2000, NativeRenewal.burstDelayMs(1, 27999, 600000));
        // Never sleep into the 6 s safety margin of the window.
        assertEquals(-1, NativeRenewal.burstDelayMs(1, 0, 8000));
        assertEquals(2000, NativeRenewal.burstDelayMs(1, 0, 8001));
        // Nonsense input never yields a retry.
        assertEquals(-1, NativeRenewal.burstDelayMs(0, 0, 600000));
        assertEquals(-1, NativeRenewal.burstDelayMs(1, -1, 600000));
    }

    /** ADR-121: restore-engine wake-ups every 4 minutes, never into the window's safety margin. */
    @Test public void restoreWakeUpsStayInsideTheWindow() {
        assertEquals(240000, NativeRenewal.restoreWakeDelayMs(900000));
        assertEquals(240000, NativeRenewal.restoreWakeDelayMs(246000));
        assertEquals(100000, NativeRenewal.restoreWakeDelayMs(106000));
        assertEquals(1000, NativeRenewal.restoreWakeDelayMs(7000));
        assertEquals(-1, NativeRenewal.restoreWakeDelayMs(6999));
        assertEquals(-1, NativeRenewal.restoreWakeDelayMs(0));
    }
}
