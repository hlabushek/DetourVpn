# ADR-106: User-selected tunnel DNS (Android and Windows)

Status: implemented in code for Android (run 1) and Windows (run 2), overnight 2026-11-11.
Not verified on a phone or on a Windows PC.

## Findings (verified in code)
- The tunnel DNS comes from the server profile: `DNS =` in the WireGuard/AmneziaWG text.
  Android parses it in `ClientProfile.parse` via `ClientApplication.routedConfig`.
  Windows parses it in `apps/client/windows/runner/profile.cpp` and uses
  `profile.dns_servers.front()` in `config_factory.cpp`.
- No user DNS setting existed.
- Android has two config builders. `LibboxConfigFactory.create` has no call site in
  `apps/client` (it looks unused on this path). The live path is `routedConfig`, which
  is called by `ClientApplication` and `ClientActivity`. Only that path is changed.
- Android "VpnService DNS" is not set separately: the DNS is part of the engine config, so one
  place covers it.

## Decision
1. Allowed values are exactly 1.1.1.1, 8.8.8.8, 94.140.14.14, 9.9.9.9 (`DnsChoice.java`,
   `dns_choice.dart`). "Default" = unset = profile DNS, which is the current behavior.
2. Persisted in the native `routing` SharedPreferences under key `dns` (empty = default), the
   same store as split settings. Read and written via the existing `project.vpn/tunnel.v1`
   channel (`dnsRead` / `dnsWrite`). Writes are refused while the tunnel is up or leased.
3. Applied at connect time: `routedConfig` rewrites the `DNS` line in the `[Interface]`
   section of the profile text before parsing (`DnsChoice.applyToConfigText`). Other sections
   are not touched. A profile without DNS gets one inserted.
4. "Applies on next connect": an active tunnel keeps its DNS until it is reconnected. The UI
   says so and disables the row while the VPN is on.
5. Stored values that are not on the list are read as default; they are never sent to the engine.

## Scope and limits
- Windows (run 2). The Windows client does NOT share Android's config generation: Android
  rewrites the AmneziaWG text, Windows builds a sing-box JSON config in `config_factory.cpp`.
  So the same setting is wired separately:
  - `RoutingPolicy::dns_server` ("" = profile DNS). `CreateEngineConfig` uses it as the
    `server` of the `profile-dns` resolver (detour `awg`, i.e. through the tunnel) and rejects
    any unlisted value with `routing_policy_invalid`. Nothing else in the config changes.
  - Stored in the existing Windows prefs file as a `dns	<server>` line; unknown values read as
    default. `TunnelController::Dns` / `WriteDns`; writes refused while connected or leased.
  - Channel methods `dnsRead` / `dnsWrite` with the same contract as Android
    (`tunnel_channel.cpp`). The Dart settings row is now shown on Windows too.
- DNS queries are still sent through the tunnel to the chosen resolver (the engine detour is
  unchanged). The server must forward UDP/53 to that resolver; this is not verified on a device.
- Resolver choice does not change routing rules or the hijack-dns rule.

## Verification (run 2)
- `flutter analyze`: no issues. `flutter test test/dns_choice_test.dart` (with the other
  relevant tests): passed.
- JUnit `DnsChoiceTest` (+ `UpdatePolicyTest`): 11 passed, run with JUnit 4 outside Gradle.
- Windows C++ (`apps/client/windows/tests`, MSVC 2022, `/W4 /WX`): `config_factory_tests`
  145 checks, 0 failures (new `ConfigDnsChoice`); `adapter_tests` passed under ctest
  (new DNS preference checks in `ControllerTests`). `tunnel_channel.cpp` is not part of the
  test build (it needs Flutter types) and was not compiled here.
- Still no device test on Android or Windows.

## Verification (run 1)
- `DnsChoice` logic was compiled and run with `javac` in a scratch harness (all checks passed).
  The JUnit file `DnsChoiceTest.java` is written but was NOT run through Gradle here.
- `apps/client/test/dns_choice_test.dart`: written, NOT run (no Flutter SDK here).
- No device test. The DNS change on a real phone is unverified.
