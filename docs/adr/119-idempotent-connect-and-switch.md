# ADR-119 — Idempotent connect, server switch, and accurate activation errors

**Status:** proposed 2026-11-11 (connection reliability, `RELIABILITY_ROOT_CAUSE.md` §4–§5).
**Amends:** ADR-099 (automatic reconnect).

## Context
Verified in code:
1. **Reconnect never fires after a pending revoke.** When a refresh fails with
   `window_not_active` or `lease_expired`, `_cleanup()` sends `DELETE`. The server answers
   `revoke_pending` until the node confirms, so the connection id is kept and `hasLease` is true.
   Auto reconnect is scheduled only when `!hasLease`. When the revoke later completes, nothing
   schedules the reconnect. The user sees "the app retries" but it does not.
2. **A connect after a recent disconnect fails fast** with `revoke_unconfirmed` if the old revoke
   is still pending, even though it would close a few seconds later.
3. **Picking another server while connected does nothing.** `selectNode` returns when
   `hasLease || busy`, and the picker closes as if the choice was accepted.
4. **`window_not_active` at `lease_validation`** means the new connection left `pending` in a state
   other than `active` (revoked by another engine or the node), or came back `active` without a
   client config. The Russian text for this code talks about a failed *renewal* and promises an
   automatic retry, which a manual connect never did.
5. An automatic reconnect that collides with a running refresh (`_polling`) returns silently, and
   the retry is lost.

## Decision
1. Remember "reconnect after cleanup" when a refresh failure qualifies for automatic reconnect but
   the old connection is still closing. When the remote cleanup finishes, schedule the reconnect.
   A user disconnect clears it.
2. Before creating a new connection, wait for an old one to close: up to 10 revoke checks,
   `pollInterval` apart, inside `connect()`. Only then report `revoke_unconfirmed`.
3. New `switchNode(id)`: if connected or connecting, disconnect (cancels a pending create and
   settles it with its original idempotency key), select the new node, and connect. The region
   picker uses it. A switch while another switch runs is ignored.
4. New stable code `window_activation_refused` for a non-active result of the *first* validation
   (`initial: true`). Its own Russian text. It is retried automatically, at most 3 times in a row,
   with the ADR-099 backoff. `window_not_active` stays for a window that stopped during a session.
   Both codes and their texts describe only what the app does.
5. If an automatic reconnect cannot start because the controller is busy, it is rescheduled.

## Security considerations
- The server still refuses a second live connection per device (`window_connection_exists`), so
  no stale window can stay funded. The client now waits for that closure instead of failing.
- The bounded retry (3) keeps a deterministic refusal from creating windows in a loop.

## Limits / not verified
- Not verified on a physical device or against a real node.
