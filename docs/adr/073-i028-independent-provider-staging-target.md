# ADR-073 — I-028 independent-provider staging target

Status: accepted by the owner on 2026-10-31 for one bounded staging drill.

## Context

ADR-072 removed the broad DNS/IPv6 capture from the owner's bounded I-028
completion checklist. The remaining physical criterion is the actual manual
recovery flow after loss of the current provider's VPN node. Existing A/B
staging targets are same-provider and cannot establish that criterion.

The owner purchased and approved one XorekCloud DE-E-2 VPS for this test:
`150.241.99.227`, Ubuntu 22.04.5 LTS, 1 vCPU, approximately 1.9 GiB RAM,
30 GiB disk. The owner verified the SSH host-key SHA-256 fingerprint against
the provider's VNC console. A read-only baseline observed root access, 26 GiB
free disk, synchronized NTP and no installed test runtime. No service has been
installed or started on this VPS by this ADR.

Public BGP lookups report the existing A prefix `45.10.166.0/24` originated by
AS41745 and the new Xorek prefix `150.241.99.0/24` originated by AS215590.
This supports network-level separation for this bounded provider test; it does
not establish that all upstream transit, datacenter or corporate dependencies
are independent.

## Decision

- Authorize the exact pair `45.10.166.106` (A) and `150.241.99.227` (B) for
  one I-019 manual failover drill to supply I-028's independent-provider gate.
- Reuse the existing fixed-action I-019 staging runtime and physical Android
  test. Manual recovery remains explicit; no automatic or seamless failover is
  claimed.
- Permit the artifact-only B staging playbook on this exact host with Ubuntu
  22.04 amd64 and systemd, only under the existing explicit second-node opt-in.
  All host, assigned-address, TUN, pinned-artifact, timer and cleanup checks
  remain mandatory. Other staging targets remain restricted to their existing
  supported OS and inventory.
- Keep the existing same-provider receipts unchanged. This target change does
  not alter the production API/database, Telegram, account credentials or
  user-facing node registry.
- Do not mark the independent-provider criterion passed until the new physical
  receipt demonstrates A failure, deadline/recovery behavior, fresh egress via
  B, exact ledger settlement, cleanup and restoration.

## Verification — 2026-11-01

The physical receipt
`../evidence/i-019-attempt-2df6394bc51e41e3b1fb066a1ba7e395.json` passed the
bounded criterion: all 15 facts, fresh A/B egress, both recovery paths, four
settlements, two full lost-counter debits, zero reserved bytes, and cleanup on
both hosts. It verifies network separation by distinct origin ASNs only; full
upstream/provider-chain independence and automatic failover remain unverified.

## Consequences and limits

The current I-019 runner remains an owner-operated staging tool; its SSH key and
sudo passwords stay local. Before artifact staging, create/use the dedicated
`vpn-deploy` account and verify the new host through the existing pinned SSH
trust. No production root credential is involved. A failure or missing host
precondition leaves this criterion open and must not be retried blindly.

The first physical attempt found that Ubuntu 22.04's system Python 3.10 cannot
import `datetime.UTC`, which the shared node agent used. The agent now uses the
standard-library `datetime.timezone.utc`, compatible with Python 3.10 and newer;
the staging playbook must reinstall the pinned source before another attempt.
New I-019 receipts describe the approved pair as separate origin ASNs. They do
not claim that every upstream, datacenter, or corporate dependency is separate.
The next physical run found the Android egress oracle still expected the former
B address `85.209.132.205`; the approved Xorek address is `150.241.99.227`. The
test fixture and a local regression check now use the approved pair.

The first cross-provider physical attempt also observed repeated B command
delivery with no lifecycle ACK reaching the API. The staging agent now emits
only a fixed request stage and HTTP status or allow-listed command fault code/
exception category (never a response body or request data). The test-only API
observer records only lifecycle endpoint, HTTP status, allow-listed API error
code and allow-listed exception class. The fixed `diagnose` action returns only
the agent's allow-listed diagnostic history.
This is diagnostic instrumentation; it does not change production routes,
authentication, peer admission, or firewall policy. A fresh bounded run is
required to identify and verify the ACK failure cause.

The subsequent receipt isolated the B command failure as `command_value_error`.
The wire lease deadline is serialized with the ISO-8601 UTC `Z` suffix, which
Python 3.10's `datetime.fromisoformat()` does not accept. The shared agent now
normalizes that suffix before parsing both incoming lease deadlines and journal
expiry deadlines, and rejects timestamps without a timezone. A regression test
covers both UTC encodings and the timezone requirement. The next staging run
must install this source on B before the independent-provider drill can pass.

The next physical attempt reached a fresh B handshake but reported
`other_address` at the egress check. Inspection showed this was an invalid test
oracle: `ClientActivity` deliberately excludes its own application UID from the
VPN so control-plane calls remain reachable, while the Flutter acceptance test
queried `api.ipify.org` from that same UID. The test therefore measured the
phone's ordinary route rather than the selected VPN node. Production routing
is unchanged. The I-019 test now invokes the fixed IPv4 probe through the
separately installed I-028 instrumentation app UID; the Android side compares
the response in memory and returns only `node_a`, `node_b`, `other_address`, or
a fixed failure category. APK hashes and package identities are checked before
installation, pre-existing probe packages are preserved by refusing to run,
and both test packages are removed during cleanup. A new physical run is
required; earlier `other_address` results from the VPN client UID are not B
egress evidence.

The probe also supplies the first outbound packet after tunnel configuration.
For I-019 it first makes one bounded TCP connect to fixed IPv4 literal
`1.1.1.1:443` without DNS, from the separate test-app UID. This creates tunnel
traffic even when hostname resolution fails. The connect outcome is not an
acceptance fact; the confirmed native handshake and two fresh HTTPS egress
responses identifying the selected node remain required. No payload or address
is written to the receipt. Physical attempt
`evidence/i-019-attempt-5b914fde876e4c0e866bcce4729835f8.json` still had no
fresh B handshake and four DNS errors; the trigger connect outcome was not
retained, so the cause remains unknown. The test-only probe and receipt now
retain only a fixed connect outcome category for this literal trigger; no
address, payload or exception text is retained. The diagnostic change passed
local checks and still requires physical verification.
WireGuard starts a handshake on demand, so waiting for a handshake before
generating traffic can leave a reconnect test waiting when the phone has no
background traffic. The integration test now triggers one independent-UID
egress probe before waiting for the handshake. A successful response from the
ordinary route or the wrong node still fails the attempt; only a fixed
transient probe error is tolerated while the native handshake is pending.

The public route records are supporting evidence only:

- [A prefix and AS41745](https://ipv4.bgp.he.net/net/45.10.166.0/24)
- [Xorek AS215590 and registered upstream](https://bgp.he.net/AS215590)

Neither this bounded test nor prior component receipts establish commercial
readiness, complete provider-chain independence or device-wide leak freedom.
