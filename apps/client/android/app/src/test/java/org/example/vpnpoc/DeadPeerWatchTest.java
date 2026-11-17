package org.example.vpnpoc;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import org.junit.Test;

/** ADR-148 (Phase 5): data path fed from the tick, active probe and the escalation ladder. */
public class DeadPeerWatchTest {
    private static final DeadPeerWatch.Action NONE = DeadPeerWatch.Action.NONE;
    private static final DeadPeerWatch.Action PROBE = DeadPeerWatch.Action.PROBE;
    private static final DeadPeerWatch.ProbeResult PASS = DeadPeerWatch.ProbeResult.PASS;
    private static final DeadPeerWatch.ProbeResult FAIL = DeadPeerWatch.ProbeResult.FAIL;
    private static final DeadPeerWatch.ProbeResult UNAVAILABLE =
            DeadPeerWatch.ProbeResult.UNAVAILABLE;

    /** A healthy handshake age and an old window: only the data path can make it suspect. */
    private static final long FRESH = 30_000;
    private static final long OLD_WINDOW = 600_000;

    /** Outbound bytes grow on every tick, inbound never does: a silent drop after 60 s. */
    private long outbound = 1_000;

    private DeadPeerWatch.Action silentTick(DeadPeerWatch w, long now) {
        outbound += 10_000;
        return w.tick(now, 500, 500 + outbound, FRESH, OLD_WINDOW, true);
    }

    /** Ticks until the probe is asked for; returns its time. */
    private long untilProbe(DeadPeerWatch w, long from) {
        for (long now = from; now < from + 3_600_000; now += 5_000) {
            if (silentTick(w, now) == PROBE) return now;
        }
        throw new AssertionError("no probe");
    }

    /** Two failed probes from {@code from}; returns the escalation of the second. */
    private DeadPeerWatch.Action failTwice(DeadPeerWatch w, long from) {
        long first = untilProbe(w, from);
        assertEquals(NONE, w.probeResult(first + 1_000, w.probeId(), FAIL));
        long second = untilProbe(w, first + 1_000);
        assertTrue("the second probe follows quickly",
                second - first <= DeadPeerWatch.PROBE_RETRY_MS + 5_000);
        return w.probeResult(second + 1_000, w.probeId(), FAIL);
    }

    // --- 5.1: the data path is fed from the watch tick only ---

    @Test public void theDataPathIsFedOnlyByTheTick() {
        var w = new DeadPeerWatch(false, false);
        w.tick(0, 100, 150, FRESH, OLD_WINDOW, true);
        // Reads (what snapshot() does) add no sample: no amount of time and reads makes a stall.
        for (long now = 0; now <= 10 * 60_000; now += 1_000) {
            assertFalse(w.stalled(now));
            assertFalse(w.flowing(now));
        }
        // Ticks with outbound growth and no inbound: a stall after the flow window.
        w.tick(10_000, 100, 150 + 9_000, FRESH, OLD_WINDOW, true);
        assertFalse(w.stalled(DataPathWatch.FLOW_MS - 1));
        assertTrue(w.stalled(DataPathWatch.FLOW_MS));
        // Inbound progress on a tick: flowing again.
        w.tick(200_000, 101, 150 + 9_001, FRESH, OLD_WINDOW, true);
        assertTrue(w.flowing(200_000));
        assertFalse(w.stalled(200_000));
    }

    @Test public void aNewWindowForgetsTheDataPathButKeepsTheLadder() {
        var w = new DeadPeerWatch(true, false);
        assertEquals(DeadPeerWatch.Action.RESTART, failTwice(w, 0));
        w.tick(1_000_000, 100, 20_000, FRESH, OLD_WINDOW, true);
        w.windowStarted();
        assertFalse(w.stalled(10_000_000));
        assertEquals(DeadPeerWatch.STEP_RESTARTED, w.step());
    }

    // --- 5.2: when a probe runs ---

    @Test public void withoutAProbeUrlNothingIsProbedAndTheWatchdogKeepsStalls() {
        var w = new DeadPeerWatch(false, true);
        for (long now = 0; now < 30 * 60_000; now += 15_000) assertEquals(NONE, silentTick(w, now));
        assertFalse(w.ownsStallRecovery(0));
    }

