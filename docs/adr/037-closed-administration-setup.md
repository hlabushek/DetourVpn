# ADR-037 — explicit closed administration setup

2026-10-23, I-021/I-023 deployment integration. Telegram Serverless menu already
exists, but its trusted user authentication relay is not implemented. Do not
invent a bot token or disable validation merely to start the administrative API.

Add `VPN_CLOSED_ADMIN_ONLY=true` as an explicit temporary setup mode. It requires
closed environment, dedicated password-protected runtime DB, HTTPS origin,
admin enabled with independent encryption key, user auth disabled and API docs
disabled. Other combinations fail startup. No default credentials or admin seed.
Admin login keeps the existing password/MFA/RBAC/rate-limit/session requirements.
User endpoints depend on the absent AuthService and return 503; readiness still
checks schema and DB privileges, not user-service readiness. No tunnel manifest.

This permits a real administrative service to start independently of Telegram.
It is not acceptance of user login or daily VPN. To leave setup mode, provision
real auth configuration and set this flag false together with enabling auth.
Existing Serverless bot/webhook are unchanged. Runtime auth bypass is not added.
