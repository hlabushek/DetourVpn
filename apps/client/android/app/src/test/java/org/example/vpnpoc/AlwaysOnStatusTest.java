package org.example.vpnpoc;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertNotEquals;
import static org.junit.Assert.assertNull;

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

    /**
     * ADR-141: Android answers isAlwaysOn() == false whenever this app's VPN is not established
     * (Always-on start before establish(), onDestroy after the tunnel stopped). Such a read must
     * never become "off": that was the false «Постоянная VPN выключена» hint on beta.33.
     */
    @Test public void readWithoutEstablishedVpnIsNotAReading() {
        assertNull(AlwaysOnStatus.reading(true, false, false, false));
        assertNull(AlwaysOnStatus.reading(true, false, true, true));
        assertEquals("on", AlwaysOnStatus.reading(true, true, true, false));
        assertEquals("lockdown", AlwaysOnStatus.reading(true, true, true, true));
        assertEquals("off", AlwaysOnStatus.reading(true, true, false, false));
        assertEquals("unknown", AlwaysOnStatus.reading(false, false, false, false));
        assertEquals("unknown", AlwaysOnStatus.reading(false, true, true, false));
    }

    /** ADR-141: the store moved, so a false "off" persisted by beta.33 is never read back. */
    @Test public void legacyStoreKeyIsNotTheCurrentOne() {
        assertNotEquals(AlwaysOnStatus.LEGACY_PREF_KEY, AlwaysOnStatus.PREF_KEY);
        assertEquals("always_on", AlwaysOnStatus.LEGACY_PREF_KEY);
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
