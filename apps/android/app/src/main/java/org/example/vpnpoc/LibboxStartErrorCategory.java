package org.example.vpnpoc;

import java.util.Locale;

/** Maps native startup errors to safe, non-sensitive diagnostic categories. */
final class LibboxStartErrorCategory {
    private LibboxStartErrorCategory() {}

    static String classify(String message) {
        if (message == null || message.length() > 4096) return null;
        String normalized = message.toLowerCase(Locale.ROOT);
        if (normalized.contains("invalid memory address or nil pointer dereference"))
            return "go_nil_pointer";
        if (normalized.contains("context deadline exceeded")) return "dial_context_deadline";
        if (normalized.contains("context canceled") || normalized.contains("context cancelled"))
            return "dial_context_canceled";
        if (normalized.contains("no available network interface"))
            return "udp_no_physical_interface";
        if (normalized.contains("permission denied") || normalized.contains("operation not permitted"))
            return "udp_socket_permission";
        if (normalized.contains("address already in use")) return "udp_socket_in_use";
        if (normalized.contains("address family not supported")
                || normalized.contains("address family unavailable"))
            return "udp_family_unsupported";
        if (normalized.contains("cannot assign requested address")
                || normalized.contains("address not available"))
            return "udp_bind_address_unavailable";
        if (normalized.contains("no buffer space available")
                || normalized.contains("too many open files")
                || normalized.contains("resource temporarily unavailable"))
            return "udp_socket_resources_exhausted";
        if (normalized.contains("protocol not supported")
                || normalized.contains("socket type not supported")
                || normalized.contains("operation not supported"))
            return "udp_socket_unsupported";
        if (normalized.contains("network is down")) return "udp_network_down";
        if (normalized.contains("network is unreachable") || normalized.contains("no route to host"))
            return "udp_route_unavailable";
        if (normalized.contains("use of closed network connection")) return "udp_socket_closed";
        if (normalized.contains("invalid argument")) return "udp_socket_invalid_argument";
        String bindCategory = classifyBindFailure(message);
        if (bindCategory != null) return bindCategory;
        return null;
    }

    static String classifyBindFailure(String message) {
        if (message == null || message.length() > 4096) return null;
        String normalized = message.toLowerCase(Locale.ROOT);
        if (normalized.contains("create ipv4 connection")) return "awg_bind_ipv4_open";
        if (normalized.contains("create ipv6 connection")) return "awg_bind_ipv6_open";
        if (normalized.contains("unable to update bind")) return "awg_bind_open";
        return null;
    }

    static String classifyEngineBindFailure(String message) {
        if (message == null || message.length() > 4096) return null;
        String normalized = message.toLowerCase(Locale.ROOT);
        if (!normalized.contains("create ipv4 connection")
                && !normalized.contains("create ipv6 connection")
                && !normalized.contains("unable to update bind")) return null;
        return classify(message);
    }
}
