# ADR-084 — beta.22 returns to the AWG GoBackend engine

**Status:** accepted for the isolated beta by the owner on 2026-11-07.
**Date:** 2026-11-07
**Issue:** I-030

## Context

Every build from beta.2 to beta.21 used the Libbox / Amnezia Box engine that was
adopted for hostname routing (ADR-079). Their physical results were native start
failures (`libbox_service_start`, `awg_device_up`, `awg_bind_ipv4_open`), two
SIGABRTs in `amneziawg-go` `Device.BindUpdate`, and runs without a confirmed
handshake or egress. The beta.21 attempt allowed by ADR-083 had an unknown
outcome, so that authorization is exhausted.

The pre-Libbox build (`0.1.0-beta.1`, `AwgProviderV1` over the pinned GoBackend
`v3.1.20260814`) reached a confirmed handshake on 2026-11-02, answered a
single-packet IPv4 probe and a hostname probe, and applied Android per-app
include/exclude correctly (I-029). It is not clean evidence of egress: its last
physical attempt on 2026-11-02 had a handshake with unanswered probes, and its
1 MiB windows were exhausted within seconds.

Hostname routing is a goal of ADR-079, which keeps it experimental. I-029
accepts per-app routing only. Nothing in the beta acceptance criteria requires
domains.

## Decision

1. Beta.22 (`0.1.0-beta.22`, versionCode 23) uses `AwgProviderV1` and the
   GoBackend `VpnService` with its Always-on callback. Package routing is
   `all`, `exclude` or `include`, built by `ClientProfile.packagesForMode`. The
   app's own package is excluded in `all` and `exclude`, so control-plane
   traffic never uses the tunnel.
2. The Libbox sources (`LibboxProviderV1`, `LibboxVpnService`, `LibboxConfigFactory`,
   `DomainRule`, `third_party/amnezia-box/*.patch`) stay in the tree and are not
   deleted. The beta build excludes `LibboxProviderV1` and `LibboxVpnService`
   from compilation, no longer builds or bundles the libbox AAR and removes its
   manifest service. Re-enabling it is a deliberate future change under ADR-079/080.
3. Domain routing is unavailable. The domain tab is hidden and the client never
   reads or sends domain rules. The native layer ignores rules for an engine
   without `domain-routing.v1` and clears any stored ones when split settings
   are saved. A connect with stored domains and no such capability fails closed.
4. The window request is 16 MiB (`VPN_WINDOW_BUDGET_BYTES=16777216`), within the
   API range 64 KiB to 64 MiB. The duration stays at 90 s. An `unknown` window
   charges its full hold under ADR-082, so a failure costs up to 16 MiB.
5. Owner acceptance of beta.22 allows at most three owner Connect attempts, each
   following the preflight, bounded probe and postflight sequence of ADR-083.
   Another attempt after those needs a new ADR.

## Consequences and limits

- Domain routing, `domain-routing.v1` and the hostname UI are absent from the beta.
- The proven GoBackend path still needs a controlled acceptance run: confirmed
  handshake, source-bound egress, disconnect, server closure and ledger
  reconciliation. A build, UI message or quota debit is not that evidence.
- The 16 MiB window raises the held quota per connection. Renewal pins a second
  hold while the first is open, so an owner needs at least 32 MiB available.
- Always-on and Lockdown remain unaccepted; no kill switch is claimed.

## Verification

Dart formatting and analysis of the touched files, `split_apps`, `window_connection`
and `quota` Flutter tests, Android unit tests and lint through the build script,
and signed-APK verification by `scripts/verify_beta_apk.py`. Physical acceptance
follows the I-030 runbook.
