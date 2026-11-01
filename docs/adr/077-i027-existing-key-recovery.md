# ADR-077 — Recover the existing beta backup key without rotation

Status: accepted; owner-profile recovery and an explicit scheduled task run
passed 2026-11-01. A natural daily trigger has not yet been observed.

## Context

The daily `DetourVpn Beta Backup Daily` task failed before transfer because its
local Fernet key was not visible. The owner then confirmed
`Test-Path "$env:LOCALAPPDATA\DetourVpn\beta-backup\backup.key"` returned false
in ordinary Windows PowerShell. The Codex execution shell sees a key at that
path, demonstrating that its environment is not a reliable proxy for the
owner's interactive Windows profile. The scheduled task must not be tested
from the Codex shell.

The original Fernet key and encrypted archive remain on the beta host. The
existing SSH identity and pinned-host configuration are also used by the
current pull script. Generating and deploying a replacement key would make
existing archives unreadable.

## Decision

Use a dedicated Windows PowerShell helper to retrieve the existing key and
current encrypted archive over the existing pinned SSH connection. The helper
checks remote ownership and modes, stages both files under an owner-and-SYSTEM
only ACL, authenticates the archive with the existing Fernet importer, and
installs the key only if the local target is absent. It never prints key data,
never regenerates or deploys a key, and removes temporary copies after the
check. Existing local files are not overwritten.

When explicitly invoked with `-StartScheduledTask`, the helper verifies that
the registered task still runs the expected pull script as the current
interactive, limited user. It starts that task and requires both result code 0
and a newly imported encrypted archive. Run this helper from the owner's
ordinary Windows PowerShell in the project checkout so `$env:LOCALAPPDATA` and
the task profile resolve in the same context.

## Security and limitations

This restores local usability of the existing encrypted backups; it does not
create an independent off-host copy of the Fernet key. Anyone with access to
the beta host's root account or the owner's Windows account may be able to
obtain both key and ciphertext. SSH host-key validation remains strict; if the
host key is unknown or changes, stop and investigate rather than bypassing the
check. The signing JKS is a separate artifact and must be copied only to an
owner-approved encrypted destination, with its passwords kept in the owner's
password manager.

## Verification

Windows PowerShell 5.1 parsing and the no-overwrite guard passed. The owner ran
the helper from ordinary Windows PowerShell: the prior encrypted archive
authenticated, the existing key was installed, the task returned result 0,
and a new local encrypted archive was verified. The natural daily trigger and
a database restore from this newly imported archive remain unverified. The
owner's Task Scheduler reports last run `2026-11-01 21:05:10` with result 0 and
next run `2026-11-02 07:00:00`; the last run was the explicit helper-triggered
run, not the natural daily trigger. See
`../evidence/i-027-key-recovery-20261101.json`.
