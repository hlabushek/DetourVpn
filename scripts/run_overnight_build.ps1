# One-shot build for the connection-survivability work (ADR-149, owner steps 1).
#
# What it does, in order. It stops at the first failure and prints why:
#   1. Prepares the new SDK tree .cache\i-011\sdk-go-only-c6-20261117 with
#      prepare_android_poc.py, unless that tree already exists (it is never overwritten).
#   2. Copies jniLibs and assets from the c5 tree, and checks that only GoBackend.java
#      differs between c5 and c6.
#   3. Verifies PREPARED.json (patch_sha256) and both libwg-go.so files.
#   4. Asks for the API origin and the keystore path, and asks for the keystore password
#      as hidden input.
#   5. Sets the beta keystore environment variables and runs
#      scripts\build_flutter_client.ps1 -ApiOrigin <origin> -WindowTunnels.
#      The password is removed from the environment afterwards, win or lose.
#   6. Prints the path of the generated APK and the adb install command.
#
# It does not push, deploy, or install anything. Run it from any folder:
#   powershell -ExecutionPolicy Bypass -File .\scripts\run_overnight_build.ps1

$ErrorActionPreference = 'Stop'

$expectedPatchHash = 'd84f22bb296bfb7ab287b222e7e1b60c639eab011d6482dde31c214eb1ac326c'
$oldTreeName = 'sdk-go-only-c5-20261113'
$newTreeName = 'sdk-go-only-c6-20261117'

function Stop-Overnight([string]$Message) {
    Write-Host ''
    Write-Host "STOPPED: $Message" -ForegroundColor Red
    Write-Host 'Nothing was installed or deployed. Fix the problem above and run the script again.' -ForegroundColor Red
    exit 1
}

$startDir = (Get-Location).ProviderPath
$root = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $root

# Reads a secret and shows one '*' per character (Read-Host -AsSecureString shows nothing).
function Read-MaskedSecret([string]$Prompt) {
    Write-Host -NoNewline "$Prompt "
    $chars = New-Object System.Collections.Generic.List[char]
    while ($true) {
        $key = [Console]::ReadKey($true)
        if ($key.Key -eq [ConsoleKey]::Enter) { break }
        if ($key.Key -eq [ConsoleKey]::Backspace) {
            if ($chars.Count -gt 0) {
                $chars.RemoveAt($chars.Count - 1)
                Write-Host -NoNewline "`b `b"
            }
            continue
        }
        if ([int]$key.KeyChar -lt 32) { continue }
        $chars.Add($key.KeyChar)
        Write-Host -NoNewline '*'
    }
    Write-Host ''
    return -join $chars.ToArray()
}
$cacheDir = Join-Path $root '.cache\i-011'
$oldTree = Join-Path $cacheDir $oldTreeName
$newTree = Join-Path $cacheDir $newTreeName
$newReceiptPath = Join-Path $newTree 'PREPARED.json'

Write-Host "Repository: $root"

if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    Stop-Overnight 'python is not on PATH. Add it, or open a shell where "python" works.'
}
if (-not (Test-Path -LiteralPath $oldTree -PathType Container)) {
    Stop-Overnight "The c5 SDK tree is missing: $oldTree"
}

# ---- Step 1: prepare the new tree -------------------------------------------------
Write-Host ''
Write-Host 'Step 1: prepare the c6 SDK tree'
if (Test-Path -LiteralPath $newReceiptPath -PathType Leaf) {
    Write-Host "  Already prepared, not overwritten: $newTree"
} else {
    # prepare_android_poc.py only accepts targets that resolve inside this worktree.
    # In this worktree .cache is a junction to the main checkout's cache, so the tree is
    # prepared in build\ (git-ignored) and then moved into .cache\i-011.
    $stagingTree = Join-Path $root 'build\sdk-prep-c6'
    $stagingReceipt = Join-Path $stagingTree 'PREPARED.json'
    if (Test-Path -LiteralPath $stagingReceipt -PathType Leaf) {
        Write-Host "  Reusing the staged prepared tree: $stagingTree"
    } else {
        if ((Test-Path -LiteralPath $stagingTree) -and @(Get-ChildItem -LiteralPath $stagingTree -Force).Count -gt 0) {
            Stop-Overnight "The staging folder is not empty and not prepared: $stagingTree. Delete it and run again."
        }
        & python (Join-Path $root 'scripts\prepare_android_poc.py') --target-dir 'build/sdk-prep-c6'
        if ($LASTEXITCODE -ne 0) { Stop-Overnight 'prepare_android_poc.py failed (see its output above).' }
    }
    Move-Item -LiteralPath $stagingTree -Destination $newTree
    if (-not (Test-Path -LiteralPath $newReceiptPath -PathType Leaf)) {
        Stop-Overnight "The move did not produce $newReceiptPath."
    }
    Write-Host "  Prepared and moved to: $newTree"
}

