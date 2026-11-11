package org.example.vpnpoc;

import java.util.ArrayList;
import java.util.List;

/**
 * User-selected tunnel DNS (ADR-106). Null or empty means "as in the server profile", which is
 * the behavior before this setting existed. Only the four listed resolvers are accepted, so a
 * stored value can never carry an arbitrary address into the tunnel configuration.
 */
final class DnsChoice {
    static final List<String> SERVERS = java.util.Collections.unmodifiableList(java.util.Arrays.asList("1.1.1.1", "8.8.8.8", "94.140.14.14", "9.9.9.9"));

    private DnsChoice() {}

    /**
     * Canonical stored form: null for empty input (default), the server for an allowed value.
     * Any other non-empty value is rejected.
     */
    static String canonicalOrNull(String value) {
        if (value == null || value.isEmpty()) return null;
        if (!SERVERS.contains(value)) throw new IllegalArgumentException("dns_server_invalid");
        return value;
    }

    /** Read path: a stored value that is not on the list falls back to the default. */
    static String storedOrNull(String value) {
        try {
            return canonicalOrNull(value);
        } catch (IllegalArgumentException invalid) {
            return null;
        }
    }

    /**
     * Replaces the DNS entries in the [Interface] section of a WireGuard/AmneziaWG text with
     * {@code DNS = server}. With a null server the text is returned unchanged. A profile without
     * any DNS line gets one inserted at the top of [Interface]. Other sections are untouched.
     */
    static String applyToConfigText(String text, String server) {
        if (server == null) return text;
        if (text == null) throw new IllegalArgumentException("profile_invalid");
        List<String> out = new ArrayList<>();
        boolean inInterface = false;
        boolean sawInterface = false;
        boolean wroteDns = false;
        for (String line : text.split("\n", -1)) {
            String trimmed = line.trim();
            if (trimmed.startsWith("[")) {
                if (inInterface && !wroteDns) {
                    out.add("DNS = " + server);
                    wroteDns = true;
                }
                inInterface = trimmed.equalsIgnoreCase("[Interface]");
                if (inInterface) sawInterface = true;
                out.add(line);
                continue;
            }
            if (inInterface && trimmed.regionMatches(true, 0, "DNS", 0, 3)
                    && trimmed.substring(3).trim().startsWith("=")) {
                if (!wroteDns) {
                    out.add("DNS = " + server);
                    wroteDns = true;
                }
                continue; // drop any further DNS lines of the profile
            }
            out.add(line);
        }
        if (!sawInterface) throw new IllegalArgumentException("profile_invalid");
        if (!wroteDns) {
            // [Interface] was the last section and had no DNS line: append it at the end.
            out.add("DNS = " + server);
        }
        return String.join("\n", out);
    }
}
