# Backup API host (sslip.io, beta) — deploy_ha_server.sh

Status: **written and syntax-checked locally only. Nothing is pushed, deployed or migrated.**
The database layer now accepts a remote TLS database (ADR-166), unit-tested only.

## Decisions in force

- **One database for primary and backup:** a managed Neon PostgreSQL, migrated from the old host.
- **TLS to the database:** required. Use `sslmode=verify-full` (recommended) or `require`.
  `require` encrypts but does not verify the certificate (ADR-166, Security notes).
- **Direct endpoint, not the pooler:** use the Neon host without `-pooler` (ADR-166).
- **No Docker:** the API runs under systemd with uvicorn, like the existing beta unit.
- **No CORS:** the Flutter app is native and does not send browser CORS requests.
- **Node agent not installed by the script:** nodes are installed from your workstation
  with `scripts/install_beta_node.py` over SSH. See "VPN node".

## Open items (not solved here)

1. **Commits must be pushed before `REPO_REF` can point at them.** The ADR-151..166 work is
   local only on `claude/connection-reliability`.
2. **Let's Encrypt on sslip.io** is a shared domain and can hit rate limits. If issuance fails
   with a rate-limit error, the repo's approved IP-certificate path (`infra/beta/deploy-certificate.sh`)
   is the alternative.
3. **Other API secrets** (auth pepper, Telegram secret, admin key, ...) are not written by the
   script. Copy them from the primary's environment yourself.
4. **Replication and failover of Neon itself** are not configured here. ADR-163 lists the work.
5. **`uv sync` at the repo root** is an assumption about the workspace layout and is unverified.

## Part A — Migrate the old database to Neon

### A1. Prepare Neon (you, in the Neon dashboard)

1. Create the project and the database. Create a role for the API (not the owner role).
2. Copy the **direct** connection string. Its shape is:

   ```
   postgresql+psycopg://<ROLE>:<PASSWORD>@<ENDPOINT_HOST>.<REGION>.aws.neon.tech/<DATABASE>?sslmode=verify-full
   ```

   Two changes from Neon's default string, which are required by the API:
   - use the scheme `postgresql+psycopg://` instead of `postgresql://`;
   - remove `-pooler` from the host;
   - keep only `sslmode` (and optionally `channel_binding=require`) as query parameters.

   **Do not paste the password into chat.** I do not have your Neon string.

3. Check the server version: `psql "<DIRECT_URL_WITHOUT_+psycopg>" -c 'show server_version'`
   (replace `postgresql+psycopg://` with `postgresql://` for `psql`).

### A2. Dump and restore (on the old host)

Stop the old API first so the dump is consistent. Use the service name on that host.

```bash
sudo systemctl stop <OLD_API_SERVICE>
```

Install a PostgreSQL client whose major version matches or is newer than both the source
server and Neon (for example `sudo apt-get install -y postgresql-client-17`).

Run the migration script (values are placeholders, do not hardcode them):

```bash
SOURCE_DATABASE_URL='postgresql://<LOCAL_USER>:<LOCAL_PASSWORD>@127.0.0.1/<DB>' \
TARGET_DATABASE_URL='postgresql://<ROLE>:<PASSWORD>@<ENDPOINT_HOST>.<REGION>.aws.neon.tech/<DATABASE>?sslmode=verify-full&sslrootcert=system' \
DUMP_DIR=/root/neon-migration \
bash scripts/migrate_db_to_neon.sh
```

Note: `pg_dump`, `pg_restore` and `psql` take plain `postgresql://` URLs, and libpq needs
`sslrootcert=system` on Linux to verify Neon's public certificate. The API uses
`postgresql+psycopg://` and does not accept `sslrootcert`, so the two URLs differ.

The script dumps in custom format, restores in one transaction, and compares row counts per
table. It stops if they differ. **The dump contains personal and account data:** keep it
mode 0600 and delete it after a verified restore (`rm -f <dump file>`).

