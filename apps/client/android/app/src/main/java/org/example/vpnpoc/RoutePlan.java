package org.example.vpnpoc;

import java.util.Collection;

/**
 * ADR-137 (E): the fixed route and DNS choices of the local libbox config, as pure values so they
 * are unit-tested on the JVM. {@link LibboxConfigFactory} uses these instead of inline literals.
 *
 * <p>What this means on a device (documented, not a leak guarantee): the TUN always captures
 * {@code 0.0.0.0/0} and {@code ::/0}, whatever the profile says. Whether an IPv6 destination can
 * actually reach the peer depends on the profile's AllowedIPs, which the factory copies unchanged.
 * DNS answers are IPv4-only, and only the first DNS server of the profile is used as the upstream.
 * Packet captures on a phone are needed to prove that no IPv6 or DNS traffic leaves the tunnel.
 */
final class RoutePlan {
    private RoutePlan() {}

    /** The TUN routes all IPv4 and IPv6 destinations into the tunnel. */
    static final String[] ROUTE_ADDRESSES = {"0.0.0.0/0", "::/0"};
    /** AAAA answers are not synthesized for the profile upstream. */
    static final String DNS_STRATEGY = "ipv4_only";

    /**
     * The one DNS upstream the profile DNS uses: the first server listed in the profile, in order.
     * Additional servers are not used.
     *
     * @throws IllegalArgumentException "profile_invalid" when the profile lists no DNS server
     */
    static String dnsUpstream(Collection<String> hostAddresses) {
        if (hostAddresses == null || hostAddresses.isEmpty()) throw new IllegalArgumentException("profile_invalid");
        String first = hostAddresses.iterator().next();
        if (first == null || first.isEmpty()) throw new IllegalArgumentException("profile_invalid");
        return first;
    }
}
