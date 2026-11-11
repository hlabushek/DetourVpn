# ADR-111: Import panel servers (3x-ui, Remnawave) by a vless:// Reality link

Status: proposed (overnight run 3, 2026-11-11). Parser and admin storage implemented locally;
not deployed; not verified against a live panel. Builds on ADR-108.

## Context
The owner runs separate, cheaper VPN servers that already have a 3x-ui or Remnawave panel
(VLESS + Reality). The panel must not be installed on our normal VPS, and we have no root shell on
the panel servers. We need a way to register such a server in our control plane.

## Decision
1. **Input:** the admin pastes a single `vless://` share link (exported by the panel for one
   client). The app never receives panel logins, passwords or API tokens, and never calls a panel
   API (no read, no write).
2. **Subscription links are not implemented in this run** (body format since verified and parsed, ADR-116). The Remnawave and 3x-ui subscription
   formats (base64 list of links, JSON/Clash output, headers) could not be verified offline: no
   official sample is in the repo and no live panel may be contacted. Open question for the owner;
   the subscription body would be parsed by the same `vless://` parser once its format is pinned.
3. **Strict parser** (`services/api/src/vpn_api/panel_sources.py`, pure function):
   - scheme `vless`, UUID user part (RFC 4122 text form), host (DNS name or IPv4; IPv6 refused for
     now, matching the IPv4-only beta), port 1–65535;
   - query: `security=reality` (required), `pbk` (base64url, 32-byte X25519 key, required),
     `sni` (DNS name, required), `sid` (0–16 hex chars, even length, may be empty), `fp`
     (one of the uTLS names sing-box accepts), `flow` (empty or `xtls-rprx-vision`),
     `type` (`tcp` only in this run), `encryption` (`none` or absent);
   - unknown query keys, duplicate keys, userinfo passwords, paths other than empty or `/`, and
     control characters are rejected. The `#fragment` is ignored (it is a display name and may
     contain personal text); the admin types a label instead.
   - every failure raises a stable error code, for example `panel_link_scheme`,
     `panel_link_uuid`, `panel_link_host`, `panel_link_port`, `panel_link_security`,
     `panel_link_public_key`, `panel_link_sni`, `panel_link_short_id`, `panel_link_fingerprint`,
     `panel_link_flow`, `panel_link_transport`, `panel_link_unknown_field`, `panel_link_format`.
   - no host is hard-coded; nothing is resolved or dialled.
4. **Storage:** table `panel_server_sources` (migration `0027_panel_server_sources`): id, label,
   panel kind (`3x-ui` | `remnawave`), protocol (`vless_reality`), `link_ciphertext` (Fernet with
   the existing `VPN_ADMIN_ENCRYPTION_KEY`, same as admin TOTP secrets), created/revoked times.
   The plaintext link, UUID, host, short id and public key are not stored in clear and never
   returned by the API after import. The `#fragment` is removed before encryption, so the stored
   link is exactly the part the parser checked (plain ASCII, no display name).
5. **Admin API:** `POST /api/admin/v1/panel-servers` (admin role, write session, idempotency key,
   reason, audited in `admin_actions` with a result that holds only id and label) and
   `GET /api/admin/v1/panel-servers` (id, label, panel kind, protocol, created time, source
   `panel`). `GET /api/admin/v1/nodes` now returns `source: "vps"`. The admin page shows the
   source column on both lists.
6. **Not in this run:** delivering a panel server to client devices, quotas/usage on panel
   servers, health checks, failover between sources (backlog), panel admin access.

## Security notes
- The link is a credential for one panel client. Anyone with the admin encryption key and a
  database copy can read it; key rotation makes stored links unreadable (re-import needed), the
  same limit as TOTP secrets today.
- The request digest for idempotency hashes the link with SHA-256 (the UUID has 122 random bits,
  so the digest is not enumerable). The link is not logged; the Pydantic field is a `SecretStr`
  and input is hidden from validation errors.

## Verification
- Parser unit tests (valid link, each rejection code). Admin endpoint tests on PostgreSQL if the
  throwaway runner works. No live panel was contacted; no link from a real panel was tested.
