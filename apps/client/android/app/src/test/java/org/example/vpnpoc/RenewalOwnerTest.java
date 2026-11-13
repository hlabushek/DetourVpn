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

    /**
     * ADR-139: the app-closed case. With the UI gone, native renewal is the only owner that keeps a
     * window alive without Dart; under Lockdown it is refused, so only the restore engine renews
     * (alarm wake-ups, Doze-limited). Never two owners.
     */
    @Test public void appClosedLeavesExactlyOneOwnerOrNone() {
        // App closed, no Lockdown: native renews alone.
        assertTrue(RenewalOwner.nativeMayStart(false, false));
        assertEquals(1, RenewalOwner.owners(true, false, false));
        // App closed under Lockdown: native refused; the restore engine is the only owner.
        assertFalse(RenewalOwner.nativeMayStart(true, true));
        assertEquals(1, RenewalOwner.owners(false, false, true));
        // App closed, Lockdown, no restore engine: no owner, the window ends (ADR-139 limit).
        assertFalse(RenewalOwner.nativeMayStart(false, true));
        assertEquals(0, RenewalOwner.owners(false, false, false));
    }

    @Test public void uiReopenOverNativeRenewalIsAConflictTheUiMustNotCreate() {
        // The reopened UI observes; it must not also renew while native renews.
        assertTrue(RenewalOwner.conflict(true, true, false));
        assertFalse(RenewalOwner.conflict(true, false, false));
    }
}
