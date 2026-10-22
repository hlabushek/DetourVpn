# I-010: staging node deployment package

`staging-node.yml` installs reviewed AWG userspace binaries and upstream license
files on one approved Ubuntu 24.04 amd64 staging host. It does not start a tunnel,
open a port, change firewall/routing, create a peer, or provision credentials.
Artifacts were installed on the approved staging VPS; the operator reported a
second run with changed=0 and failed=0. Remote hashes, ownership and the selected
release were checked on 2026-10-19. The closing acceptance subsequently passed
real isolated AWG exchanges and runtime cleanup on this VPS. I-010 is complete
within ADR-018; public ingress, persistent user tunnels and Android/egress remain
unverified. See
[the verification report](../../docs/verification-i-010.md).

The playbook accepts exactly one host in the `vpn_staging` inventory group. The
operator must supply `vpn_staging_approved: true`, `vpn_environment: staging`,
the exact inventory name in `vpn_approved_inventory_name`, the current approved
`vpn_approved_host_address: 45.10.166.106` matching `ansible_host`, and a non-root
`ansible_user`. Remote facts are read only after local approval and artifact hash
checks. Only Ubuntu 24.04 amd64 with systemd and `/dev/net/tun` is accepted.
Ansible `become` executes modules with root privileges; sudo is effectively
root-equivalent during the run. Use a dedicated account on this staging host,
require interactive sudo authentication, and do not reuse production access.

Use `inventory.example.yml` as a shape reference. Keep actual `inventory.yml` and
`release.yml` local and ignored by Git. Do not include SSH keys, passwords, private
VPN keys, node credentials, or production addresses in either file or chat.

The release variables are `awg_go_artifact`, `awg_go_sha256`,
`awg_go_source_commit`, `awg_tools_artifact`, `awg_tools_sha256`,
`awg_tools_source_commit`, `awg_go_license`, `awg_tools_license`, and the approval
fields above. Binary source revisions must match ADR-003; hashes must be lowercase
SHA-256. Binary provenance, build input pinning, licence review and security
triage are separate prerequisites; a matching hash alone proves byte integrity.

Files are copied root-owned to `/opt/project-vpn/releases/<release-id>` where the
release id is derived from both binary hashes. The playbook verifies remote hashes
before changing `/opt/project-vpn/current`. It keeps prior release directories for
rollback. No service is restarted by this playbook.

Commands and acceptance steps are in [the staging runbook](../../docs/runbooks/staging-node.md).
`staging-network-preflight.yml` reads selected socket, route and Docker metadata
using interactive sudo. It requires an explicit `vpn_approved_host_address` in
addition to the staging approval fields. It does not dump configs or environment
variables. Read commands also execute in check mode. Syntax and six pre-connection
denial cases passed. The operator supplied a successful privileged live run
(ok=3, changed=0, failed=0): existing Amnezia containers publish UDP 34053/34610.
This does not validate their tunnels or our separate test peer.
Controller syntax was checked with `ansible-core==2.21.5` on local Linux. It is
GPL-3.0-or-later, used as a separate admin tool, and is not bundled in the VPN
service. Before live use, pin and review its full controller dependency set.

The preflight now additionally runs the stdlib-only helper
`files/staging_network_inventory.py` through host Python. It selects Docker IPAM
and endpoint address fields and uses only the host `ip` binary in each fixed
Amnezia container's network namespace via `nsenter`. No container executable,
full config, environment or traffic is read. Container identity/start/PID/network
attachments are checked before/after; failure emits no partial helper JSON.
The extension passed 17 fake-result tests, strict mypy, lint, syntax/guard and
local code-transport checks. The operator then supplied its successful live
run (ok=5, changed=0, failed=0): both existing tunnel namespaces route
10.8.1.0/24. The separate local WSL smoke script passed real handshake/packet/
peer-removal/cleanup checks; it does not deploy or verify our new staging tunnel.

`staging-acceptance.yml` is the I-010 closing procedure: controller guard before
SSH, artifact installation twice, four deadline-bounded namespace tests on the
real approved VPS, and restoration checks before each next run. It does not open
public ports or provision a user-facing VPN. A dated non-secret live receipt is
written only after every assertion passes. The package passed local tests,
including an actual systemd deadline/cleanup run; the final VPS run has passed and its dated receipt has been verified. See ADR-018 and the final runbook section.

The first live acceptance failed at start_pinned_awg because Ubuntu /run is
noexec; all restoration/cleanup checks passed. The runner now keeps only secrets
in /run and verified public executable copies in private /tmp, with an early X_OK
check. NoExecPaths=/run is explicit in the transient unit. Four real local
systemd regressions passed, followed by a successful live rerun (ok=48,
changed=2, failed=0). I-010 is complete within ADR-018; external ingress and
Android/egress are not verified.
