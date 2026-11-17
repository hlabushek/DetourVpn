package org.example.vpnpoc;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertNotEquals;
import static org.junit.Assert.assertNull;
import static org.junit.Assert.assertTrue;

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
    /**
     * ADR-149 (R6): Lockdown for the renewal transport is read after establish(). A read before
     * establish() (Android answers false) must not reset a known Lockdown to false.
     */
    @Test public void lockdownIsTakenOnlyFromAReadWhileTheVpnIsEstablished() {
        // Restore start: the read before establish() proves nothing.
        String beforeEstablish = AlwaysOnStatus.reading(true, false, false, false);
        assertNull(beforeEstablish);
        assertTrue(AlwaysOnStatus.lockdownAfterRead(beforeEstablish, true));
        assertFalse(AlwaysOnStatus.lockdownAfterRead(beforeEstablish, false));
        // beginWindow(): the read after establish() sets it.
        String afterEstablish = AlwaysOnStatus.reading(true, true, true, true);
        assertTrue(AlwaysOnStatus.lockdownAfterRead(afterEstablish, false));
        // Then the renewal transport is the protected one, not the blocked direct path.
        assertEquals(RenewalOwner.Transport.PROTECTED, RenewalOwner.transport(
                AlwaysOnStatus.lockdownAfterRead(afterEstablish, false), true));
    }

    @Test public void lockdownClearsOnlyOnAnEstablishedReadingWithoutIt() {
        assertFalse(AlwaysOnStatus.lockdownAfterRead(AlwaysOnStatus.reading(true, true, true, false), true));
        assertFalse(AlwaysOnStatus.lockdownAfterRead(AlwaysOnStatus.reading(true, true, false, false), true));
        // Before API 29 the setting cannot be read: never Lockdown (unchanged behaviour).
        assertFalse(AlwaysOnStatus.lockdownAfterRead(AlwaysOnStatus.reading(false, true, true, true), true));
        // After the tunnel went down (onDestroy) the last valid reading is kept.
        assertTrue(AlwaysOnStatus.lockdownAfterRead(AlwaysOnStatus.reading(true, false, false, false), true));
    }
}
