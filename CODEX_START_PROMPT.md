# First prompt for Codex

Copy the text below into a new Codex session in the root of the **private** GitHub repository. Before starting, put `TECHNICAL_SPEC.md`, `ROADMAP.md`, `AGENTS.md` in the root. Codex must work with the local project and must not receive real production credentials.

---

You are a lead software architect, security engineer and senior full-stack developer. We are starting development of our own commercial VPN service with an Android app, backend, traffic quota ledger, gamification and extensible infrastructure.

**Mandatory source documents:** read `TECHNICAL_SPEC.md`, `ROADMAP.md`, `AGENTS.md` in full; they define the project boundaries. Do not change them without stating reasons. Do not deploy to third-party/production servers and do not ask for secrets.

**For now we do ONLY the preparatory stage I-001 and the analysis of ADR-001/002/003**, without a real VPS, payments, traffic proxying and a nice Flutter UI.

Your tasks:
1. Study the documentation and identify contradictions, uncertainties, potentially unsafe decisions and gaps. Record them briefly in `docs/architecture-review.md` with a priority and a proposed solution.
2. Draft three ADR files: `docs/adr/001-authentication.md`, `002-mobile-tunnel-sdk.md`, `003-baseline-engine.md`. Each with options, pros/cons, security/licence impact and a recommendation. The ADR status is initially **Proposed**; do not present a decision as approved.
3. Create a minimal monorepo ready for future growth:
   ```text
   apps/
     android/                 # Flutter placeholder project or README
     telegram-mini-app/       # README, not a full app
     admin/                   # README
   services/
     api/                     # minimal FastAPI app + health endpoint
     worker/                  # README
     node-agent/              # README/contract placeholder
   packages/
     contracts/               # API schema / shared types placeholder
   infra/
     compose/                 # local dev DB/Redis with no production secrets
     ansible/                 # README only
   docs/
     adr/
     runbooks/
   .github/workflows/
   ```
4. Implement only a minimal FastAPI backend healthcheck `GET /api/v1/health` and its unit test. Create a basic local configuration and a `README.md` with the startup command for Linux Mint. Pin dependency versions.
5. Add a correct `.gitignore`, `.env.example`, formatter/linter, type check, pytest and CI for the backend. Do not add unused heavy dependencies.
6. Do not connect any real payment gateways, Telegram API or VPN SDK without a separate ADR and issue.
7. Run the available checks and output a report: files, commands, results, further risks. If something could not be run, say clearly why and do not claim success.

**Separately:** propose the exact text of the next issue I-002 with acceptance criteria, but do not carry it out without a new assignment.

Constraints: do not write your own cryptographic algorithm, do not disguise a mock as a functional VPN, do not change the legal restrictions on the product without agreement. The main goal is a safe, reproducible project start.

---

## Template for the next Codex assignments

```text
Read AGENTS.md, TECHNICAL_SPEC.md, ROADMAP.md and the current ADRs.
Implement ONLY issue <ID>: <title>.
Before editing, list the affected files, risks, tests, and definition of done.
Write tests for the core invariants first, then the implementation.
Do not do a production deploy or any paid external actions.
When done, run the tests and report: changes, commands, results, known limitations.
```
