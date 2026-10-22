# Node Agent — I-012 bounded staging daemon

`agent.py` реализует single-peer native add/remove, kernel UDP budget/timeout,
durable journal и restart reconciliation. Локальный настоящий AWG acceptance
прошёл; live VPS/Pixel gate принят 2026-10-20: API revoke, actual agent restart
и expiry при потере control-plane transport, settlement и cleanup подтверждены.
[Запуск и границы](../../docs/runbooks/tunnel-lifecycle-staging.md),
[security review](../../docs/security-review-i-012.md).

В I-009 добавлены реестр узлов, одноразовая выдача случайного credential и локальный
HTTP endpoint для authenticated usage-sample v1. Реестр можно создать только через внутренний
`NodeRegistry.provision`; административный HTTP доступ закрыт до RBAC/MFA.

Agent route: `POST /api/internal/v1/agent/usage`, version `1`. Headers:
`X-Agent-Node-Id`, `X-Agent-Credential`, `X-Agent-Timestamp` (Unix seconds),
`X-Agent-Nonce` (UUID). HTTPS is mandatory outside loopback. Credential is returned
once by trusted provisioning, stored as SHA-256 digest, and cannot be recovered.
The ±60 second timestamp window and per-node unique nonce prevent replay; nonce
consumption and usage receipt/debit commit atomically. A retry needs a fresh nonce
and the same sample idempotency key. Stable errors use the standard API envelope.
JSON contract: `packages/contracts/agent-usage.v1.json`.

Counters are integer bytes from the node. Device private keys, URLs, DNS, and payloads
are not accepted. No shell command endpoint exists. See
[ADR-008](../../docs/adr/008-node-agent-contract.md).

I-012 adds control-plane removal endpoints `/api/internal/v1/agent/peers/poll`
and `/ack`, capability `peer-removal.v1`, durable outbox and exact peer-version
checks. These endpoints do not install or run a node agent. Synthetic assigned
peers in tests are not real node changes. See [tunnel runbook](../../docs/runbooks/tunnels.md).

V2 adds `/api/internal/v1/agent/peers/lifecycle/{poll,ack}`, `peer-lease.v2` and
`kernel-udp-budget.v1`, observed inventory/runtime checks and quota settlement.
Loopback transport in the reviewed staging harness is protected by SSH forwarding.
Do not connect these leases to an independent I-008 debit pipeline.

Production staging entrypoint pins the native core network namespace from private
endpoint readiness metadata, checking root UID, exact service cgroup, executable hash
and namespace inode. Fixed nft children receive this fd explicitly through nsenter;
the agent HTTP transport stays in host loopback. A named netns placeholder outside
the endpoint's private mount namespace is not used. The separate real local systemd
reproduction is `scripts/i012_systemd_acceptance.py` (no WAN acceptance claim).

Not implemented: production credential rotation, nonce retention job, public TLS ingress,
multi-peer/fleet operation. Actual bounded I-012 VPS/Android acceptance has passed.
Synthetic PostgreSQL/API checks are not evidence of tunnel connectivity or bounded
quota enforcement.

Это исследовательский поток, не блокирующий основной релиз:
- Исследовать ограничения сетей и поведения выбранных версий движка.
- Тестировать только сети и инфраструктуру, на которую есть полномочия.
- Не обещать универсальный обход строгого allowlist.
