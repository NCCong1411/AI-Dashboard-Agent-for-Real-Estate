"""Restore safety guards run before any database connection or tool execution."""

import hashlib
import os
from pathlib import Path
import shutil
import subprocess

import pytest

from scripts.test_local_postgres import isolated_libpq_environment


ROOT = Path(__file__).resolve().parents[3]


def test_libpq_environment_is_isolated_and_restored_on_failure(monkeypatch):
    monkeypatch.setenv("PGHOSTADDR", "192.0.2.1")
    monkeypatch.setenv("PGSERVICE", "untrusted-service")
    monkeypatch.delenv("PGSERVICEFILE", raising=False)
    with pytest.raises(RuntimeError):
        with isolated_libpq_environment():
            assert all(key not in os.environ for key in ("PGHOSTADDR", "PGSERVICE", "PGSERVICEFILE"))
            raise RuntimeError("simulated setup failure")
    assert os.environ["PGHOSTADDR"] == "192.0.2.1"
    assert os.environ["PGSERVICE"] == "untrusted-service"
    assert "PGSERVICEFILE" not in os.environ


def test_backup_restores_process_environment_when_output_setup_fails(tmp_path):
    powershell = shutil.which("powershell.exe") or shutil.which("pwsh")
    if powershell is None:
        pytest.skip("PowerShell is not installed")
    output_file = tmp_path / "not-a-directory"
    output_file.write_text("keep", encoding="ascii")
    command = """
$keys = @('PGHOST', 'PGHOSTADDR', 'PGSERVICE', 'PGSERVICEFILE', 'PGPORT', 'PGDATABASE', 'PGUSER', 'PGPASSWORD')
$before = @{}
foreach ($key in $keys) { $before[$key] = [Environment]::GetEnvironmentVariable($key, 'Process') }
$failed = $false
try { & $env:TEST_BACKUP_SCRIPT -OutputDirectory $env:TEST_OUTPUT_DIRECTORY } catch { $failed = $true }
if (-not $failed) { throw 'Expected output setup failure' }
foreach ($key in $keys) {
    if ([Environment]::GetEnvironmentVariable($key, 'Process') -cne $before[$key]) { throw 'Process environment was not restored' }
}
"""
    result = subprocess.run([powershell, "-NoProfile", "-Command", command],
        env=dict(os.environ, MIGRATION_DATABASE_URL="postgresql://owner:private-source@localhost:5433/source",
                 PGHOSTADDR="192.0.2.1", PGSERVICE="prior-service", PGPASSWORD="prior-private",
                 TEST_BACKUP_SCRIPT=str(ROOT / "scripts/backup-db.ps1"), TEST_OUTPUT_DIRECTORY=str(output_file)),
        capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, "backup setup failure did not preserve caller environment"
    assert output_file.read_text(encoding="ascii") == "keep"


@pytest.mark.parametrize(
    ("target", "source", "manifest_valid", "confirm", "message"),
    [
        ("postgresql://operator:private-target@127.0.0.1:5433/restore_test", "", True, False, "ConfirmSeparateTestDatabase is required"),
        ("postgresql://operator:private-target@127.0.0.1:5433/production", "", True, True, "database name must contain test"),
        ("postgresql://operator:private-target@127.0.0.1:5433/restore_test", "", False, True, "SHA-256 mismatch"),
        ("postgresql://operator:private-target@localhost:5433/restore_test", "postgresql://owner:private-source@127.0.0.1:5433/source", True, True, "separate PostgreSQL cluster endpoint"),
        ("postgresql://operator:private-target@127.0.0.1:5433/restore_test", "postgresql://owner:private-source@other/source?host=127.0.0.1&port=5433", True, True, "source URL options are unsupported"),
    ],
)
def test_restore_refuses_unsafe_target_without_echoing_secrets(
    tmp_path, target, source, manifest_valid, confirm, message
):
    powershell = shutil.which("powershell.exe") or shutil.which("pwsh")
    if powershell is None:
        pytest.skip("PowerShell is not installed")
    archive = tmp_path / "source.dump"
    archive.write_bytes(b"not a real archive: guards must run before pg_restore")
    digest = hashlib.sha256(archive.read_bytes()).hexdigest() if manifest_valid else "0" * 64
    Path(str(archive) + ".sha256").write_text(digest + "  source.dump\n", encoding="ascii")
    command = [powershell, "-NoProfile", "-File", str(ROOT / "scripts/restore-db.ps1"), "-BackupPath", str(archive)]
    if confirm:
        command.append("-ConfirmSeparateTestDatabase")
    result = subprocess.run(command, env=dict(os.environ, RESTORE_TEST_DATABASE_URL=target, MIGRATION_DATABASE_URL=source),
                            capture_output=True, text=True, timeout=15)
    output = result.stdout + result.stderr
    assert result.returncode != 0
    assert message in output
    assert "private-target" not in output and "private-source" not in output
