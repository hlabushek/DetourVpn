# ADR-138 — Transport recovery keeps the TUN; transition decisions are tested

**Status:** accepted 2026-11-13 (connection reliability run 3, items G and H; tests for ADR-127).

## Context
ADR-127 made the in-place restart real. Run 2 left two gaps: (G) the claim that a restart replaces the
interface through one `establish()` on the same service, with no teardown on success, rested on review
only; (H) the Wi-Fi to cellular and loss-and-return decisions had no supervisor-level tests.

## Decision
- **G.** `RestartInPlacePatchTest` now checks the reviewed patch text: the success path between `try` and
  `catch` brings the tunnel up with no `State.DOWN`, no `awgTurnOff` and no `stopSelf`; the old device is
  closed only after the success check; the AWG UP path has exactly one `builder.establish()`. This is a
  static guard on text, not a device proof.
- **H.** `ConnectionSupervisorTest.wifiToCellularAndBackAreRecoveryDecisions` covers a network change with
  cellular still usable (no action), a Wi-Fi loss with no usable network (waiting), cellular becoming usable
  (recovery starts at once), a failed restore (backoff), and the next allowed attempt. The existing
  `PhysicalNetworkTest` covers the network-side events.

## Security considerations
- Tests and checks only. No runtime behaviour changed in this item.

## Limits / not verified
- **Device proof is missing** for both: the TUN replacement during a restart, the Wi-Fi to mobile hand-over,
  and recovery when Android resumes execution. Device tests A, C and E in `RELIABILITY2_DEVICE_TESTS.md`.
