[CmdletBinding()]
param(
    # Defaults to the pinned SDK used for Android, else the repository's .cache copy.
    [string]$FlutterSdk = '',
    [string]$ApiOrigin = 'https://193.68.88.239',
    # ADR-161: up to two https fallback origins of the same API, comma-separated (empty = none).
    [string]$ApiFallbackOrigins = '',
    # ADR-084: 16 MiB window; the API accepts 64 KiB..64 MiB per window.
    [ValidateRange(65536, 67108864)]
    [int]$WindowBudgetBytes = 16777216,
    [string]$EngineOutDir = (Join-Path $env:LOCALAPPDATA 'ProjectVpn\engine-windows\out'),
    # Rebuild detour-engine.exe from the pinned source before packaging (recommended).
    [switch]$BuildEngine,
    [string]$OutputDir = ''
)
# I-032 stage 6. Builds the unsigned portable Windows beta zip:
#   source snapshot outside OneDrive -> locked pub get -> format/analyze/test -> C++ tests ->
#   flutter build windows --release -> add engine + wintun + GPL source -> zip -> SHA-256.
# Nothing is signed, installed or published. It proves a build, not a working tunnel.
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$client = Join-Path $root 'apps\client'
if (-not $OutputDir) { $OutputDir = Join-Path $client 'build\windows-zip' }
if ($ApiOrigin -notmatch '^https://[A-Za-z0-9.\-]+(:[0-9]{1,5})?$') { throw 'ApiOrigin must be an https origin without a path.' }

# ---- toolchain ---------------------------------------------------------------------------------
if (-not $FlutterSdk) {
    $FlutterSdk = Join-Path $env:LOCALAPPDATA 'ProjectVpn\toolchains\flutter'
    if (-not (Test-Path -LiteralPath (Join-Path $FlutterSdk 'bin\flutter.bat'))) {
        $FlutterSdk = Join-Path $root '.cache\toolchains\flutter'
    }
}
$flutter = Join-Path $FlutterSdk 'bin\flutter.bat'
$dart = Join-Path $FlutterSdk 'bin\dart.bat'
if (-not (Test-Path -LiteralPath $flutter)) { throw 'Flutter SDK not found; run scripts/install_flutter.ps1 first.' }
$pin = Get-Content -LiteralPath (Join-Path $client 'toolchain.json') -Raw | ConvertFrom-Json
if ((& git -C $FlutterSdk rev-parse HEAD).Trim() -ne $pin.revision) { throw 'Flutter revision mismatch.' }
$lock = Get-Content -LiteralPath (Join-Path $root 'third_party\amnezia-box\source-lock.json') -Raw | ConvertFrom-Json

$pubspec = Get-Content -LiteralPath (Join-Path $client 'pubspec.yaml') -Raw
if ($pubspec -notmatch '(?m)^version:\s*([0-9A-Za-z.\-]+)\+([0-9]+)\s*$') { throw 'Cannot read the version from pubspec.yaml.' }
$versionName = $Matches[1]
$zipName = "DetourVpn-windows-x64-$versionName.zip"

