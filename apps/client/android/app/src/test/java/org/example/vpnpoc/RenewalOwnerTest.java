package org.example.vpnpoc;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import org.junit.Test;

/** ADR-136/140: one renewal owner per window, and the native transport choice. */
public class RenewalOwnerTest {
    @Test public void transportIsDirectWithoutLockdownAndProtectedOnlyWithAnInstance() {
        assertEquals(RenewalOwner.Transport.DIRECT, RenewalOwner.transport(false, false));
        assertEquals("no protection needed: the app is outside its own VPN",
                RenewalOwner.Transport.DIRECT, RenewalOwner.transport(false, true));
        assertEquals(RenewalOwner.Transport.PROTECTED, RenewalOwner.transport(true, true));
        assertEquals("Lockdown without a VpnService instance has no way out",
                RenewalOwner.Transport.NONE, RenewalOwner.transport(true, false));
    }

    @Test public void uiChannelMayNotStartNativeWhileAnUnadoptedRestoreEngineLives() {
        assertTrue(RenewalOwner.nativeMayStart(false, false, false, false));
        assertFalse("adopt the restore engine first",
                RenewalOwner.nativeMayStart(false, true, false, true));
        assertFalse("UI-started tunnel under Lockdown: renewal needs the app open",
                RenewalOwner.nativeMayStart(false, false, true, false));
        assertTrue(RenewalOwner.nativeMayStart(false, false, true, true));
    }

    /** ADR-140: after a kill, Always-on start or boot, renewal no longer depends on Dart timers. */
    @Test public void restoreEngineMayHandRenewalToNative() {
        assertTrue(RenewalOwner.nativeMayStart(true, true, false, true));
        assertTrue("restore under Lockdown uses the protected path",
                RenewalOwner.nativeMayStart(true, true, true, true));
        assertFalse(RenewalOwner.nativeMayStart(true, true, true, false));
    }

    @Test public void dartOnlyObservesWhileNativeRenews() {
        assertTrue(RenewalOwner.dartRenews(true, false));
        assertFalse(RenewalOwner.dartRenews(true, true));
        assertFalse(RenewalOwner.dartRenews(false, false));
    }

    @Test public void everyHandOverLeavesExactlyOneOwner() {
        // Restore engine alive and renewing: single owner.
        assertEquals(1, RenewalOwner.owners(false, false, RenewalOwner.dartRenews(true, false)));
        // Restore engine hands to native: restore observes, native owns.
        assertEquals(1, RenewalOwner.owners(true, false, RenewalOwner.dartRenews(true, true)));
        // UI adopts (restore engine destroyed) while native renews: the UI observes.
        assertEquals(1, RenewalOwner.owners(true, RenewalOwner.dartRenews(true, true), false));
        // App closed, no Lockdown: native renews alone.
        assertEquals(1, RenewalOwner.owners(true, false, false));
    }

    @Test public void anyTwoOwnersAreAConflict() {
        assertTrue(RenewalOwner.conflict(true, true, false));
        assertTrue(RenewalOwner.conflict(true, false, true));
        assertTrue(RenewalOwner.conflict(false, true, true));
        assertTrue(RenewalOwner.conflict(true, true, true));
        assertFalse(RenewalOwner.conflict(false, false, false));
    }
}
