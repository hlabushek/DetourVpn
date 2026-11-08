# ADR-081: provide a strictly read-only beta v3 peer status view

**Status:** accepted and deployed to the isolated beta control plane for I-030.

**Date:** 2026-11-03

**Issue:** I-030

## Context

The owner needs an independent server check of v3 peer closure before another
physical VPN attempt. The owner-facing `GET /api/v1/window-tunnels` and detail
routes call `view()`. That helper may change an `active` connection to
`revoke_pending` when its grant is no longer authorized. The routes can also
return `client_config` for an active connection. They are therefore unsuitable
for a strict read-only support check and their complete response must not be
printed or saved.

The legacy admin node status counts only `Tunnel` and `PeerCommand` rows; it
does not expose v3 window peer state. The admin user detail read already has
MFA-protected access to owner quota and device summary.

## Decision

Add a separate MFA-admin-only `GET /api/admin/v1/users/{user_id}/window-status`
that reads raw v3 state without calling `view()` or updating `PeerConnection`.
It returns only:

- newest connection state (or null when no connection exists),
- count of all non-closed owner v3 connections,
- owner `reserved_bytes`.

It returns no account/device/connection IDs, endpoint, public or private keys,
configuration, traffic destinations, or peer payload. The endpoint acquires the
existing owner ledger lock to serialize the snapshot with admission and ledger
writes; the lock itself changes no database row. The local PowerShell helper
prints only the three returned values and logs out its temporary admin session.

## Security and limits

- The endpoint is a read under the existing MFA admin role and does not revoke,
  settle, or otherwise change peer state.
- `latest_connection_state=closed`, `non_closed_connection_count=0`, and
  `reserved_bytes=0` are jointly required before a new physical attempt.
- This reports control-plane state and reservation only. It does not prove the
  node removed its runtime peer unless the existing server lifecycle has
  recorded that close into `PeerConnection.state`.
- Deployment is limited to the isolated beta control plane at the verified
  `hiplet-137728` host. It changes only `services/api/src/vpn_api/admin.py`;
  it adds no migration and changes no database rows. It does not authorize
  changes to `infra/staging-v3`, production, or the VPN data plane.

## Verification

The local route test seeds one synthetic active peer and asserts the response
contains only the sanitized fields and the database state remains `active` after
the GET. API contract is represented by a Pydantic response model and therefore
appears in generated OpenAPI. The PowerShell helper is parse-checked locally;
no credentials are supplied to it by automation.
The beta rollout verified the previous and deployed source hashes, API
readiness `200`, and `401` from the new route without credentials. Sanitized
receipt: `../evidence/i-030-admin-status-endpoint-deploy-20261103T211650Z.json`.
The authenticated owner status read is pending; no VPN Connect was made.
