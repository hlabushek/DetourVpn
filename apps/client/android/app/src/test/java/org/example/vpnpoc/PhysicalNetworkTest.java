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
