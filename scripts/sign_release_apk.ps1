# Signs an unsigned release APK with a local keystore.
# Passwords are typed into this console (hidden); they are never printed or written to disk.
#
# Example (defaults sign app-release-unsigned.apk with the beta keystore):
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\sign_release_apk.ps1
[CmdletBinding()]
param(
    [string]$ApkPath = '',
    [string]$KeystorePath = (Join-Path $env:LOCALAPPDATA 'ProjectVpn\beta-signing\detourvpn-beta.jks'),
    [string]$KeyAlias = 'detourvpn-beta',
    [string]$AndroidSdk = (Join-Path $env:LOCALAPPDATA 'Android\Sdk'),
    [string]$BuildToolsVersion = '36.0.0'
)

$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot

if ([string]::IsNullOrWhiteSpace($ApkPath)) {
    $ApkPath = Join-Path $repo 'apps\client\build\app\outputs\apk\release\app-release-unsigned.apk'
}
if (-not (Test-Path -LiteralPath $ApkPath -PathType Leaf)) { throw "APK not found: $ApkPath" }
$ApkPath = (Resolve-Path -LiteralPath $ApkPath).Path

$buildTools = Join-Path $AndroidSdk "build-tools\$BuildToolsVersion"
$apksigner = Join-Path $buildTools 'apksigner.bat'
$zipalign = Join-Path $buildTools 'zipalign.exe'
foreach ($tool in @($apksigner, $zipalign)) {
    if (-not (Test-Path -LiteralPath $tool -PathType Leaf)) { throw "Build tool missing: $tool" }
}
if (-not (Test-Path -LiteralPath $KeystorePath -PathType Leaf)) { throw "Keystore not found: $KeystorePath" }

$outputPath = Join-Path (Split-Path -Parent $ApkPath) 'app-release-signed.apk'
# apksigner runs on the JVM, which cannot write to the non-ASCII repo path (OneDrive "Документы").
# Sign inside an ASCII temp folder, then copy the result with PowerShell.
$workDir = Join-Path $env:TEMP ('apk-sign-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $workDir | Out-Null
$alignedPath = Join-Path $workDir 'aligned.apk'
$signedTempPath = Join-Path $workDir 'signed.apk'

function ConvertTo-PlainText([System.Security.SecureString]$Secure) {
    $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($Secure)
    try { return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr) }
    finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr) }
}

Write-Host "APK:      $ApkPath"
Write-Host "Keystore: $KeystorePath (alias $KeyAlias)"
$secureStore = Read-Host 'Keystore password (hidden)' -AsSecureString
$secureKey = Read-Host 'Key password (hidden; Enter = same as keystore)' -AsSecureString

$storePassword = ConvertTo-PlainText $secureStore
$keyPassword = ConvertTo-PlainText $secureKey
if ([string]::IsNullOrEmpty($storePassword)) { throw 'Keystore password is empty.' }
if ([string]::IsNullOrEmpty($keyPassword)) { $keyPassword = $storePassword }

$env:DV_KEYSTORE_PASS = $storePassword
$env:DV_KEY_PASS = $keyPassword
try {
    # Page-align, then sign with APK Signature Scheme v2 and v4 (apksigner default for this SDK).
    & $zipalign -f -p 4 $ApkPath $alignedPath
    if ($LASTEXITCODE -ne 0) { throw 'zipalign failed.' }

    & $apksigner sign --ks $KeystorePath --ks-key-alias $KeyAlias `
        --ks-pass env:DV_KEYSTORE_PASS --key-pass env:DV_KEY_PASS `
        --v4-signing-enabled true --out $signedTempPath $alignedPath
    if ($LASTEXITCODE -ne 0) { throw 'apksigner sign failed (check the passwords and alias).' }

    & $apksigner verify --print-certs $signedTempPath
    if ($LASTEXITCODE -ne 0) { throw 'Signature verification failed.' }

    Copy-Item -LiteralPath $signedTempPath -Destination $outputPath -Force
}
finally {
    Remove-Item -LiteralPath $workDir -Recurse -Force -ErrorAction SilentlyContinue
    Remove-Item -Path Env:\DV_KEYSTORE_PASS -ErrorAction SilentlyContinue
    Remove-Item -Path Env:\DV_KEY_PASS -ErrorAction SilentlyContinue
    $storePassword = $null
    $keyPassword = $null
}

Write-Host ''
Write-Host "Signed APK: $outputPath"
Get-FileHash -LiteralPath $outputPath -Algorithm SHA256 | Format-List Algorithm, Hash