When you are satisfied, restart the old API only if the primary stays on the old host.
Otherwise leave it stopped and move the primary to Neon.

## Part B — Deploy the backup API on the new server

### B1. Set the environment (on the new server, as root)

These are placeholders. Do not hardcode real values in the script or in chat.

```bash
sudo -i
export REPO_URL='<REPO_URL>'                       # git URL the server can clone
export REPO_REF='<PUSHED_COMMIT_OR_BRANCH>'        # must be pushed first (open item 1)
export VPN_DATABASE_URL='postgresql+psycopg://<ROLE>:<PASSWORD>@<ENDPOINT_HOST>.<REGION>.aws.neon.tech/<DATABASE>?sslmode=verify-full'
export VPN_ENVIRONMENT='<ENVIRONMENT_NAME>'        # the value the primary API uses
export ACME_EMAIL='<YOUR_CONTACT_EMAIL>'           # Let's Encrypt contact
export NODE_UDP_PORT='51899'                       # confirm against the node inventory
# optional: export PUBLIC_IP='<IPV4>'  (otherwise the script asks)
```

`VPN_DATABASE_URL` must not contain quotes or newlines.

### B2. Run the script

Upload or clone the repo at `REPO_REF`, then (still as root, `sudo -i` shell with the exports):

```bash
bash scripts/deploy_ha_server.sh
```

The script:
- asks for the server's public IPv4 (or uses `PUBLIC_IP`) and builds `<IP>.sslip.io`;
- installs packages, Caddy (official repo) and uv 0.11.17 (sha256 checked);
- writes `/etc/detourvpn-backup/api.env` (mode 0640, values not printed);
- runs `uv sync --frozen --no-dev`;
- checks `VPN_DATABASE_URL` with the API's own parser (ADR-166) and stops if it is rejected;
- starts `detourvpn-backup-api` on `127.0.0.1:28000`;
- configures Caddy for `https://<IP>.sslip.io` with Let's Encrypt and a path allowlist;
- configures ufw for SSH, 80, 443 and the node UDP port (SSH is allowed first).

### B3. Add the remaining API secrets (you)

The script does not write them. Copy the other values from the primary API's environment
into `/etc/detourvpn-backup/api.env`, mode 0640, then restart:

```bash
sudo systemctl restart detourvpn-backup-api
```

### B4. Check

```bash
systemctl status detourvpn-backup-api --no-pager
journalctl -u detourvpn-backup-api -n 50 --no-pager
sudo ufw status verbose
curl -sI https://<IP>.sslip.io/api/v1/plans
```

`/api/v1/ready` is blocked at the public proxy by design. Check it on loopback only:
`curl -s http://127.0.0.1:28000/api/v1/ready` on the server.

## Part C — Use the backup in the app

1. Build the client with `-ApiFallbackOrigins 'https://<IP>.sslip.io'` (ADR-161). Up to two
   fallback origins. Nothing changes if it is empty.
2. Do not point the primary at the backup. The client tries the primary first and falls back in
   order, so no DNS change is needed.

## VPN node

The script does not install the node. The repo installs nodes from your workstation with
`scripts/install_beta_node.py` over SSH, using `infra/beta/nodes.json`. It is a dry run unless
`--execute` is passed, and it does not start the node agent. Binding a node to the API needs a
node-agent credential and an inventory entry, which are owner steps. The script opens the UDP port
in ufw as you asked, but nothing listens on it until a node is installed. Close it with
`sudo ufw delete allow <NODE_UDP_PORT>/udp` if you prefer.

## Tests

- `services/api/tests/test_database_config.py`: 30 tests, all passed (ADR-166).
- Full API suite from the repo root with the project virtualenv: 726 passed, 413 skipped. The
  skipped tests need `VPN_TEST_DATABASE_URL` (a real PostgreSQL) and were not run.
- The deploy and migration scripts: `bash -n` only. shellcheck was not available.
- Nothing was connected to Neon, to the old host, or to any server.
