# ADR-108: 3x-ui and Remnawave (VLESS + Reality) as sources: feasibility

Status: feasibility only. NO implementation. Decision: do not add a fake engine path.

## Findings (verified in this repo)
- The tunnel engines are AmneziaWG/WireGuard (`profile.cpp`, `LibboxConfigFactory`,
  `AwgProviderV1`/`GoBackend`). No VLESS, Reality, Xray or sing-box outbound code exists here.
  `LibboxConfigFactory` builds an `awg` endpoint only.
- The Android app's `libbox` sing-box config path is present but has no VLESS outbound and no
  call site on the live path (see ADR-106 notes).
- No code in the repo talks to 3x-ui or Remnawave. The only mention is ADR-094 (add-beta-node
  flow), which is unrelated.
- AGENTS.md: tunnel engines must implement versioned adapter interfaces and capability checks.
  A VLESS+Reality engine is a new engine capability, not a parser.

## Panel facts (external; NOT verified against live panels)
- Web lookup (secondary sources, not verified against live panels):
  - 3x-ui: community clients report `POST /login` with username and password (session cookie),
    inbound routes under `/panel/api/inbounds/...`; v3 moved to `/panel/api/*` and may require
    CSRF handling. Route layout differs between v2 and v3, so pin the version.
  - Remnawave: public subscription path is `/api/sub/{shortUuid}`; a `/api/sub/{shortUuid}/info`
    endpoint is mentioned in the changelog. Least-privilege token scopes are discussed
    (metadata/subscription read). The VLESS Reality output format was not confirmed.
  - Sources: [3x-ui API client wiki](https://github.com/iamhelitha/3xui-api-client/wiki/Authentication-Guide.md),
    [3x-ui endpoint list](https://git.txlyre.website/txlyre/3x-ui/raw/3c5e9fa7745d08d29c68a4b105aabf7cab1680ca/frontend/src/pages/api-docs/endpoints.ts),
    [Remnawave subscription page](https://docs.rw/docs/install/remnawave-subscription-page),
    [Remnawave changelog](https://docs.rw/docs/changelog/remnawave-panel).
- Implication: a read-only subscription URL import is the least-privilege option. Panel admin
  APIs are not needed for the MVP and should not be requested.

## Recommendation (minimum viable path)
1. Prefer a **subscription URL or a single `vless://` Reality share link** supplied by the owner
   or user. This is read-only and avoids panel admin credentials. Panel URL plus admin token
   is not needed.
2. Parse and validate the link (scheme, UUID, host, port, `security=reality`, `pbk`, `sni`,
   `sid`, `fp`, `flow`) in a pure function with tests. Reject anything unknown (fail closed).
3. Add a **new versioned engine adapter** (sing-box/Xray core). That is a separate issue:
   it needs a new capability flag, the Android native build, Windows engine build, and physical
   device verification. Not in this overnight run.
4. Secrets: a subscription URL and the `pbk`/`sid` values are credentials for the user's account.
   Store them only in the existing secure store (`secure_vault` on Windows, encrypted prefs on
   Android). Never log them or send them to the control plane.

## Not done tonight
- No parser, no engine, no UI. The parser step is listed as the next issue.
- Owner decision needed: which panel(s) and whether a subscription link can be provided.
