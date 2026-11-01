package org.example.vpnpoc;

import java.util.Set;

final class DiagnosticReportText {
    private DiagnosticReportText() { }

    private static final Set<String> ERROR_CODES = Set.of(
        "admission_closed", "api_unavailable", "device_conflict", "device_limit_reached",
        "device_revoked", "insufficient_quota", "insufficient_window_funding",
        "invalid_response", "lease_expired", "login_failed", "native_disconnect_failed",
        "native_down", "native_unavailable", "network_unavailable", "permission_denied",
        "rate_limited", "remote_revoked", "revoke_unconfirmed", "session_device_mismatch",
        "session_erasure_failed", "session_expired", "session_storage_failed",
        "tunnel_node_capacity", "tunnel_node_unavailable", "tunnel_profile_unavailable",
        "tunnel_recovery_not_ready", "unauthorized", "unknown_error",
        "window_generation_conflict", "window_node_required", "window_node_unavailable",
        "window_not_active", "window_tunnels_unavailable"
    );

    private static final Set<String> STAGES = Set.of(
        "android_vpn_permission", "connection_reconcile", "device_key_read",
        "device_registration", "key_challenge", "key_proof", "lease_create", "lease_poll",
        "lease_validation", "native_disconnect", "native_tunnel_start", "old_lease_cleanup",
        "selected_node", "unknown"
    );

    static String format(String code, String stage) {
        if (code == null || stage == null || !ERROR_CODES.contains(code) || !STAGES.contains(stage))
            throw new IllegalArgumentException();
        return "DetourVpn — отчёт диагностики\nКод: " + code + "\nЭтап: " + stage;
    }
}
