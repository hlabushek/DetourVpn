#!/usr/bin/env bash
set -Eeuo pipefail

run_id="$1"
script_hash="$2"
module_hash="$3"
unit_hash="$4"
[[ "$run_id" =~ ^[0-9a-f]{32}$ ]]

stage="/root/.cache/detourvpn-beta-backup-$run_id"
root=/opt/detourvpn-beta
script="$root/scripts/beta_backup.py"
module="$root/services/api/src/vpn_api/beta_backup.py"
unit=/etc/systemd/system/detourvpn-beta-backup.service
key_dir=/etc/detourvpn-beta/backup-secrets
key="$key_dir/backup-key"
archive_dir=/var/lib/detourvpn-beta-backups
backup_script="$script.pre-beta-local-pull-$run_id"
backup_module="$module.pre-beta-local-pull-$run_id"
updated_script=0
updated_module=0
updated_unit=0

rollback() {
  set +e
  if (( updated_script )); then cp -a -- "$backup_script" "$script"; fi
  if (( updated_module )); then cp -a -- "$backup_module" "$module"; fi
  if (( updated_unit )); then rm -f -- "$unit"; fi
  /usr/bin/systemctl daemon-reload
}
on_error() {
  status=$?
  rollback
  echo beta_backup_rollout_failed_rolled_back >&2
  exit "$status"
}
trap on_error ERR

for file in install-local-backup.sh beta_backup.py backup-module.py backup.service backup.key; do
  test -f "$stage/$file"
done
printf '%s  %s\n' "$script_hash" "$stage/beta_backup.py" | sha256sum -c - >/dev/null
printf '%s  %s\n' "$module_hash" "$stage/backup-module.py" | sha256sum -c - >/dev/null
printf '%s  %s\n' "$unit_hash" "$stage/backup.service" | sha256sum -c - >/dev/null
test "$(sha256sum "$script" | cut -d ' ' -f 1)" = bad3533fe39e05b8da6d5d39fbd0ff7eaae531c1c5eeba0387af0e4320be225e
test "$(sha256sum "$module" | cut -d ' ' -f 1)" = d0438503b855711905c0606db6d3f931bb57b43c41e588db500f4b2f16edb860
test ! -e "$unit"
test "$(systemctl is-active detourvpn-beta-api.service)" = active
test "$(curl -fsS -o /dev/null -w '%{http_code}' http://127.0.0.1:28000/api/v1/ready)" = 200
test -r /etc/detourvpn-beta/operations.env
grep -q '^VPN_DATABASE_URL=.' /etc/detourvpn-beta/operations.env
test "$(wc -c < "$stage/backup.key")" -eq 44
"$root/.venv/bin/python" -c 'from cryptography.fernet import Fernet; import sys; Fernet(open(sys.argv[1], "rb").read())' "$stage/backup.key"

cp -a -- "$script" "$backup_script"
cp -a -- "$module" "$backup_module"
updated_script=1
updated_module=1
/usr/bin/install -o root -g root -m 0644 "$stage/beta_backup.py" "$script"
/usr/bin/install -o root -g root -m 0644 "$stage/backup-module.py" "$module"
"$root/.venv/bin/python" "$script" --help >/dev/null
"$root/.venv/bin/python" -c 'import sys; compile(open(sys.argv[1], "rb").read(), sys.argv[1], "exec")' "$module"

if test -e "$archive_dir"; then
  test -d "$archive_dir" && test ! -L "$archive_dir"
  test "$(stat -c '%U:%G:%a' "$archive_dir")" = detourvpn-beta:detourvpn-beta:700
else
  /usr/bin/install -d -o detourvpn-beta -g detourvpn-beta -m 0700 "$archive_dir"
fi
if test -e "$key_dir"; then
  test -d "$key_dir" && test ! -L "$key_dir"
  test "$(stat -c '%U:%G:%a' "$key_dir")" = root:detourvpn-beta:750
else
  /usr/bin/install -d -o root -g detourvpn-beta -m 0750 "$key_dir"
fi
if test -e "$key"; then
  test -f "$key" && test ! -L "$key"
  test "$(stat -c '%U:%G:%a' "$key")" = root:detourvpn-beta:440
  cmp -s -- "$stage/backup.key" "$key"
else
  /usr/bin/install -o root -g detourvpn-beta -m 0440 "$stage/backup.key" "$key"
fi
test ! -e "$archive_dir/latest.enc"
/usr/bin/systemd-analyze verify "$stage/backup.service"
/usr/bin/install -o root -g root -m 0644 "$stage/backup.service" "$unit"
updated_unit=1
/usr/bin/systemctl daemon-reload
/usr/bin/systemd-analyze verify "$unit"
/usr/bin/systemctl start detourvpn-beta-backup.service
test "$(systemctl show detourvpn-beta-backup.service --property=Result --value)" = success
test "$(systemctl show detourvpn-beta-backup.service --property=ExecMainStatus --value)" = 0
test -f "$archive_dir/latest.enc" && test ! -L "$archive_dir/latest.enc"
test "$(stat -c '%U:%G:%a' "$archive_dir/latest.enc")" = detourvpn-beta:detourvpn-beta:600
size=$(stat -c '%s' "$archive_dir/latest.enc")
test "$size" -gt 0 && test "$size" -le 67108864
test "$(curl -fsS -o /dev/null -w '%{http_code}' http://127.0.0.1:28000/api/v1/ready)" = 200

trap - ERR
printf 'beta_backup_service_passed\napi_readiness=200\narchive_bytes=%s\n' "$size"
printf 'rollback_script=%s\nrollback_module=%s\nunit_installed=yes\n' \
  "$(basename "$backup_script")" "$(basename "$backup_module")"
