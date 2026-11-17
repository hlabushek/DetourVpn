package org.example.vpnpoc;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import org.junit.Test;

public class ConnectionEventLogTest {
    @Test public void keepsOnlyTheNewestEntriesUpToCapacity() {
        var log = new ConnectionEventLog();
        for (int i = 0; i < ConnectionEventLog.CAPACITY + 25; i++) log.record(i, "tick");
        assertEquals(ConnectionEventLog.CAPACITY, log.size());
        assertEquals(25, log.dropped());
        assertEquals(25, log.entries().get(0).at);
    }

    @Test public void onlyCodesAreRecordedNeverFreeText() {
        var log = new ConnectionEventLog();
        log.record(0, "network", "10.0.0.1");
        log.record(1, "Renew Failed", "token=abc");
        log.record(2, "stopped", "lease_expired");
        var entries = log.entries();
        assertEquals("invalid", entries.get(0).detail);
        assertEquals("invalid", entries.get(1).event);
        assertEquals("invalid", entries.get(1).detail);
        assertEquals("lease_expired", entries.get(2).detail);
        String text = log.format(3_000, 200);
        assertFalse(text.contains("10.0.0.1"));
        assertFalse(text.contains("abc"));
    }

    @Test public void formatsAgesRelativeToNowAndHonoursTheLimit() {
        var log = new ConnectionEventLog();
        log.record(1_000, "owner", "native");
        log.record(61_000, "stopped", "service_died");
        log.record(62_000, "recovery_start");
        String all = log.format(122_000, 10);
        assertTrue(all.contains("-121s owner native"));
        assertTrue(all.contains("-61s stopped service_died"));
        assertTrue(all.endsWith("-60s recovery_start"));
        String last = log.format(122_000, 1);
        assertEquals("\n-60s recovery_start", last);
    }
}
