package org.example.vpnpoc;

import java.util.Set;

final class DiagnosticReportText {
    private DiagnosticReportText() { }

    private static final Set<String> ERROR_CODES = Set.of(
        "admission_closed", "api_unavailable", "device_clock_skew", "device_conflict",
        "device_limit_reached", "device_revoked", "handshake_not_established", "insufficient_quota",
        "insufficient_window_funding", "invalid_response", "lease_expired", "login_failed",
        "native_config_invalid", "native_disconnect_failed", "native_down",
        "native_engine_unavailable", "native_io_error", "native_permission_error",
        "native_restart_failed", "native_service_state_invalid", "native_start_failed",
        "native_state_unknown",
        "native_unavailable", "network_unavailable", "permission_denied", "rate_limited",
        "recovery_exhausted", "remote_revoked", "renew_token_invalid", "revoke_unconfirmed", "session_device_mismatch",
        "session_erasure_failed", "session_expired", "session_storage_failed",
        "tunnel_config_not_ready", "tunnel_exists", "tunnel_idempotency_conflict",
        "tunnel_node_capacity", "tunnel_node_unavailable", "tunnel_profile_unavailable",
        "tunnel_recovery_not_ready", "unauthorized", "unknown_error", "vpn_revoked",
        "window_activation_refused", "window_connection_exists", "window_deadline_not_advanced",
        "window_duration_invalid", "window_epoch_compare_failed", "window_epoch_conflict",
        "window_expired", "window_funding_conflict", "window_funding_deadline",
        "window_generation_conflict", "window_idempotency_conflict", "window_inconsistent",
        "window_legacy_tunnel_active", "window_manifest_protocol_conflict",
        "window_manifest_unavailable", "window_node_capacity", "window_node_required",
        "window_node_unavailable", "window_not_active", "window_not_found", "window_not_renewable",
        "window_operator_inventory_conflict", "window_operator_manifest_changed",
        "window_receipt_conflict", "window_receipt_not_found", "window_reconciliation_required",
        "window_replay_conflict", "window_runtime_mismatch", "window_tunnels_unavailable",
        "renewal_too_late", "refresh_reused", "connection_fenced", "runtime_unavailable",
        "device_traffic_cap_reached", "recovery_failed", "session_missing",
        "node_validation_failed", "invalid_origin", "background_start_refused", "not_found",
        "forbidden");

    private static final Set<String> STAGES = Set.of(
        "android_vpn_permission", "connection_reconcile", "device_key_read",
        "device_registration", "key_challenge", "key_proof", "lease_create", "lease_poll",
        "lease_validation", "native_disconnect", "native_tunnel_start", "old_lease_cleanup",
        "panel_profile", "selected_node", "network_change", "session_resume", "tunnel_monitor",
        "tunnel_restart", "window_advance", "window_renewal",
        // Native steps that failed while Dart was not running (ClientApplication.failedAt).
        "native_init", "native_deadline", "native_watchdog_restart", "native_renewal",
        "native_restore", "unknown"
    );

    /** Coarse context categories; the same sets as in diagnostic_report.dart. */
    private static final Set<String> HANDSHAKE_AGES = Set.of("never", "under_1m", "m1_to_5",
            "m5_to_30", "m30_to_120", "over_2h");
    private static final Set<String> WINDOWS = Set.of("open", "ended", "unknown");
    private static final Set<String> NETWORK_CHANGES = Set.of("none", "recent", "earlier");

    /** Russian explanation texts by hint key; kept equal to diagnosticHints in Dart (sync test). */
    private static final java.util.Map<String, String> HINTS = java.util.Map.of(
        "vpn_permission", "Вероятно, Android отозвал разрешение VPN (включили другой VPN или отключили). "
            + "Что сделать: подключитесь заново и подтвердите запрос Android.",
        "login_needed", "Вероятно, вход в аккаунт устарел или устройство отозвано. "
            + "Что сделать: войдите заново и подключитесь.",
        "network_lost", "Вероятно, не было сети или сервис недоступен. "
            + "Что сделать: проверьте интернет, подождите минуту и подключитесь заново.",
        "server_busy", "Вероятно, сервер временно занят или недоступен. "
            + "Что сделать: подождите пару минут и подключитесь заново.",
        "quota_out", "Вероятно, закончился доступный трафик или лимит. "
            + "Что сделать: проверьте остаток на главном экране.",
        "renewal_failed", "Вероятно, окно не удалось продлить вовремя (телефон мог спать). "
            + "Что сделать: подключитесь заново и напишите, как долго телефон был без работы.",
        "tunnel_dropped", "Вероятно, Android остановил туннель или перезапуск не удался. "
            + "Что сделать: подключитесь заново; если повторяется, отключите экономию батареи для приложения.",
        "window_ended", "Вероятно, окно подключения закончилось. "
            + "Что сделать: подключитесь заново.",
        "general", "Причина по этому коду не определена. "
            + "Что сделать: отправьте этот отчёт и напишите, что делали перед сбоем.");

