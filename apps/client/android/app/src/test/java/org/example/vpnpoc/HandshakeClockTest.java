package org.example.vpnpoc;

import static org.junit.Assert.assertEquals;

import org.junit.Test;

/** ADR-160: handshake ages are monotonic deltas; the wall clock is read once per handshake. */
public class HandshakeClockTest {
    static final long WALL = 1_800_000_000_000L; // TEST ONLY wall clock (ms)
    static final long ELAPSED = 50_000_000L;      // TEST ONLY elapsedRealtime (ms)
    static final long HOUR = 3_600_000L;

    @Test public void noHandshakeIsMinusOne() {
        HandshakeClock clock = new HandshakeClock();
        assertEquals(-1, clock.ageMs(0, WALL, ELAPSED));
        assertEquals(-1, clock.ageMs(-3, WALL, ELAPSED));
        assertEquals(-1, clock.ageSeconds(0, WALL, ELAPSED));
    }

    @Test public void withoutAClockChangeTheAgeMatchesTheWallClockRule() {
        HandshakeClock clock = new HandshakeClock();
        long handshake = WALL / 1000 - 30;
        assertEquals(30, clock.ageSeconds(handshake, WALL, ELAPSED));
        assertEquals(90, clock.ageSeconds(handshake, WALL + 60_000, ELAPSED + 60_000));
    }

    @Test public void aForwardClockJumpDoesNotAgeTheHandshake() {
        HandshakeClock clock = new HandshakeClock();
        long handshake = WALL / 1000 - 10;
        assertEquals(10_000, clock.ageMs(handshake, WALL, ELAPSED));
        // The wall clock jumps forward one hour; only 5 s really passed.
        assertEquals(15_000, clock.ageMs(handshake, WALL + HOUR + 5_000, ELAPSED + 5_000));
    }

    @Test public void aBackwardClockJumpDoesNotHideTheHandshake() {
        HandshakeClock clock = new HandshakeClock();
        long handshake = WALL / 1000 - 10;
        assertEquals(10_000, clock.ageMs(handshake, WALL, ELAPSED));
        // The wall clock jumps back one hour: before, the handshake was "in the future" (= none).
        assertEquals(12_000, clock.ageMs(handshake, WALL - HOUR + 2_000, ELAPSED + 2_000));
    }

    @Test public void aNewHandshakeIsAnchoredAgain() {
        HandshakeClock clock = new HandshakeClock();
        clock.ageMs(WALL / 1000 - 100, WALL, ELAPSED);
        // After a forward jump, the engine reports a fresh handshake in the new wall frame.
        long jumped = WALL + HOUR;
        assertEquals(1_000, clock.ageMs(jumped / 1000 - 1, jumped, ELAPSED + 3_000));
        assertEquals(4_000, clock.ageMs(jumped / 1000 - 1, jumped + 3_000, ELAPSED + 6_000));
    }

    @Test public void aFutureHandshakeAtFirstSightIsUnknownUntilTheNextOne() {
        HandshakeClock clock = new HandshakeClock();
        long future = WALL / 1000 + 600;
        assertEquals(-1, clock.ageMs(future, WALL, ELAPSED));
        assertEquals(-1, clock.ageMs(future, WALL + 700_000, ELAPSED + 1_000));
        assertEquals(2_000, clock.ageMs(WALL / 1000 - 2, WALL, ELAPSED + 2_000));
    }

    @Test public void aTunnelWithoutHandshakeResetsTheAnchor() {
        HandshakeClock clock = new HandshakeClock();
        long handshake = WALL / 1000 - 5;
        clock.ageMs(handshake, WALL, ELAPSED);
        assertEquals(-1, clock.ageMs(0, WALL, ELAPSED + 1_000));
        assertEquals(5_000, clock.ageMs(handshake, WALL, ELAPSED + 2_000));
    }

    /**
     * Acceptance test: a wall-clock jump does not make the watchdog see a stale handshake. The
     * tunnel has been up longer than STALE_MS; its last handshake is really 150 s old, but the
     * wall clock jumped two hours forward meanwhile.
     */
    @Test public void aClockJumpDoesNotTriggerAStaleHandshakeRestart() {
        HandshakeClock clock = new HandshakeClock();
        TunnelWatchdog watchdog = new TunnelWatchdog();
        long up = ELAPSED - 60_000;
        watchdog.tunnelUp(up);
        long handshake = WALL / 1000;
        assertEquals(0, clock.ageMs(handshake, WALL, ELAPSED));
        long now = ELAPSED + 150_000;
        long jumpedWall = WALL + 2 * HOUR + 150_000;
        long age = clock.ageMs(handshake, jumpedWall, now);
        assertEquals(150_000, age);
        assertEquals(TunnelWatchdog.Action.NONE, watchdog.decide(now, age, true));

        // Contrast: the former per-read wall rule gives a two-hour age, which the same watchdog
        // state reads as stale and restarts.
        TunnelWatchdog before = new TunnelWatchdog();
        before.tunnelUp(up);
        long wallRuleAge = (jumpedWall / 1000 - handshake) * 1000;
        assertEquals(TunnelWatchdog.Action.RESTART, before.decide(now, wallRuleAge, true));
    }
}