function Step([string]$Title) { Write-Host ''; Write-Host "=== $Title ===" -ForegroundColor Cyan }
function Hash([string]$Path) { (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant() }

# ---- engine + wintun -----------------------------------------------------------------------------
Step 'Engine and wintun'
if ($BuildEngine -or -not (Test-Path -LiteralPath (Join-Path $EngineOutDir 'detour-engine.exe'))) {
    & (Join-Path $PSScriptRoot 'build_engine_windows.ps1') -Build
}
if (-not (Test-Path -LiteralPath (Join-Path $EngineOutDir 'wintun.dll'))) {
    # Pinned download, verified against the zip SHA-256 and the WireGuard LLC signature.
    & (Join-Path $PSScriptRoot 'build_engine_windows.ps1') -FetchWintun
}
$engineExe = Join-Path $EngineOutDir 'detour-engine.exe'
$wintunDll = Join-Path $EngineOutDir 'wintun.dll'
foreach ($p in @($engineExe, $wintunDll)) { if (-not (Test-Path -LiteralPath $p)) { throw "Missing component: $p" } }
if ((Hash $wintunDll) -ne $lock.windows_wintun.dll_sha256) { throw 'wintun.dll does not match the pinned SHA-256.' }

# ---- source snapshot outside OneDrive --------------------------------------------------------------
Step 'Source snapshot'
$buildRoot = Join-Path $env:LOCALAPPDATA ('ProjectVpn\windows-builds\' + [guid]::NewGuid().ToString('N'))
$snapshot = Join-Path $buildRoot 'apps\client'
New-Item -ItemType Directory -Force -Path $snapshot | Out-Null
function Copy-Source([string]$Source, [string]$Destination) {
    New-Item -ItemType Directory -Force -Path $Destination | Out-Null
    foreach ($entry in Get-ChildItem -LiteralPath $Source -Force) {
        if ($entry.PSIsContainer) {
            # android/ is not needed; ephemeral/ and build/ are regenerated.
            if ($entry.Name -notin @('build', '.dart_tool', '.gradle', '.git', '.idea', 'android', 'ephemeral', 'integration_test')) {
                Copy-Source $entry.FullName (Join-Path $Destination $entry.Name)
            }
        } elseif ($entry.Name -notin @('local.properties', 'key.properties') -and
                  $entry.Name -notlike '.env*' -and $entry.Name -notlike '.flutter-*' -and
                  $entry.Extension -notin @('.key', '.pem', '.p12', '.pfx', '.jks', '.keystore', '.iml')) {
            [IO.File]::WriteAllBytes((Join-Path $Destination $entry.Name), [IO.File]::ReadAllBytes($entry.FullName))
        }
    }
}
Copy-Source $client $snapshot
# One Dart test compares its allowlist with the Android source; keep exactly that file, not android/.
$androidTestInput = 'android\app\src\main\java\org\example\vpnpoc\DiagnosticReportText.java'
New-Item -ItemType Directory -Force -Path (Split-Path -Parent (Join-Path $snapshot $androidTestInput)) | Out-Null
Copy-Item -LiteralPath (Join-Path $client $androidTestInput) -Destination (Join-Path $snapshot $androidTestInput)
Write-Output "snapshot=$buildRoot"

# ---- checks ---------------------------------------------------------------------------------------------
Step 'Dart checks'
Push-Location -LiteralPath $snapshot
try {
    & $flutter pub get --enforce-lockfile
    if ($LASTEXITCODE -ne 0) { throw 'Locked pub get failed.' }
    & $dart format --output=none --set-exit-if-changed lib test
    if ($LASTEXITCODE -ne 0) { throw 'Dart formatting check failed.' }
    & $dart analyze
    if ($LASTEXITCODE -ne 0) { throw 'Dart analysis failed.' }
    & $flutter test --no-pub
    if ($LASTEXITCODE -ne 0) { throw 'Flutter tests failed.' }
} finally { Pop-Location }

Step 'C++ unit tests (config factory, key confirmation, vault, engine process)'
& (Join-Path $PSScriptRoot 'test_windows_config.ps1')
if (-not $?) { throw 'C++ unit tests failed.' }

# ---- release build -----------------------------------------------------------------------------------------
Step 'flutter build windows --release'
Push-Location -LiteralPath $snapshot
try {
    $lockBefore = Hash (Join-Path $snapshot 'pubspec.lock')
    $windowsDefines = @()
    if ($ApiFallbackOrigins) { $windowsDefines += "--dart-define=VPN_API_FALLBACK_ORIGINS=$ApiFallbackOrigins" }
    & $flutter build windows --release --dart-define=VPN_API_ORIGIN=$ApiOrigin `
        --dart-define=VPN_WINDOW_TUNNELS=true --dart-define=VPN_WINDOW_BUDGET_BYTES=$WindowBudgetBytes @windowsDefines
    if ($LASTEXITCODE -ne 0) { throw 'Flutter Windows release build failed.' }
    if ((Hash (Join-Path $snapshot 'pubspec.lock')) -ne $lockBefore) { throw 'The release build changed the enforced pub lockfile.' }
} finally { Pop-Location }
$release = Join-Path $snapshot 'build\windows\x64\runner\Release'
if (-not (Test-Path -LiteralPath (Join-Path $release 'DetourVpn.exe'))) { throw 'DetourVpn.exe was not produced.' }

# ---- package -----------------------------------------------------------------------------------------------------
Step 'Package'
$stage = Join-Path $buildRoot 'package'
$appDir = Join-Path $stage 'DetourVpn'
New-Item -ItemType Directory -Force -Path $appDir | Out-Null
# -Path (not -LiteralPath): the wildcard must expand. Fail if the copy produced nothing.
Copy-Item -Path (Join-Path $release '*') -Destination $appDir -Recurse -Force
# Filter by extension explicitly: -Include is silently ignored together with -LiteralPath in PS 5.1.
Get-ChildItem -LiteralPath $appDir -Recurse -File |
    Where-Object { $_.Extension -in @('.pdb', '.ilk', '.lib', '.exp') } | Remove-Item -Force
foreach ($required in @('DetourVpn.exe', 'flutter_windows.dll', 'data\app.so', 'data\icudtl.dat')) {
    if (-not (Test-Path -LiteralPath (Join-Path $appDir $required))) { throw "Release file missing from the package: $required" }
}
Copy-Item -LiteralPath $engineExe -Destination (Join-Path $appDir 'detour-engine.exe')
Copy-Item -LiteralPath $wintunDll -Destination (Join-Path $appDir 'wintun.dll')
$sourceDir = Join-Path $appDir 'SOURCE'
New-Item -ItemType Directory -Force -Path $sourceDir | Out-Null
$amnezia = Join-Path $root 'third_party\amnezia-box'
foreach ($name in @($lock.archive, 'source-lock.json') + @($lock.windows_patches)) {
    Copy-Item -LiteralPath (Join-Path $amnezia $name) -Destination (Join-Path $sourceDir $name)
}
Copy-Item -LiteralPath (Join-Path $root 'THIRD_PARTY_NOTICES.md') -Destination (Join-Path $appDir 'THIRD_PARTY_NOTICES.md')
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'windows-dist\README.txt') -Destination (Join-Path $appDir 'README.txt')

New-Item -ItemType Directory -Force -Path $OutputDir | Out-Null
$zip = Join-Path $OutputDir $zipName
if (Test-Path -LiteralPath $zip) { Remove-Item -LiteralPath $zip -Force }
Add-Type -AssemblyName System.IO.Compression.FileSystem
[IO.Compression.ZipFile]::CreateFromDirectory($stage, $zip, [IO.Compression.CompressionLevel]::Optimal, $false)
Write-Output "zip=$zip"
Write-Output "zip_sha256=$(Hash $zip)"
Write-Output "size_mb=$([math]::Round((Get-Item -LiteralPath $zip).Length / 1MB, 1))"
Write-Output "version=$versionName; engine_sha256=$(Hash $engineExe)"
Write-Output 'unsigned=true; runtime_verified=false (a build is not proof of a working tunnel)'