# ---- Step 2: copy the unchanged native libraries and assets from c5 --------------
Write-Host ''
Write-Host 'Step 2: copy jniLibs and assets from c5, and check that only GoBackend.java differs'

foreach ($name in 'jniLibs', 'assets') {
    $from = Join-Path $oldTree $name
    $to = Join-Path $newTree $name
    if (-not (Test-Path -LiteralPath $from -PathType Container)) {
        Stop-Overnight "The c5 tree has no $name folder: $from"
    }
    if (Test-Path -LiteralPath $to) {
        Write-Host "  $name already present in c6, kept as is."
        continue
    }
    Copy-Item -LiteralPath $from -Destination $to -Recurse
    Write-Host "  copied $name"
}

try {
    $oldReceipt = Get-Content -LiteralPath (Join-Path $oldTree 'PREPARED.json') -Raw | ConvertFrom-Json
    $newReceipt = Get-Content -LiteralPath $newReceiptPath -Raw | ConvertFrom-Json
    $oldSrc = @{}
    foreach ($p in $oldReceipt.source_sha256.PSObject.Properties) { $oldSrc[$p.Name] = $p.Value }
    $newSrc = @{}
    foreach ($p in $newReceipt.source_sha256.PSObject.Properties) { $newSrc[$p.Name] = $p.Value }
} catch {
    Stop-Overnight "Cannot read the source hashes from PREPARED.json: $($_.Exception.Message)"
}
$changed = @()
foreach ($key in (@($oldSrc.Keys) + @($newSrc.Keys) | Sort-Object -Unique)) {
    if ($oldSrc[$key] -ne $newSrc[$key]) { $changed += $key }
}
Write-Host "  source files that differ from c5: $(if ($changed.Count) { $changed -join ', ' } else { '(none)' })"
$unexpected = @($changed | Where-Object { $_ -notmatch 'GoBackend\.java$' })
if ($unexpected.Count -gt 0) {
    Stop-Overnight "Unexpected source differences from c5 (only GoBackend.java may differ): $($unexpected -join ', ')"
}

# ---- Step 3: verify the receipt and the native libraries -------------------------
Write-Host ''
Write-Host 'Step 3: verify PREPARED.json and the native libraries'
if ($newReceipt.patch_sha256 -ne $expectedPatchHash) {
    Stop-Overnight "PREPARED.json patch_sha256 is '$($newReceipt.patch_sha256)', expected '$expectedPatchHash'."
}
foreach ($abi in 'arm64-v8a', 'x86_64') {
    $so = Join-Path $newTree "jniLibs\$abi\libwg-go.so"
    if (-not (Test-Path -LiteralPath $so -PathType Leaf)) { Stop-Overnight "Missing native library: $so" }
}
Write-Host '  patch hash matches, both libwg-go.so files present.'

# ---- Step 4: ask for the API origin, the keystore path and the password -----------
Write-Host ''
Write-Host 'Step 4: inputs'
$apiOrigin = (Read-Host 'API origin, e.g. https://api.example.org (no path)').Trim()
if ($apiOrigin -notmatch '^https://[A-Za-z0-9.\-]+(:\d+)?$') {
    Stop-Overnight 'The API origin must look like https://host or https://host:port, with no path.'
}