    /** Rounds seconds since the last handshake into a fixed range (negative = no handshake). */
    static String handshakeAgeBucket(long seconds) {
        if (seconds < 0) return "never";
        if (seconds < 60) return "under_1m";
        if (seconds < 300) return "m1_to_5";
        if (seconds < 1800) return "m5_to_30";
        if (seconds < 7200) return "m30_to_120";
        return "over_2h";
    }

    /** Task 7: whether the app saw a confirmed handshake; null omits the line. */
    private static final Set<String> HANDSHAKES = Set.of("confirmed", "not_seen");

    static String format(String code, String stage) { return format(code, stage, null); }

    static String format(String code, String stage, String handshake) {
        return format(code, stage, handshake, null, null, null, "general");
    }

    /**
     * Every optional value is checked against a fixed set; anything else rejects the whole
     * report. The hint key selects a Russian explanation; it never carries free text.
     */
    static String format(String code, String stage, String handshake, String handshakeAge,
            String window, String networkChange, String hint) {
        if (code == null || stage == null || !ERROR_CODES.contains(code) || !STAGES.contains(stage)
                || (handshake != null && !HANDSHAKES.contains(handshake))
                || (handshakeAge != null && !HANDSHAKE_AGES.contains(handshakeAge))
                || (window != null && !WINDOWS.contains(window))
                || (networkChange != null && !NETWORK_CHANGES.contains(networkChange))
                || hint == null || !HINTS.containsKey(hint))
            throw new IllegalArgumentException();
        return "DetourVpn — отчёт диагностики\nКод: " + code + "\nЭтап: " + stage
                + (handshake == null ? "" : "\nHandshake: " + handshake)
                + (handshakeAge == null ? "" : "\nВремя с handshake: " + handshakeAge)
                + (window == null ? "" : "\nОкно: " + window)
                + (networkChange == null ? "" : "\nСмена сети: " + networkChange)
                + "\n" + HINTS.get(hint);
    }

    /** Screen and Doze state at share time; booleans only. */
    static String deviceLine(boolean screenOn, boolean dozing) {
        return "\nУстройство: экран=" + (screenOn ? "вкл" : "выкл")
                + ", doze=" + (dozing ? "да" : "нет");
    }

    private static final Set<String> TRANSPORTS = Set.of("disconnected", "connecting", "connected",
            "degraded", "reconnecting", "waiting_for_network", "failed");
    private static final Set<String> EVIDENCE = Set.of("none", "core_running", "inbound_data",
            "peer_handshake", "peer_and_inbound_data");
    private static final Set<String> CATEGORIES = Set.of("none", "transient", "user_stop",
            "permission_revoked", "auth_rejected", "quota_exhausted", "invalid_config",
            "window_expired", "native_core_failure", "tun_failure", "network_unavailable",
            "server_unreachable");

    /**
     * ADR-124/128: one line of native supervisor state, appended natively at share time. Fixed
     * code sets and small counters only: no address, key, account or traffic data.
     */
    static String nativeLine(String transport, String evidence, String category, boolean desired,
            boolean networkValidated, long recoveryAttempts, long tunnelRestarts) {
        if (!TRANSPORTS.contains(transport) || !EVIDENCE.contains(evidence)
                || !CATEGORIES.contains(category) || recoveryAttempts < 0 || tunnelRestarts < 0)
            throw new IllegalArgumentException();
        return "\nVPN: " + transport + ", " + evidence + ", " + category
                + ", intent=" + (desired ? "on" : "off")
                + ", validated=" + (networkValidated ? "yes" : "no")
                + ", recoveries=" + Math.min(recoveryAttempts, 99)
                + ", restarts=" + Math.min(tunnelRestarts, 999);
    }
}
