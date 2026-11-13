package org.example.vpnpoc;

import static org.junit.Assert.assertEquals;

import org.junit.Test;

/** ADR-139: Always-on status shown to the user. */
public class AlwaysOnStatusTest {
    @Test public void mapsPlatformFactsToOneStatus() {
        assertEquals("unknown", AlwaysOnStatus.of(false, true, true));
        assertEquals("off", AlwaysOnStatus.of(true, false, false));
        assertEquals("off", AlwaysOnStatus.of(true, false, true));
        assertEquals("on", AlwaysOnStatus.of(true, true, false));
        assertEquals("lockdown", AlwaysOnStatus.of(true, true, true));
    }

    @Test public void storedValuesAreSanitized() {
        assertEquals("on", AlwaysOnStatus.sanitize("on"));
        assertEquals("lockdown", AlwaysOnStatus.sanitize("lockdown"));
        assertEquals("off", AlwaysOnStatus.sanitize("off"));
        assertEquals("unknown", AlwaysOnStatus.sanitize(null));
        assertEquals("unknown", AlwaysOnStatus.sanitize("ON"));
        assertEquals("unknown", AlwaysOnStatus.sanitize(""));
    }
}
