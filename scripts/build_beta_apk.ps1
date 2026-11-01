[CmdletBinding()]
param(
    [string]$KeystorePath = (Join-Path $env:LOCALAPPDATA 'ProjectVpn\beta-signing\detourvpn-beta.jks'),
    [string]$KeyAlias = 'detourvpn-beta',
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^https://[^\s/?#]+/?$')]
    [string]$ApiOrigin,
    [switch]$WindowTunnels,
    [string]$FlutterSdk = (Join-Path $env:LOCALAPPDATA 'ProjectVpn\toolchains\flutter'),
    [string]$JavaHome = 'C:\Program Files\Android\Android Studio\jbr'
)

$ErrorActionPreference = 'Stop'
$parsedApiOrigin = $null
if (![uri]::TryCreate($ApiOrigin, [UriKind]::Absolute, [ref]$parsedApiOrigin) -or
    $parsedApiOrigin.Scheme -ne 'https' -or
    [string]::IsNullOrEmpty($parsedApiOrigin.Host) -or
    ![string]::IsNullOrEmpty($parsedApiOrigin.UserInfo) -or
    $parsedApiOrigin.Query -or $parsedApiOrigin.Fragment -or
    ($parsedApiOrigin.AbsolutePath -ne '/' -and $parsedApiOrigin.AbsolutePath -ne '')) {
    throw 'ApiOrigin must be a credential-free HTTPS origin without a path, query, or fragment.'
}
$flutterExecutable = Join-Path $FlutterSdk 'bin\flutter.bat'
if (!(Test-Path -LiteralPath $flutterExecutable -PathType Leaf)) {
    throw 'Configured Flutter SDK is unavailable; signing credentials were not requested.'
}
$root = Split-Path -Parent $PSScriptRoot
$sourceRoot = [IO.Path]::GetFullPath($root).TrimEnd([IO.Path]::DirectorySeparatorChar, [IO.Path]::AltDirectorySeparatorChar)
if (![IO.Path]::IsPathRooted($KeystorePath)) { throw 'KeystorePath must be absolute and outside the source checkout.' }
$KeystorePath = [IO.Path]::GetFullPath($KeystorePath)
$sourcePrefix = $sourceRoot + [IO.Path]::DirectorySeparatorChar
if ($KeystorePath.StartsWith($sourcePrefix, [StringComparison]::OrdinalIgnoreCase)) {
    throw 'KeystorePath must be outside the source checkout.'
}
if ([string]::IsNullOrWhiteSpace($KeyAlias) -or $KeyAlias -match '[\r\n]') { throw 'KeyAlias is invalid.' }

$keytool = Join-Path $JavaHome 'bin\keytool.exe'
if (!(Test-Path -LiteralPath $keytool -PathType Leaf)) { throw 'Android Studio JDK keytool.exe is unavailable.' }
$keystoreDirectory = Split-Path -Parent $KeystorePath
$currentUser = [Security.Principal.WindowsIdentity]::GetCurrent().User

function Test-OwnerOnlyAcl($Acl, [Security.Principal.SecurityIdentifier]$Owner, [bool]$DirectoryAcl) {
    if ($Acl.GetOwner([Security.Principal.SecurityIdentifier]) -ne $Owner -or !$Acl.AreAccessRulesProtected) {
        return $false
    }
    $rules = @($Acl.Access)
    if ($rules.Count -ne 1) { return $false }
    $rule = $rules[0]
    $ruleUser = $rule.IdentityReference
    if ($ruleUser -isnot [Security.Principal.SecurityIdentifier]) {
        $ruleUser = $ruleUser.Translate([Security.Principal.SecurityIdentifier])
    }
    $expectedInheritance = if ($DirectoryAcl) {
        [Security.AccessControl.InheritanceFlags]::ContainerInherit -bor [Security.AccessControl.InheritanceFlags]::ObjectInherit
    } else {
        [Security.AccessControl.InheritanceFlags]::None
    }
    return ($ruleUser -eq $Owner -and
        !$rule.IsInherited -and
        $rule.AccessControlType -eq [Security.AccessControl.AccessControlType]::Allow -and
        $rule.FileSystemRights -eq [Security.AccessControl.FileSystemRights]::FullControl -and
        $rule.InheritanceFlags -eq $expectedInheritance -and
        $rule.PropagationFlags -eq [Security.AccessControl.PropagationFlags]::None)
}

