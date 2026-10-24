# ADR-041 — bounded v3 staging transport and operator boundary

2026-10-23. Owner requested the missing staging and physical VPN tests after
I-025 native/local-auth acceptance. This is I-028 bounded test scope, not release.

Use staging A only; B's live API/DB/Caddy is not changed. Two fixed slots use
separate netns/core/UDP ports 51901/51902. The host gains only dedicated named
NAT/FORWARD/INPUT rules and veths; namespaces deny private/special/host/other-slot
destinations. Existing chains are never flushed or restored from a broad dump.
Each slot's actual WindowRuntime/SlotGate/SlotPeer enforces <=90s/64MiB windows.

The control plane is disposable real PostgreSQL/API on the owner's PC. Device
debug HTTP uses USB ADB reverse on 127.0.0.1:18766. The root node's fixed Control
uses verified HTTPS localhost:443; a loopback-only TLS relay forwards only the
three internal v3 paths over a scoped SSH reverse tunnel to that local API.
On this Windows/WSL NAT machine, the tunnel's local end is WSL loopback:18767;
a private subprocess stdin/stdout relay connects it to Windows loopback:18766.
This relay allows only the same three node paths, bounded bodies and the four
node authentication headers. It never binds a LAN address or writes request logs.
An ephemeral root-private localhost certificate is trusted only by the agent's
SSL_CERT_FILE. No certificate verification is disabled and no system/phone trust
store is modified. The API still verifies per-node credentials/nonces/timestamps.
No agent or DB endpoint is published on WAN. Synthetic OTP routes/sender exist
only in the disposable pytest application and test APKs.

Root-owned immutable code and pinned native/dependency artifacts precede any
privilege grant. The fixed helper permits only enumerated lifecycle verbs;
stdin contains strict node credential/UUID data, never shell/path commands.
An independent systemd cleanup timer bounds the complete exposure to 30 minutes;
slot kernel deadlines remain effective without API/agent/operator. The helper
does not extend that deadline on status/retry. Sudo password is entered only in
the owner's local interactive SSH terminal; no root SSH credentials are used.

Cleanup closes gates, stops agent/native cores, removes only own namespace and
host rules, restores only sysctls changed from the recorded baseline, revokes
temporary sudo grants and removes test secret material. Aggregate private journal
and sanitized evidence are handled separately; unknown holds are not guessed
usage. Cleanup errors fail acceptance and require targeted inspection.

These choices avoid changing a persistent backend before its separate auth,
backup and operations gates. They do not establish permanent fleet deployment,
provider independence, production readiness, guaranteed Doze wakeups, a universal
allowlist bypass or multiple simultaneous physical devices.

## I-028 continuation: delayed renewal response

The physical runner may delay one actual HTTP 202 renewal response for at most
six seconds in its disposable test API. The request, database funding, agent
command and node ACK remain real. The test waits for the active next generation,
disconnects native before releasing the response, repeats Disconnect and observes
that native stays down. Server gates/peers, settlement and zero holds are checked
separately. These coordination routes exist only in `test_i028_live.py`; no
production route, timeout, handshake threshold or renewal policy changes.

This exercises a delayed accepted response with a real ACK. It does not claim
all possible packet orderings. Natural suspension, forced idle and system
permission revocation require distinct observations; an app-op change cannot be
reported as an owner action through Android Settings. The positive IPv6 baseline
and independent-provider requirements remain mandatory. Existing A/B inventory
and ADR-033 identify only a same-provider pair, and v3 unknown-counter holds must
not be replaced with the older v2 full-budget recovery policy from ADR-031.

## Separate physical lifecycle suite

`Test -Suite Lifecycle` uses the same disposable control plane and actual A
runtime, with separate evidence and a fresh bounded installation after Main
cleanup. Only its test driver changes the phone: background/screen off/forced
deep idle for 100 seconds, temporary Wi-Fi/mobile switches, then owner revocation
through Android Settings. It refuses an existing battery/idle override and
unknown original network switches. Each owned restoration is attempted even
when another fails; restoration observations and overall cleanup gate acceptance.
The operator must confirm system revoke locally; the driver never manufactures
the revoke by changing app-ops. Test setup/cleanup still temporarily grants and
restores its previously recorded app-op as already documented above.

Positive IPv6 baseline now requires a parsed IPv6 address from the fixed HTTPS
probe, rather than merely a successful response. A baseline/included contrast
only establishes that scoped routed probe result. It is not exhaustive DNS/IPv6
leak observation across transitions and failures. USB charging and forced battery
unplug simulation do not constitute natural physical sleep acceptance.

The shorter `NetworkRevoke` suite isolates Wi-Fi/mobile/loss and Settings revoke
after a failed complete lifecycle run. The owner still performs the real Settings
action. Once the driver observes revoked app-op and native down, it foregrounds
only its own test activity to keep instrumentation running; it never restores
permission before verifying server closure, exact settlement and a 40-second
no-restart interval. The earlier Settings-timeout receipt remains failed and
source-bound; the subsequent physical receipt passed. Neither substitutes for
natural sleep, a full passing lifecycle run, broad leak capture, a second device
or independent-provider recovery.
