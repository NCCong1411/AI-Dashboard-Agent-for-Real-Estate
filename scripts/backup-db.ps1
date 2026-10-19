[CmdletBinding()]
param(
    [string]$OutputDirectory
)

$ErrorActionPreference = "Stop"
if ([string]::IsNullOrWhiteSpace($OutputDirectory)) {
    $OutputDirectory = Join-Path $PSScriptRoot "..\backups"
}
$databaseUrl = $env:MIGRATION_DATABASE_URL
if ([string]::IsNullOrWhiteSpace($databaseUrl)) {
    throw "MIGRATION_DATABASE_URL is required. Use the migration account; the value is never logged."
}

# System.Uri does not recognize SQLAlchemy's driver-qualified scheme. Normalize only
# that spelling; the resulting connection is still passed to libpq as separate fields.
$uriInput = $databaseUrl
if ($uriInput.StartsWith("postgresql+psycopg://", [System.StringComparison]::OrdinalIgnoreCase)) {
    $uriInput = "postgresql://" + $uriInput.Substring("postgresql+psycopg://".Length)
}
try {
    $uri = [System.Uri]$uriInput
} catch {
    throw "MIGRATION_DATABASE_URL must be a valid PostgreSQL URL; the value is never logged."
}
if ($uri.Scheme -notin @("postgres", "postgresql") -or [string]::IsNullOrWhiteSpace($uri.UserInfo)) {
    throw "MIGRATION_DATABASE_URL must be a PostgreSQL URL with an explicit migration user."
}
if (-not [string]::IsNullOrEmpty($uri.Query) -or -not [string]::IsNullOrEmpty($uri.Fragment)) {
    throw "MIGRATION_DATABASE_URL query parameters and fragments are not supported by backup-db.ps1. Set required libpq options (for example PGSSLMODE) as process environment variables."
}

function Resolve-PostgresTool([string]$Name) {
    $command = Get-Command $Name -ErrorAction SilentlyContinue
    if ($command) {
        return $command.Source
    }
    $installRoot = "C:\Program Files\PostgreSQL"
    if (Test-Path -LiteralPath $installRoot -PathType Container) {
        $candidate = Get-ChildItem -LiteralPath $installRoot -Directory |
            Sort-Object Name -Descending |
            ForEach-Object { Join-Path $_.FullName "bin\$Name.exe" } |
            Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } |
            Select-Object -First 1
        if ($candidate) {
            return $candidate
        }
    }
    return $null
}

$pgDump = Resolve-PostgresTool "pg_dump"
$pgRestore = Resolve-PostgresTool "pg_restore"
if (-not $pgDump -or -not $pgRestore) {
    throw "pg_dump and pg_restore must be available on PATH."
}

$userParts = $uri.UserInfo -split ':', 2
$temporaryPgEnvironment = @{
    PGHOSTADDR = $null
    PGSERVICE = $null
    PGSERVICEFILE = $null
    PGHOST = $uri.Host
    PGPORT = if ($uri.IsDefaultPort) { "5432" } else { [string]$uri.Port }
    PGDATABASE = [System.Uri]::UnescapeDataString($uri.AbsolutePath.TrimStart('/'))
    PGUSER = [System.Uri]::UnescapeDataString($userParts[0])
    PGPASSWORD = if ($userParts.Count -eq 2) { [System.Uri]::UnescapeDataString($userParts[1]) } else { "" }
}
$previousPgEnvironment = @{}
try {
    foreach ($name in $temporaryPgEnvironment.Keys) {
        $previousPgEnvironment[$name] = [System.Environment]::GetEnvironmentVariable($name, "Process")
        [System.Environment]::SetEnvironmentVariable($name, $temporaryPgEnvironment[$name], "Process")
    }
    $resolvedOutput = [System.IO.Path]::GetFullPath($OutputDirectory)
    New-Item -ItemType Directory -Path $resolvedOutput -Force | Out-Null
    $stamp = Get-Date -Format "yyyyMMdd-HHmmss"
    $backupPath = Join-Path $resolvedOutput "pre-migration-$stamp.dump"
    $hashPath = "$backupPath.sha256"
    & $pgDump --format=custom --file=$backupPath --no-owner --no-privileges
    if ($LASTEXITCODE -ne 0) {
        throw "pg_dump failed with exit code $LASTEXITCODE."
    }
    & $pgRestore --list $backupPath | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "pg_restore could not read the backup archive."
    }
} finally {
    foreach ($name in $previousPgEnvironment.Keys) {
        [System.Environment]::SetEnvironmentVariable($name, $previousPgEnvironment[$name], "Process")
    }
}

$hash = (Get-FileHash -LiteralPath $backupPath -Algorithm SHA256).Hash.ToLowerInvariant()
Set-Content -LiteralPath $hashPath -Encoding ascii -Value "$hash  $([System.IO.Path]::GetFileName($backupPath))"
Write-Host "Validated custom-format backup: $backupPath"
Write-Host "SHA-256 manifest: $hashPath"
Write-Host "Restore into a separate test database before setting MIGRATION_BACKUP_VERIFIED=true."
