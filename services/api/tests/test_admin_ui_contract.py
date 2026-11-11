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
        "for (const id of ['changes', 'tab-plans', 'tab-invites', 'panel-import'])"
        " $(id).hidden = !admin;"
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


def test_user_list_has_telegram_id_column_sort_and_hides_absent_values() -> None:
    assert 'id="users-sort"' in ADMIN_HTML
    for value in ("created_desc", "created_asc", "telegram", "username", "status"):
        assert f'<option value="{value}">' in ADMIN_HTML
    assert "r.telegram_id ? '0' + String(r.telegram_id)" in ADMIN_JS
    assert "if (rows.some(r => r.telegram_id)) columns.splice(1, 0, ['Telegram ID'" in ADMIN_JS
    assert "$('users-sort').addEventListener('change', renderUsers);" in ADMIN_JS


def test_user_list_has_telegram_username_column_sort_and_search_hint() -> None:
    # ADR-110 (supersedes ADR-109): username column only when a listed user has one, empty
    # usernames sort last, and the page states that the value can be stale.
    assert (
        "if (rows.some(r => r.telegram_username)) columns.splice(1, 0, ['Telegram username'"
        in ADMIN_JS
    )
    assert "r.telegram_username ? '0' + String(r.telegram_username).toLowerCase() : '1'" in ADMIN_JS
    assert "Telegram ID или @username" in ADMIN_HTML
    assert 'id="users-username-note"' in ADMIN_HTML
    assert "Telegram username не хранится" not in ADMIN_HTML


def test_panel_servers_show_source_and_link_input_is_masked() -> None:
    # ADR-111: source column on both server lists; the link is typed into a masked input that
    # is cleared after saving, and only admins see the import form.
    assert "['Источник', r => r.source === 'vps' ? 'VPS' : r.source]" in ADMIN_JS
    assert "table('panel-servers', await request('panel-servers')" in ADMIN_JS
    assert '<input name="link" type="password"' in ADMIN_HTML
    assert 'id="panel-import" class="card" hidden' in ADMIN_HTML
    assert "'panel-import']) $(id).hidden = !admin" in ADMIN_JS
    assert "form.reset(); // drop the link from the page" in ADMIN_JS
    assert "Android: ' + (r.android_supported ? 'да' : 'не поддерживается')" in ADMIN_JS
    # Every parser error code has a Russian message.
    for code in (
        "format",
        "scheme",
        "uuid",
        "host",
        "port",
        "security",
        "public_key",
        "sni",
        "short_id",
        "fingerprint",
        "flow",
        "transport",
        "unknown_field",
    ):
        assert f"panel_link_{code}:" in FORMAT_JS
