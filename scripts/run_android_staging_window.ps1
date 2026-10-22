# I-011 only. Owner approved WAN staging acceptance in this chat on 2026-10-19.
# Sudo password is entered directly into Ansible; never stored by this helper.
param([switch]$SyntaxCheck, [switch]$Diagnose, [switch]$GrantControl)
$ErrorActionPreference = 'Stop'
$repoPath = Split-Path -Parent $PSScriptRoot
$publicKeyPath = Join-Path $repoPath '.cache\i-011\device-public-key'
if (!(Test-Path -LiteralPath $publicKeyPath -PathType Leaf)) {
    throw 'Current Android POC public key file is missing. Do not use a private key.'
}
if (([IO.File]::ReadAllText($publicKeyPath).Trim()) -notmatch '^[A-Za-z0-9+/]{43}=$') {
    throw 'The captured public key file is invalid.'
}
$ansibleArguments = @(
    '-d', 'kali-linux', '--cd', $repoPath, '--',
    '/tmp/vpn-ansible-2.21.5/bin/ansible-playbook',
    '-i', 'infra/ansible/inventory.yml',
    'infra/ansible/staging-android-window.yml',
    '--extra-vars', '@infra/ansible/release.yml',
    '--extra-vars', 'vpn_approved_host_address=45.10.166.106',
    '--extra-vars', 'vpn_i011_wan_approved=true'
)
if ($SyntaxCheck) { $ansibleArguments += '--syntax-check' }
else { $ansibleArguments += '--ask-become-pass' }
if ($Diagnose) { $ansibleArguments += @('--extra-vars', 'vpn_i011_diagnose_only=true') }
if ($GrantControl) {
    $ansibleArguments += @('--extra-vars', 'vpn_i011_grant_control=true',
                          '--extra-vars', 'vpn_i011_diagnose_only=true')
}
& wsl @ansibleArguments
if ($LASTEXITCODE -ne 0) {
    throw 'I-011 acceptance did not pass. Keep the task name and PLAY RECAP; do not enable verbose logs or send credentials.'
}