$directory = New-Item -ItemType Directory -Force -Path $keystoreDirectory
if ($directory.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Signing directory must not be a link.' }
$directoryAcl = Get-Acl -LiteralPath $keystoreDirectory
if ($directoryAcl.GetOwner([Security.Principal.SecurityIdentifier]) -ne $currentUser) {
    throw 'Signing directory must belong to the current Windows user.'
}
if (!(Test-OwnerOnlyAcl $directoryAcl $currentUser $true)) {
    $directoryAcl = [Security.AccessControl.DirectorySecurity]::new()
    $directoryAcl.SetAccessRuleProtection($true, $false)
    $directoryAcl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new(
        $currentUser, 'FullControl', 'ContainerInherit,ObjectInherit', 'None', 'Allow'))
    Set-Acl -LiteralPath $keystoreDirectory -AclObject $directoryAcl
}
if (!(Test-OwnerOnlyAcl (Get-Acl -LiteralPath $keystoreDirectory) $currentUser $true)) {
    throw 'Signing directory ACL must grant only its owner full control.'
}

if (!(Test-Path -LiteralPath $KeystorePath -PathType Leaf)) {
    Write-Host "A permanent beta signing key will be created at: $KeystorePath"
    Write-Host 'Back up the keystore and its passwords under owner control; losing them prevents future APK updates.'
    if ((Read-Host 'Type CREATE to create this key') -cne 'CREATE') { throw 'Signing key creation was not confirmed.' }
    Write-Host 'No path is needed. keytool will prompt for the keystore password, then for the key password.'
    Write-Host 'Use passwords of at least 16 characters; press Enter at the key password prompt to reuse the keystore password.'
    & $keytool -genkeypair -v -keystore $KeystorePath -storetype JKS -alias $KeyAlias `
        -keyalg RSA -keysize 3072 -validity 10000 -dname 'CN=DetourVPN beta signing key'
    if ($LASTEXITCODE -ne 0 -or !(Test-Path -LiteralPath $KeystorePath -PathType Leaf)) {
        throw 'keytool did not create the beta signing keystore.'
    }
}

$fileAcl = Get-Acl -LiteralPath $KeystorePath
if ($fileAcl.GetOwner([Security.Principal.SecurityIdentifier]) -ne $currentUser) {
    throw 'Keystore file must belong to the current Windows user.'
}
if (!(Test-OwnerOnlyAcl $fileAcl $currentUser $false)) {
    $fileAcl.SetAccessRuleProtection($true, $false)
    $fileAcl.ResetAccessRule([Security.AccessControl.FileSystemAccessRule]::new($currentUser, 'FullControl', 'Allow'))
    Set-Acl -LiteralPath $KeystorePath -AclObject $fileAcl
}
$verifiedFileAcl = Get-Acl -LiteralPath $KeystorePath
if (!(Test-OwnerOnlyAcl $verifiedFileAcl $currentUser $false)) {
    throw 'Keystore file owner/ACL protection could not be confirmed.'
}
foreach ($rule in $verifiedFileAcl.Access) {
    $ruleUser = $rule.IdentityReference
    if ($ruleUser -isnot [Security.Principal.SecurityIdentifier]) {
        $ruleUser = $ruleUser.Translate([Security.Principal.SecurityIdentifier])
    }
    if ($ruleUser -ne $currentUser -or $rule.IsInherited -or
        $rule.AccessControlType -ne [Security.AccessControl.AccessControlType]::Allow -or
        $rule.FileSystemRights -ne [Security.AccessControl.FileSystemRights]::FullControl) {
        throw 'Keystore file ACL must grant only its owner full control.'
    }
}

function ConvertFrom-SecureInput([Security.SecureString]$Value) {
    $pointer = [IntPtr]::Zero
    try {
        $pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($Value)
        [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer)
    } finally {
        if ($pointer -ne [IntPtr]::Zero) {
            [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer)
        }
    }
}

$secureStorePassword = $null
$secureKeyPassword = $null
$storePassword = $null
$keyPassword = $null
$environmentNames = @(
    'DETOURVPN_BETA_KEYSTORE_PATH',
    'DETOURVPN_BETA_KEYSTORE_PASSWORD',
    'DETOURVPN_BETA_KEY_ALIAS',
    'DETOURVPN_BETA_KEY_PASSWORD'
)
$previousEnvironment = @{}
foreach ($name in $environmentNames) { $previousEnvironment[$name] = [Environment]::GetEnvironmentVariable($name, 'Process') }
Push-Location -LiteralPath $root

try {
    $secureStorePassword = Read-Host 'Beta keystore password (hidden)' -AsSecureString
    $secureKeyPassword = Read-Host 'Beta key password (hidden)' -AsSecureString
    if ($secureStorePassword.Length -lt 16 -or $secureKeyPassword.Length -lt 16) {
        throw 'Use beta signing passwords of at least 16 characters.'
    }
    $storePassword = ConvertFrom-SecureInput $secureStorePassword
    $keyPassword = ConvertFrom-SecureInput $secureKeyPassword

    $env:DETOURVPN_BETA_KEYSTORE_PATH = $KeystorePath
    $env:DETOURVPN_BETA_KEYSTORE_PASSWORD = $storePassword
    $env:DETOURVPN_BETA_KEY_ALIAS = $KeyAlias
    $env:DETOURVPN_BETA_KEY_PASSWORD = $keyPassword

    $buildParameters = @{
        JavaHome = $JavaHome
        ApiOrigin = $ApiOrigin
        FlutterSdk = $FlutterSdk
    }
    if ($WindowTunnels) { $buildParameters.WindowTunnels = $true }
    & (Join-Path $PSScriptRoot 'build_flutter_client.ps1') @buildParameters
    if ($LASTEXITCODE -ne 0) { throw 'Signed Android build did not complete successfully.' }

    $apk = Join-Path $root 'apps\client\build\app\outputs\flutter-apk\app-release.apk'
    if (!(Test-Path -LiteralPath $apk -PathType Leaf)) { throw 'Signed release APK was not produced.' }
    $receiptName = 'i-030-beta-apk-' + [DateTime]::UtcNow.ToString('yyyyMMddTHHmmssfffZ') + '-' +
        [guid]::NewGuid().ToString('N').Substring(0, 8) + '.json'
    $receipt = Join-Path $root ('docs\evidence\' + $receiptName)
    & uv run --frozen python (Join-Path $root 'scripts\verify_beta_apk.py') `
        --apk $apk --expected-application-id com.detourvpn.client --output $receipt `
        --sdk-root (Join-Path $env:LOCALAPPDATA 'Android\Sdk') --java-home $JavaHome
    if ($LASTEXITCODE -ne 0) { throw 'APK signing verification failed; do not distribute this artifact.' }
    Write-Host 'beta_apk_application_id=com.detourvpn.client'
    Write-Host "beta_apk_receipt=$receipt"
    Write-Host 'update_install=not_verified; distribution=not_accepted'
} finally {
    Pop-Location
    $storePassword = $null
    $keyPassword = $null
    if ($secureStorePassword) { $secureStorePassword.Dispose() }
    if ($secureKeyPassword) { $secureKeyPassword.Dispose() }
    foreach ($name in $environmentNames) {
        $prior = $previousEnvironment[$name]
        if ($null -eq $prior) {
            Remove-Item -LiteralPath "Env:$name" -ErrorAction SilentlyContinue
        } else {
            [Environment]::SetEnvironmentVariable($name, $prior, 'Process')
        }
    }
}
