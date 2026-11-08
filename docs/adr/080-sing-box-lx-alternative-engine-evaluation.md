# ADR-080: evaluate sing-box-lx as an alternate Android AWG engine

**Status:** accepted for a bounded compatibility spike; engine adoption is not accepted.

**Date:** 2026-11-03

**Issue:** I-030

## Context

The signed beta.12, beta.13 and beta.14 builds all failed on the Pixel at
`libbox_service_start` / `awg_device_up`; beta.14's allowlisted classifier did
not identify a narrower cause. The beta.13 artifact contained the Amnezia Box
context-propagation patch, so that patch is not a sufficient root-cause
explanation. The latest physical result does not prove whether the defect is in
the engine, Android platform adapter, or generated configuration.

The client requires both package-based and hostname-based routing while keeping
the existing AmneziaWG 3.x server profile and IPv6-blocking policy. The prior
`AwgProviderV1` GoBackend path is a comparison for package split, but it
does not supply hostname route rules; the current Libbox config builder emits
an Amnezia Box `type: "awg"` endpoint and the Android adapter reads an added
`peerStats()` binding.

## Decision

Evaluate the pinned `Leadaxe/sing-box-lx` core in an isolated build/config
compatibility spike. Do not switch the beta client, change the server transport,
or make another physical Connect attempt based on source claims alone.

The reviewed source pin is commit
`a3bb4a0418c70ae1db650e4fbd06def1ddb5bc70`; its AWG runtime submodule is pinned
by that commit to `7188e1516995adbb116e7dbe343b9eb1c2159c82`. The fork documents
an Android `libbox.aar`, an optional `with_awg` build feature, AWG 3.x fields,
and sing-box route rules for packages and domains. Those are upstream project
claims, not DetourVpn acceptance evidence. There is a version-documentation
mismatch: the core documents AWG 3.x/3.1, while the L×Box app README describes
AWG 2.0. Exact support for DetourVpn's exported AWG 3.1 profile remains to be
validated against the pinned AAR. The current sing-box routing
documentation lists package and domain match fields; Android itself provides
per-app routing through `VpnService.Builder` allowed/disallowed application
lists. The official Amnezia client routes domain entries through a one-time IP
resolution snapshot, which does not meet the desired dynamic hostname-rule
behavior.

This is not a drop-in AAR replacement:

- The current profile builder emits `type: "awg"`; the candidate uses a
  `type: "wireguard"` endpoint with AWG fields behind `with_awg`.
- The current app calls `Libbox.newService()` and a DetourVpn-specific
  `BoxService.peerStats()`. The candidate instead binds `CommandServer` and
  `CommandClient`; peer status is exposed through the fork's
  `GetWireGuardStatus` command extension when its build tag is included. This
  requires a service lifecycle and status adapter, not just a renamed method.
- Although the Java package name matches, the candidate's required
  `PlatformInterface` is incompatible with the current Android implementation:
  it adds shell/user/SFTP/system-SSH/Tailscale/bridge methods and changes the
  connection-owner contract. The Android adapter has no implementation for
  these methods. Its obsolete override methods also need reconciliation before
  the app can compile against the candidate API.
- Both engines still create a userspace WireGuard/AWG device and call its
  `Up()` during endpoint start. The candidate's runtime is different, so it may
  help if the defect is specific to Amnezia Box's implementation; it will not
  inherently fix a failure in Android `VpnService`, socket protection, routing,
  or invalid profile parameters.
- The candidate is GPL-3.0-or-later and its source tree pins Go 1.26.10 plus
  forked dependencies. Redistribution requires a complete license/source-offer
  review and updated notices/SBOM before any beta publication. The current
  DetourVpn Amnezia Box build script pins Go 1.25.5, so candidate adoption also
  changes the native toolchain and requires a fresh dependency/security scan.
  No independent security audit or support commitment was established in this
  review.

## Adoption gates

Before proposing an engine change, the spike must:

1. Build an immutable Android AAR from the reviewed commit and pinned
   submodules, recording hashes and the complete dependency/license inventory.
2. Validate a sanitized AWG 3.1 configuration with both app and domain rules;
   verify that include mode, DNS routing, and IPv6 blocking retain the existing
   security contract.
3. Compile the DetourVpn adapter and unit tests against that exact AAR without
   logging keys, endpoints, raw errors, or browsing data.
4. Obtain independent server confirmation that any prior revoke is `closed`
   before a supervised Pixel test. Physical acceptance must separately prove
   start, fresh handshake, constrained egress, disconnect/revoke, and the
   selected split routes. No websites or background traffic are needed.
5. Complete GPL compatibility, notices, SBOM, and source-offer review before
   distributing any artifact that includes the fork.

The owner authorized one bounded, instrumented diagnostic Connect on the
installed beta.19 in the 2026-11-05 work session. This narrowly supersedes the
no-Connect-until-diagnosed stop only to obtain the missing beta.19 trace; it does
not authorize retries, quota changes, distribution, or fallback to direct
traffic. Before that one attempt, the fresh read-only owner preflight must
confirm the signed beta.19 artifact on one authorized Pixel, no active VPN or
lockdown, `latest_state=closed`, zero non-closed connections and reserved
bytes, and at least one 1 MiB window available. The app must use include-only
Chrome routing, with no unrelated VPN active. If startup aborts or the trace
does not establish a safe state, stop: do not retry or send egress probes.

