"""Static contract checks for the closed-beta admin panel (invites, units, safety properties)."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
STATIC = ROOT / "apps/admin/static"
ADMIN_HTML = (STATIC / "index.html").read_text(encoding="utf-8")
ADMIN_JS = (STATIC / "admin.js").read_text(encoding="utf-8")
FORMAT_JS = (STATIC / "format.js").read_text(encoding="utf-8")
ADMIN_CSS = (STATIC / "style.css").read_text(encoding="utf-8")


def test_admin_only_sections_are_hidden_until_admin_login() -> None:
    assert 'id="tab-invites" hidden' in ADMIN_HTML
    assert '<section id="panel-invites" class="panel" role="tabpanel" hidden>' in ADMIN_HTML
    assert (
        "for (const id of ['changes', 'tab-plans', 'tab-invites']) $(id).hidden = !admin;"
        in ADMIN_JS
    )


def test_telegram_subject_uses_exact_decimal_integer_contract() -> None:
    assert 'name="telegram_subject" type="text" inputmode="numeric"' in ADMIN_HTML
    assert "'telegram_subject'" in ADMIN_JS
    assert "BigInt(subject) > 4503599627370495n" in ADMIN_JS
    assert 'period_days|telegram_subject)":"([0-9]+)"' in ADMIN_JS


def test_invite_actions_use_existing_allowlist_and_confirm_destructive_revoke() -> None:
    assert "grant_beta_invite" in ADMIN_JS
    assert "revoke_beta_invite" in ADMIN_JS
    assert "window.confirm('Отзыв отключит аккаунт тестера" in ADMIN_JS
    assert "отзовёт его сессии и VPN-подключения" in ADMIN_JS
    assert "credentials:'omit'" in ADMIN_JS


def test_invite_forms_clear_after_success_without_persisting_subject() -> None:
    assert "await action({ operation, telegram_subject: subject, reason: f.reason });" in ADMIN_JS
    assert "e.target.reset();" in ADMIN_JS
    for script in (ADMIN_JS, FORMAT_JS):
        assert "localStorage." not in script
        assert "sessionStorage." not in script
        assert "innerHTML" not in script
        assert "eval(" not in script


def test_invite_list_is_read_from_the_server_endpoint_and_has_the_three_states() -> None:
    assert "request('beta-invites')" in ADMIN_JS
    for state in ("waiting", "joined", "revoked"):
        assert state + ":" in FORMAT_JS
    assert "Ждёт входа" in FORMAT_JS and "Вошёл" in FORMAT_JS and "Отозван" in FORMAT_JS


def test_invite_wizard_explains_the_three_steps_and_the_tester_id_flow() -> None:
    assert 'id="invite-message"' in ADMIN_HTML
    assert "Скопировать ID" in ADMIN_HTML
    assert "Добавить в бету" in ADMIN_HTML
    assert "псевдоним, без имени и контактов" in ADMIN_HTML


def test_traffic_amounts_are_entered_in_human_units_and_sent_as_exact_bytes() -> None:
    assert 'name="unit"' in ADMIN_HTML
    assert "parseAmount(f.amount, f.unit)" in ADMIN_JS
    assert "BigInt" in FORMAT_JS and "Number(" not in FORMAT_JS
    assert "amount_bytes|quota_bytes" in ADMIN_JS


def test_page_works_under_the_admin_content_security_policy() -> None:
    """The API serves /admin with script-src 'self'; style-src 'self' (no inline code or styles)."""
    assert "<script>" not in ADMIN_HTML and "style=" not in ADMIN_HTML
    assert 'type="module" src="admin.js"' in ADMIN_HTML
    assert "http://" not in ADMIN_JS + FORMAT_JS and "https://" not in ADMIN_JS + ADMIN_CSS
    assert "prefers-color-scheme:dark" in ADMIN_CSS
