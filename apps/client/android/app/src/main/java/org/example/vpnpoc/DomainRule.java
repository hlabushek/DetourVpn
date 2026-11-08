package org.example.vpnpoc;

import java.net.IDN;
import java.util.Locale;
import java.util.regex.Pattern;

/** Local-only hostname rule. A suffix matches the apex and all label-boundary subdomains. */
final class DomainRule {
    private static final Pattern IPV4 = Pattern.compile("[0-9]{1,3}(\\.[0-9]{1,3}){3}");
    private static final Pattern LABEL = Pattern.compile("[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?");

    final String asciiHost;
    final boolean suffix;

    private DomainRule(String asciiHost, boolean suffix) {
        this.asciiHost = asciiHost;
        this.suffix = suffix;
    }

    static DomainRule parse(String input) {
        if (input == null || input.length() > 256) throw new IllegalArgumentException("domain_invalid");
        String value = input.trim();
        if (value.isEmpty() || value.contains("://") || value.indexOf('/') >= 0
                || value.indexOf('?') >= 0 || value.indexOf('#') >= 0 || value.indexOf('@') >= 0
                || value.indexOf(':') >= 0 || value.indexOf('\\') >= 0)
            throw new IllegalArgumentException("domain_invalid");
        boolean suffix = value.startsWith("*.");
        if (value.indexOf('*') >= 0 && !suffix) throw new IllegalArgumentException("domain_invalid");
        String host = suffix ? value.substring(2) : value;
        if (host.endsWith(".")) host = host.substring(0, host.length() - 1);
        if (host.isEmpty() || host.endsWith(".") || IPV4.matcher(host).matches())
            throw new IllegalArgumentException("domain_invalid");
        final String ascii;
        try {
            ascii = IDN.toASCII(host, IDN.USE_STD3_ASCII_RULES).toLowerCase(Locale.ROOT);
        } catch (IllegalArgumentException invalidIdn) {
            throw new IllegalArgumentException("domain_invalid");
        }
        if (ascii.length() > 253 || (ascii.indexOf('.') < 0 && !suffix))
            throw new IllegalArgumentException("domain_invalid");
        for (String label : ascii.split("\\.", -1))
            if (label.length() > 63 || !LABEL.matcher(label).matches())
                throw new IllegalArgumentException("domain_invalid");
        return new DomainRule(ascii, suffix);
    }

    String canonical() { return suffix ? "*." + asciiHost : asciiHost; }
}
