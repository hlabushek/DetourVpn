[CmdletBinding()]
param([string]$DriveLetter = 'E:')

$ErrorActionPreference = 'Stop'
$drive = $DriveLetter.TrimEnd('\').TrimEnd(':').ToUpperInvariant() + ':'
$source = Join-Path $env:LOCALAPPDATA 'ProjectVpn\beta-signing\detourvpn-beta.jks'
$volumeInfo = Get-CimInstance Win32_LogicalDisk -Filter "DeviceID='$drive'"
if (-not $volumeInfo -or $volumeInfo.DriveType -ne 2 -or $volumeInfo.VolumeName -ne 'GERFFINSUSB') {
    throw 'The selected removable drive is not the expected GERFFINSUSB volume.'
}
if (-not (Test-Path -LiteralPath $source -PathType Leaf)) {
    throw 'The beta signing keystore is not visible in this Windows user profile.'
}
if ((Get-Item -LiteralPath $source -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) {
    throw 'The beta signing keystore path must not be a reparse point.'
}

try {
    $bitLocker = Get-BitLockerVolume -MountPoint $drive -ErrorAction Stop
} catch {
    throw 'Could not verify BitLocker status. Run this script in an elevated PowerShell after encryption completes.'
}
if ([string]$bitLocker.VolumeStatus -ne 'FullyEncrypted' -or
    [string]$bitLocker.ProtectionStatus -ne 'On' -or
    [int]$bitLocker.EncryptionPercentage -ne 100) {
    throw 'GERFFINSUSB is not fully encrypted with BitLocker protection on.'
}

$destinationDirectory = Join-Path ($drive + '\') 'DetourVpn-Beta-Backup'
if (-not (Test-Path -LiteralPath $destinationDirectory)) {
    New-Item -ItemType Directory -Path $destinationDirectory | Out-Null
}
if ((Get-Item -LiteralPath $destinationDirectory -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) {
    throw 'The backup destination directory must not be a reparse point.'
}
$destination = Join-Path $destinationDirectory 'detourvpn-beta-signing.jks'
if (Test-Path -LiteralPath $destination) { throw 'A signing-key backup already exists; it will not be overwritten.' }

$partial = Join-Path $destinationDirectory '.detourvpn-beta-signing.jks.partial'
if (Test-Path -LiteralPath $partial) { throw 'A previous partial backup exists; inspect it before retrying.' }
$partialCreated = $false
try {
    [IO.File]::Copy($source, $partial, $false)
    $partialCreated = $true
    $sourceHash = (Get-FileHash -LiteralPath $source -Algorithm SHA256).Hash
    $copyHash = (Get-FileHash -LiteralPath $partial -Algorithm SHA256).Hash
    if ($sourceHash -ne $copyHash) { throw 'The copied signing-key checksum does not match.' }
    [IO.File]::Move($partial, $destination)
    $partialCreated = $false
    [pscustomobject]@{
        code = 'beta_signing_keystore_backup_verified'
        drive = $drive
        bitlocker_fully_encrypted = $true
        checksum_matches = $true
        password_values_logged = $false
    } | ConvertTo-Json -Compress
} finally {
    if ($partialCreated -and (Test-Path -LiteralPath $partial -PathType Leaf)) {
        Remove-Item -LiteralPath $partial -Force -ErrorAction SilentlyContinue
    }
}
