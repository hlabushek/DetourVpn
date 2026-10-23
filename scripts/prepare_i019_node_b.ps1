# I-019 artifact placement only. No endpoint, firewall, route or service start.
param([switch]$SyntaxCheck)
$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot
$taskKnownHosts = Join-Path $env:USERPROFILE '.ssh\known_hosts'
if (!$SyntaxCheck) {
    # Transfer only an existing owner-trusted public host key; never trust keyscan.
    $taskTrustedHost = @(& ssh-keygen -F '85.209.132.205' -f $taskKnownHosts |
        Where-Object { $_ -and !$_.StartsWith('#') })
    if ($LASTEXITCODE -ne 0 -or $taskTrustedHost.Count -eq 0) {
        throw 'No previously trusted Windows SSH host key for B. Staging unchanged.'
    }
    # Appends public known-host records only. SSH private material is never read.
    $taskTrustedHost | & wsl -d kali-linux -- /bin/sh -c 'umask 077; mkdir -p /home/ad/.ssh; cat >> /home/ad/.ssh/known_hosts'
    if ($LASTEXITCODE -ne 0) { throw 'Cannot transfer the existing public host-key trust.' }
}
$taskArguments = @('-d', 'kali-linux', '--cd', $taskRoot, '--',
    '/tmp/vpn-ansible-2.21.5/bin/ansible-playbook',
    '-i', 'infra/ansible/inventory.i019-b.yml', 'infra/ansible/staging-node.yml',
    '--extra-vars', '@infra/ansible/release.yml',
    '--extra-vars', 'vpn_approved_host_address=85.209.132.205 vpn_approved_inventory_name=i019-node-b vpn_i019_second_node_approved=true')
if ($SyntaxCheck) { $taskArguments += '--syntax-check' }
else {
    # Force a real Linux PTY for getpass, even when Windows/WSL stdin is redirected.
    # No input/timing log; output log is /dev/null, and input echo is disabled.
    # The shell command contains only fixed public arguments, never credentials.
    $taskCommand = 'test -t 0 && test -t 1 || exit 70; exec /tmp/vpn-ansible-2.21.5/bin/ansible-playbook -i infra/ansible/inventory.i019-b.yml infra/ansible/staging-node.yml --extra-vars @infra/ansible/release.yml --extra-vars ''vpn_approved_host_address=85.209.132.205 vpn_approved_inventory_name=i019-node-b vpn_i019_second_node_approved=true'' --ask-become-pass'
    $taskArguments = @('-d', 'kali-linux', '--cd', $taskRoot, '--',
        '/usr/bin/script', '--quiet', '--return', '--echo', 'never',
        '--command', $taskCommand, '/dev/null')
    Write-Host 'Enter the sudo password for vpn-deploy on B at BECOME password (hidden input).'
}
& wsl @taskArguments
if ($LASTEXITCODE -ne 0) { throw 'I-019 B artifact preparation failed; retain sanitized task/recap, do not retry blindly.' }
