package org.example.vpnpoc;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import java.util.Set;
import org.junit.Test;

public class PhysicalNetworkTest {
    @Test public void firstNetworkIsNotAChangeButIsUsable() {
        PhysicalNetwork net = new PhysicalNetwork();
        assertFalse(net.usable());
        assertFalse(net.available("wifi", true, false, true));
        assertTrue(net.usable());
        assertTrue(net.validated());
        assertEquals(0, net.generation());
    }

    @Test public void wifiToCellularIsAChange() {
        PhysicalNetwork net = new PhysicalNetwork();
        net.available("wifi", true, false, true);
        assertTrue(net.available("cell", true, false, true));
        assertEquals(1, net.generation());
    }

    @Test public void completeLossThenReturnOfSameNetworkIsAChange() {
        PhysicalNetwork net = new PhysicalNetwork();
        net.available("wifi", true, false, true);
        net.lost("wifi");
        assertFalse("offline: nothing to recover over", net.usable());
        assertTrue(net.available("wifi", true, false, true));
        assertTrue(net.usable());
    }

    @Test public void staleLossOfAnOlderNetworkIsIgnored() {
        PhysicalNetwork net = new PhysicalNetwork();
        net.available("wifi", true, false, true);
        net.available("cell", true, false, true);
        net.lost("wifi"); // late callback for the previous network
        assertTrue(net.usable());
    }

    @Test public void unvalidatedNetworkIsStillUsable() {
        // The system connectivity check can be blocked while the tunnel endpoint is reachable.
        PhysicalNetwork net = new PhysicalNetwork();
        net.available("cell", true, false, false);
        assertTrue(net.usable());
        assertFalse(net.validated());
    }

    @Test public void captivePortalAndNoInternetAreNotUsable() {
        PhysicalNetwork net = new PhysicalNetwork();
        net.available("hotel", true, true, false);
        assertFalse(net.usable());
        assertTrue(net.captivePortal());
        assertTrue("signing in to the portal makes it usable",
                net.capabilities("hotel", true, false, true));
        PhysicalNetwork none = new PhysicalNetwork();
        none.available("wifi", false, false, false);
        assertFalse(none.usable());
    }

    @Test public void capabilitiesOfAnotherNetworkAreIgnored() {
        PhysicalNetwork net = new PhysicalNetwork();
        net.available("cell", true, false, true);
        assertFalse(net.capabilities("wifi", false, true, false));
        assertTrue(net.usable());
    }

    @Test public void addressChangeOnTheSameNetworkIsAChange() {
        PhysicalNetwork net = new PhysicalNetwork();
        net.available("cell", true, false, true);
        assertFalse("first reading only learns", net.linkAddresses("cell", Set.of("10.0.0.2")));
        assertFalse(net.linkAddresses("cell", Set.of("10.0.0.2")));
        assertTrue(net.linkAddresses("cell", Set.of("10.0.0.9")));
        assertEquals(1, net.generation());
        assertFalse("an empty reading is not a change", net.linkAddresses("cell", Set.of()));
    }

    /** ADR-146 (4.2): a loss is unusable at once but confirmed only after 8 s. */
    @Test public void aLossIsConfirmedAfterEightSecondsUnlessANetworkReturns() {
        PhysicalNetwork net = new PhysicalNetwork();
        net.available("wifi", true, false, true);
        assertFalse(net.lossPending(0));
        assertTrue(net.lost("wifi", 1_000));
        assertFalse("no attempt runs while offline", net.usable());
        assertTrue(net.lossPending(1_000));
        assertTrue(net.lossPending(8_999));
        assertFalse("confirmed after 8 s", net.lossPending(9_000));
        assertTrue(net.lost("wifi", 9_500));
        assertFalse("a repeated loss does not restart the timer", net.lossPending(9_500));

        net.available("cell", true, false, true);
        assertTrue(net.lost("cell", 20_000));
        net.available("wifi", true, false, true); // onAvailable cancels the pending loss
        assertFalse(net.lossPending(21_000));
        assertTrue(net.usable());
        assertFalse("a late loss of an older network is ignored", net.lost("cell", 22_000));
        assertTrue(net.usable());
    }

    @Test public void rapidOscillationBumpsGenerationEachTime() {
        PhysicalNetwork net = new PhysicalNetwork();
        net.available("wifi", true, false, true);
        for (int i = 0; i < 10; i++) {
            net.available(i % 2 == 0 ? "cell" : "wifi", true, false, true);
        }
        assertEquals(10, net.generation());
        assertTrue(net.usable());
    }
}
