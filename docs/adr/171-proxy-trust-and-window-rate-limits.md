# ADR-171 — Proxy trust boundary and per-caller rate limits for window tunnels

**Status:** accepted. Code, Caddy configuration (repo only) and tests in the working tree (not
committed: commits were blocked by the permission system on 2026-11-23). **Not deployed.**

## Context
The API listens on 127.0.0.1 behind Caddy with `--no-proxy-headers`. `network()` returned
`request.client.host`, which is always the proxy, so every "per-network" rate bucket was one
bucket for all users. The `device` limit (30 per minute, global 300) covered create, renew,
renew-token, native status, native renew, revoke and the panel profile, and it was checked
before authentication. About 30 unauthenticated requests a minute to
`/api/v1/window-tunnels/<any id>/native-renew` made every user's connects and renewals fail
with 429; normal use could reach the limit too. Found by the 2026-11-22 audit (C1, critical).

## Decision
1. **Trust boundary.** New setting `VPN_TRUSTED_PROXY_ADDRESS` (an IP address; unset by
   default, which keeps the old behaviour). `network()` uses `X-Forwarded-For` only when the
   direct peer equals this address. Exactly one header with exactly one valid IP address is
   accepted; anything else from the proxy maps to the shared, fail-closed subject
   `forwarded-invalid`. IPv6 clients are grouped by /64. A direct client's forged header is
   ignored.
2. **Caddy overwrites the header.** Every `reverse_proxy` in `infra/beta/Caddyfile.ip` and the
   backup host's Caddyfile (`scripts/deploy_ha_server.sh`) sets
   `header_up X-Forwarded-For {remote_host}`: one value, Caddy's own view of the peer; a
   client-sent value never reaches the API. `deploy_ha_server.sh` writes
   `VPN_TRUSTED_PROXY_ADDRESS=127.0.0.1` into the backup API's environment.
3. **Window-tunnel limits.** The window routes no longer use `device`:
   - before anything else, per client network: `window_ip`, 120 per minute (several users may
     share a carrier NAT), no global bucket;
   - routes with an access token (create, renew, renew-token, revoke, panel profile): then
     `window_device`, 30 per minute per session family (one signed-in device). The family is
     read from the credential row without locks, in the rate transaction, before full
     authentication. An unknown token is not counted there (no family) and fails
     authentication; the per-network bucket bounds it;
   - native status and native renew (renew token, no access token): `window_native`, 30 per
     minute per connection id from the path;
   - every count runs in its own short transaction, so it survives a later failure of the
     request (404, 409), and none of these operations has a global bucket.
4. Other operations (`start`, `verify`, `refresh`, `device` on device, account and legacy
   tunnel routes, mini app, links) keep their limits; they now see real client networks once
   the setting is configured.

## Security notes
- Trusting the header from anything but the local proxy would let a client choose its bucket.
  Only one exact address is trusted, and Caddy replaces the header.
- Someone who knows a connection id (random UUIDv4, visible only to its owner) can use up that
  connection's `window_native` bucket for a minute. The renew token is still required for any
  effect.
- No global bucket on window operations means a flood from many networks is limited only per
  network. That is the price of not letting any group of callers lock everyone out.

## Owner steps (not done here)
1. Add `VPN_TRUSTED_PROXY_ADDRESS=127.0.0.1` to the primary's `/etc/detourvpn-beta/api.env`.
2. Install the updated `Caddyfile.ip` (rendered with the node allowlist; see
   `scripts/connect_beta_node.py`, and note that `activate_beta_caddy_allowlist.ps1` renders a
   single node IP), validate and reload Caddy, then restart the API.
3. Deploy the API code. Until step 1 is done the API keeps the old single-bucket behaviour for
   non-window operations; window operations are already per session and per connection.

## Tests
- `services/api/tests/test_audit_stage2_rate_limit.py`: distinct proxied clients get distinct
  subjects; no trust without the setting or from a non-proxy peer; malformed headers fail
  closed; IPv6 /64 grouping; both Caddy configurations overwrite the header on every proxied
  route; (PostgreSQL) 120 unauthenticated native-renew calls from one network do not affect
  another network; the 31st authenticated request of one session is refused even when the
  requests fail with 404, while another user on the same network keeps working.
- Related: `test_authentication.py`, `test_window_service.py`, `test_peer_windows.py`,
  `test_window_funding.py`, `test_database_config.py`, `test_panel_delivery.py`,
  `infra/ansible/tests/test_beta_config.py`.

## Not verified
- No real Caddy, no deploy, no real client traffic or carrier NAT.
