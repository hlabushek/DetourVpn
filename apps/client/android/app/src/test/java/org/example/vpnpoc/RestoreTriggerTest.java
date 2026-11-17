package org.example.vpnpoc;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import org.junit.Test;

/** ADR-098/149: boot and app-update restore use one gate. */
public class RestoreTriggerTest {
    private static final String BOOT = "android.intent.action.BOOT_COMPLETED";
    private static final String UPDATE = "android.intent.action.MY_PACKAGE_REPLACED";

    @Test public void bothBroadcastsRestoreWhenTheUserLeftTheVpnConnected() {
        assertEquals(RestoreTrigger.Decision.RESTORE, RestoreTrigger.decide(BOOT, true, true));
        assertEquals(RestoreTrigger.Decision.RESTORE, RestoreTrigger.decide(UPDATE, true, true));
    }

    /** 6.3: an update must not start a VPN the user had disconnected. */
    @Test public void anUpdateAfterAManualDisconnectStartsNothing() {
        assertEquals(RestoreTrigger.Decision.NOT_WANTED, RestoreTrigger.decide(UPDATE, false, true));
        assertEquals(RestoreTrigger.Decision.NOT_WANTED, RestoreTrigger.decide(UPDATE, false, false));
        assertEquals(RestoreTrigger.Decision.NOT_WANTED, RestoreTrigger.decide(BOOT, false, true));
    }

    @Test public void revokedConsentClearsTheIntentAndStartsNothing() {
        assertEquals(RestoreTrigger.Decision.CLEAR_INTENT, RestoreTrigger.decide(UPDATE, true, false));
        assertEquals(RestoreTrigger.Decision.CLEAR_INTENT, RestoreTrigger.decide(BOOT, true, false));
    }

    @Test public void otherActionsAreIgnored() {
        for (String action : new String[] {null, "", "android.intent.action.LOCKED_BOOT_COMPLETED",
                "android.intent.action.PACKAGE_REPLACED", "org.amnezia.awg.backend.action.RESTORE",
                "android.intent.action.boot_completed"}) {
            assertFalse(action, RestoreTrigger.restoreAction(action));
            assertEquals(action, RestoreTrigger.Decision.IGNORE, RestoreTrigger.decide(action, true, true));
        }
        assertTrue(RestoreTrigger.restoreAction(BOOT));
        assertTrue(RestoreTrigger.restoreAction(UPDATE));
    }
}
