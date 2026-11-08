package org.example.vpnpoc;

import java.util.ArrayList;
import java.util.Collection;
import java.util.HashSet;
import org.amnezia.awg.config.Config;
import org.amnezia.awg.crypto.KeyPair;

final class ClientProfile {
    enum RoutingMode { EXCLUDE_SELECTED, INCLUDE_SELECTED }

    static RoutingMode routingModeFor(String storedMode) {
        return switch (storedMode) {
            case "all", "exclude" -> RoutingMode.EXCLUDE_SELECTED;
            case "include" -> RoutingMode.INCLUDE_SELECTED;
            default -> throw new IllegalArgumentException();
        };
    }

    static java.util.List<String> packagesForMode(String storedMode,
            Collection<String> selected, String ownPackage) {
        if (selected == null || selected.size() > 63 || ownPackage == null
                || !ownPackage.matches("[a-zA-Z][a-zA-Z0-9_]*(\\.[a-zA-Z][a-zA-Z0-9_]*)+"))
            throw new IllegalArgumentException();
        var unique = new HashSet<>(selected);
        if (unique.size() != selected.size()) throw new IllegalArgumentException();
        for (String name : selected) {
            if (name == null || !name.matches("[a-zA-Z][a-zA-Z0-9_]*(\\.[a-zA-Z][a-zA-Z0-9_]*)+")
                    || name.equals(ownPackage)) throw new IllegalArgumentException();
        }

        var packages = new ArrayList<String>();
        switch (storedMode) {
            case "all" -> packages.add(ownPackage);
            case "exclude" -> {
                packages.addAll(selected);
                packages.add(ownPackage);
            }
            case "include" -> packages.addAll(selected);
            default -> throw new IllegalArgumentException();
        }
        return packages;
    }

    static Config parse(String text, KeyPair key, Collection<String> selected,
            RoutingMode mode) throws Exception {
        // Remote app directives, custom routes, IPv6 passthrough and DNS hostnames fail closed.
        Profile.parse(text, key);
        if (selected == null || mode == null || selected.size() > 63
                || new HashSet<>(selected).size() != selected.size()
                || (mode == RoutingMode.INCLUDE_SELECTED && selected.isEmpty()))
            throw new IllegalArgumentException();
        for (String name : selected)
            if (!name.matches("[a-zA-Z][a-zA-Z0-9_]*(\\.[a-zA-Z][a-zA-Z0-9_]*)+"))
                throw new IllegalArgumentException();
        if (selected.isEmpty()) return Profile.parse(text, key);
        String directive = mode == RoutingMode.INCLUDE_SELECTED
            ? "IncludedApplications" : "ExcludedApplications";
        String modified = text.replaceFirst("(?im)^\\[Interface\\]\\s*$",
            "[Interface]\n" + directive + " = " + String.join(", ", selected));
        // Already validated remote profile; add only local package identifiers.
        modified = modified.replaceFirst("(?im)^\\[Interface\\]\\s*$",
            "[Interface]\nPrivateKey = " + key.getPrivateKey().toBase64());
        return Config.parse(new java.io.BufferedReader(new java.io.StringReader(modified)));
    }

    static Config parse(String text, KeyPair key, Collection<String> excluded) throws Exception {
        return parse(text, key, excluded, RoutingMode.EXCLUDE_SELECTED);
    }
}
