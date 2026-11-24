# ADR-095 — Windows beta client

**Status:** proposed 2026-11-10; plan approved by the owner the same day (`docs/issues/I-032-windows-beta.md`).
Engine viability is **not** accepted: it depends on the stage 1 gate below.
**Issue:** I-032
**Amends:** ADR-090 (Windows clients were listed as disabled during beta).
**Builds on:** ADR-079 (domain routing), ADR-080 (engine alternatives), ADR-081 (read-only peer status).

## Context

The closed beta is Android only. The owner wants a Windows client for the beta with sign-in, device
registration, a full tunnel, app exclusion and domain-based routing. Distribution is the same private
channel as the APK. The Flutter client has no third-party plugins, so the Windows work is a native
adapter behind the existing MethodChannel `project.vpn/tunnel.v1` plus an engine.

## Decision

### Engine
Run **Amnezia Box** (sing-box fork with an AWG endpoint), the same pinned upstream as ADR-079
(`v1.260910.0`, same local patches), as a separate process `detour-engine.exe`.

- TUN is created by the engine through `wintun.dll`. Wintun is loaded at run time (no Go build tag);
  the build tags are `with_gvisor,with_awg`, as on Android.
- App exclusion uses `process_path` rules and domain routing uses `domain` / `domain_suffix` rules in
  the same engine. No custom WFP driver.
- ADR-079 semantics apply unchanged on Windows: one split toggle and one direction shared by the
  Domains and Apps tabs, OR combination, `*.example.ru` matches the apex and subdomains, no guessing
  from shared IPs, rules never go to the API, engine failure stops the VPN instead of falling back to
  direct. Names are identified only by sniffing (TLS SNI, HTTP Host, QUIC) and by the hijacked DNS
  query. IP-to-domain reverse mapping and fakeip stay disabled.
- Control-plane bypass is a route rule `API IPv4 -> direct`, always present. It is the analogue of the
  Android own-package exclusion (ADR-078).
- Rule order: `sniff`, `hijack-dns`, API bypass, app and domain rules, `final`. The Android factory
  places `hijack-dns` before `sniff`; the sing-box documentation requires sniffing first because the
  `protocol: dns` match depends on it. This is an observed difference, not an explanation of the
  Android `awg_device_up` failure (ADR-080).
- IPv6: the TUN gets a local ULA address so `::/0` is captured, and a rule rejects every IPv6 flow.
  Without it Windows could send IPv6 around the tunnel. Needs confirmation at the gate.
- DNS: one UDP server through the tunnel (the profile DNS) and the system resolver for names that route
  direct, so a direct domain is not resolved by the VPN resolver. DNS rules mirror the routing rules.
- We write an adapter, not a tunnel (AGENTS.md). No custom cryptography.

**Gate.** Amnezia Box failed on Android at `awg_device_up` (ADR-080). Stage 1 is therefore a gate: the
owner runs the built engine with a real window profile and confirms handshake and egress on the
server's read-only peer status (ADR-081), and one test domain going direct in exclude mode. No later
stage starts before that result. There is no complete fallback engine: amneziawg-windows has no domain
routing. If the gate fails, debug the Amnezia Box build, endpoint and wintun first; the replacement
candidate is sing-box-lx (ADR-080). The owner chooses the engine in that case; this ADR is then revised.

### Elevation
The executable manifest is `requireAdministrator`: creating the TUN and routes needs it. UAC prompts at
every launch. No Windows service in the beta (smaller attack surface, less code). The engine runs as a
child in a Job Object so it ends with the app.

### Keys and storage
Windows CNG and DPAPI only, no new dependencies. X25519 through `BCRYPT_ECDH_ALGORITHM` with
`BCRYPT_ECC_CURVE_25519`, HMAC-SHA256 through CNG, vault through `CryptProtectData` (current user) in
`%LOCALAPPDATA%\DetourVpn\vault`. The device private key never reaches Dart or the backend, in parity
with `SecureVault` and `KeyConfirmation.java`. The engine config (which holds the key) goes to the
engine on stdin; if that cannot work, a temporary file with an owner-only ACL deleted right after
start. It is never logged.

### Distribution
Unsigned portable zip with a SHA-256, posted with the same private channel process as the APK. Testers
see a SmartScreen warning. No auto-update. x64 only. The zip ships the exact Amnezia Box source
reference and patches (GPL-3.0-or-later with the upstream additional term) and a notice for the
redistributed `wintun.dll` (prebuilt, signed by WireGuard LLC, pinned by SHA-256). No Amnezia branding.

### Platform on the backend
Devices register with `platform=windows`. This needs a migration and an API change deployed to the beta
by the owner (stage 5). Until then the gate uses an Android-labelled throwaway device, as the existing
control client does.

### Disabled on Windows
Always-on and "Работа в фоне" (battery) are hidden. Kill switch: optional and off by default (ADR-196,
not leak-tested); with it off, when the engine dies or restarts, traffic can go direct until it is back.
No tray mode (ADR-194). Rules, domains and app paths stay
local. An in-app DoH/ECH client or a cached answer can hide a name, so a domain rule is not guaranteed
(ADR-079). The beta limits in ADR-090 are otherwise unchanged. Payments, referrals, iOS and Linux stay
disabled.

## Consequences and limits

- Nothing here proves a Windows tunnel works. Until the gate passes, a build, a unit test or a config
  that the engine accepts is not evidence of handshake, egress, server attribution or beta readiness.
- Unverified assumptions: wintun works with the AWG endpoint's userspace stack; `process_path` matches
  how Windows reports executable paths; IPv6 rejection holds; strict route removes DNS leaks.
- ROADMAP lists "Windows/Linux" as I-022, which clashes with the I-022 file (manual subscriptions).
  The Windows work is I-032.
