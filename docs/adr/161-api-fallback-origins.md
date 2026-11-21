# ADR-161 — Fallback API origins in the client

**Status:** implemented on branch `claude/connection-reliability`, committed locally. **Unit-test
verified only; no real failover between API instances was run.**
**Part of:** the API high-availability strategy (ADR-163). Offline grace: ADR-162.

## Context
The client knows exactly one API origin (`VPN_API_ORIGIN`, build time). When that host is
unreachable (provider outage, routing problem, blocked address) every API call fails: sign-in,
connect, renewal from the app, quota. The server side for more than one API instance is not built
yet (ADR-163); this change prepares the client so that adding instances needs no client release
beyond setting a build value.

## Decision
- New optional build value `VPN_API_FALLBACK_ORIGINS`: up to **two** https origins of the same API,
  comma-separated (`scripts/build_flutter_client.ps1 -ApiFallbackOrigins
  "<API_FALLBACK_URL_1>,<API_FALLBACK_URL_2>"`). Empty (the default) keeps today's single-origin
  behaviour exactly. Real values are supplied only at build time; the repository holds none.
- `Api` gets `origins` = primary, then fallbacks. Each request runs on the primary first. A
  **transport failure** (no HTTP answer: connection refused, DNS, TLS handshake failure, connect or
  request timeout, reset) moves the same request to the next origin, in order.
- **Repeat safety:** a failure before the request could leave the device (inside `openUrl`, i.e.
  connect and TLS) is retried for any request. A failure after sending started is retried only
  when repeating is safe: an idempotent method (GET, HEAD, PUT, DELETE, OPTIONS) or a body with a
  non-empty `idempotency_key` (tunnel/window creation and renewal already carry one). Example:
  `/auth/refresh` that timed out after sending is not repeated elsewhere (the server's rotation
  grace already covers a replay by the next normal attempt, ADR-100).
- **An HTTP answer is final:** any status (2xx, 3xx, 4xx, 5xx) or a malformed body from an origin
  ends the request there. A 5xx is not moved to another origin — with a shared database the other
  instances would usually fail the same way, and repeating could double side effects.
- Unchanged per attempt: TLS verification by `HttpClient` (no override), `followRedirects = false`
  (a redirect is an answer, its target is never contacted), `Cache-Control: no-store`, the 64 KiB
  answer limit, the path allow-list and response parsing. The per-attempt timeout stays 8 s.
- Configuration is fail-closed: a fallback that is not https, has user info, a path, query or
  fragment, duplicates the primary or another fallback, or more than two entries make `Api` throw
  `invalid_origin`, so the app shows its setup screen instead of running half-configured. A
  plain-HTTP fallback is refused even in debug builds.
- The session record keeps the **primary** origin as its identity (`restoreSession` origin check
  unchanged). The bearer token is sent only to configured origins; all instances must share one
  session database (ADR-163).

## Scope limits (not changed here)
- The native renewal path (ADR-140, `NativeRenewer`, protected HTTPS) and the Always-on restore
  engine (`Api.protected`, the loopback relay that admits only the primary host) still use the
  primary origin only. Extending them needs the relay allow-list and the native renewer to accept
  the fallback list; recorded in ADR-163 as follow-up work.
- No "sticky" preference: every request starts at the primary, so while the primary black-holes
  each request first waits for its connect timeout (5 s). Chosen for predictability; a time-boxed
  preference for the last working origin is a possible later improvement.
- Worst case for one call is three attempts × 8 s. Callers that wait for an API timeout (for
  example ADR-154's tap during a poll) wait longer when fallbacks are configured.

## Security and privacy
Only build-time configured hosts are contacted (test: "session credentials reach only configured
origins", "a redirect is an answer"). No TLS relaxation, no plain HTTP, no certificate pinning
change. No new logging. Payment and reward operations are server-side and require idempotency keys
there (ADR-163); the client never repeats an un-keyed POST that may have been delivered.

## Test plan and results
`apps/client/test/api_fallback_test.dart` (TEST ONLY fake `HttpClient`, no sockets): fallback order;
healthy primary is the only host; no retry on 400/401/404/409/422/500/502/503; redirect not followed
and not retried; un-keyed POST not repeated after send (reset and timeout); un-keyed POST repeated
when never sent; keyed POST and GET repeated after a timeout; all origins failing; credentials only
to configured origins; no fallbacks = single origin; configuration validation.
`flutter test test/api_fallback_test.dart test/api_test.dart`: 34 passed. Full run in HANDOVER.

## Not verified
No real network, DNS, TLS or multi-instance API run; no device run.
