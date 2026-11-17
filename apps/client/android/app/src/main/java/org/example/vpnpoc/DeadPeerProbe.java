package org.example.vpnpoc;

import java.net.URI;
import java.util.Locale;

/**
 * ADR-148: configuration and result rules of the active probe through the tunnel. Pure (no Android
 * types), so the URL checks are unit-tested on the JVM; {@link TunnelProbeRunner} does the I/O.
 *
 * <p>The health URL and the failover opt-in are read from the app manifest
 * ({@code <meta-data>} {@link #META_URL} and {@link #META_FAILOVER}). The repository ships a
 * placeholder ({@code "none"}) that is not a URL, so the probe is off until the owner sets a
 * project-owned HTTPS health endpoint. The URL is never logged.
 *
 * <p>The probe carries no user data: a fixed path with no query or fragment, no credentials, no
 * cookies, a fixed User-Agent instead of the platform one (which names the device model), and only
 * the response status is used.
 */
final class DeadPeerProbe {
    /** Manifest meta-data key of the health URL (placeholder {@code "none"} = off). */
    static final String META_URL = "org.example.vpnpoc.DEAD_PEER_PROBE_URL";
    /** Manifest meta-data key of the failover opt-in (default and shipped value: false). */
    static final String META_FAILOVER = "org.example.vpnpoc.DEAD_PEER_FAILOVER";
    /** Fixed, data-free User-Agent; replaces the platform one, which names the device. */
    static final String USER_AGENT = "detour-probe/1";
    static final int MAX_URL_CHARS = 256;

    private static final String LABEL = "[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?";

    /** The validated health URL, or null when the probe is off. */
    final String url;
    final boolean failoverOptIn;

    private DeadPeerProbe(String url, boolean failoverOptIn) {
        this.url = url;
        this.failoverOptIn = failoverOptIn;
    }

    boolean enabled() { return url != null; }

    /**
     * From the raw manifest values. Anything but a valid URL (see {@link #validUrl}) turns the
     * probe off; anything but boolean {@code true} (or the string "true") keeps failover off.
     */
    static DeadPeerProbe fromConfig(Object rawUrl, Object rawFailover) {
        String url = rawUrl instanceof String ? validUrl((String) rawUrl) : null;
        boolean failover = Boolean.TRUE.equals(rawFailover)
                || (rawFailover instanceof String && "true".equals(rawFailover));
        return new DeadPeerProbe(url, failover);
    }

    /** The probe off, failover off. */
    static DeadPeerProbe off() { return new DeadPeerProbe(null, false); }

    /**
     * Returns {@code raw} if it is an HTTPS URL to a DNS host name (no IP literal) with an optional
     * port and a plain path, and no user info, query or fragment; otherwise null.
     */
    static String validUrl(String raw) {
        if (raw == null || raw.isEmpty() || raw.length() > MAX_URL_CHARS) return null;
        URI uri;
        try {
            uri = new URI(raw);
        } catch (Exception malformed) {
            return null;
        }
        if (!"https".equals(uri.getScheme()) || uri.getRawUserInfo() != null
                || uri.getRawQuery() != null || uri.getRawFragment() != null || uri.isOpaque())
            return null;
        String host = uri.getHost();
        if (host == null || !host.equals(host.toLowerCase(Locale.ROOT))
                || !host.matches(LABEL + "(\\." + LABEL + ")+")) return null;
        String last = host.substring(host.lastIndexOf('.') + 1);
        if (last.matches("[0-9]+")) return null; // an IPv4 literal
        int port = uri.getPort();
        if (port != -1 && (port < 1 || port > 65535)) return null;
        String path = uri.getRawPath();
        if (path != null && !path.isEmpty() && !path.matches("/[A-Za-z0-9/._-]{0,127}")) return null;
        return raw;
    }

    /** Only a 2xx answer passes; a redirect or an error status is a failed probe. */
    static DeadPeerWatch.ProbeResult classify(int status) {
        return status >= 200 && status < 300 ? DeadPeerWatch.ProbeResult.PASS
                : DeadPeerWatch.ProbeResult.FAIL;
    }
}
