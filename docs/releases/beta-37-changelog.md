# DetourVpn beta.37 candidate — changelog (draft)

Status: **not released; version not bumped** (`pubspec.yaml` still says beta.36). Unit-tested
and build-verified only. Not verified on a real Windows PC or a phone. No tunnel handshake was
observed. The Windows zip is unsigned.

## Windows client

- **Watchdog parity with Android** (ADR-194). This is the code for W-02 sleep/resume and the
  watchdog part of the reliability work.
  - A crashed engine is restarted in place on the same window (at most 2 times per 10 min), then
    reported for a new lease as before.
  - A physical network change or a resume from sleep restarts the tunnel unless a newer handshake
    is seen. After two restarts without a handshake, the window is given up and the app reconnects
    on a new lease, keeping the wish to stay connected.
  - No handshake for 180 s restarts the tunnel, with backoff from 2 to 10 min.
  - The handshake rules run only when the engine reports peer stats. VLESS sessions are unchanged.
  - Window deadlines now use a clock that counts sleep.
- **Fallback API origins bypass the tunnel** (ADR-195, W-05). Windows now routes the configured
  fallback API hosts directly, as Android does. Without fallbacks, nothing changes.

## Docs

- ADR-194 (Windows watchdog, network change, sleep/resume; why the dead-peer probe stays off on
  Windows; tray mode deferred), ADR-195 (fallback API bypass), ADR-196 (kill switch design, **not
  implemented**). ADR-095 and I-032 limits updated.

## Known gaps, not in this release

- **No kill switch on Windows** (W-01). ADR-196 has the WFP design. Implementing it needs an
  owner-authorised test on a real PC and a decision on DNS during a gap. When the engine dies or
  restarts, traffic can go direct until it is up again.
- **No tray mode on Windows** (W-02, tray part). Closing the window still stops the VPN.
- **Dead-peer probe is inert** (A-03) on Android and absent on Windows. No project-owned health URL
  exists. On Windows the stall rule is the 180 s stale-handshake restart.
- Unchanged from beta.36: A-01, A-06, B-01.
