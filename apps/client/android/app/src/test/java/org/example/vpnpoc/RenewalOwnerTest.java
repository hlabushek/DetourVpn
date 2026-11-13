package org.example.vpnpoc;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import org.junit.Test;

/** ADR-136: one renewal owner per window. */
public class RenewalOwnerTest {
    @Test public void nativeRenewalMayStartOnlyWithoutRestoreEngineAndLockdown() {
        assertTrue(RenewalOwner.nativeMayStart(false, false));
        assertFalse("a restore engine owns renewal", RenewalOwner.nativeMayStart(true, false));
        assertFalse("Lockdown keeps renewal in Dart", RenewalOwner.nativeMayStart(false, true));
        assertFalse(RenewalOwner.nativeMayStart(true, true));
    }

    @Test public void theUiAdoptionSequenceNeverHasTwoOwners() {
        // Restore engine alive: Dart restore is the single owner.
        assertEquals(1, RenewalOwner.owners(false, false, true));
        // Hand-over: adoptRestore destroys the restore engine first, then the UI and native take over.
        assertFalse(RenewalOwner.conflict(false, true, false));
        assertEquals("after the hand-over native is the single owner, the UI only observes", 1,
                RenewalOwner.owners(true, false, false));
    }

    @Test public void anyTwoOwnersAreAConflict() {
        assertTrue(RenewalOwner.conflict(true, true, false));
        assertTrue(RenewalOwner.conflict(true, false, true));
        assertTrue(RenewalOwner.conflict(false, true, true));
        assertTrue(RenewalOwner.conflict(true, true, true));
        assertFalse(RenewalOwner.conflict(false, false, false));
    }
}
