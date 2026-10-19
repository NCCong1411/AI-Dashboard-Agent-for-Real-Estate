[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$BackupPath,
    [switch]$ConfirmSeparateTestDatabase
)

$ErrorActionPreference = 'Stop'
if (-not $ConfirmSeparateTestDatabase) {
    throw 'ConfirmSeparateTestDatabase is required. Use an empty database in a separate test cluster.'
}
$archive = (Resolve-Path -LiteralPath $BackupPath -ErrorAction Stop).Path
if (-not (Test-Path -LiteralPath $archive -PathType Leaf)) { throw 'Backup must be a file.' }
$manifest = "$archive.sha256"
if (-not (Test-Path -LiteralPath $manifest -PathType Leaf)) { throw 'Backup SHA-256 manifest is required.' }
$expected = ((Get-Content -LiteralPath $manifest -Raw).Trim() -split '\s+')[0]
if ($expected -notmatch '^[a-fA-F0-9]{64}$' -or
    (Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash -ne $expected) {
    throw 'Backup SHA-256 mismatch.'
}
$url = $env:RESTORE_TEST_DATABASE_URL
if ([string]::IsNullOrWhiteSpace($url)) { throw 'RESTORE_TEST_DATABASE_URL is required; its value is never logged.' }
if ($url.StartsWith('postgresql+psycopg://', [StringComparison]::OrdinalIgnoreCase)) {
    $url = 'postgresql://' + $url.Substring('postgresql+psycopg://'.Length)
}
try { $target = [Uri]$url } catch { throw 'Invalid restore target URL; its value is never logged.' }
if ($target.Scheme -notin @('postgres', 'postgresql') -or
    [string]::IsNullOrWhiteSpace($target.Host) -or [string]::IsNullOrWhiteSpace($target.UserInfo) -or
    $target.Query -or $target.Fragment) { throw 'Restore needs an explicit PostgreSQL target without URL options.' }
$database = [Uri]::UnescapeDataString($target.AbsolutePath.TrimStart('/'))
if ($database -notmatch '(?i)test' -or $database.Contains('/')) {
    throw 'Refusing restore: target database name must contain test.'
}
# A different database on the source cluster is insufficient: role definitions
# are cluster-wide. Compare the endpoint when the source URL is available.
if ($env:MIGRATION_DATABASE_URL) {
    try { $source = [Uri]($env:MIGRATION_DATABASE_URL -replace '^postgresql\+psycopg:', 'postgresql:') }
    catch { throw 'Cannot validate separation from migration source.' }
    if ($source.Query -or $source.Fragment) {
        throw 'Migration source URL options are unsupported; cannot validate cluster separation.'
    }
    $sourcePort = if ($source.Port -lt 1) { 5432 } else { $source.Port }
    $targetPort = if ($target.Port -lt 1) { 5432 } else { $target.Port }
    $sourceHost = $source.Host.ToLowerInvariant()
    $targetHost = $target.Host.ToLowerInvariant()
    if ($sourceHost -in @('localhost', '127.0.0.1', '[::1]')) { $sourceHost = 'loopback' }
    if ($targetHost -in @('localhost', '127.0.0.1', '[::1]')) { $targetHost = 'loopback' }
    if ($sourceHost -eq $targetHost -and $sourcePort -eq $targetPort) {
        throw 'Restore target must use a separate PostgreSQL cluster endpoint.'
    }
}
function Resolve-PgTool([string]$Name) {
    $found = Get-Command $Name -ErrorAction SilentlyContinue
    if ($found) { return $found.Source }
    $root = 'C:\Program Files\PostgreSQL'
    if (Test-Path -LiteralPath $root) {
        $found = Get-ChildItem -LiteralPath $root -Directory | Sort-Object Name -Descending |
            ForEach-Object { Join-Path $_.FullName "bin\$Name.exe" } |
            Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } | Select-Object -First 1
        if ($found) { return $found }
    }
    throw 'psql and pg_restore must be installed.'
}
$psql = Resolve-PgTool 'psql'
$restore = Resolve-PgTool 'pg_restore'
$parts = $target.UserInfo -split ':', 2
$temporary = @{
    PGHOSTADDR = $null
    PGSERVICE = $null
    PGSERVICEFILE = $null
    PGHOST = $target.Host
    PGPORT = if ($target.Port -lt 1) { '5432' } else { [string]$target.Port }
    PGDATABASE = $database
    PGUSER = [Uri]::UnescapeDataString($parts[0])
    PGPASSWORD = if ($parts.Count -eq 2) { [Uri]::UnescapeDataString($parts[1]) } else { '' }
    PGCONNECT_TIMEOUT = '5'
    PGOPTIONS = '-c statement_timeout=60000 -c lock_timeout=5000'
}
$previous = @{}
try {
    foreach ($key in $temporary.Keys) {
        $previous[$key] = [Environment]::GetEnvironmentVariable($key, 'Process')
        [Environment]::SetEnvironmentVariable($key, $temporary[$key], 'Process')
    }
    # Only an empty target is accepted. Never use --clean or overwrite objects.
    $check = "SELECT (SELECT count(*) FROM pg_namespace WHERE nspname !~ '^pg_' AND nspname NOT IN ('public','information_schema')) + (SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public') + (SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='public') + (SELECT count(*) FROM pg_type t JOIN pg_namespace n ON n.oid=t.typnamespace WHERE n.nspname='public')"
    $objects = & $psql -X -w -A -t -v ON_ERROR_STOP=1 -c $check 2>$null
    if ($LASTEXITCODE -ne 0 -or [string]($objects -join '').Trim() -ne '0') {
        throw 'Restore target must be reachable and empty.'
    }
    & $restore --list $archive 1>$null 2>$null
    if ($LASTEXITCODE -ne 0) { throw 'Backup is not a readable PostgreSQL archive.' }
    & $restore --dbname=$database --no-password --no-owner --no-privileges --exit-on-error --single-transaction $archive 1>$null 2>$null
    if ($LASTEXITCODE -ne 0) { throw 'Restore failed; transaction was rolled back. Inspect target configuration.' }
    Write-Host 'Restore completed in the empty test database. Run migration and data verification next.'
} finally {
    foreach ($key in $previous.Keys) { [Environment]::SetEnvironmentVariable($key, $previous[$key], 'Process') }
}
