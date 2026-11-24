[CmdletBinding()]
param(
    # Beta API address without https://, for example the one in scripts\enable_app_update.ps1.
    [string]$ApiHost = '',
    # Beta SSH key. Default is the key already used by the deploy scripts.
    [string]$Identity = (Join-Path $env:USERPROFILE '.ssh\detourvpn-beta-20261026')
)
# READ ONLY. Collects what the backend rollout needs and prints it in one block:
#   - the commit and the migration files in this repository,
#   - the revision of the managed database (asks for its URL in a hidden prompt),
#   - the migration files, service state and health on the beta host.
# It changes nothing: no migration, no file copy, no restart. Paste the whole output into the chat.
# The database URL is kept in memory for this run only and is masked in the output.

$ErrorActionPreference = 'Continue'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Set-Location $root

function Section([string]$Title) { Write-Host ''; Write-Host "===== $Title =====" -ForegroundColor Cyan }
function Mask([string]$Text) { return ($Text -replace '://[^/\s@]*@', '://<hidden>@') }

Section 'Repository'
$commit = (git rev-parse --short HEAD).Trim()
$full = (git rev-parse HEAD).Trim()
Write-Host "HEAD=$commit ($full)"
$dirty = @(git status --porcelain | Where-Object { $_ -notmatch '^\?\? (\.claude/|docs/evidence/|docs/research/)' })
if ($dirty.Count -eq 0) { Write-Host 'working tree: clean' } else { Write-Host "working tree: NOT clean ($($dirty.Count) changes). Commit before deploying." }
$localHead = (Get-ChildItem (Join-Path $root 'services\api\migrations\versions') -Filter '*.py' | Sort-Object Name | Select-Object -Last 1).BaseName
Write-Host "latest migration in code: $localHead"

Section 'Managed database (read only)'
$secure = Read-Host -AsSecureString 'Managed database URL (typed hidden, Enter to skip)'
$plain = [Runtime.InteropServices.Marshal]::PtrToStringAuto([Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure))
if ([string]::IsNullOrWhiteSpace($plain)) {
    Write-Host 'skipped: no database URL given.'
} else {
    $env:VPN_DATABASE_URL = $plain
    $plain = $null
    # Windows does not trust the server certificate by default (verify-full). Point libpq at the
    # CA bundle shipped with the project's Python packages (certifi, Mozilla roots incl. Amazon).
    $ca = (uv run --frozen python -c "import certifi; print(certifi.where())" 2>$null | Out-String).Trim()
    if (-not $ca -or -not (Test-Path -LiteralPath $ca)) { throw 'CA bundle (certifi) not found in the project venv.' }
    $env:PGSSLROOTCERT = $ca
    # Read-only SELECT of the migration version. The app's own engine is not used here: on Windows it
    # asks for a Linux CA file that does not exist, so the TLS check would fail before the query.
    $probe = Join-Path $env:TEMP 'beta_db_version.py'
    @'
import os
import psycopg
url = os.environ["VPN_DATABASE_URL"].replace("postgresql+psycopg://", "postgresql://", 1)
try:
    with psycopg.connect(url, sslrootcert=os.environ["PGSSLROOTCERT"], connect_timeout=10) as conn:
        row = conn.execute("select version_num from alembic_version").fetchall()
        print("database migration revision:", row)
except psycopg.errors.UndefinedTable:
    print("database migration revision: no alembic_version table (empty database)")
'@ | Set-Content -LiteralPath $probe -Encoding utf8
    $current = (uv run --frozen python $probe 2>&1 | Out-String)
    Remove-Item -LiteralPath $probe -ErrorAction SilentlyContinue
    Write-Host 'database revision (read only):'
    Write-Host (Mask $current.Trim())
    $heads = (uv run --frozen alembic heads 2>&1 | Out-String)
    Write-Host 'alembic heads (code):'
    Write-Host (Mask $heads.Trim())
    Remove-Item Env:\VPN_DATABASE_URL -ErrorAction SilentlyContinue
    Remove-Item Env:\PGSSLROOTCERT -ErrorAction SilentlyContinue
}

Section 'Beta API host (read only)'
if ([string]::IsNullOrWhiteSpace($ApiHost)) {
    $ApiHost = Read-Host 'Beta API address (IP or name, Enter to skip)'
}
if ([string]::IsNullOrWhiteSpace($ApiHost)) {
    Write-Host 'skipped: no API address given.'
} elseif (-not (Test-Path -LiteralPath $Identity)) {
    Write-Host "skipped: SSH key not found at $Identity"
} else {
    $remote = @'
cd /opt/detourvpn-beta 2>/dev/null || { echo "no /opt/detourvpn-beta"; exit 0; }
echo "migrations on host:"; ls services/api/migrations/versions 2>/dev/null | grep '\.py$' | sort | tail -5
echo "service: $(systemctl is-active detourvpn-beta-api 2>/dev/null)"
echo "health: $(curl -fsS --max-time 10 http://127.0.0.1:28000/api/v1/health 2>/dev/null || echo no-answer)"
echo "uptime: $(uptime -p 2>/dev/null)"
'@
    $out = & ssh -o BatchMode=yes -o ConnectTimeout=15 -i $Identity "root@$ApiHost" $remote 2>&1 | Out-String
    Write-Host (Mask $out.Trim())
}

Section 'Done'
Write-Host 'Copy everything from the first line of this output and paste it into the chat.'