    @Test public void outboundWithoutInboundFor60SecondsIsProbed() {
        var w = new DeadPeerWatch(true, false);
        assertTrue(w.ownsStallRecovery(0));
        assertEquals(NONE, w.tick(0, 500, 1_000, FRESH, OLD_WINDOW, true));
        assertEquals(NONE, w.tick(30_000, 500, 20_000, FRESH, OLD_WINDOW, true));
        assertEquals("not 60 s yet", NONE, w.tick(59_999, 500, 30_000, FRESH, OLD_WINDOW, true));
        assertEquals(PROBE, w.tick(60_000, 500, 40_000, FRESH, OLD_WINDOW, true));
        assertTrue(w.probeInFlight());
        assertEquals("one probe at a time", NONE,
                w.tick(61_000, 500, 50_000, FRESH, OLD_WINDOW, true));
    }

    @Test public void anIdleTunnelIsNeverProbed() {
        var w = new DeadPeerWatch(true, false);
        for (long now = 0; now < 30 * 60_000; now += 15_000)
            assertEquals(NONE, w.tick(now, 500, 1_000 + now / 15_000, FRESH, OLD_WINDOW, true));
    }

    @Test public void noHandshakeFor180SecondsIsProbed() {
        var w = new DeadPeerWatch(true, false);
        assertEquals(NONE, w.tick(0, 500, 1_000, 179_999, OLD_WINDOW, true));
        assertEquals(PROBE, w.tick(15_000, 500, 1_000, 180_000, OLD_WINDOW, true));
        var fresh = new DeadPeerWatch(true, false);
        assertEquals("a new window without a handshake yet", NONE,
                fresh.tick(0, 0, 0, -1, 179_999, true));
        assertEquals(PROBE, fresh.tick(15_000, 0, 0, -1, 180_000, true));
    }

    @Test public void noProbeWithoutAUsableNetworkOrBehindACaptivePortal() {
        var w = new DeadPeerWatch(true, true);
        for (long now = 0; now < 30 * 60_000; now += 15_000) {
            outbound += 10_000;
            assertEquals(NONE, w.tick(now, 500, 500 + outbound, 400_000, OLD_WINDOW, false));
        }
    }

    // --- 5.2: escalation order ---

    @Test public void aPassedProbeMeansHealthyAndNothingEscalates() {
        var w = new DeadPeerWatch(true, true);
        long at = untilProbe(w, 0);
        assertEquals(NONE, w.probeResult(at + 500, w.probeId(), PASS));
        assertEquals(DeadPeerWatch.STEP_NONE, w.step());
        // The pass holds for two minutes even though the counters still look silent.
        for (long now = at + 5_000; now < at + DeadPeerWatch.PASS_HOLD_MS; now += 5_000)
            assertEquals(NONE, silentTick(w, now));
    }

    @Test public void oneFailureThenAPassDoesNotEscalate() {
        var w = new DeadPeerWatch(true, true);
        long first = untilProbe(w, 0);
        assertEquals(NONE, w.probeResult(first + 500, w.probeId(), FAIL));
        long second = untilProbe(w, first + 500);
        assertEquals(NONE, w.probeResult(second + 500, w.probeId(), PASS));
        assertEquals(DeadPeerWatch.STEP_NONE, w.step());
    }

    @Test public void twoFailuresRestartThenNewLeaseThenStopWithoutOptIn() {
        var w = new DeadPeerWatch(true, false);
        assertEquals(DeadPeerWatch.Action.RESTART, failTwice(w, 0));
        assertEquals(DeadPeerWatch.Action.NEW_LEASE, failTwice(w, 100_000));
        w.windowStarted(); // the new lease's tunnel
        assertEquals("failover only when the owner opted in", NONE, failTwice(w, 300_000));
        assertEquals(DeadPeerWatch.STEP_EXHAUSTED, w.step());
        assertFalse("the watchdog takes stalls back", w.ownsStallRecovery(500_000));
    }

