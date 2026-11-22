# ADR-172 — Slot bootstrap refuses a host whose firewall drops routed traffic

**Status:** accepted. Code, runbook and tests in the working tree (not committed: commits were
blocked by the permission system on 2026-11-23). **Not deployed; no host was changed.**

## Context
The slot bootstrap installs its host rules in its own `inet` table (forward chain at priority
-10, `policy accept`). In nftables an accept verdict in one base chain does not stop a later
base chain on the same hook from dropping the packet. ufw with the default `deny (routed)`
creates an iptables-nft `FORWARD` chain with `policy drop`, so the DNAT'd client UDP and the
slots' outbound traffic are dropped although the project's table accepts them. The preflight
checked only `ip_forward`, the bootstrap reported success and the node passed no traffic. The
Poland node needed ufw route rules by hand; no script or runbook mentioned it. Found by the
2026-11-22 audit (H5). Docker's `FORWARD` policy `DROP` has the same effect.

## Decision
1. `_host_network_preflight` reads `nft -j list ruleset`. For every forward-hook chain with
   `policy drop` outside the project's tables, the same table must contain plain accept rules
   for both directions of every slot veth: `iifname dvbh<N> oifname <uplink>` and
   `iifname <uplink> oifname dvbh<N>` (what `ufw route allow in on … out on …` creates).
   Otherwise the bootstrap fails with `host_forward_policy_drops` before it creates anything.
2. The bootstrap never changes a foreign firewall. The owner adds the rules by hand
   (`docs/runbooks/beta-add-node.md`, step 1.4).
3. `scripts/install_beta_node.py` holds the two rule lines as placeholders (`dvbh<N>`,
   `<UPLINK>`) and prints them as `manual_fix_on_new_host` when an install fails with this
   code. It runs no firewall command.
4. Changing ufw's `DEFAULT_FORWARD_POLICY` to `ACCEPT` is not recommended: the host would then
   forward any traffic routed to it.

## Limits
- Rule order, jumps and earlier drop rules inside the foreign chain are not evaluated; the
  check proves that the rules exist, not that they are reached. ufw places route rules in
  `ufw-user-forward`, which its `FORWARD` chain reaches.
- iptables-legacy rules are invisible to nft; on such a host the check passes without proof.
  Ubuntu 24.04 (the only supported node OS) uses iptables-nft.
- Existing nodes are unaffected until their bootstrap restarts with the new code; a node that
  works today without route rules has no dropping forward chain.

## Tests
- `infra/ansible/tests/test_audit_stage2_forward_policy.py`: a ufw-style dropping chain
  without route rules is refused; with both directions for the slot it passes; one direction
  or another slot's veth is refused; a host without a dropping chain passes; the install
  failure hint carries placeholders only and matches the runbook.
- Related: `infra/ansible/tests/test_beta_slot_bootstrap.py`,
  `services/api/tests/test_install_beta_node.py`, `services/api/tests/test_add_beta_node.py`.

## Not verified
- No real kernel, ufw, Docker or node. The nft JSON shape of ufw's rules is taken from
  iptables-nft's documented translation, not captured from a host.
