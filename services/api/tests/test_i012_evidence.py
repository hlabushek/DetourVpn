"""Sanitized opt-in harness diagnostics; no device, database or VPS calls."""

from test_i012_live import PhoneAcceptanceFailure, pending_control_actions, safe_phone_failure


def test_phone_failure_preserves_only_bounded_diagnostics() -> None:
    assert safe_phone_failure(
        {
            "stage": "staging_egress",
            "native_interface_up": True,
            "last_http_status": 200,
            "egress_http_status": 503,
            "api_error": "tunnel_config_not_ready",
            "egress_result": "unavailable",
            "last_tunnel_state": "revoked",
            "native_up_at_cutoff": True,
            "config": "TEST SECRET MUST BE DROPPED",
        }
    ) == {
        "stage": "staging_egress",
        "native_interface_up": True,
        "last_http_status": 200,
        "egress_http_status": 503,
        "api_error": "tunnel_config_not_ready",
        "egress_result": "unavailable",
        "last_tunnel_state": "revoked",
        "native_up_at_cutoff": True,
    }


def test_phone_failure_drops_untrusted_labels_and_wrong_types() -> None:
    failure = PhoneAcceptanceFailure(
        {
            "stage": "TEST SECRET",
            "api_error": "TEST SECRET",
            "native_interface_up": "TEST SECRET",
            "last_http_status": True,
            "egress_http_status": 999999,
            "last_tunnel_state": ["TEST SECRET"],
            "revoke_response_state": {"TEST": "SECRET"},
            "egress_result": ["TEST SECRET"],
        }
    )
    assert failure.facts == {"stage": "unknown"}
    assert str(failure) == "phone_lifecycle_failed_at_unknown"


def test_control_action_survives_overwritten_phase_and_is_consumed_once() -> None:
    # Regression: host never saw the transient waiting_agent_restart phase.
    state = {"stage": "waiting_revoke_ack", "control_events": ["restart_agent"]}
    seen: set[str] = set()
    actions = pending_control_actions(state, seen)
    assert actions == ["waiting_agent_restart"]
    seen.update(actions)
    assert pending_control_actions(state, seen) == []


def test_control_restore_survives_late_poll_and_unknown_events_never_execute() -> None:
    state = {"stage": "waiting_revoke_ack", "control_events": ["pause_control", "restore_control"]}
    assert pending_control_actions(state, {"waiting_lease_expiry"}) == [
        "lease_cutoff_without_control"
    ]
    assert pending_control_actions({"control_events": ["shell", {}, "restart_agent"]}, set()) == [
        "waiting_agent_restart"
    ]
    assert pending_control_actions({"control_events": ["restart_agent"] * 4}, set()) == []
