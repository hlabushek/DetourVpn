# ADR-104: The orbit is a second tap target for the primary connection action

Status: implemented overnight 2026-11-11 (Flutter; compiler not run here, see report).

## Context
The connection screen (`apps/client/lib/home_screen.dart`) has one primary action in a
bottom button: connect, disconnect, cancel, or disabled (waiting for the server). The owner
asked that the big orbit graphic trigger exactly the same action. The orbit is Flutter
artwork (`OrbitBlob` in `orbit_widgets.dart`); it has no native Android UI counterpart on
this screen. Previously the button's choice was computed inline in `build`.

## Decision
1. `resolvePrimaryAction(...)` (new `apps/client/lib/primary_action.dart`) is the single pure
   decision. The button and the orbit both use its result.
2. Both controls dispatch through `PrimaryActionGate.shared.dispatch(...)`, which ignores a
   second accepted action within 600 ms. This covers an orbit tap and a button tap in quick
   succession before a rebuild.
3. The orbit is wrapped in a `GestureDetector` with `HitTestBehavior.opaque` and no visual
   change. `onTap` is null when the action is `none`, so it is disabled exactly when the
   button is disabled. Taps that become drags (scroll) are not delivered as taps by Flutter.
4. No new label, icon or animation.

## Consequences
- The gate is process-wide (`static final`). The screen is stateless and has one primary
  action, so this is acceptable; tests call `reset()`.
- Cancel and disconnect both call `ConnectionController.disconnect`, as before.
- The 600 ms window also blocks a legitimate disconnect tapped right after a connect. That is
  intended: the controller's state is not yet settled in that window.
- Semantics: no new accessibility label was added (the rule was "no new labels").

## Verification
- `apps/client/test/primary_action_test.dart`: decision table, dispatch mapping, debounce,
  disabled state, reset. NOT run (Flutter SDK not installed in this environment).
