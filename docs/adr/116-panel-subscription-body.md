# ADR-116: Panel subscription body format (3x-ui, Remnawave)

Status: accepted (run 4, 2026-11-11). Parser implemented and unit-tested with synthetic samples.
**Not wired to an admin endpoint**, and no live subscription was read. Answers the open question
in ADR-111 decision 2.

## Verified from official sources (read 2026-11-11)
| Fact | Source | Kind |
|---|---|---|
| 3x-ui raw path (`subPath`) returns newline-joined share links (`vless://`, `vmess://`, …), standard-base64 encoded when `subEncrypt` is on; `subEncrypt` defaults to `true` | `MHSanaei/3x-ui` repo, `docs/content/docs/en/config/subscription.mdx` ("Base64 vs JSON", settings table) | official docs |
| 3x-ui also serves JSON (`subJsonPath`, Xray client config(s)) and Clash/Mihomo YAML (`subClashPath`) on separate paths; `Accept: text/html` or `?html=1` returns an HTML page | same file, "Output formats" | official docs |
| 3x-ui headers: `Subscription-Userinfo` (upload, download, total, expire), `Profile-Update-Interval`, optional `Profile-Title`, `Support-Url`, `Profile-Web-Page-Url`, `Announce` | same file, "Response headers" | official docs |
| Remnawave `XRAY_BASE64` response: links joined with `\n`, then `Buffer.from(joined).toString('base64')` (standard alphabet, padded); content type `text/plain` | `remnawave/backend` `src/modules/subscription-template/generators/xray.generator.service.ts`, `src/modules/subscription-template/constants/config-types.ts` | official source |
| Remnawave chooses the response type with response rules (BROWSER, XRAY_BASE64, XRAY_JSON, MIHOMO, …); XRAY_BASE64 is the generic fallback; the body may be encrypted by an optional setting | `remnawave/backend` `src/modules/subscription/subscription.service.ts`, `controllers/subscription.controller.ts` | official source |

## Decision
`vpn_api.panel_sources.parse_subscription_body(body: bytes)` (pure, no network):
- accepts exactly that verified body: ASCII text with share links one per line (`\n` or `\r\n`),
  or the same text in standard base64 (padding optional, line wraps allowed);
- keeps only `vless://` lines that pass the strict Reality parser (ADR-111); other protocols are
  counted (`skipped_other_protocol`); invalid VLESS lines are counted by their stable error code;
  no link content is ever in an error or `repr`;
- refuses JSON, YAML, HTML, encrypted or binary bodies with `panel_subscription_format`, empty
  bodies with `panel_subscription_empty`, and bodies over 256 KiB or 200 lines with
  `panel_subscription_too_large`.
- It never fetches a subscription URL. ADR-111 still holds: the app does not contact panels.

## Not verified (do not rely on these)
- Remnawave public route prefix (`SUBSCRIPTION_ROUTES.GET`, read as `/api/sub/` in community
  material only) and the list of client-type path suffixes.
- Which User-Agents get XRAY_BASE64 versus JSON in Remnawave's default response rules.
- 3x-ui source code (raw source URLs returned 404 to our reader); facts above come from its
  official docs in the same repo.
- JSON, Clash/Mihomo and encrypted (for example Happ) bodies: refused, not parsed.
- No real subscription from a live panel was tested.

## Follow-up
- An admin "import from subscription body" endpoint (paste the body, import each valid link as
  a panel server with the same audit and encryption as ADR-111). Not built here.

## Verification
- `services/api/tests/test_panel_subscriptions.py`: plain list, base64 with and without padding,
  `\r\n`, wrapped base64, other protocols counted, invalid VLESS counted by code, refused formats,
  size limits, no credential in `repr`. Run with the existing `test_panel_sources.py`: 64 passed.