If the diagnostic start succeeds, continue only with the bounded physical
acceptance sequence in the I-030 runbook: fresh handshake, one controlled
source-bound egress check, disconnect, independently confirmed server closure,
and fresh quota reconciliation. This diagnostic exception does not itself
close any physical acceptance gate. Do not silently fall back to direct
traffic.

## Sources

- [sing-box-lx source and Android AAR build instructions](https://github.com/Leadaxe/sing-box-lx/tree/lx)
- [sing-box-lx AWG 3.x and build-tag configuration](https://github.com/Leadaxe/sing-box-lx/blob/lx/docs-lx/lx-config.md)
- [L×Box Android client capabilities](https://github.com/Leadaxe/LxBox)
- [sing-box route-rule reference](https://sing-box.sagernet.org/configuration/route/rule/)
- [Android per-app VPN documentation](https://developer.android.com/develop/connectivity/vpn)
- [Amnezia split tunneling, including domain-to-IP snapshot behavior](https://docs.amnezia.org/documentation/instructions/vpn-split-tunneling/)

## Spike result (2026-11-03)

The pinned AWG transport test passed:
`go test -tags with_awg ./transport/wireguard` (`ok`, 3.243s). The user
authorized cleanup of only `%LOCALAPPDATA%\go-build`; the exact Go cache path
and absence of active Go processes were checked before `go clean -cache`.

A full upstream Android AAR build did not finish because optional components
exhausted available temporary disk. A separate minimal arm64 AAR was built from
the pinned tree (SHA-256
`62ff2aec004bae2131ca739cd24b63e88abb25bbf57b8fcc3c27f0f9e79874d5`). This is
an exploratory core artifact; it omits the full upstream build tags and has
not been integrated into the app or used to validate the AWG 3.1 config. ADB
reported no attached device, and no Connect was attempted.

Other paths have different tradeoffs: the existing GoBackend/AWG provider
already offers Android package split but not dynamic domain rules; the
official AmneziaWG Android app is a standalone client and does not supply the
combined embeddable engine/API needed here; upstream sing-box Android libbox
offers the route-rule model but the standard WireGuard endpoint does not
establish compatibility with DetourVpn's AmneziaWG 3.1 server profile. The
sing-box-lx fork is the only candidate reviewed here that claims both rule
types and AWG support in one core, but its current Android release description
states AWG 2.0 while the pinned tree docs state 3.x/3.1. It is therefore the
best candidate for a compatibility port, not a safe replacement yet.

The exact findings are recorded in
`docs/evidence/i-030-sing-box-lx-source-evaluation-20261103.json`. The spike
does not claim a repaired VPN, validated DetourVpn config, physical acceptance,
or beta readiness.

## Beta.20 bounded retest authorization (2026-11-05)

After the single beta.19 diagnostic attempt ended in a native crash, the owner
explicitly requested a fix and another attempt to close I-030. This supersedes
the earlier no-retry instruction for exactly one supervised beta.20 physical
acceptance attempt after all of the following are true: the patched pinned
arm64 AAR and signed beta.20 APK are built and hash-verified; beta.20 is
installed over beta.19; a fresh owner-authenticated read-only preflight confirms
the connection is closed, no reservation remains, revoke gate is closed, and
at least one window is available; Android VPN transport and lockdown are off;
and only the selected Chrome test route is enabled with no other VPN active.

The beta.19 crash root cause is not established. The beta.20 source patch fixes
a confirmed concurrent read/write race in default-interface network-cost state
and serializes interface-update callbacks; it is a targeted mitigation in the
observed `BindUpdate`-reachable path, not proof that the race caused SIGABRT.
If beta.20 crashes or fails to start, stop and preserve the quota/revoke
readback; no additional retry is authorized. If it starts, continue only with
the bounded physical acceptance sequence in the I-030 runbook and stop on any
uncertain handshake, egress, disconnect, or server-closure result. No quota
change, tester distribution, or fallback to direct traffic is authorized.

The patched AAR and source-level race test are recorded in
`../evidence/i-030-beta20-network-monitor-race-20261105.json`. Signing inputs
must remain in the owner's local build prompt.

## beta.21 quota-display correction (2026-11-05)

The single beta.20 physical attempt authorized above has occurred. The owner
reported that Chrome could not load the selected public IP-check page while the
VPN was active; after automatic VPN shutdown, Chrome reloaded directly. This
did not establish handshake, egress, or source attribution. No additional
Connect attempt is authorized by this ADR.

An independent I-030 client defect was found: the quota-history parser rejected
the backend's audited `peer_window_resolution` ledger source, preventing a
successful atomic quota refresh when that entry was present in the loaded
page. beta.21 increments the app version solely to deliver this parser fix and
does not alter tunnel behavior or authorize another physical VPN attempt.
Permitted device verification is limited to installation and refreshing quota
and node status with Android VPN transport off. Any further Connect requires a
new explicit ADR decision after reviewing beta.21 evidence.

