"""Run the approved live MVP journey against the provisioned local database.

Uses existing demo accounts without reseeding. Creates demo conversations and a
published dashboard through the normal API. Stop the regular local backend first:
the MVP supports one backend process per database. Only this command's processes
are restarted/stopped. Source tables
are fingerprinted through an audit-only read-only connection before and after.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
from uuid import UUID

from dotenv import dotenv_values
from sqlalchemy import select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
sys.path.insert(0, str(BACKEND))

from app.agent.llm import require_live_configuration
from app.config import Settings
from app.db.connections import create_runtime_engine
from app.db.models import Conversation, DashboardVersion, UsageRecord
from scripts.audit_db import configured_audit_dsn, isolated_libpq_environment
from scripts.evaluate_agent import verify_executed_queries
from scripts.rehearse_main_backup import fingerprints
from scripts.test_local_postgres import run_browser_gates


def main() -> int:
    # Recovery uses one process epoch per database. Do not run a second backend
    # beside the standard local service on the same database.
    with socket.socket() as probe:
        probe.settimeout(1)
        if probe.connect_ex(("127.0.0.1", 8000)) == 0:
            raise RuntimeError("stop the local backend on port 8000 before the main demo")
    role_values = dotenv_values(BACKEND / ".runtime/local-postgres-credentials.env", interpolate=False)
    account_values = dotenv_values(BACKEND / ".runtime/local-demo-accounts.env", interpolate=False)
    names = ("RUNTIME_DATABASE_URL", "READER_ALL_DATABASE_URL", "READER_CA_DATABASE_URL", "READER_TX_DATABASE_URL")
    roles = {name: role_values.get(name) for name in names}
    if any(not value for value in roles.values()):
        raise ValueError("provisioned local roles are required")
    for value in roles.values():
        url = make_url(value)
        if url.host not in {"localhost", "127.0.0.1"} or url.database != "real_estate_db":
            raise ValueError("demo requires the provisioned loopback real_estate_db")
    accounts = {name: account_values.get(f"SEED_{name.upper()}_PASSWORD") for name in
                ("builder_all", "viewer_all", "viewer_ca", "builder_tx")}
    if any(not value for value in accounts.values()):
        raise ValueError("existing demo account credentials are required")
    settings = Settings(llm_mode="live", **{key.lower(): value for key, value in roles.items()})
    require_live_configuration(settings)
    audit_url = configured_audit_dsn(None)
    if not audit_url:
        raise ValueError("read-only audit configuration is required")
    audit_target, runtime_target = make_url(audit_url), make_url(roles["RUNTIME_DATABASE_URL"])
    if (audit_target.host not in {"localhost", "127.0.0.1"}
            or audit_target.database != runtime_target.database
            or (audit_target.port or 5432) != (runtime_target.port or 5432)):
        raise ValueError("audit and runtime must address the same loopback database")
    # Check runtime authority before starting or making paid provider calls.
    engine = create_runtime_engine(settings)
    with engine.connect():
        pass
    engine.dispose()
    work = Path(tempfile.mkdtemp(prefix="main-live-demo-", dir=BACKEND / ".runtime"))
    frontend = work / "frontend"
    frontend.mkdir()
    names = subprocess.check_output(["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z", "--", "frontend"], cwd=ROOT)
    for name in set(names.decode("utf-8").split("\0")) - {""}:
        relative = Path(name)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("invalid source path")
        if any(part.startswith(".env") and part != ".env.example" for part in relative.parts):
            continue
        source = ROOT / relative
        if source.is_symlink():
            raise ValueError("source symlinks are not supported")
        if source.is_file():
            target = work / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
    print(f"Main live demo artifacts: {work.relative_to(ROOT)}", flush=True)
    # The dependency install does not receive DB or provider credentials.
    install_env = {key: value for key, value in os.environ.items()
                   if not key.upper().startswith(("LLM_", "PG", "E2E_", "SEED_", "READER_", "RUNTIME_", "AUDIT_", "MIGRATION_"))}
    with (work / "npm-ci.log").open("w", encoding="utf-8") as log:
        installed = subprocess.run([shutil.which("npm.cmd") or "npm", "ci"], cwd=frontend,
                                   env=install_env, stdout=log, stderr=log)
    if installed.returncode:
        raise RuntimeError("frontend dependency install failed; inspect ignored log")
    env = dict(install_env, **roles, LLM_MODE="live", AUDIT_DATABASE_URL="", MIGRATION_DATABASE_URL="")
    # Use the already configured provider settings, including process overrides.
    for key, value in settings.model_dump().items():
        if key.startswith("llm_") and value is not None:
            env[key.upper()] = value.get_secret_value() if hasattr(value, "get_secret_value") else str(value)
    report = {"model": settings.llm_model, "mode": "live", "database": "real_estate_db",
              "browser_exit_code": None, "source_unchanged": False, "complete": False}
    with isolated_libpq_environment():
        before = fingerprints(audit_url)
        try:
            report["browser_exit_code"] = run_browser_gates(work, env, e2e=False, benchmark=False,
                llm_mode="live", live_smoke=True, existing_accounts=accounts, frontend_directory=frontend)
            artifacts = list((frontend / "test-results-live").glob("*/live-run-evidence.json"))
            if len(artifacts) != 1:
                raise RuntimeError("expected one browser evidence artifact")
            evidence = json.loads(artifacts[0].read_text(encoding="utf-8"))
            report["checks"] = evidence["checks"]
            report["execution_accuracy"] = [verify_executed_queries(response, settings)
                                            for response in evidence["responses"] if response.get("draft")]
            run_ids = [UUID(response["run_id"]) for response in evidence["responses"] if response.get("run_id")]
            engine = create_runtime_engine(settings)
            try:
                with Session(engine) as database:
                    usage = list(database.scalars(select(UsageRecord).where(UsageRecord.run_id.in_(run_ids))))
                    conversation = database.get(Conversation, UUID(evidence["conversationId"]))
                    versions = list(database.scalars(select(DashboardVersion.version_number).where(
                        DashboardVersion.dashboard_id == conversation.dashboard_id).order_by(DashboardVersion.version_number)))
                    report["dashboard_id"] = str(conversation.dashboard_id)
                    report["published_versions"] = versions
                    report["usage"] = {"runs": len(usage), "calls": sum(item.llm_calls for item in usage),
                        "input_tokens": sum(item.input_tokens for item in usage),
                        "output_tokens": sum(item.output_tokens for item in usage),
                        "estimated_cost_usd": str(sum(item.estimated_cost_usd for item in usage))}
            finally:
                engine.dispose()
            report["complete"] = (report["browser_exit_code"] == 0 and evidence["complete"]
                and len(evidence["responses"]) == 7 and len(usage) == 7 and versions == [1, 2]
                and all(item["all_correct"] for item in report["execution_accuracy"]))
        finally:
            after = fingerprints(audit_url)
            report["source_unchanged"] = before == after and len(before) == 16
            report["source_fingerprints"] = after
            report["complete"] = report["complete"] and report["source_unchanged"]
            (work / "acceptance.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: value for key, value in report.items() if key != "source_fingerprints"}), flush=True)
    return 0 if report["complete"] else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"Main live demo failed ({type(error).__name__}); inspect ignored artifacts; secrets suppressed")
        raise SystemExit(1) from None
