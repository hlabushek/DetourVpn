# ADR-112: VLESS + Reality outbound in the Windows engine config

Status: accepted for beta (run 4, 2026-11-11): engine rebuilt with `with_utls`; config generation unit-tested
locally. Delivered to Windows devices by ADR-115 (off by default). Not verified with a live server.

## Context
The Windows client runs Amnezia Box (`detour-engine.exe`, sing-box fork, pinned
`v1.260910.0` in `third_party/amnezia-box/source-lock.json`, build tags `with_awg,with_gvisor`).
`config_factory.cpp` builds its sing-box JSON with one `awg` endpoint. Panel servers (ADR-111)
speak VLESS + Reality, which upstream sing-box implements as a `vless` outbound with
`tls.reality`.

## Decision
1. Add `VlessRealityProfile` (already validated fields: server, port, uuid, flow, sni,
   public key, short id, uTLS fingerprint) and `CreateVlessEngineConfig(profile, policy)` next to
   the existing `CreateEngineConfig`. The AmneziaWG path and its output are unchanged.
2. The generator emits the same TUN inbound, DNS, sniff/hijack-dns rules, IPv6 reject and
   app/domain split as the AWG config, with the tunnel tag `proxy` on a `vless` outbound:
   `tls.enabled`, `server_name`, `utls {enabled, fingerprint}`, `reality {enabled, public_key,
   short_id}`, `flow`, `packet_encoding: xudp`. The profile DNS server is the chosen DNS
   (ADR-106) or `1.1.1.1` (one of the listed choices), sent through the proxy.
   The shared routing/DNS code moved into one internal builder; the AWG output is byte-identical
   (all existing exact-string checks still pass).
   Choices: TUN address `172.19.0.1/30` (VLESS has no tunnel address; private, TUN-local only),
   TUN MTU 1500, and a system resolver (`local-dns`) is always present so the panel host name
   resolves outside the tunnel (`domain_resolver: local-dns` on the outbound), avoiding a loop.
3. The generator throws `profile_invalid` (same `std::invalid_argument` contract as the AWG
   path) for a profile with any field outside the same rules as the API parser, so a bad profile
   never reaches the engine.
4. **The current engine build cannot run this config (verified in the pinned source).**
   Inspected read-only in `third_party/amnezia-box/amnezia-box-v1.260910.0-src.zip`:
   - `include/registry.go` registers the `vless` outbound unconditionally;
   - `common/tls/reality_client.go` and `utls_client.go` are `//go:build with_utls`; without the
     tag, `utls_stub.go` fails with "uTLS, which is required by reality is not included in this
     build, rebuild with -tags with_utls";
   - `source-lock.json` `windows_tags` are `with_awg, with_gvisor` (no `with_utls`);
   - `go.mod` already requires `github.com/metacubex/utls v1.8.3`.
   **Owner decision (not done here):** add `with_utls` to `windows_tags` and rebuild the engine.
   This changes the binary that AWG beta users also run (more code compiled in, possibly a
   module download for the Go cache), so it needs its own build, hash record and AWG regression
   check. After that, run `detour-engine.exe check -c <file>` on the generated sample config
   (owner steps file). Alternative if the owner prefers not to change the AWG engine: a separate
   pinned engine binary for panel servers only.
5. No delivery: there is no API route that gives a panel server to a Windows device, and the
   controller does not call the new generator. That is the next issue (needs device binding,
   per-device panel client, and capability flag in the client).

## Verification
- New checks in `apps/client/windows/tests/config_factory_tests.cpp` (structure, fields, DNS
  choice, split modes, refusals). No connection test.

## Update 2026-11-11 (run 4): engine rebuilt with `with_utls`
- `third_party/amnezia-box/source-lock.json` `windows_tags` now includes `with_utls`. One engine
  binary serves AWG and VLESS: the tag only compiles in uTLS/Reality code that a config must ask
  for; AWG configs do not.
- Built by `scripts/build_engine_windows.ps1 -Build -GoProxy off` (pinned Go 1.25.5, verified
  source archive, no module download; `metacubex/utls v1.8.3` came from the local module cache
  and is checked against the pinned `go.sum`).
  `detour-engine.exe`: 25,773,056 bytes, SHA-256
  `56eebc1cd4ff6356eb84349e6359b4093ea8e170519d6eeb1e06018b806f0869`,
  `version` reports `Tags: with_awg,with_gvisor,with_utls`.
- Self-test: `detour_config_tool --vless-selftest` (gate helper, not shipped) prints the real
  generator's output for a fixed fake profile. `detour-engine check -c` on it: new engine exit 0;
  the previous engine (tags `with_awg,with_gvisor`) exit 1 with "uTLS, which is required by
  reality is not included in this build".
- Not verified: a live VLESS connection, and the AWG path on the new binary on a PC
  (device test needed). The next Windows release zip must be built with `-BuildEngine` so it
  carries this engine.
