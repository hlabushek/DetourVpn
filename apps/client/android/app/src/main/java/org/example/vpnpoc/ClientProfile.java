package org.example.vpnpoc;

import java.util.Collection;
import java.util.HashSet;
import org.amnezia.awg.config.Config;
import org.amnezia.awg.crypto.KeyPair;

final class ClientProfile {
    static Config parse(String text, KeyPair key, Collection<String> excluded) throws Exception {
        // Remote app directives, custom routes, IPv6 passthrough and DNS hostnames fail closed.
        Profile.parse(text, key);
        if (excluded.size() > 64 || new HashSet<>(excluded).size() != excluded.size())
            throw new IllegalArgumentException();
        for (String name : excluded)
            if (!name.matches("[a-zA-Z][a-zA-Z0-9_]*(\\.[a-zA-Z][a-zA-Z0-9_]*)+"))
                throw new IllegalArgumentException();
        if (excluded.isEmpty()) return Profile.parse(text, key);
        String modified = text.replaceFirst("(?im)^\\[Interface\\]\\s*$",
            "[Interface]\nExcludedApplications = " + String.join(", ", excluded));
        // Already validated remote profile; add only local package identifiers.
        modified = modified.replaceFirst("(?im)^\\[Interface\\]\\s*$",
            "[Interface]\nPrivateKey = " + key.getPrivateKey().toBase64());
        return Config.parse(new java.io.BufferedReader(new java.io.StringReader(modified)));
    }
}
