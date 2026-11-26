# ADR-198 — Routing of the dead-peer health probe (`bind_refused`)

**Status:** proposed 2026-11-27 (overnight run, step A3). **Owner decision needed.** No behaviour
change was made; only the code comment in `TunnelProbeRunner` now points here.
**Builds on:** ADR-132 (routing: the app excludes its own package from its VPN), ADR-148 (dead-peer
ladder and active probe), ADR-140 (native renewal over protected HTTPS), ADR-099/146 (watchdog).

## Problem

ADR-148 sends one HTTPS `GET` through the tunnel when a stall is suspected, by binding the
request to the VPN `Network` (`TunnelProbeRunner`). But in every split mode the app's own UID is
outside its VPN:

- "all" and "exclude" add the app's package to the excluded applications;
- "include" lists only the chosen apps, so the app is not in it either (ADR-132).

Android (netd) lets a UID select a VPN network only when that VPN applies to the UID. The probe's
test bind (`Network.bindSocket` on a throwaway UDP socket) is therefore expected to fail on a phone,
so every probe would end as `UNAVAILABLE` / `bind_refused`. That result never escalates and pauses
probing for 10 min, and with the shipped placeholder URL (`none`) the probe does not even start.
Net effect today: the ADR-148 ladder is inert and stalls are handled only by the watchdog's 180 s
handshake rule, exactly as before ADR-148. This is **expected from the Android rules, not measured
on a device**.

## Options

| # | Option | What changes | For | Against |
|---|---|---|---|---|
| 1 | **Keep the probe off (status quo)** | Nothing. URL stays `none`. | No risk; current behaviour already reviewed. | Silent drops with fresh handshakes are not detected; only the 180 s rule acts. |
| 2 | Put the app's own UID inside the VPN | Stop excluding the package; make API, renewal and update traffic use protected sockets or the physical network explicitly. | The HTTPS probe then really tests the tunnel. | Large routing change to the security-sensitive control path (ADR-132/140/149); Lockdown interplay; needs a full device and leak re-test. Per-request inclusion is not possible: VPN membership is per UID for the whole interface. |
| 3 | Send the probe over a protected socket | — | — | **Rejected:** a protected socket bypasses the tunnel, so it proves nothing about the tunnel. |
| 4 | Engine-level liveness | Ask the tunnel engine to start a handshake and watch for a newer handshake within 5 s, instead of HTTP. | No routing change, no health endpoint, no extra server logging. Proves the peer and the UDP path. | Needs an engine capability (not known to exist in the pinned AmneziaWG backend; adapter capability check required). Does not prove egress beyond the node. |
| 5 | Passive data-stall step | Use the existing `DataPathWatch.suspect` (outbound without inbound for 60 s) as the "failed probe" signal for the ladder: one restart in place, then a new lease, with the existing settle and backoff. | Pure Java, unit-testable, no endpoint, no routing change. | False positives possible (a server that stops answering one app looks the same); behaviour change on every phone; constants are not measured. |

## Proposed decision (recommendation)

1. **Now (beta): option 1.** Keep the probe off; ship as is. The ladder code stays, inert and
   tested.
2. **First device check** (A5 checklist, phase 5): with a test build whose probe URL is a
   project-owned endpoint, confirm whether the event log shows `probe_unavailable bind_refused`.
   Only codes are logged, so this needs no traffic capture.
3. If `bind_refused` is confirmed: choose **option 5** as the next small issue (tests first,
   behind a manifest flag that ships off), and investigate **option 4** as the better long-term
   signal if the engine can expose it. **Do not take option 2 for the beta.**
4. If the bind unexpectedly succeeds on the target Android versions, the owner may instead
   provide the health endpoint and enable the probe as ADR-148 describes.

## Security and privacy

- Options 1, 4 and 5 send no extra traffic to any third party and need no new server endpoint.
- Option 2 would change what traffic of this app can leave outside the tunnel; it needs its own
  ADR and leak test.
- No URL, host, address or network id is logged in any option (ADR-148 codes only).

## Not verified

- No device run. The `bind_refused` outcome is reasoned from Android's per-UID VPN rules, not
  observed.
- No engine capability for a forced handshake was checked in the pinned backend.

## Verification for this ADR

Comment-only code change (`TunnelProbeRunner`). Java substitute re-run after the edit; see the
morning report for the result.
