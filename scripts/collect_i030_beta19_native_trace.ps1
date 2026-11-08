[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$adb = Join-Path $env:LOCALAPPDATA 'Android\Sdk\platform-tools\adb.exe'
$applicationId = 'com.detourvpn.client'
$expectedVersionCode = '20'
$expectedVersionName = '0.1.0-beta.19'
$allowedStages = @(
    'libbox_setup', 'libbox_config', 'libbox_service_start',
    'android_interface_monitor', 'android_protect_socket', 'android_open_tun'
)
$allowedCategories = @(
    'go_nil_pointer', 'dial_context_deadline', 'dial_context_canceled',
    'udp_no_physical_interface', 'udp_socket_permission', 'udp_socket_in_use',
    'udp_family_unsupported', 'udp_bind_address_unavailable',
    'udp_socket_resources_exhausted', 'udp_socket_unsupported', 'udp_network_down',
    'udp_route_unavailable', 'udp_socket_closed', 'udp_socket_invalid_argument',
    'awg_bind_ipv4_open', 'awg_bind_ipv6_open', 'awg_bind_open', 'awg_uapi_config',
    'awg_tun_start', 'awg_device_up', 'awg_endpoint_start', 'tun_inbound_start',
    'socket_protection', 'vpn_permission', 'android_tun_establish',
    'android_tun_open', 'awg_tun_create', 'interface_monitor', 'tun_open'
)

function Write-SanitizedReceipt([string]$Status, [string]$FailureCode, $Events, [bool]$Truncated) {
    $receipt = [ordered]@{
        observed_at_utc = [DateTime]::UtcNow.ToString('yyyy-MM-ddTHH:mm:ssZ')
        issue = 'I-030'
        scope = 'read-only beta.19 native-start trace'
        status = $Status
        failure_code = $FailureCode
        application_id = $applicationId
        version_code = $expectedVersionCode
        version_name = $expectedVersionName
        source_uid_filter_applied = $true
        raw_log_lines_exported = $false
        raw_log_lines_persisted = $false
        event_count = @($Events).Count
        events_truncated = $Truncated
        events = @($Events)
    }
    Write-Output ($receipt | ConvertTo-Json -Depth 6 -Compress)
}

if (-not (Test-Path -LiteralPath $adb -PathType Leaf)) {
    Write-SanitizedReceipt 'blocked' 'adb_unavailable' @() $false
    return
}

try {
    $deviceRows = @(& $adb devices -l 2>$null |
        Select-Object -Skip 1 |
        Where-Object { -not [string]::IsNullOrWhiteSpace($_) })
    $deviceState = if ($deviceRows.Count -eq 1) { ($deviceRows[0].Trim() -split '\s+')[1] } else { '' }
    if ($deviceRows.Count -ne 1 -or $deviceState -ne 'device') {
        Write-SanitizedReceipt 'blocked' 'single_authorized_usb_device_required' @() $false
        return
    }

    $packageLines = @(& $adb -d shell dumpsys package $applicationId 2>$null)
    if ($LASTEXITCODE -ne 0) {
        Write-SanitizedReceipt 'blocked' 'package_status_unavailable' @() $false
        return
    }
    $versionCodeMatch = [regex]::Match(($packageLines -join "`n"), '\bversionCode=(\d+)')
    $versionNameMatch = [regex]::Match(($packageLines -join "`n"), '\bversionName=([^\s]+)')
    if (-not $versionCodeMatch.Success -or -not $versionNameMatch.Success -or
        $versionCodeMatch.Groups[1].Value -ne $expectedVersionCode -or
        $versionNameMatch.Groups[1].Value -ne $expectedVersionName) {
        Write-SanitizedReceipt 'blocked' 'expected_beta19_not_installed' @() $false
        return
    }

    $uidLines = @(& $adb -d shell cmd package list packages -U --user 0 $applicationId 2>$null)
    if ($LASTEXITCODE -ne 0) {
        Write-SanitizedReceipt 'blocked' 'package_uid_unavailable' @() $false
        return
    }
    $uidMatch = [regex]::Match(($uidLines -join ' '), 'package:com\.detourvpn\.client\s+uid:(\d+)')
    if (-not $uidMatch.Success) {
        Write-SanitizedReceipt 'blocked' 'package_uid_unavailable' @() $false
        return
    }

    $uid = $uidMatch.Groups[1].Value
    $rawLines = @(& $adb -d logcat -d -t 2000 -v epoch,uid "--uid=$uid" -s `
        'DetourVpnTrace:I' 'DetourVpnNativeCall:E' 2>$null)
    if ($LASTEXITCODE -ne 0) {
        Write-SanitizedReceipt 'blocked' 'filtered_logcat_unavailable' @() $false
        return
    }

    $parsedEvents = New-Object 'System.Collections.Generic.List[object]'
    $unparsedTaggedLineCount = 0
    $linePattern = '^(?<epoch>\d{10}(?:\.\d+)?)\s+[A-Za-z0-9_=.-]+\s+\d+\s+\d+\s+[VDIWEF]\s+(?<tag>DetourVpnTrace|DetourVpnNativeCall):\s*(?<message>.*)$'
    $tracePattern = '^event=(?<event>libbox_new_service_(?:begin|returned)|libbox_service_start_(?:begin|returned)|network_callback_(?:available|capabilities_changed|link_properties_changed)_begin_\d{1,10}|network_callback_(?:snapshot_unavailable|interface_unavailable|update_begin|update_returned|update_failed)_\d{1,10})$'
    $stagePattern = '^start_stage=(?<stage>[a-z_]+)\s+failure_types=[A-Za-z0-9_]{1,48}(?:,[A-Za-z0-9_]{1,48}){0,3}(?:\s+failure_category=(?<category>[a-z][a-z0-9_]{0,63}))?$'
    $categoryAlternation = ($allowedCategories | ForEach-Object { [regex]::Escape($_) }) -join '|'
    foreach ($line in $rawLines) {
        $lineMatch = [regex]::Match([string]$line, $linePattern)
        if (-not $lineMatch.Success) { continue }
        $message = $lineMatch.Groups['message'].Value
        $tag = $lineMatch.Groups['tag'].Value
        $timestamp = $null
        try {
            $epochMilliseconds = [long]([double]::Parse(
                $lineMatch.Groups['epoch'].Value,
                [Globalization.CultureInfo]::InvariantCulture) * 1000)
            $timestamp = [DateTimeOffset]::FromUnixTimeMilliseconds($epochMilliseconds).UtcDateTime.ToString('yyyy-MM-ddTHH:mm:ss.fffZ')
        } catch { continue }

        if ($tag -eq 'DetourVpnTrace') {
            $eventMatch = [regex]::Match($message, $tracePattern)
            if ($eventMatch.Success) {
                $parsedEvents.Add([ordered]@{
                    timestamp_utc = $timestamp
                    kind = 'trace'
                    event = $eventMatch.Groups['event'].Value
                })
            } else { $unparsedTaggedLineCount++ }
            continue
        }

        $platformMatch = [regex]::Match($message, '^platform_stage=(?<stage>android_open_tun_enter|android_open_tun_success)$')
        if ($platformMatch.Success) {
            $parsedEvents.Add([ordered]@{
                timestamp_utc = $timestamp
                kind = 'platform_stage'
                stage = $platformMatch.Groups['stage'].Value
            })
            continue
        }

        $categoryMatch = [regex]::Match($message, ('^engine_failure_category=(?<category>' + $categoryAlternation + ')$'))
        if ($categoryMatch.Success) {
            $parsedEvents.Add([ordered]@{
                timestamp_utc = $timestamp
                kind = 'engine_failure_category'
                category = $categoryMatch.Groups['category'].Value
            })
            continue
        }

        $stageMatch = [regex]::Match($message, $stagePattern)
        if ($stageMatch.Success -and $allowedStages -contains $stageMatch.Groups['stage'].Value) {
            $event = [ordered]@{
                timestamp_utc = $timestamp
                kind = 'start_failure'
                stage = $stageMatch.Groups['stage'].Value
            }
            $category = $stageMatch.Groups['category'].Value
            if ($category -and $allowedCategories -contains $category) { $event['category'] = $category }
            $parsedEvents.Add($event)
        } else { $unparsedTaggedLineCount++ }
    }

    # Beta.18 did not emit DetourVpnTrace. Only include native-call entries from
    # the beta.19 run onward, anchored to its first fixed trace marker.
    $traceEvents = @($parsedEvents | Where-Object { $_.kind -eq 'trace' })
    if ($traceEvents.Count -eq 0) {
        Write-SanitizedReceipt 'no_beta19_trace_markers' '' @() $false
        return
    }
    $firstTraceTime = [DateTime]::Parse(
        $traceEvents[0].timestamp_utc,
        [Globalization.CultureInfo]::InvariantCulture,
        [Globalization.DateTimeStyles]::AssumeUniversal)
    $allCurrentRunEvents = @($parsedEvents | Where-Object {
        $eventTime = [DateTime]::Parse(
            $_.timestamp_utc,
            [Globalization.CultureInfo]::InvariantCulture,
            [Globalization.DateTimeStyles]::AssumeUniversal)
        $_.kind -eq 'trace' -or $eventTime -ge $firstTraceTime
    })
    $truncated = $allCurrentRunEvents.Count -gt 100
    $currentRunEvents = @($allCurrentRunEvents | Select-Object -Last 100)
    Write-SanitizedReceipt 'collected' '' $currentRunEvents $truncated
} catch {
    Write-SanitizedReceipt 'blocked' 'sanitized_collection_failed' @() $false
    return
}
