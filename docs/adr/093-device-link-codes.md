# ADR-093 — one-time device link codes and guest devices

Status: proposed 2026-11-08, from the owner's request in chat. Owner decisions the same day: a linked
device is a **guest**; traffic is drawn from the account's **shared balance** with an optional
per-device cap; the active device limit is **5**, later tied to the plan. Backend change only; no
deployment, no host touched.

## Context

Sign-in is Telegram-only (ADR-001/012). A session family is bound to one device (ADR-013), the
device limit was a code constant of 3, and the Mini-App could only list devices.

## Decision

**Code.** The owner creates a one-time code from the Mini-App (or a native owner session).
8 characters of Crockford base32 (about 40 bits), shown as `XXXX-XXXX`, valid 10 minutes, single
use. Only a keyed HMAC digest (`keyed_digest(pepper, "link-code", …)`, domain separated) is stored;
the code is returned once, never logged, never audited. Creation requires an idempotency key
(a replay returns the same row without the code: the owner cancels and creates a new one).

**Redeem.** `POST /api/v1/auth/link/redeem` (no auth, per-network and global rate limits) looks the
code up by digest `FOR UPDATE`, requires unexpired, unconsumed, unrevoked, marks it consumed and
issues a session family with `role = guest` for the owner's account. The device is then registered
by the normal `POST /devices` (key generated on the phone, ADR-013): the device inherits role,
label and traffic cap from the code, and the code records `consumed_device_id`. Brute force is
bounded by the rate limit against a 2^40 space and at most a handful of live codes; there is no
per-code failure counter because an unknown code has no row.

**Guest.** A guest session may: read `/me` (without the Telegram id), manage the tunnel, read quota,
register its own device, delete or log out **its own** device/session. A guest may not: create or
cancel codes, patch or delete other devices, `logout-all`, delete the account, claim rewards, use
referrals. Enforced server-side in `authenticate` and in the device routes, not in the UI. The guest
session depends on the owner's account and beta invite: revoking either ends it.

**Owner control.** `PATCH /devices/{id}` changes label and traffic cap; `DELETE /devices/{id}`
reuses the existing revocation chain (tunnels, windows, session families). Any non-guest session,
including the Mini-App, may do this. The last active owner device is not protected in this ADR.

**Limit.** `Settings.device_limit` (default 5, 1–50). Code creation requires
`active devices + live unconsumed codes < limit`; device registration keeps its own re-check, so a
race can at worst make the redeemer fail with `device_limit_reached`.

**Per-device traffic.** Balance stays per account. Per-device usage is the sum of measured window
receipts (v3) and charged usage epochs (v1) attributed through `device_id`; it is reported in
`GET /devices` (`used_bytes_total`, `used_bytes_30d`). Unknown/unmeasured windows count as zero, so
the figure is a lower bound, not billing.

**Cap.** If `traffic_cap_bytes` is set and the device's used total has reached it, a new or renewed
window is refused (`device_traffic_cap_reached`). A window already granted may overshoot by at most
its own budget; the account balance is always the hard limit.

## Consequences

- New table `device_link_codes`; new columns on `devices` (`role`, `label`, `traffic_cap_bytes`) and
  `session_families` (`role`, `link_code_id`); migration 0021.
- A friend with a code can spend the owner's traffic until the owner revokes the device; codes and
  device names are the only owner-visible handles.
- A guest cannot be told apart from the owner by the tunnel layer; accounting is per device only.

## Not covered

Android and Mini-App UI (stage F), the plan-dependent limit, rate limits behind a shared NAT, and
any physical verification of redeem → connect → remote disconnect.
