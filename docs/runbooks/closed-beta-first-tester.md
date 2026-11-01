# First invited tester — internal acceptance checklist

Status: preparation draft; **not an invitation or authorization to start the
test**. Use only after the owner confirms the closed-beta gates and supplies the
approved APK and invite path.

## Entry conditions

- I-026 friend onboarding is accepted for the exact live invite → Telegram OTP
  → Android sign-in path being tested. The existing owner-only happy path does
  not prove this flow for a different account.
- The signed APK receipt, application ID, version, and SHA-256 have been checked.
  The stable package ID is `com.detourvpn.client`. A debug APK is not an update
  target for the beta-signed release.
- I-027 recovery is available, and the owner has confirmed the required backup
  run for the test window.
- The relevant I-028 physical acceptance and the physical ADR-070 status check
  are accepted for the exact Android build and scenario. Do not infer
  device-wide leak freedom, Always-on, Lockdown, or kill-switch behavior.
- The owner has chosen a private APK delivery route, a support contact, a
  bounded test window and traffic allowance, and a way to revoke the tester's
  access. Do not put invite URLs or one-time codes in a shared document.
- The admin has verified that `/admin/` is the trusted HTTPS beta origin and
  the deployed build contains the invite section. The owner has selected the
  recipient account out-of-band; never paste the Telegram ID into this receipt.

If any entry condition is missing, record the gate as blocked and stop before
installing or inviting the tester.

## Owner invite operation

Use the admin panel only after the entry conditions pass. Select the invite
section, enter the recipient's numeric Telegram ID from the owner's private
record, and use a generic reason such as `Closed beta tester access`. Do not put
the ID or contact details in the reason. Confirm that the panel reports an
active invite, then send the approved APK and bot entry instructions through the
owner-selected private channel. Do not share the invite record, OTP, or admin
credentials. Follow `beta-tester-support.md` for reports and closeout.

If the outcome is unclear, inspect the admin audit before retrying. Reuse the
same operation/key for a transport retry; do not click “new operation” to bypass
an ambiguous response. Invite revocation disables the account and revokes its
sessions and tunnels; use only for an explicit test closeout and verify node
acknowledgement/cleanup before any reinvite.

## Tester steps

1. Record the tester's Android model and OS version in the owner's private test
   notes. Do not collect a phone number, Telegram ID, or device identifier in
   the acceptance receipt.
2. Install the owner-provided signed APK using the private route. Confirm the
   package is `com.detourvpn.client` and the displayed version matches the
   release note. Stop if Android reports an incompatible signature or package.
3. Open only the beta bot/Mini App link supplied by the owner. Request the OTP
   and enter it in the Android client. Never paste or forward the OTP into
   Telegram chat, support chat, screenshots, or this repository.
4. Confirm the client shows the signed-in beta account and available traffic.
   If permission is requested, review the Android VPN prompt and approve only
   for this test.
5. Select the owner-approved node, connect, and wait for the client to report
   `Подключено`. The owner then performs only the bounded egress check listed
   in the test plan; the UI status alone is not proof of a working tunnel.
6. Visit no sensitive accounts or services during the test. Do not enable
   Always-on or Lockdown for this build.
7. Disconnect once. The owner verifies server-side revoke/settlement and that
   ordinary connectivity is restored. If the owner invokes remote revoke, the
   tester confirms the client transitions to disconnected and does not
   reconnect by itself.
8. If there is an error, stop retrying and use **Поделиться диагностикой** only
   after checking its preview and choosing the designated support recipient.
   Do not attach profiles, OTPs, VPN configuration, personal traffic details,
   or screenshots containing identifiers.

## Pass criteria and evidence

Pass only if the tester completes sign-in, owner-observed bounded egress,
disconnect, remote revoke, settlement and cleanup on the approved build. Record
only build version/hash, Android model/OS category, fixed result codes/stages,
and pass/fail flags. Keep the tester's identity and contact details out of the
receipt. An incomplete step is a failed or blocked run, not a partial pass.

Start each run from the blank privacy-safe template
`docs/evidence/i-026-first-tester-run.template.json`; save a completed sanitized
receipt under a new filename. Do not overwrite the template or attach raw logs.

## Stop conditions

Stop immediately if the package/signature is unexpected, OTP is requested in
chat, the app reports connected without owner-observed egress, remote revoke
does not disconnect the client, usage fails to settle, or cleanup cannot be
confirmed. The owner records the fixed failure stage and follows the relevant
runbook before any retry. Do not leave an active session or invite grant after
the test window.

## Current gaps

The owner-only sign-in is accepted; friend onboarding is not yet accepted. The
physical release update, final key backup, next natural I-027 scheduled run,
and physical ADR-070 check remain separate gates. No tester has been invited by
this document.
