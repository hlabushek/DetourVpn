# ADR-092 — several nodes in one client

Status: proposed 2026-11-08, from the owner's request in chat to run the Helsinki, Germany and a
further other-provider server in one app. Code-only change; no host was touched and no node was
provisioned, activated or reconfigured.

## Context

The server already supports several nodes: `VPNNode` rows plus the v3 window manifest (up to 16
nodes, ADR-038/039) and the operator profile manifest for v1 (ADR-024). The Android client,
however, rejected any v1 catalog with more than two nodes and the catalog carried no country.
ADR-090 scoped the closed beta to "one to two staging/beta nodes". The owner now wants three, so this
ADR widens that scope; ADR-090 needs a matching edit once this is accepted.

## Decision

- **Selection stays manual.** The user picks a node; there is no automatic selection or failover.
  ADR-031 (manual recovery after node loss) is unchanged.
- **Client limit** equals the server manifest limit: 16 nodes (`maxCatalogNodes`). Larger or
  malformed catalogs still fail closed with `invalid_response`.
- **Display metadata** `country_code` (ISO 3166-1 alpha-2) and `city` are optional public fields on
  `vpn_nodes` (migration 0020). Missing or malformed values degrade to a globe icon and the old
  `region` text; they never reject the catalog and are never used for routing or trust.
- **Availability** shown to the user is only the server-reported reason (`available`, `capacity`, …).
  No latency or speed is displayed until it is actually measured.
- **Providers** coexist in the same manifest. Nothing in the API or client depends on the provider.
  Each node still needs its own acceptance (ADR-061/062: handshake, source-bound egress, usage
  attribution) before it is activated; a build, a catalog entry or a flag is not evidence of that.
- **Provisioning helper** `scripts/provision_beta_node.py` takes `--name`, `--region`,
  `--country-code`, `--city`; defaults reproduce the existing Helsinki node. The bootstrap, bind,
  activation and status scripts keep their fixed Helsinki identity guard on purpose: parametrising
  a fail-closed identity check is deferred until a concrete second node is defined (owner, later).

## Consequences

- Existing Helsinki rows have no country until it is set (a one-off operator update to `FI` /
  `Helsinki`, or re-provisioning).
- Changing `provision_beta_node.py` changes the SHA-256 that `deploy_beta_node_provisioning.ps1`
  verifies, so the helper must be redeployed before it is used on a host.
- No new node can be activated from this change alone.

## Verification

`flutter test` (full client suite: catalog with 16 nodes accepted, 17 rejected, country/city
parsed), `pytest` for node contracts and the provisioning CLI. PostgreSQL-backed tests need
`VPN_TEST_DATABASE_URL` and were not run where this was written.
