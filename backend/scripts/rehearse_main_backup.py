"""Restore the audited main DB backup into a new disposable PostgreSQL cluster.

This command never runs Alembic against the source database. Credentials are
loaded from the ignored audit configuration and passed only in process env.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
import subprocess
import tempfile

import psycopg
from psycopg import sql

from audit_db import DEFAULT_SQL, ROOT, audit_database, configured_audit_dsn, isolated_libpq_environment, parse_import
from test_local_postgres import unused_port


BACKEND = ROOT / "backend"
POSTGRES_BIN = Path("C:/Program Files/PostgreSQL/16/bin")
TABLES = tuple(sorted(parse_import(DEFAULT_SQL)["tables"]))


def migration_code_sha256() -> str:
    """Bind the rehearsal to the exact Alembic configuration and revisions."""
    files = [BACKEND / "alembic.ini", BACKEND / "alembic" / "env.py"]
    files.extend(sorted((BACKEND / "alembic" / "versions").glob("*.py")))
    digest = hashlib.sha256()
    for path in files:
        digest.update(path.relative_to(BACKEND).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def run(command: list[str], env: dict[str, str], *, cwd: Path = BACKEND) -> None:
    result = subprocess.run(command, cwd=cwd, env=env, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    if result.returncode:
        raise RuntimeError(f"{Path(command[0]).name} failed (exit {result.returncode})")


def fingerprints(dsn: str) -> dict[str, dict[str, object]]:
    output = {}
    with psycopg.connect(dsn, autocommit=True, connect_timeout=5) as conn:
        conn.execute("BEGIN READ ONLY")
        if conn.execute("SHOW transaction_read_only").fetchone()[0] != "on":
            raise RuntimeError("source read-only transaction was not confirmed")
        for table in TABLES:
            count, digest = conn.execute(sql.SQL(
                "SELECT COUNT(*), md5(COALESCE(string_agg(md5(to_jsonb(source)::text), '' "
                "ORDER BY md5(to_jsonb(source)::text)), '')) FROM public.{} AS source"
            ).format(sql.Identifier(table))).fetchone()
            output[table] = {"row_count": count, "content_md5": digest}
        conn.execute("ROLLBACK")
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backup", type=Path, required=True)
    args = parser.parse_args()
    archive = args.backup.resolve(strict=True)
    if archive.parent != (ROOT / "backups" / "real-estate-db-pre-migration-20261017").resolve():
        parser.error("backup must be the main DB archive in the dedicated ignored backup directory")
    manifest = Path(str(archive) + ".sha256")
    if not manifest.is_file() or archive.suffix != ".dump":
        parser.error("custom-format backup and SHA-256 manifest are required")
    expected_hash = manifest.read_text(encoding="ascii").split()[0].lower()
    actual_hash = hashlib.sha256(archive.read_bytes()).hexdigest()
    if expected_hash != actual_hash:
        parser.error("backup SHA-256 mismatch")
    source_url = configured_audit_dsn(None)
    if not source_url:
        parser.error("AUDIT_DATABASE_URL is required")
    for name in ("initdb", "pg_ctl", "psql", "pg_restore"):
        if not (POSTGRES_BIN / f"{name}.exe").is_file():
            parser.error(f"missing PostgreSQL binary: {name}")

    work = Path(tempfile.mkdtemp(prefix="main-restore-rehearsal-", dir=BACKEND / ".runtime"))
    print(f"Rehearsal artifacts: {work.relative_to(ROOT)}", flush=True)
    data = work / "data"
    secret_file = work / "init-password"
    secret = secrets.token_hex(24)
    secret_file.write_text(secret, encoding="ascii")
    port = unused_port()
    target_url = f"postgresql://test_admin:{secret}@127.0.0.1:{port}/real_estate_restore_test"
    migration_url = target_url.replace("postgresql://", "postgresql+psycopg://", 1)
    env = dict(os.environ, PGHOST="127.0.0.1", PGPORT=str(port), PGUSER="test_admin",
               PGDATABASE="postgres", PGPASSWORD=secret, PGCLIENTENCODING="UTF8")
    for key in ("PGHOSTADDR", "PGSERVICE", "PGSERVICEFILE"):
        env.pop(key, None)
    binary = lambda name: str(POSTGRES_BIN / f"{name}.exe")
    started = False
    try:
        run([binary("initdb"), "-D", str(data), "-U", "test_admin", "--encoding=UTF8",
             "--locale=C", "--auth=scram-sha-256", f"--pwfile={secret_file}"], env)
        secret_file.unlink()
        run([binary("pg_ctl"), "-D", str(data), "-l", str(work / "server.log"),
             "-o", f"-h 127.0.0.1 -p {port}", "-w", "start"], env)
        started = True
        with psycopg.connect(host="127.0.0.1", port=port, dbname="postgres",
                             user="test_admin", password=secret, autocommit=True) as conn:
            conn.execute("CREATE DATABASE real_estate_restore_test")

        restore_env = dict(env, MIGRATION_DATABASE_URL=source_url,
                           RESTORE_TEST_DATABASE_URL=target_url)
        run(["powershell.exe", "-NoProfile", "-File", str(ROOT / "scripts" / "restore-db.ps1"),
             "-BackupPath", str(archive), "-ConfirmSeparateTestDatabase"], restore_env)
        source_before = fingerprints(source_url)
        with psycopg.connect(source_url, autocommit=True, connect_timeout=5) as conn:
            conn.execute("BEGIN READ ONLY")
            source_cluster = str(conn.execute("SELECT system_identifier FROM pg_control_system()").fetchone()[0])
            conn.execute("ROLLBACK")
        restored_before = fingerprints(target_url)
        if source_before != restored_before:
            raise RuntimeError("restored source rows differ from live source fingerprint")
        print("Actual main DB archive restored; all 16 source table fingerprints match.", flush=True)

        expected = parse_import(DEFAULT_SQL)
        restored_audit = audit_database(target_url, expected)
        comparison = restored_audit["comparison_to_import"]
        if (any(comparison[key] for key in (
                "missing_tables", "column_differences", "primary_key_differences",
                "unique_constraint_differences", "row_count_differences", "baseline_differences"))
                or any(comparison["foreign_key_differences"].values())):
            raise RuntimeError("restored schema/baseline differs from import fixture")
        golden_env = dict(env, AUDIT_DATABASE_URL=target_url)
        run([os.sys.executable, "scripts/verify_golden_db.py"], golden_env)

        migration_env = dict(env, APP_ENV="test", MIGRATION_DATABASE_URL=migration_url,
                             MIGRATION_BACKUP_VERIFIED="true", RUNTIME_DATABASE_URL="",
                             READER_ALL_DATABASE_URL="", READER_CA_DATABASE_URL="",
                             READER_TX_DATABASE_URL="", PGOPTIONS="-c lock_timeout=5000 -c statement_timeout=60000")
        rehearsed_code = migration_code_sha256()
        run([os.sys.executable, "-m", "alembic", "upgrade", "0005_graph_checkpoints"], migration_env)
        if migration_code_sha256() != rehearsed_code:
            raise RuntimeError("Alembic code changed during rehearsal")
        with psycopg.connect(target_url, autocommit=True) as conn:
            head = conn.execute("SELECT version_num FROM alembic_version").fetchone()[0]
        restored_after = fingerprints(target_url)
        if restored_after != restored_before:
            raise RuntimeError("migration changed one of the 16 source tables")
        result = {"archive_sha256": actual_hash, "table_count": len(restored_after),
                  "source_fingerprint_equal": True, "restore_comparison_empty": True,
                  "golden_cases_passed": 15, "migration_head": head,
                  "migration_code_sha256": rehearsed_code,
                  "source_rows_unchanged_by_migration": True,
                  "source_cluster_system_identifier": source_cluster,
                  "source_fingerprint_sha256": hashlib.sha256(json.dumps(
                      source_before, sort_keys=True).encode("utf-8")).hexdigest()}
        (work / "rehearsal-report.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(result), flush=True)
        return 0
    finally:
        if secret_file.exists():
            secret_file.unlink()
        if started:
            run([binary("pg_ctl"), "-D", str(data), "-m", "fast", "-w", "stop"], env)
            print("Rehearsal PostgreSQL stopped.", flush=True)


if __name__ == "__main__":
    with isolated_libpq_environment():
        raise SystemExit(main())
