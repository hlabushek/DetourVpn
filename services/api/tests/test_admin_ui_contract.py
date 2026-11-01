"""Static contract checks for the closed-beta admin invite controls."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
ADMIN_HTML = (ROOT / "apps/admin/static/index.html").read_text(encoding="utf-8")
ADMIN_JS = (ROOT / "apps/admin/static/admin.js").read_text(encoding="utf-8")


def test_beta_invite_forms_are_hidden_until_admin_login() -> None:
    assert '<section id="beta-invites" hidden>' in ADMIN_HTML
    assert "'beta-invites']) $(id).hidden=role!=='admin'" in ADMIN_JS


def test_telegram_subject_uses_exact_decimal_integer_contract() -> None:
    assert 'name="telegram_subject" type="text" inputmode="numeric"' in ADMIN_HTML
    assert "'telegram_subject'" in ADMIN_JS
    assert "BigInt(subject)>4503599627370495n" in ADMIN_JS
    assert 'period_days|telegram_subject)":"([0-9]+)"' in ADMIN_JS


def test_invite_actions_use_existing_allowlist_and_confirm_destructive_revoke() -> None:
    assert "grant_beta_invite" in ADMIN_JS
    assert "revoke_beta_invite" in ADMIN_JS
    assert "window.confirm('Отзыв отключит аккаунт тестировщика" in ADMIN_JS
    assert "отзовёт его сессии и VPN-туннели" in ADMIN_JS
    assert "credentials:'omit'" in ADMIN_JS


def test_invite_form_clears_after_success_without_persisting_subject() -> None:
    assert "await action({operation,telegram_subject:subject,reason:f.reason});" in ADMIN_JS
    assert "e.target.reset();" in ADMIN_JS
    assert "localStorage." not in ADMIN_JS
    assert "sessionStorage." not in ADMIN_JS
