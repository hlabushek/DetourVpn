# Install only the reviewed expiring staging controller. Password stays in local Ansible prompt.
param([switch]$SyntaxCheck, [switch]$UseExistingGrant)
$ErrorActionPreference = 'Stop'
$repoPath = Split-Path -Parent $PSScriptRoot
function Assert-PhysicalPhoneReady {
    $taskAdb = Join-Path $env:LOCALAPPDATA 'Android\Sdk\platform-tools\adb.exe'
    if (!(Test-Path -LiteralPath $taskAdb)) { throw 'Android SDK adb is missing; staging was not changed.' }
    $taskDevices = @(& $taskAdb devices)
    if ($LASTEXITCODE -ne 0 -or @($taskDevices | Where-Object { $_ -match '\tdevice\s*$' }).Count -ne 1) {
        throw 'Connect and unlock exactly one phone, approve USB debugging, and wait for adb status device. Staging was not changed.'
    }
}
if ($SyntaxCheck -and $UseExistingGrant) { throw 'Choose syntax check or an existing grant.' }
if (!$SyntaxCheck) {
    Assert-PhysicalPhoneReady
    if (!$UseExistingGrant) {
        & (Join-Path $PSScriptRoot 'build_android_poc.ps1')
        if ($LASTEXITCODE -ne 0) { throw 'Android checks failed; staging was not changed.' }
        Push-Location -LiteralPath $repoPath
        try {
            & uv run --frozen pytest -q services/api/tests/test_i012_evidence.py
            if ($LASTEXITCODE -ne 0) { throw 'Control/evidence regression failed; staging was not changed.' }
            & uv run --frozen python scripts/check_i012_control_events.py
            if ($LASTEXITCODE -ne 0) { throw 'Phone control IPC failed; staging was not changed.' }
        } finally { Pop-Location }
    }
    Assert-PhysicalPhoneReady
    $taskPrivateDir = Join-Path $env:LOCALAPPDATA 'ProjectVpn\i012-private'
    $taskDirectory = New-Item -ItemType Directory -Path $taskPrivateDir -Force
    if ($taskDirectory.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Private directory must not be a link.' }
    $taskIdentity = [Security.Principal.WindowsIdentity]::GetCurrent().User
    $taskOwner = (Get-Acl -LiteralPath $taskPrivateDir).GetOwner([Security.Principal.SecurityIdentifier])
    if ($taskOwner -ne $taskIdentity) { throw 'Private directory must belong to the current user.' }
    # Write only the DACL. Reusing Get-Acl can also request SACL privileges on Windows PowerShell.
    $taskAcl = [Security.AccessControl.DirectorySecurity]::new()
    $taskAcl.SetAccessRuleProtection($true, $false)
    $taskAcl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new(
        $taskIdentity, 'FullControl', 'ContainerInherit,ObjectInherit', 'None', 'Allow'))
    if ($PSVersionTable.PSVersion.Major -ge 7) {
        [IO.FileSystemAclExtensions]::SetAccessControl([IO.DirectoryInfo]$taskDirectory, $taskAcl)
    } else {
        $taskDirectory.SetAccessControl($taskAcl)
    }
}
$taskArguments = @('-d', 'kali-linux', '--cd', $repoPath, '--',
    '/tmp/vpn-ansible-2.21.5/bin/ansible-playbook',
    '-i', 'infra/ansible/inventory.yml', 'infra/ansible/staging-tunnel-lifecycle.yml',
    '--extra-vars', '@infra/ansible/release.yml',
    '--extra-vars', 'vpn_approved_host_address=45.10.166.106',
    '--extra-vars', 'vpn_i012_staging_approved=true')
if ($SyntaxCheck) { $taskArguments += '--syntax-check' }
else { $taskArguments += '--ask-become-pass' }
if (!$UseExistingGrant) {
    & wsl @taskArguments
    if ($LASTEXITCODE -ne 0) { throw 'I-012 preparation failed. Keep task name/recap; do not send secrets or enable verbose logs.' }
}
if ($SyntaxCheck) { exit 0 }
$taskKey = Join-Path $taskPrivateDir 'staging-key'
$taskPreviousApproval = $env:VPN_I012_STAGING_APPROVED
$taskPreviousPytest = $env:PYTEST_ADDOPTS
try {
    Copy-Item -LiteralPath '\\wsl.localhost\kali-linux\home\ad\.ssh\vpn_staging_20261019' -Destination $taskKey -Force
    $env:VPN_I012_STAGING_APPROVED = '45.10.166.106'
    $env:PYTEST_ADDOPTS = '-q -s --tb=short -k i012_live'
    Push-Location -LiteralPath $repoPath
    try {
        & uv run --frozen python scripts/test_with_postgres.py --postgres-bin .cache/i-004/portable/pgsql/bin
        if ($LASTEXITCODE -ne 0) { throw 'I-012 live acceptance failed. Keep only sanitized evidence; do not repeat blindly.' }
    } finally { Pop-Location }
} finally {
    $env:VPN_I012_STAGING_APPROVED = $taskPreviousApproval
    $env:PYTEST_ADDOPTS = $taskPreviousPytest
    if (Test-Path -LiteralPath $taskKey) { Remove-Item -LiteralPath $taskKey -Force }
}
