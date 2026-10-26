# ADR-063: privacy-minimal OTP delivery receipt

- Status: accepted for local implementation; beta rollout remains unapproved.
- Issue: I-026.

## Decision

Store the OTP send outcome on its existing `login_attempts` row. The states are
`pending`, `api_accepted`, and `unknown`. `api_accepted` means Telegram Bot API
returned a successful `sendMessage` response; it does not mean the user read or
received a notification. An ambiguous transport/API failure is `unknown` and is
not retried automatically.

The row records only the state and the time it was recorded. It does not store
Telegram user/chat IDs, OTP, message text, `initData`, Bot API response body,
Telegram `message_id`, or an additional correlation identifier. The existing
login-attempt cleanup removes the receipt after its expiry plus one day.
Updates are conditional on the OTP generation so a late result cannot overwrite
the state for a newer code.

## Consequences

- Operators with existing restricted database access can distinguish a
  confirmed Bot API success from an ambiguous send without relying on screenshots.
- A process crash after Telegram accepts a message but before the database
  update leaves `pending`; this is intentionally not interpreted as success.
- This local implementation does not prove the hosted backend runs this schema,
  nor does it prove user-side delivery, OTP replay rejection, or end-to-end beta
  readiness. Deployment and hosted verification remain separate I-026 gates.

## Rollback

Migration `0017_login_delivery_receipt` is reversible while the fields are not
needed for audit. After deployment, inspect retention and operational evidence
before downgrading; no production or beta migration is authorized by this ADR.
