package org.example.vpnpoc;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertNull;
import static org.junit.Assert.assertTrue;

import java.util.Set;
import org.junit.Test;

/** ADR-146: the default-network callback decisions and the 8 s loss timer, with a fake clock. */
public class NetworkCallbacksTest {
    private final PhysicalNetwork physical = new PhysicalNetwork();
    private final NetworkCallbacks callbacks = new NetworkCallbacks(physical);

    /** The state a running, healthy tunnel shows at {@code now} with the current network facts. */
    private String stateAt(long now) {
        var f = new ConnectionState.Facts();
        f.desired = true;
        f.tunnelUp = true;
        f.windowAgeMs = 600_000;
        f.handshakeAgeSeconds = 30;
        f.networkUsable = physical.usable();
        f.networkLossPending = physical.lossPending(now);
        return ConnectionState.of(f).toString();
    }

    /** Fires the armed loss timer if it is due by {@code now} (what the worker scheduler does). */
    private boolean runTimerAt(long now) {
        return callbacks.lossTimerDue(now) && callbacks.lossTimerFired();
    }

    @Test public void aLossThatLastsEightSecondsMarksTheTunnelRecovering() {
        callbacks.available("wifi", true, false, true);
        var lost = callbacks.lost("wifi", 1_000);
        assertTrue(lost.armLossTimer);
        assertTrue(lost.clearDefault);
        assertTrue(lost.publishState);
        assertEquals("network_lost", lost.log);
        assertEquals(1_000 + NetworkCallbacks.LOSS_TIMER_MS, 9_050);

        assertEquals("connected:verified", stateAt(5_000));
        assertFalse("not due yet", callbacks.lossTimerDue(9_000));
        assertTrue("the timer fires and confirms the loss", runTimerAt(9_050));
        assertFalse(callbacks.lossTimerArmed());
        assertEquals("recovering:waiting_for_network", stateAt(9_050));
    }

    @Test public void onAvailableBeforeEightSecondsCancelsTheLossTimer() {
        callbacks.available("wifi", true, false, true);
        callbacks.lost("wifi", 1_000);
        var back = callbacks.available("cell", true, false, true);
        assertTrue(back.cancelLossTimer);
        assertFalse(callbacks.lossTimerArmed());
        assertFalse("nothing fires at 8 s", runTimerAt(9_050));
        assertEquals("connected:verified", stateAt(9_050));
        assertTrue("another network is a new path", back.pathChanged);
        assertTrue(back.underlyingStale);
        assertTrue(back.freshSignal);
    }

    @Test public void aShorterLossNeverConfirms() {
        callbacks.available("wifi", true, false, true);
        callbacks.lost("wifi", 1_000);
        assertEquals("connected:verified", stateAt(3_000));
        callbacks.available("wifi", true, false, true); // the same network back after 2 s
        assertFalse(callbacks.lossTimerDue(100_000));
        // Even a task that already started (cancel race) does not confirm a returned network.
        assertFalse(callbacks.lossTimerFired());
        assertEquals("connected:verified", stateAt(9_050));
    }

    @Test public void aRepeatedLossReArmsAndAnOlderNetworkLossIsIgnored() {
        callbacks.available("wifi", true, false, true);
        callbacks.lost("wifi", 1_000);
        var again = callbacks.lost("wifi", 2_000);
        assertTrue("the old task is cancelled", again.cancelLossTimer);
        assertTrue(again.armLossTimer);
        callbacks.available("cell", true, false, true);
        var stale = callbacks.lost("wifi", 3_000);
        assertNull(stale.log);
        assertFalse(stale.armLossTimer);
        assertFalse(stale.clearDefault);
        assertTrue(physical.usable());
    }

    @Test public void theSameNetworkAgainOnlySettlesTheWatch() {
        callbacks.available("wifi", true, false, true);
        var same = callbacks.available("wifi", true, false, true);
        assertEquals("same", same.logDetail);
        assertFalse(same.pathChanged);
        assertTrue(same.settleWatch);
        assertFalse(same.underlyingStale);
        assertFalse(same.freshSignal);
        assertTrue(same.evaluateRecovery);
    }

    @Test public void theFirstNetworkAfterAnOfflineStartSendsAFreshSignal() {
        var first = callbacks.available("wifi", true, false, true);
        assertEquals("same", first.logDetail);
        assertTrue(first.underlyingStale);
        assertTrue("it became usable", first.freshSignal);
    }

    @Test public void capabilitiesThatMakeTheNetworkUsableRecover() {
        callbacks.available("hotel", true, true, false);
        var other = callbacks.capabilities("wifi", true, false, true);
        assertNull("another network's capabilities are ignored", other.log);
        var signedIn = callbacks.capabilities("hotel", true, false, true);
        assertEquals("network_usable", signedIn.log);
        assertTrue(signedIn.settleWatch);
        assertTrue(signedIn.evaluateRecovery);
        assertTrue(signedIn.freshSignal);
        var again = callbacks.capabilities("hotel", true, false, true);
        assertFalse("no repeat signal while it stays usable", again.freshSignal);
    }

    @Test public void aNewAddressOnTheSameNetworkIsAPathChange() {
        callbacks.available("cell", true, false, true);
        assertFalse(callbacks.linkAddresses("cell", Set.of("10.0.0.2")).pathChanged);
        var moved = callbacks.linkAddresses("cell", Set.of("10.0.0.9"));
        assertTrue(moved.pathChanged);
        assertEquals("network_address_changed", moved.log);
    }

    /** ADR-148 (5.3): CAPTIVE_PORTAL without VALIDATED: the hint, and no restart or attempt. */
    @Test public void aCaptivePortalPublishesTheHintAndStartsNothing() {
        callbacks.available("wifi", true, false, true);
        var portal = callbacks.capabilities("wifi", true, true, false);
        assertEquals("network_captive_portal", portal.log);
        assertTrue(portal.publishState);
        assertFalse(portal.freshSignal);
        assertFalse(portal.evaluateRecovery);
        assertFalse(portal.settleWatch);
        assertFalse(portal.pathChanged);
        assertTrue(physical.captivePortalBlocking());
        assertFalse("no restart, probe or recovery attempt runs", physical.usable());
        var f = new ConnectionState.Facts();
        f.desired = true;
        f.tunnelUp = true;
        f.windowAgeMs = 600_000;
        f.handshakeAgeSeconds = 30;
        f.networkUsable = physical.usable();
        f.captivePortal = physical.captivePortalBlocking();
        assertEquals("recovering:captive_portal", ConnectionState.of(f).toString());
        // Signed in: usable again; recovery resumes at once.
        var signedIn = callbacks.capabilities("wifi", true, false, true);
        assertEquals("network_usable", signedIn.log);
        assertTrue(signedIn.freshSignal);
        assertFalse(physical.captivePortalBlocking());
    }

    @Test public void aValidatedNetworkWithAPortalFlagIsNotBlocking() {
        callbacks.available("wifi", true, true, true);
        assertFalse(physical.captivePortalBlocking());
    }
}
