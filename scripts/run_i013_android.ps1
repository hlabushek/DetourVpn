param([switch]$Staging, [switch]$UseExistingGrant)
$ErrorActionPreference = 'Stop'
if ($Staging -and $env:VPN_I014_LOCAL_ANDROID -eq '1') { throw 'I-014 checks are local only. Staging was not changed.' }
if ($Staging -and $env:VPN_I015_LOCAL_ANDROID -eq '1') { throw 'I-015 checks are local only. Staging was not changed.' }
$taskRoot = Split-Path -Parent $PSScriptRoot
$taskFlutterSdk = if ($env:VPN_I013_FLUTTER_SDK) { $env:VPN_I013_FLUTTER_SDK } else { Join-Path $env:LOCALAPPDATA 'ProjectVpn\toolchains\flutter' }
$taskClientDirectory = if ($env:VPN_I013_CLIENT_DIR) { $env:VPN_I013_CLIENT_DIR } else { Join-Path $taskRoot 'apps\client' }
if ($UseExistingGrant -and !$Staging) { throw 'Existing grant applies only to staging mode.' }
$taskAdb = Join-Path $env:LOCALAPPDATA 'Android\Sdk\platform-tools\adb.exe'
$taskDevices = @(& $taskAdb devices | Where-Object { $_ -match '\tdevice\s*$' })
if ($taskDevices.Count -ne 1) { throw 'Connect/unlock exactly one physical Android phone. Staging was not changed.' }
if (!(Test-Path -LiteralPath (Join-Path $taskRoot 'apps\client\build\app\outputs\flutter-apk\app-debug.apk'))) {
    throw 'Run scripts/build_flutter_client.ps1 first.'
}
# Compile the exact physical test target BEFORE any staging grant/endpoint action.
$taskAbi = (& $taskAdb shell getprop ro.product.cpu.abi).Trim()
$taskPlatform = switch ($taskAbi) {
    'arm64-v8a' { 'android-arm64' }
    'x86_64' { 'android-x64' }
    default { throw 'Unsupported phone ABI. Staging was not changed.' }
}
$taskPreviousJava = $env:JAVA_HOME
$taskPreviousPub = $env:PUB_CACHE
try {
    $env:JAVA_HOME = 'C:\Program Files\Android\Android Studio\jbr'
    $env:PUB_CACHE = Join-Path (Split-Path -Parent $taskFlutterSdk) 'pub-cache'
    Push-Location -LiteralPath $taskClientDirectory
    try {
        # Restore debug native-plugin metadata after a release build, and
        # regenerate package paths when using a writable source/SDK snapshot.
        & (Join-Path $taskFlutterSdk 'bin\flutter.bat') pub get --offline --enforce-lockfile
        if ($LASTEXITCODE -ne 0) { throw 'Locked physical-test dependencies are unavailable.' }
        & (Join-Path $taskFlutterSdk 'bin\flutter.bat') build apk `
            --debug --no-pub --target-platform $taskPlatform --target integration_test/client_api_test.dart `
            "--dart-define=VPN_I013_LIVE=$($Staging.IsPresent.ToString().ToLowerInvariant())" `
            "--dart-define=VPN_I014_LOCAL=$(($env:VPN_I014_LOCAL_ANDROID -eq '1').ToString().ToLowerInvariant())" `
            "--dart-define=VPN_I015_LOCAL=$(($env:VPN_I015_LOCAL_ANDROID -eq '1').ToString().ToLowerInvariant())"
        if ($LASTEXITCODE -ne 0) { throw 'Physical test compilation failed. Staging was not changed.' }
    } finally { Pop-Location }
} finally { $env:JAVA_HOME = $taskPreviousJava; $env:PUB_CACHE = $taskPreviousPub }
$taskPreviousLocal = $env:VPN_I013_LOCAL_ANDROID
$taskPreviousApproval = $env:VPN_I013_STAGING_APPROVED
$taskPreviousPytest = $env:PYTEST_ADDOPTS
$taskPrivate = Join-Path $env:LOCALAPPDATA 'ProjectVpn\i012-private'
$taskKey = Join-Path $taskPrivate 'staging-key'
try {
    if ($Staging) {
        # Explicit -Staging is the reviewed opt-in to ADR-024's fixed isolated host.
        # Password is typed only in the user's local Ansible prompt, never here/chat.
        $taskDirectory = New-Item -ItemType Directory -Force -Path $taskPrivate
        if ($taskDirectory.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Private directory must not be a link.' }
        $taskIdentity = [Security.Principal.WindowsIdentity]::GetCurrent().User
        if ((Get-Acl -LiteralPath $taskPrivate).GetOwner([Security.Principal.SecurityIdentifier]) -ne $taskIdentity) {
            throw 'Private directory must belong to current user.'
        }
        $taskAcl = [Security.AccessControl.DirectorySecurity]::new()
        $taskAcl.SetAccessRuleProtection($true, $false)
        $taskAcl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new(
            $taskIdentity, 'FullControl', 'ContainerInherit,ObjectInherit', 'None', 'Allow'))
        if ($PSVersionTable.PSVersion.Major -ge 7) {
            [IO.FileSystemAclExtensions]::SetAccessControl([IO.DirectoryInfo]$taskDirectory, $taskAcl)
        } else { $taskDirectory.SetAccessControl($taskAcl) }
        if (!$UseExistingGrant) {
            & wsl -d kali-linux --cd $taskRoot -- /tmp/vpn-ansible-2.21.5/bin/ansible-playbook `
                -i infra/ansible/inventory.yml infra/ansible/staging-tunnel-lifecycle.yml `
                --extra-vars '@infra/ansible/release.yml' `
                --extra-vars 'vpn_approved_host_address=45.10.166.106' `
                --extra-vars 'vpn_i012_staging_approved=true' --ask-become-pass
            if ($LASTEXITCODE -ne 0) { throw 'Bounded staging preparation failed. Do not repeat blindly or send secrets.' }
        }
        Copy-Item -LiteralPath '\\wsl.localhost\kali-linux\home\ad\.ssh\vpn_staging_20261019' -Destination $taskKey -Force
        $env:VPN_I013_STAGING_APPROVED = '45.10.166.106'
        $env:VPN_I013_LOCAL_ANDROID = $null
    } else {
        $env:VPN_I013_STAGING_APPROVED = $null
        $env:VPN_I013_LOCAL_ANDROID = '1'
    }
    $env:PYTEST_ADDOPTS = '-q --tb=short -k test_i013_android_client'
    Push-Location -LiteralPath $taskRoot
    try {
        $taskPython = Join-Path $taskRoot '.venv\Scripts\python.exe'
        if (Test-Path -LiteralPath $taskPython) {
            & $taskPython scripts/test_with_postgres.py --postgres-bin .cache/i-004/portable/pgsql/bin
        } else {
            & uv run --frozen python scripts/test_with_postgres.py --postgres-bin .cache/i-004/portable/pgsql/bin
        }
        if ($LASTEXITCODE -ne 0) { throw 'I-013 physical acceptance failed. Inspect only sanitized evidence.' }
    } finally { Pop-Location }
} finally {
    $env:VPN_I013_LOCAL_ANDROID = $taskPreviousLocal
    $env:VPN_I013_STAGING_APPROVED = $taskPreviousApproval
    $env:PYTEST_ADDOPTS = $taskPreviousPytest
    if ($Staging -and (Test-Path -LiteralPath $taskKey)) { Remove-Item -LiteralPath $taskKey -Force }
}
