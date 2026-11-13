package org.example.vpnpoc;

import static org.junit.Assert.assertArrayEquals;
import static org.junit.Assert.assertEquals;
import static org.junit.Assert.fail;

import java.util.Arrays;
import java.util.Collections;
import java.util.List;
import org.junit.Test;

/**
 * ADR-137 (E): route and DNS choices of the local libbox config. The JSON assembly itself is not
 * unit-tested (org.json is an Android stub on the JVM); these are the values it is built from.
 */
public class RoutePlanTest {
    @Test public void tunAlwaysCapturesIpv4AndIpv6() {
        // IPv4-only profile or dual-stack profile: the TUN route set does not depend on the profile.
        assertArrayEquals(new String[] {"0.0.0.0/0", "::/0"}, RoutePlan.ROUTE_ADDRESSES);
    }

    @Test public void dnsStrategyStaysIpv4OnlyEvenWithIpv6InTheProfile() {
        assertEquals("ipv4_only", RoutePlan.DNS_STRATEGY);
    }

    @Test public void ipv4OnlyProfileUsesItsOnlyDnsServer() {
        assertEquals("10.0.0.53", RoutePlan.dnsUpstream(List.of("10.0.0.53")));
    }

    @Test public void dualStackProfileUsesTheFirstDnsServerOnly() {
        assertEquals("1.1.1.1", RoutePlan.dnsUpstream(Arrays.asList("1.1.1.1", "2606:4700:4700::1111")));
    }

    @Test public void ipv6FirstProfileUsesTheIpv6ServerWhenItIsListedFirst() {
        assertEquals("2606:4700:4700::1111",
                RoutePlan.dnsUpstream(Arrays.asList("2606:4700:4700::1111", "1.1.1.1")));
    }

    @Test public void profileWithoutDnsIsRejected() {
        assertRejected(Collections.emptyList());
        assertRejected(null);
        assertRejected(Arrays.asList("", "1.1.1.1"));
    }

    private static void assertRejected(List<String> servers) {
        try {
            RoutePlan.dnsUpstream(servers);
            fail("expected profile_invalid");
        } catch (IllegalArgumentException expected) {
            assertEquals("profile_invalid", expected.getMessage());
        }
    }
}