$defaultKeystore = $env:DETOURVPN_BETA_KEYSTORE_PATH
$keystorePrompt = if ($defaultKeystore) { "Keystore path [Enter = $defaultKeystore]" } else { 'Keystore path (full path, e.g. C:\keys\beta.jks; outside this repository)' }
$keystorePath = $null
while ($null -eq $keystorePath) {
    $keystoreInput = (Read-Host $keystorePrompt).Trim().Trim('"')
    if (-not $keystoreInput -and $defaultKeystore) { $keystoreInput = $defaultKeystore }
    if (-not $keystoreInput) { Write-Host '  Enter a path.' -ForegroundColor Yellow; continue }
    # A relative path is taken from the folder where the script was started.
    $candidate = if ([IO.Path]::IsPathRooted($keystoreInput)) { $keystoreInput } else { Join-Path $startDir $keystoreInput }
    $full = [IO.Path]::GetFullPath($candidate)
    if ($full.StartsWith($root, [StringComparison]::OrdinalIgnoreCase)) {
        Write-Host '  The keystore must be outside this repository. Try again.' -ForegroundColor Yellow
        continue
    }
    if (-not (Test-Path -LiteralPath $full -PathType Leaf)) {
        Write-Host "  File not found: $full. Try again." -ForegroundColor Yellow
        continue
    }
    $keystorePath = $full
}
Write-Host "  Keystore: $keystorePath"

$plainPassword = Read-MaskedSecret 'Keystore password (typed as *):'
if ([string]::IsNullOrEmpty($plainPassword)) { Stop-Overnight 'The keystore password is empty.' }

# Default alias is the one scripts\build_beta_apk.ps1 creates the beta key with.
$keyAlias = (Read-Host 'Key alias [Enter = detourvpn-beta]').Trim()
if (-not $keyAlias) { $keyAlias = 'detourvpn-beta' }
$keyPassword = Read-MaskedSecret 'Key password (typed as *; press Enter alone if it is the same as the keystore password):'
if ([string]::IsNullOrEmpty($keyPassword)) { $keyPassword = $plainPassword }

# ---- Step 5: build ---------------------------------------------------------------
Write-Host ''
Write-Host 'Step 5: build (flutter test, debug build, Gradle unit tests and lint, then the release APK)'
$env:DETOURVPN_BETA_KEYSTORE_PATH = $keystorePath
$env:DETOURVPN_BETA_KEYSTORE_PASSWORD = $plainPassword
$env:DETOURVPN_BETA_KEY_ALIAS = $keyAlias
$env:DETOURVPN_BETA_KEY_PASSWORD = $keyPassword
$plainPassword = $null
$keyPassword = $null
$buildError = $null
try {
    # ADR-150: request 3600 s windows (the API and nodes accept them since the 3600 s deploy).
    & (Join-Path $root 'scripts\build_flutter_client.ps1') -ApiOrigin $apiOrigin -WindowTunnels -WindowSeconds 3600
} catch {
    $buildError = $_.Exception.Message
} finally {
    Remove-Item Env:DETOURVPN_BETA_KEYSTORE_PASSWORD -ErrorAction SilentlyContinue
    Remove-Item Env:DETOURVPN_BETA_KEY_PASSWORD -ErrorAction SilentlyContinue
}
if ($buildError) { Stop-Overnight "The build failed: $buildError" }

# ---- Step 6: report the APK ------------------------------------------------------
Write-Host ''
Write-Host 'Step 6: result'
$apk = Join-Path $root 'apps\client\build\app\outputs\flutter-apk\app-release.apk'
if (-not (Test-Path -LiteralPath $apk -PathType Leaf)) {
    $found = @(Get-ChildItem -LiteralPath (Join-Path $root 'apps\client\build\app\outputs') -Recurse -Filter '*.apk' -ErrorAction SilentlyContinue | ForEach-Object { $_.FullName })
    Stop-Overnight "The expected release APK was not found: $apk. APK files present: $(if ($found.Count) { $found -join '; ' } else { '(none)' })"
}
Write-Host 'Build finished. The release APK is:' -ForegroundColor Green
Write-Host $apk
Write-Host ''
Write-Host 'Install it on the phone with:'
Write-Host "adb install -r `"$apk`""
