# AGENTS.md — project-wide instructions for Codex

You are developing a privacy-preserving, modular VPN service. This is a real infrastructure product. Treat all code as security-sensitive.

## Product references
- Canonical product requirements: `TECHNICAL_SPEC.md`.
- Canonical delivery plan: `ROADMAP.md`.
- When any requirement is ambiguous, create a brief ADR in `docs/adr/` and state assumptions; do not silently improvise security-critical behavior.

## Scope control
1. Work on **only one explicitly assigned issue** at a time. No speculative features.
2. Before making edits, list files you expect to change, planned tests, and security risks.
3. Keep every change reviewable. Report any out-of-scope bug instead of solving it implicitly.
4. Never claim a working VPN connection, payment flow, DDoS protection, or allowlist compatibility from a mock.
5. Label all test mocks clearly. No mock code or dev keys in production builds.

## Reuse and delivery pace
- Before implementing, inspect the existing code and check for a suitable maintained upstream project or library. Do not reimplement a solved capability without a concrete reason.
- Prefer mature components with compatible licenses and clear security boundaries. Check compatibility with `TECHNICAL_SPEC.md` and the architecture constraints before adopting a dependency; do not add one solely to save time.
- Write new code only for missing behavior or integration that existing components do not satisfy. Briefly explain the choice when reporting the work.
- Choose the smallest verifiable result within the assigned issue. Keep scope bounded and do not start future issues.
- Run checks relevant to the changed code and acceptance criteria. Avoid repeating the full suite without a concrete reason; report what passed and what remains unverified.
- Avoid extra design work when ambiguity does not affect security, architecture, or a contract: make a reasonable choice and proceed. For material ambiguities, record assumptions in a brief ADR as required above.

## Architecture constraints
- Control plane (FastAPI/PostgreSQL/Redis) is distinct from VPN data plane (node + tunnel engine).
- No user browsing content/history in control-plane logs.
- No custom cryptographic primitives. Prefer maintained, audited upstream implementations.
- Tunnel engines must implement versioned adapter interfaces and capability checks.
- Private device keys should be generated locally and not sent to backend whenever supported.
- Amounts, quotas and traffic usage stored as integer **bytes**; money as integer **minor units**; no floating-point balances.
- All reward, usage and payment operations require idempotency keys and transaction-level correctness.
- Unknown incoming webhooks must fail closed; verify provider signatures in constant-time where relevant.

## Security and privacy constraints
- No secrets in the repo, prompts, logs, tests or screen captures. Use `.env.example` placeholders and a secret manager.
- Never request or use production SSH root credentials. Infrastructure commands may target explicitly approved staging systems only.
- No production deploy, VPS purchase, DNS/account registration, transfer of money, or external service side-effect without explicit approval.
- Use least privilege, RBAC, MFA for administrative access, parameterized queries, authz checks on every user-owned resource.
- No arbitrary remote shell execution endpoints in node agents.
- Do not build infrastructure designed around false registration details, hiding legally required beneficial ownership, or evading financial reporting.
- No promises of guaranteed bypass under strict IP allowlists; obfuscation is not a substitute for reachable transport endpoints.
- Collect only minimum operational telemetry; don't log DNS, URLs or tunnel payloads. Document retention before any production collection.
- Follow applicable app-store, consumer-protection, privacy, payment and advertising rules.

## Quality gates
- Python: formatter, Ruff, type checking, pytest incl. negative/concurrency tests.
- Database changes: Alembic migration + rollback considerations + indexes + tests.
- Android/Flutter: dart analyze, flutter test, Android native build/tests; physical-device verification for tunnel changes.
- API: OpenAPI updated whenever contracts change. Implement structured errors with stable codes.
- Dependencies: pin versions, review licences, supply-chain scanning and SBOM before public releases.
- Any significant new library requires a licence/maintenance note in the PR.

## Definition of done per issue
- Implemented code (not just UI).
- Tests relevant to the acceptance criteria, run and reported with actual results.
- Docs/ADR updates for changed behavior.
- Security considerations and known limitations listed.
- Local reproduction steps and explicit instructions for any necessary **manual** environment changes.
- Summary: changes, files, test commands, failures, and recommended next issue.

## Response language
- Explain decisions and tradeoffs in Russian; code and identifiers in English.
