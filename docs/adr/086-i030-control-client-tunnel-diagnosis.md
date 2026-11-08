# ADR-086 — I-030 control-client diagnosis of the beta.22 handshake failure

**Status:** DRAFT, needs owner acceptance before any measurement.
**Date:** 2026-11-08
**Issue:** I-030

## Context

All three owner Connect attempts allowed by ADR-084 are used. Evidence
(`docs/evidence/i-030-beta22-udp-path-diagnosis-20261107T204251Z.json`): the node,
slot, 16 MiB window and control path are healthy; on Wi-Fi no UDP reached the
slot; on LTE one 148-byte request reached the peer and a 116-byte response was
sent, then nothing, and the phone stayed at "waiting for handshake" with
`tun0` tx 15 / rx 0. The AWG profile is shared by the API and the node
(`vpn_api/tunnels/awg_profile.py`, `infra/staging-v3/runtime.py`), so a plain
parameter mismatch is unlikely. The cause may be our client, the phone network,
or the node (window rotation, engine versions). Repeating phone attempts cannot
separate these. Note: beta.1 on the same GoBackend `v3.1.20260814` reached a
handshake on 2026-11-02, so what changed since (beta slots, gate, window
rotation, 16 MiB windows, beta.22 client glue) is the first suspect.

## Decision (proposed)

1. Measure with a **control client**: the official AmneziaVPN client already
   installed on the owner PC, with the same AWG parameters and a **separate
   PC-generated key pair** (private key stays on the PC), against the same beta
   slot through one normal window.
2. Run on the PC over wired internet or a hotspot. The result is read from the
   node, read-only: peer counters, latest handshake, gate counter.
3. Budget: at most **2 control connects**, each with the ADR-083 preflight,
   bounded probe and postflight; the owner is present. Up to 16 MiB per connect
   is accepted.
4. Reading the result:
   - handshake completes: node and profile are fine; next compare the official
     client on the phone, then our client, to find the client or network cause;
   - no handshake: inspect the node side (window rotation vs. handshake timing,
     node `amneziawg-go` vs. Android GoBackend engine versions).
5. No node configuration change, no new endpoint, no packet content capture.
   A header-only capture (60-90 s, slot port) needs a separate owner "yes".

## Consequences and limits

- A working PC handshake is not a claim of a working beta.
- Quota is spent and must be reconciled afterwards.
## Path for the PC key (found in code, no code change needed)

1. Generate an x25519 key pair on the PC (official client or `awg genkey`); the
   private key never leaves the PC.
2. Log in as the owner through the existing mini-app flow
   (`POST /api/v1/auth/mini-app/request-code`, then `/api/v1/auth/mini-app`).
   Result: access token; the session family has no device yet.
3. `POST /api/v1/devices` with `platform="android"` (the only value accepted by
   `DeviceInput`, used here only as a label), `key_type="x25519"` and the PC
   public key. `MAX_ACTIVE_DEVICES = 3`; the phone is the first device, so the
   PC fits. A session binds to one device, so this needs a new session, not the
   phone's.
4. `POST /api/v1/window-tunnels` for that `device_id` with the 16 MiB budget
   (as beta.22 does). The response `client_config` has the slot endpoint and
   the AWG parameters; the PC adds its own PrivateKey locally and imports it.
5. After the test, `DELETE /api/v1/devices/{id}` for the PC device; confirm the
   window is closed and the peer is removed on the node.

### Corrections found while preparing the script (supersede the text above)

- **The official AmneziaVPN client cannot be the control client.** The profile
  from `WindowSlot.client_config()` contains a project-specific
  `HeaderProtectionKey` (also in `beta_slot_bootstrap.py`, `LibboxConfigFactory`).
  Stock clients do not know this field. The control client must be the project's
  pinned `awg` / `amneziawg-go` build (node hash `84a3913e…`), run from WSL
  (`kali-linux` is installed on the PC) or in a netns on the node.
- **Login does not use Telegram initData.** Use `POST /api/v1/auth/start` and
  `POST /api/v1/auth/verify` (bot code, as in `scripts/local_auth_client.py`).
- **Window creation needs a key proof.** `POST /api/v1/window-tunnels/..key-challenge`
  (`routes.py`) returns `id`, `ephemeral_public_key`, `transcript`; the client
  answers `proof_response = HMAC-SHA256(X25519(device_private, ephemeral_public),
  transcript)` as hex (64 chars), then sends `ConnectionInput`: `device_id`,
  `node_id`, `idempotency_key`, `budget_bytes`, `proof_id`, `proof_response`.
- New suspect: the Android GoBackend (`go-only.patch`, `v3.1.20260814`) and the
  node core may implement `HeaderProtectionKey` differently. A control client
  built from the node's pinned source separates this from the network.

### Results (2026-11-08, owner present, `scripts/i030_control_client.py`)

| Run | Network | Result |
| --- | --- | --- |
| 1 | owner PC at home, WSL | handshake=false, rx 0 B, tx 4356 B over 45 s (11 attempts) |
| 2 | datacenter test host (SSH), same client/profile/key flow | handshake=true, rx 188 B, tx 3062 B, rx grows steadily |

Run 2 shows the node, the slot, the AWG profile with `HeaderProtectionKey`, the API
window flow and the pinned core all work. The same flow fails from the home
network. So the primary failure is the network path, not the node or profile.
Caveats: in run 2 the rx growth (32 B steps) looks like keepalive traffic; ICMP
replies from the tunnel peer were not shown, so egress is not proven. The phone
LTE case (148 B in, 116 B out, then silence) is still unexplained: it is either
the carrier path or the beta.22 client, and was not retested.
Budget: both control runs used. Quota debit and window closure to be confirmed
with the owner preflight.

Remaining risks: the login code arrives through the Telegram bot and needs the
owner; the exact request bodies of steps 2 and 4 must be copied from
`window_routes.py` / `mini_app.py` and tested locally first; the official client
may not accept every AWG 2.0 field (S3/S4, I1-I5), which would need a fallback
decision before a second connect.
