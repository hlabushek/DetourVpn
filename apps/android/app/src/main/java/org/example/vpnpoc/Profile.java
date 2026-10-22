package org.example.vpnpoc;

import org.amnezia.awg.config.Config;
import org.amnezia.awg.config.Interface;
import org.amnezia.awg.crypto.KeyPair;
import java.io.BufferedReader;
import java.io.StringReader;
import java.util.ArrayList;
import java.util.List;

/** Staging-only profile: key generated locally, full IPv4 route, IPv6 blocked by VpnService. */
final class Profile {
    static Config parse(String text, KeyPair key) throws Exception {
        if (text.length() > 16384 || text.indexOf('\0') >= 0) throw new IllegalArgumentException();
        List<String> lines = new ArrayList<>();
        boolean injected = false;
        for (String line : text.split("\\r?\\n")) {
            String stripped = line.split("#", 2)[0].trim();
            if (stripped.matches("(?i)^PrivateKey\\s*=.*")) throw new IllegalArgumentException();
            lines.add(line);
            if (stripped.equalsIgnoreCase("[Interface]")) {
                if (injected) throw new IllegalArgumentException();
                lines.add("PrivateKey = " + key.getPrivateKey().toBase64());
                injected = true;
            }
        }
        if (!injected) throw new IllegalArgumentException();
        Config config = Config.parse(new BufferedReader(new StringReader(String.join("\n", lines))));
        Interface iface = config.getInterface();
        if (config.getPeers().size() != 1 || iface.getDnsServers().isEmpty()
                || !iface.getDnsSearchDomains().isEmpty()
                || !iface.getIncludedApplications().isEmpty()
                || !iface.getExcludedApplications().isEmpty()) throw new IllegalArgumentException();
        var peer = config.getPeers().get(0);
        if (peer.getEndpoint().isEmpty() || peer.getAllowedIps().size() != 1
                || !peer.getAllowedIps().iterator().next().toString().equals("0.0.0.0/0"))
            throw new IllegalArgumentException();
        // No endpoint DNS bootstrap and no IPv6 passthrough: IPv4-only test boundary.
        if (!peer.getEndpoint().get().getHost().matches("[0-9]+\\.[0-9]+\\.[0-9]+\\.[0-9]+"))
            throw new IllegalArgumentException();
        for (var address : iface.getAddresses())
            if (address.getAddress().getAddress().length != 4) throw new IllegalArgumentException();
        for (var dns : iface.getDnsServers())
            if (dns.getAddress().length != 4) throw new IllegalArgumentException();
        return config;
    }
}
