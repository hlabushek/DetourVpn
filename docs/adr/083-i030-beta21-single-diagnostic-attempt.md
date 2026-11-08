# ADR-083 — one bounded beta.21 I-030 diagnostic attempt

**Status:** accepted for isolated beta under the owner's delegated authority
to make decisions that accelerate beta launch.
**Date:** 2026-11-05
**Issue:** I-030

## Context

The single beta.20 attempt ended without confirmed handshake or egress. Its
sanitized trace confirms `libbox_service.start()` returned and seven network
callback updates returned. It does not establish that Android opened the TUN,
that a peer handshake occurred, or that packets reached the node. The attempt's
initial native-tag collection did not retain INFO-level platform markers; the
later INFO-level read was too late to recover them. The owner's post-attempt
read-only preflight passed with a closed connection, zero reserve, and a closed
revoke gate. Beta.21 is installed and the owner confirmed quota/API and node
catalog refresh in VPN-off mode. No tunnel implementation changed in beta.21.

The client has a bounded server-issued lease and closes it on expiration. The
owner's report of automatic shutdown is compatible with this fail-closed
cleanup, but the precise shutdown cause was not captured and is not asserted.

## Decision

Authorize exactly one beta.21 diagnostic Connect, solely to distinguish native
TUN startup from a missing handshake/return path and to capture the fixed
INFO-level markers. This decision supersedes ADR-080's no-retry rule only for
this attempt. It is not a declaration that the beta.20 failure is fixed and
does not authorize distribution.

Before Connect, all of these conditions must pass:

- Run `scripts/i030_beta_owner_preflight.py` locally. Its hidden prompts remain
  local; output must show a closed latest state, zero non-closed connections,
  zero reserved bytes, closed revoke gate, and at least one funded window.
- Read-only ADB must confirm beta.21/versionCode 22, the installed APK matches
  its signed receipt, VPN transport is off, Always-on is off, and lockdown is
  off.
- The owner confirms that only Chrome is routed through the VPN and no other
  VPN is active. Keep the direct baseline local.

Then press Connect once, open the same fixed public IP-check page in Chrome,
and observe the app until it either confirms a fresh handshake or fails/closes.
If the fresh handshake is confirmed and the page loads with an egress result
different from the owner's direct baseline, stop browsing and disconnect once.
Do not retry, renew manually, change routes, or send additional probes. Do not
share IP values, screenshots, raw logs, or tunnel configuration. Immediately
after disconnection or automatic closure, run the read-only native trace
collector and owner preflight again. Stop if any result is ambiguous.

The app lease is capped by its existing 1 MiB window budget. No quota grant,
ledger adjustment, operator resolution, or server configuration change is
authorized. A passed browser check alone does not prove server-side packet
attribution; retain the control-plane settlement and tunnel evidence as
separate facts.

## Verification and stop condition

The beta.21 owner preflight, filtered logcat collector, and post-attempt owner
preflight are required evidence. If the attempt fails or lacks a handshake,
this authorization is exhausted. Diagnose from the collected sanitized events
and source; no further Connect is allowed without another ADR decision.
