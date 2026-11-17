package org.example.vpnpoc;

import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import org.junit.Test;

public class DataPathWatchTest {
    private static final long MIN = 60_000;

    @Test public void unknownCountersChangeNothing() {
        var w = new DataPathWatch();
        w.observe(-1, 100, 0);
        w.observe(10, -1, 1000);
        assertFalse(w.flowing(1000));
        assertFalse(w.stalled(10 * MIN));
    }

    @Test public void inboundProgressIsFlowingForTheFlowWindowOnly() {
        var w = new DataPathWatch();
        w.observe(100, 150, 0);
        w.observe(120, 170, 10_000);
        assertTrue(w.flowing(10_000));
        assertTrue(w.flowing(10_000 + DataPathWatch.FLOW_MS));
        assertFalse(w.flowing(10_001 + DataPathWatch.FLOW_MS));
    }

    @Test public void idlePhoneIsNeverStalled() {
        var w = new DataPathWatch();
        w.observe(100, 150, 0);
        w.observe(100, 151, 10 * MIN); // only a few bytes (keepalive level)
        assertFalse("below the outbound threshold", w.stalled(10 * MIN));
    }

    @Test public void outboundWithoutInboundForTheWindowIsAStall() {
        var w = new DataPathWatch();
        w.observe(100, 150, 0);
        w.observe(100, 150 + 4000, 60_000);
        assertFalse("not long enough yet", w.stalled(60_000));
        w.observe(100, 150 + 9000, 110_000);
        assertFalse("two outbound samples, but FLOW_MS has not passed", w.stalled(119_999));
        assertTrue(w.stalled(DataPathWatch.FLOW_MS));
        assertFalse("a stall is not flowing", w.flowing(DataPathWatch.FLOW_MS));
    }

    @Test public void inboundProgressEndsAStall() {
        var w = new DataPathWatch();
        w.observe(100, 150, 0);
        w.observe(100, 150 + 20_000, 130_000);
        assertTrue(w.stalled(130_000));
        w.observe(101, 150 + 20_001 + 20_000, 140_000); // one inbound byte arrives
        assertFalse(w.stalled(140_000));
        assertTrue(w.flowing(140_000));
    }

    @Test public void neverReceivingAnythingIsAStallOnceOutboundAccumulates() {
        var w = new DataPathWatch();
        w.observe(0, 0, 0);
        w.observe(0, 9000, 5_000);
        assertTrue(w.stalled(DataPathWatch.FLOW_MS + 5_000));
        assertFalse(w.flowing(DataPathWatch.FLOW_MS + 5_000));
    }

    @Test public void counterResetStartsAFreshBaseline() {
        var w = new DataPathWatch();
        w.observe(500, 900, 0);
        w.observe(500, 900 + 20_000, 10_000);
        w.observe(10, 30, 20_000); // rx went backwards: tunnel restarted
        assertFalse(w.stalled(20_000 + DataPathWatch.FLOW_MS - 1));
        assertFalse(w.flowing(20_000));
    }

    @Test public void resetForgetsEverything() {
        var w = new DataPathWatch();
        w.observe(0, 0, 0);
        w.observe(0, 20_000, 1000);
        w.reset();
        assertFalse(w.stalled(10 * MIN));
        assertFalse(w.flowing(0));
    }

    /** ADR-148: the same rule after 60 s only makes a stall suspected (a probe decides). */
    @Test public void outboundWithoutInboundFor60SecondsIsSuspected() {
        var w = new DataPathWatch();
        w.observe(100, 150, 0);
        w.observe(100, 150 + 9000, 30_000);
        assertFalse(w.suspect(DataPathWatch.SUSPECT_MS - 1));
        assertTrue(w.suspect(DataPathWatch.SUSPECT_MS));
        assertFalse("not yet a stall", w.stalled(DataPathWatch.SUSPECT_MS));
        w.observe(100, 150 + 100, 10 * MIN); // counter reset: a fresh baseline
        assertFalse(w.suspect(20 * MIN));
    }
}
