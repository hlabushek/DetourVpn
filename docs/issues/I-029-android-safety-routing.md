# I-029 — Android safety controls and per-app routing

## Goal

Add the Android VPN safety and routing controls needed by an advanced client,
reusing the existing AmneziaWG GoBackend and Android `VpnService` APIs. Do not
replace the Flutter application with the full AmneziaVPN client.

## Current baseline

- The client already supports excluding selected installed apps from the VPN.
- The client now also supports the complementary included-app mode locally;
  an empty included-app list is rejected to avoid accidental full-tunnel routing.
- The selected server node is stored locally as a validated UUID preference;
  after sign-in it is restored only if the API still reports that node available.
  It is not a lease, credential, or authority to restore a tunnel.
- The included-app mode passed local Flutter/native parser tests and Android
  debug assembly; physical routing acceptance is still pending.
- I-028 has a physical receipt for included/excluded app routing; this does not
  prove every package, Android version, lockdown combination, or OEM behavior.
- The client does not claim Always-on VPN or a kill switch.
- The profile screen links to Android's VPN settings and explicitly warns that
  recovery is experimental and not yet accepted on a physical device; see ADR-042.
- A headless Always-on bootstrap is now implemented locally. It restores only the
  origin-bound encrypted refresh session, validates the saved node against the
  authenticated API, reconciles/revokes prior remote state, and requests a new
  lease through the existing `WindowConnectionController`. Missing session,
  permission, availability, API access, or an ambiguous lease leaves the tunnel
  down. No saved peer config or old grant is restored.
- The pinned GoBackend callback now supplies its `VpnService` to the application;
  the application owns the fixed-origin protected relay and headless Flutter
  engine. Debug manifest advertises Android Always-on for controlled acceptance;
  release manifest explicitly opts out. Lockdown remains user-controlled.
- This local implementation is not physical acceptance. Service start behavior,
  DNS reachability under Lockdown, actual lease recovery, Android/OEM behavior,
  and post-failure recovery remain unverified. Do not call this a kill switch or
  production-ready Always-on.
- The full AmneziaVPN application is GPL-3.0 and uses a different app stack.
  Any source reuse must pass the existing per-component license review.

## Scope

1. Implement safe Android Always-on startup for leased sessions: a system-started
   service must revalidate device ownership and the current server lease before
   bringing up a tunnel. Expired, unavailable, or ambiguous authorization fails
   closed. Do not assume manifest merging implies functional recovery.
2. Explain and link to Android system VPN settings for Always-on and
   “Block connections without VPN” (Lockdown). Never claim the app can silently
   enable system Lockdown.
3. Preserve the current excluded-app mode and add the complementary
   included-app mode, with exactly one routing mode active at a time.
4. Make the Lockdown interaction explicit: Android may block apps excluded
   from the VPN. The UI must not imply excluded apps remain online under
   Lockdown.
5. Keep app selections on-device. Do not upload installed-app lists or add
   `QUERY_ALL_PACKAGES` solely for this feature.
6. Persist only the selected public node identifier needed for restart policy;
   restore it only after API availability validation and clear it on sign-out.
7. No server/API/database changes, protocol additions, production changes, or
   staging runs.

## Acceptance

- Parser rejects malformed, duplicate, over-limit, or conflicting routing
  selections and preserves fail-closed behavior for remote profile directives.
- Flutter settings expose both routing modes and explain which traffic bypasses
  the VPN and the effect of Android Lockdown.
- A local/native connection uses the selected mode after reconnect; modifying
  the mode while connected cannot silently claim success before the tunnel is
  rebuilt.
- Android debug build/unit tests and relevant Flutter/parser tests pass; lint has
  environment/upstream failures documented in `../verification-i-029.md`.
- Process/service restart does not revive an expired lease; authorization loss
  must leave the tunnel down and preserve Android Lockdown's block behavior;
  this remains a physical acceptance criterion.
- Physical acceptance on the target Android device separately verifies
  included/excluded egress, DNS/IPv6 policy, Always-on restart, Lockdown behavior,
  and recovery after service failure. Until then, the feature is not called
  production-verified and no kill-switch marketing claim is allowed.

## Security and compatibility risks

- Android Lockdown and per-app exclusions can conflict by design; users may
  lose connectivity for excluded apps.
- Package visibility, shared UIDs, cloned apps, work profiles, Private DNS,
  Android versions, and OEM behavior can alter observed routing.
- Per-app routing is a routing convenience, not a security boundary against a
  hostile app that can bind directly to the TUN interface on a non-rooted
  device.
- Full-client GPL-3.0 code is not copied into the Flutter client without a
  separate licensing decision. Prefer platform APIs and the already selected
  AWG backend.

## Out of scope

Natural-sleep recovery remains within I-028. DNS/IPv6 leak certification,
network-change recovery, autoconnect policy, region selection, multihop,
additional tunnel protocols, and ad/tracker filtering require separate
acceptance criteria and are not implicitly completed by I-029.
