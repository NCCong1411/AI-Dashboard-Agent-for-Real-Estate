"""Apply rehearsed Alembic revisions to real_estate_db with bounded locks.

Requires the actual-source backup, separate-cluster rehearsal and read-only
preflight to have completed successfully. The admin credential is process-local
and is never installed as the application's runtime/reader credential.
"""

from __future__ import annotations

import json
import hashlib
import os
from pathlib import Path
import subprocess
import sys

import psycopg

from audit_db import ROOT, configured_audit_dsn, isolated_libpq_environment
from rehearse_main_backup import fingerprints, migration_code_sha256


BACKEND = ROOT / "backend"
ARCHIVE_DIR = ROOT / "backups" / "real-estate-db-pre-migration-20261017"


def main() -> int:
    source_url = configured_audit_dsn(None)
    if not source_url:
        print("Migration not started: AUDIT_DATABASE_URL missing")
        return 2
    archives = list(ARCHIVE_DIR.glob("*.dump"))
    if len(archives) != 1:
        print("Migration not started: expected exactly one actual-source archive")
        return 2
    preflight_path = BACKEND / ".runtime" / "main-migration-preflight.json"
    if not preflight_path.is_file() or not json.loads(preflight_path.read_text(encoding="utf-8")).get("guard_preflight_passed"):
        print("Migration not started: read-only consumer preflight missing or failed")
        return 2
    manifest = Path(str(archives[0]) + ".sha256")
    archive_hash = hashlib.sha256(archives[0].read_bytes()).hexdigest()
    if not manifest.is_file() or manifest.read_text(encoding="ascii").split()[0].lower() != archive_hash:
        print("Migration not started: backup manifest mismatch")
        return 2
    before = fingerprints(source_url)
    before_hash = hashlib.sha256(json.dumps(before, sort_keys=True).encode("utf-8")).hexdigest()
    with psycopg.connect(source_url, autocommit=True, connect_timeout=5) as conn:
        conn.execute("BEGIN READ ONLY")
        database, cluster = conn.execute("SELECT current_database(), system_identifier "
                                         "FROM pg_control_system()").fetchone()
        conn.execute("ROLLBACK")
    cluster = str(cluster)
    if database != "real_estate_db":
        print("Migration not started: source database is not real_estate_db")
        return 2
    reports = list((BACKEND / ".runtime").glob("main-restore-rehearsal-*/rehearsal-report.json"))
    matching = [json.loads(path.read_text(encoding="utf-8")) for path in reports]
    if not any(item.get("migration_head") == "0005_graph_checkpoints"
               and item.get("source_rows_unchanged_by_migration")
               and item.get("source_fingerprint_equal")
               and item.get("archive_sha256") == archive_hash
               and item.get("migration_code_sha256") == migration_code_sha256()
               and item.get("source_fingerprint_sha256") == before_hash
               and item.get("source_cluster_system_identifier") == cluster for item in matching):
        print("Migration not started: separate-cluster rehearsal missing or failed")
        return 2
    preflight_env = dict(os.environ, AUDIT_DATABASE_URL=source_url)
    preflight = subprocess.run([sys.executable, "scripts/preflight_main_migration.py"],
                              cwd=BACKEND, env=preflight_env, capture_output=True, text=True)
    if preflight.returncode:
        print("Migration not started: current read-only consumer preflight failed")
        return 2
    current_preflight = json.loads(preflight_path.read_text(encoding="utf-8"))
    if current_preflight.get("cluster_system_identifier") != cluster:
        print("Migration not started: preflight targeted a different PostgreSQL cluster")
        return 2
    environment = dict(os.environ, MIGRATION_DATABASE_URL=source_url.replace(
        "postgresql://", "postgresql+psycopg://", 1),
        MIGRATION_BACKUP_VERIFIED="true", RUNTIME_DATABASE_URL="",
        READER_ALL_DATABASE_URL="", READER_CA_DATABASE_URL="", READER_TX_DATABASE_URL="",
        PGOPTIONS="-c lock_timeout=5000 -c statement_timeout=60000")
    for key in ("PGHOSTADDR", "PGSERVICE", "PGSERVICEFILE"):
        environment.pop(key, None)
    try:
        migration = subprocess.run([sys.executable, "-m", "alembic", "upgrade", "0005_graph_checkpoints"],
                                   cwd=BACKEND, env=environment, capture_output=True,
                                   text=True, timeout=120)
    except subprocess.TimeoutExpired:
        print("Migration timed out; inspect DB revision and locks before retrying")
        return 2
    if migration.returncode:
        print("Migration failed; inspect DB revision and guards before retrying")
        return 2
    after = fingerprints(source_url)
    with psycopg.connect(source_url, autocommit=True, connect_timeout=5) as conn:
        conn.execute("BEGIN READ ONLY")
        head = conn.execute("SELECT version_num FROM alembic_version").fetchone()[0]
        groups = conn.execute("SELECT count(*) FROM pg_roles WHERE rolname IN "
                              "('analytics_reader_all','analytics_reader_ca','analytics_reader_tx')").fetchone()[0]
        rls = conn.execute("SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
                           "WHERE n.nspname='public' AND c.relname IN "
                           "('properties','transactions','offers','repairs') AND c.relrowsecurity").fetchone()[0]
        conn.execute("ROLLBACK")
    result = {"migration_head": head, "reader_group_count": groups,
              "source_table_count": len(after), "source_rows_unchanged": before == after,
              "scoped_rls_table_count": rls}
    destination = BACKEND / ".runtime" / "main-migration-result.json"
    destination.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result))
    return 0 if (head == "0005_graph_checkpoints" and groups == 3 and rls == 4
                 and len(after) == 16 and before == after) else 1


if __name__ == "__main__":
    with isolated_libpq_environment():
        raise SystemExit(main())