    @Test public void failoverIsTheThirdStepOnlyWhenOptedIn() {
        var w = new DeadPeerWatch(true, true);
        assertEquals(DeadPeerWatch.Action.RESTART, failTwice(w, 0));
        assertEquals(DeadPeerWatch.Action.NEW_LEASE, failTwice(w, 100_000));
        w.windowStarted();
        assertEquals(DeadPeerWatch.Action.FAILOVER, failTwice(w, 300_000));
        w.windowStarted();
        assertEquals("one failover per ladder", NONE, failTwice(w, 500_000));
        assertEquals(DeadPeerWatch.STEP_EXHAUSTED, w.step());
    }

    @Test public void eachStepGetsTimeBeforeTheNextProbe() {
        var w = new DeadPeerWatch(true, false);
        assertEquals(DeadPeerWatch.Action.RESTART, failTwice(w, 0)); // escalated at 67 s
        long next = untilProbe(w, 70_000); // the first probe after the restart
        assertTrue(w.probeInFlight());
        assertTrue("settle time after the restart",
                next >= 67_000 + DeadPeerWatch.STEP_SETTLE_MS);
    }

    @Test public void anExhaustedLadderProbesRarelyAndAPassStartsOver() {
        var w = new DeadPeerWatch(true, false);
        failTwice(w, 0);
        failTwice(w, 100_000);
        failTwice(w, 300_000);
        assertEquals(DeadPeerWatch.STEP_EXHAUSTED, w.step());
        long next = untilProbe(w, 400_000);
        assertEquals(NONE, w.probeResult(next + 500, w.probeId(), FAIL));
        long after = untilProbe(w, next + 500);
        assertTrue(after - next >= DeadPeerWatch.QUIET_PROBE_MS);
        assertEquals(NONE, w.probeResult(after + 500, w.probeId(), PASS));
        assertEquals(DeadPeerWatch.STEP_NONE, w.step());
        assertTrue(w.ownsStallRecovery(after + 500));
    }

    @Test public void anUnavailableProbeNeverEscalatesAndHandsStallsBack() {
        var w = new DeadPeerWatch(true, true);
        for (int i = 0; i < 3; i++) {
            long at = untilProbe(w, i * 1_000_000L);
            assertEquals(NONE, w.probeResult(at + 500, w.probeId(), UNAVAILABLE));
            assertFalse(w.ownsStallRecovery(at + 500));
            assertTrue(w.ownsStallRecovery(at + 500 + DeadPeerWatch.QUIET_PROBE_MS));
        }
        assertEquals(DeadPeerWatch.STEP_NONE, w.step());
    }

    @Test public void aLostResultCountsAsAFailure() {
        var w = new DeadPeerWatch(true, false);
        long at = untilProbe(w, 0);
        assertEquals(NONE, silentTick(w, at + DeadPeerWatch.LOST_RESULT_MS - 1));
        assertEquals(NONE, silentTick(w, at + DeadPeerWatch.LOST_RESULT_MS));
        assertFalse(w.probeInFlight());
        long second = untilProbe(w, at + DeadPeerWatch.LOST_RESULT_MS);
        assertEquals(DeadPeerWatch.Action.RESTART,
                w.probeResult(second + 500, w.probeId(), FAIL));
    }

    @Test public void aResultForAnOlderProbeIsIgnored() {
        var w = new DeadPeerWatch(true, false);
        long at = untilProbe(w, 0);
        long old = w.probeId();
        w.windowStarted();
        assertEquals(NONE, w.probeResult(at + 500, old, FAIL));
        long again = untilProbe(w, at + 500);
        assertEquals(NONE, w.probeResult(again + 500, old, FAIL));
        assertTrue("the current probe is still awaited", w.probeInFlight());
    }

    @Test public void aNetworkChangeOrAUserDisconnectStartsTheLadderOver() {
        var w = new DeadPeerWatch(true, true);
        failTwice(w, 0);
        assertEquals(DeadPeerWatch.STEP_RESTARTED, w.step());
        w.networkChanged();
        assertEquals(DeadPeerWatch.STEP_NONE, w.step());
        failTwice(w, 100_000);
        w.reset();
        assertEquals(DeadPeerWatch.STEP_NONE, w.step());
        assertFalse(w.probeInFlight());
    }
}
