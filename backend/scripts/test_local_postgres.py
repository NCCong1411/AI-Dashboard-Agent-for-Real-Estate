"""Run DB gates in a disposable loopback PostgreSQL cluster, never the user's DB.

Uses installed PostgreSQL binaries, imports the synthetic source into a new
cluster, backs it up, restores a separate test DB, runs migration/RLS/auth and
executor tests, and stops the cluster even on failure. Credentials are random
and passed through environment variables, never CLI arguments or output.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import json
from decimal import Decimal
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import tempfile
from threading import Event, Thread
import time
from uuid import UUID
from urllib.request import urlopen

import psycopg
from psycopg import sql


BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent


def require_live_evaluation_preflight() -> None:
    """Validate live provider/pricing before creating any cluster artifacts."""
    if str(BACKEND) not in sys.path:
        sys.path.insert(0, str(BACKEND))
    from app.agent.llm import require_live_configuration
    from app.config import Settings

    # Explicit construction avoids changing the cached/default application
    # mode.  The rest of this harness remains mock-only.
    require_live_configuration(Settings(llm_mode="live"))


def run_agent_evaluation(work: Path, env: dict[str, str], *, mode: str) -> int:
    """Run one evaluator subprocess without changing the caller environment."""
    if mode not in {"mock", "live"}:
        raise ValueError("evaluation mode must be mock or live")
    output = work / f"{mode}-full-evaluation.json"
    evaluation_env = dict(env, LLM_MODE=mode)
    evaluated = subprocess.run(
        [sys.executable, "scripts/evaluate_agent.py", "--mode", mode, "--phase", "full",
         "--output", str(output)],
        cwd=BACKEND,
        env=evaluation_env,
    )
    if mode == "live":
        # Live FAIL/BLOCKED are release gates. Preserve evaluator exit 1/2.
        return evaluated.returncode
    if evaluated.returncode not in (0, 1):
        return evaluated.returncode
    # Mock failures are reported, never relabeled as live quality PASS.
    report = json.loads(output.read_text(encoding="utf-8"))
    print(f"Mock evaluation (not a release gate): {report['summary']}", flush=True)
    return 0


@contextmanager
def isolated_libpq_environment():
    """Prevent libpq overrides from redirecting direct and subprocess calls."""
    keys = ("PGHOSTADDR", "PGSERVICE", "PGSERVICEFILE")
    previous = {key: os.environ.pop(key, None) for key in keys}
    try:
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def unused_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def source_fingerprint(port: int, password: str, database: str = "dashboard_test") -> dict:
    """Hash every source row without writing/exporting any PII values.

    The admin principal belongs only to this disposable cluster. It observes
    all rows despite FORCE RLS; application tests retain their scoped logins.
    """
    result = {}
    with psycopg.connect(host="127.0.0.1", port=port, dbname=database, user="test_admin", password=password) as connection:
        connection.execute("SET TRANSACTION READ ONLY")
        # Alembic's pre-existing version ledger lives in public; it is app
        # metadata, not one of the imported source tables. No other table is
        # excluded, so unexpected source additions/removals still fail.
        tables = connection.execute("SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename <> 'alembic_version' ORDER BY tablename").fetchall()
        for (table,) in tables:
            count, digest = connection.execute(sql.SQL(
                "SELECT COUNT(*), md5(COALESCE(string_agg(md5(to_jsonb(source)::text), '' ORDER BY md5(to_jsonb(source)::text)), '')) FROM public.{} AS source"
            ).format(sql.Identifier(table))).fetchone()
            result[table] = {"row_count": count, "content_md5": digest}
    return result


def run_benchmark_process(
    work: Path, env: dict[str, str], *, base_url: str, password: str, mode: str
) -> int:
    """Run the existing benchmark CLI with an isolated mock/live environment."""
    if mode not in {"mock", "live"}:
        raise ValueError("benchmark mode must be mock or live")
    benchmark_env = dict(
        env,
        LLM_MODE=mode,
        BENCHMARK_PASSWORD=password,
        BENCHMARK_SAVED_USERNAME="viewer_ca",
        BENCHMARK_AGENT_USERNAME="builder_all",
    )
    return subprocess.run(
        [sys.executable, "scripts/benchmark.py", "--base-url", base_url,
         "--group", "both", "--expected-mode", mode, "--samples", "30", "--warmup", "3",
         "--frontend-mode", "production", "--output", str(work / f"benchmark-{mode}.json")],
        cwd=BACKEND,
        env=benchmark_env,
    ).returncode


def run_playwright_process(env: dict[str, str], *, live_smoke: bool = False,
                           frontend_directory: Path | None = None) -> int:
    """Run either the deterministic suite or the explicitly opted-in live smoke."""
    command = ["node", "node_modules/@playwright/test/cli.js", "test"]
    if live_smoke:
        command.extend(("--config", "playwright.live.config.ts"))
    else:
        command.append("--reporter=line")
    return subprocess.run(command, cwd=frontend_directory or ROOT / "frontend", env=env).returncode


def run_browser_gates(
    work: Path,
    env: dict[str, str],
    *,
    e2e: bool = True,
    benchmark: bool = False,
    llm_mode: str = "mock",
    live_smoke: bool = False,
    existing_accounts: dict[str, str] | None = None,
    frontend_directory: Path | None = None,
) -> int:
    """Launch only this run's app processes and stop their process trees."""
    if llm_mode not in {"mock", "live"}:
        raise ValueError("browser gate LLM mode must be mock or live")
    if live_smoke and (e2e or benchmark or llm_mode != "live"):
        raise ValueError("live smoke requires its dedicated live-only browser mode")
    if llm_mode == "live" and not (benchmark or live_smoke):
        raise ValueError("live browser mode requires an explicit paid measurement flag")
    if existing_accounts is not None and not live_smoke:
        raise ValueError("existing accounts are supported only by the dedicated live journey")
    frontend = frontend_directory or ROOT / "frontend"
    backend_port, frontend_port = unused_port(), unused_port()
    env = dict(env, FRONTEND_ORIGIN=f"http://127.0.0.1:{frontend_port}",
               BACKEND_URL=f"http://127.0.0.1:{backend_port}",
               PLAYWRIGHT_BASE_URL=f"http://127.0.0.1:{frontend_port}",
               LLM_MODE=llm_mode)
    password = secrets.token_hex(24)
    if existing_accounts is None:
        for variable in ("SEED_BUILDER_ALL_PASSWORD", "SEED_VIEWER_ALL_PASSWORD", "SEED_VIEWER_CA_PASSWORD", "SEED_BUILDER_TX_PASSWORD"):
            env[variable] = password
        env["E2E_DEMO_PASSWORD"] = password
        run([sys.executable, "scripts/seed_users.py"], env)
        run([sys.executable, "scripts/seed_dashboard.py", "--owner", "builder_all", "--confirm-demo"], env)
    else:
        for account in ("builder_all", "viewer_all", "viewer_ca", "builder_tx"):
            if not existing_accounts.get(account):
                raise ValueError("existing demo password is missing")
            env[f"E2E_{account.upper()}_PASSWORD"] = existing_accounts[account]
    env["E2E_CONTROL_DIRECTORY"] = str(work)
    if benchmark:
        run(["node", "node_modules/next/dist/bin/next", "build"], env, cwd=ROOT / "frontend")
    commands = [
        ([sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(backend_port)], BACKEND),
        (["node", "node_modules/next/dist/bin/next", "start" if benchmark else "dev", "--hostname", "127.0.0.1", "--port", str(frontend_port)], frontend),
    ]
    processes = []
    logs = []
    stopped, restart_errors = Event(), []
    restart_thread = None

    def stop_process(process):
        if process.poll() is None:
            if os.name == "nt":
                subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW)
            else:
                process.terminate()
            process.wait(timeout=15)

    def launch_backend(fault="none"):
        from sqlalchemy.engine import make_url
        base_command, directory = commands[0]
        command, backend_env = list(base_command), dict(env)
        if fault == "reader":
            backend_env["READER_CA_DATABASE_URL"] = make_url(env["READER_CA_DATABASE_URL"]).set(port=unused_port()).render_as_string(hide_password=False)
        elif fault == "provider":
            command[3] = "scripts.browser_test_app:create_app"
            command.append("--factory")
            backend_env["E2E_PROVIDER_FAILURE"] = "true"
        return subprocess.Popen(command, cwd=directory, env=backend_env, stdout=logs[0], stderr=logs[0],
                                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)

    def restart_control():
        # Trusted, local test harness only: no restart endpoint in the app.
        request_file, ack_file = work / "restart-request.json", work / "restart-completed.json"
        while not stopped.wait(0.1):
            if not request_file.exists():
                continue
            try:
                request = json.loads(request_file.read_text(encoding="utf-8"))
            except (ValueError, OSError):
                continue  # writer may not have completed its small JSON file
            identifier = request.get("id")
            try:
                actions = {"restart_backend": "none", "restart_backend_with_reader_outage": "reader",
                           "restart_backend_with_provider_outage": "provider"}
                if request.get("action") not in actions or str(UUID(identifier)) != identifier:
                    raise ValueError("invalid test restart request")
                request_file.unlink()
                old_pid = processes[0].pid
                stop_process(processes[0])
                processes[0] = launch_backend(actions[request["action"]])
                deadline = time.monotonic() + 15
                while not stopped.is_set() and time.monotonic() < deadline:
                    if processes[0].poll() is not None:
                        raise RuntimeError("restarted test backend exited")
                    try:
                        with urlopen(f"http://127.0.0.1:{backend_port}/api/health", timeout=1) as response:
                            if response.status == 200:
                                break
                    except OSError:
                        stopped.wait(0.1)
                else:
                    raise RuntimeError("restarted test backend did not become ready")
                ack_file.write_text(json.dumps({"id": identifier, "status": "ready", "old_pid": old_pid,
                                               "new_pid": processes[0].pid}), encoding="utf-8")
            except Exception as error:
                restart_errors.append(type(error).__name__)
                ack_file.write_text(json.dumps({"id": identifier, "status": "error"}), encoding="utf-8")
                return
    try:
        for index, (command, directory) in enumerate(commands):
            log = (work / f"app-{index}.log").open("w", encoding="utf-8")
            logs.append(log)
            processes.append(subprocess.Popen(command, cwd=directory, env=env, stdout=log, stderr=log,
                              creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0))
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            if any(process.poll() is not None for process in processes):
                raise RuntimeError("test app exited during startup; inspect ignored app logs")
            try:
                with urlopen(env["PLAYWRIGHT_BASE_URL"] + "/api/health", timeout=2) as response:
                    if response.status == 200:
                        break
            except OSError:
                time.sleep(0.3)
        else:
            raise RuntimeError("test app startup timed out")
        restart_thread = Thread(target=restart_control, daemon=True)
        restart_thread.start()
        result = run_playwright_process(env, live_smoke=live_smoke, frontend_directory=frontend) if e2e or live_smoke else 0
        if restart_errors:
            raise RuntimeError("test backend restart failed; inspect ignored app logs")
        if result == 0 and benchmark:
            result = run_benchmark_process(
                work, env, base_url=env["PLAYWRIGHT_BASE_URL"], password=password, mode=llm_mode
            )
        return result
    finally:
        stopped.set()
        if restart_thread is not None:
            restart_thread.join(timeout=30)
        for process in reversed(processes):
            stop_process(process)
        for log in logs:
            log.close()


def run(command: list[str], env: dict[str, str], *, cwd: Path = BACKEND) -> None:
    result = subprocess.run(command, cwd=cwd, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    if result.returncode:
        # Subprocess command lines have no secrets. DB server errors from setup
        # may contain password literals, so setup output stays in ignored files.
        raise RuntimeError(f"{Path(command[0]).name} failed (exit {result.returncode})")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--postgres-bin", type=Path, default=Path("C:/Program Files/PostgreSQL/16/bin"))
    parser.add_argument("--e2e", action="store_true", help="also seed demo users and run Playwright with isolated app ports")
    parser.add_argument("--evaluate", action="store_true", help="also write a mock-only full evaluation report; not a live quality gate")
    parser.add_argument("--live-evaluate", action="store_true", help="opt in to the paid live full evaluation after mock DB regression")
    parser.add_argument("--live-smoke", action="store_true", help="opt in to the dedicated paid live Playwright smoke journey")
    benchmark_group = parser.add_mutually_exclusive_group()
    benchmark_group.add_argument("--benchmark", action="store_true", help="production frontend; 30 browser samples/group, mock AI only")
    benchmark_group.add_argument("--live-benchmark", action="store_true", help="opt in to paid live AI latency measurement: 30 samples/group plus 3 warmups")
    parser.add_argument("--restore-only", action="store_true", help="verify transactional restore and populated-target refusal, then stop")
    args = parser.parse_args()
    if args.live_benchmark and args.e2e:
        parser.error("--live-benchmark cannot be combined with --e2e")
    if args.live_benchmark and args.live_evaluate:
        parser.error("run --live-evaluate and --live-benchmark separately")
    if args.live_smoke and (args.e2e or args.evaluate or args.live_evaluate or args.benchmark or args.live_benchmark):
        parser.error("--live-smoke must run separately from e2e, evaluation, and benchmark modes")
    if args.restore_only and (args.live_evaluate or args.live_benchmark or args.live_smoke):
        parser.error("live evaluation/benchmark/smoke cannot be combined with --restore-only")
    if args.live_evaluate or args.live_benchmark or args.live_smoke:
        try:
            require_live_evaluation_preflight()
        except (TypeError, ValueError):
            # Do not render Pydantic inputs or exception details: they may
            # contain a provider key supplied through the environment file.
            parser.error("live operation requires valid provider, HTTPS endpoint, model, key and verified pricing")
    for program in ("initdb", "pg_ctl", "psql", "pg_dump", "pg_restore"):
        if not (args.postgres_bin / (program + (".exe" if os.name == "nt" else ""))).is_file():
            parser.error(f"missing PostgreSQL binary: {program}")
    runtime = BACKEND / ".runtime"
    runtime.mkdir(exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="postgres-test-", dir=runtime))
    print(f"Isolated cluster artifacts: {work.relative_to(ROOT)}", flush=True)
    data = work / "data"
    password_file = work / "init-password"
    passwords = {name: secrets.token_hex(24) for name in ("test_admin", "test_migration", "test_runtime", "test_all", "test_ca", "test_tx")}
    password_file.write_text(passwords["test_admin"], encoding="utf-8")
    port = unused_port()
    env = dict(os.environ)
    for variable in ("PGHOSTADDR", "PGSERVICE", "PGSERVICEFILE"):
        env.pop(variable, None)
    env.update(PGHOST="127.0.0.1", PGPORT=str(port), PGUSER="test_admin", PGDATABASE="postgres",
               PGPASSWORD=passwords["test_admin"], PGCLIENTENCODING="UTF8")

    def binary(name: str) -> str:
        return str(args.postgres_bin / (name + (".exe" if os.name == "nt" else "")))

    def url(user: str, database: str = "dashboard_test") -> str:
        return f"postgresql+psycopg://{user}:{passwords[user]}@127.0.0.1:{port}/{database}"

    started = False
    source_before = None
    try:
        run([binary("initdb"), "-D", str(data), "-U", "test_admin", "--encoding=UTF8",
             "--locale=C", "--auth=scram-sha-256", f"--pwfile={password_file}"], env)
        password_file.unlink()
        run([binary("pg_ctl"), "-D", str(data), "-l", str(work / "server.log"),
             "-o", f"-h 127.0.0.1 -p {port}", "-w", "start"], env)
        started = True
        with psycopg.connect(host="127.0.0.1", port=port, dbname="postgres", user="test_admin",
                             password=passwords["test_admin"], autocommit=True) as connection:
            for user in ("test_migration", "test_runtime", "test_all", "test_ca", "test_tx"):
                attributes = sql.SQL("CREATEDB CREATEROLE") if user == "test_migration" else sql.SQL("")
                connection.execute(sql.SQL("CREATE ROLE {} LOGIN {} PASSWORD {}").format(
                    sql.Identifier(user), attributes, sql.Literal(passwords[user])))
            for database in ("dashboard_source", "dashboard_test"):
                connection.execute(sql.SQL("CREATE DATABASE {} OWNER test_migration").format(sql.Identifier(database)))
        migration_env = dict(env, PGUSER="test_migration", PGPASSWORD=passwords["test_migration"])
        run([binary("psql"), "-X", "-v", "ON_ERROR_STOP=1", "-d", "dashboard_source",
             "-f", str(ROOT / "data/real-estate-sales-sample-database-v3-sql-postgres.sql")], migration_env)
        backup_env = dict(migration_env, MIGRATION_DATABASE_URL=url("test_migration", "dashboard_source"),
                          PGHOSTADDR="192.0.2.1", PGSERVICE="nonexistent_backup_guard_test")
        run(["powershell.exe", "-NoProfile", "-File", str(ROOT / "scripts/backup-db.ps1"),
             "-OutputDirectory", str(work)], backup_env)
        archives = list(work.glob("*.dump"))
        if len(archives) != 1:
            raise RuntimeError("backup script did not produce exactly one archive")
        backup = archives[0]
        # Both databases are synthetic fixtures in this disposable cluster,
        # separate from the user's source cluster. No production URL is used.
        restore_env = dict(migration_env, MIGRATION_DATABASE_URL="", RESTORE_TEST_DATABASE_URL=url("test_migration"),
                           PGHOSTADDR="192.0.2.1", PGSERVICE="nonexistent_restore_guard_test")
        restore_command = ["powershell.exe", "-NoProfile", "-File", str(ROOT / "scripts/restore-db.ps1"),
                           "-BackupPath", str(backup), "-ConfirmSeparateTestDatabase"]
        run(restore_command, restore_env)
        refused = subprocess.run(restore_command, cwd=BACKEND, env=restore_env,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                 creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        if refused.returncode == 0:
            raise RuntimeError("restore script did not reject the non-empty target")
        (work / "restore-verification.json").write_text(json.dumps({
            "archive_sha256": hashlib.sha256(backup.read_bytes()).hexdigest(),
            "empty_target_restore": True, "nonempty_target_refused": True,
        }, indent=2) + "\n", encoding="utf-8")
        source_before = source_fingerprint(port, passwords["test_admin"])
        if len(source_before) != 16 or source_before != source_fingerprint(port, passwords["test_admin"], "dashboard_source"):
            raise RuntimeError("restored source fingerprint differs from the imported source database")
        print("Restore verified: SHA-256, 16-table fingerprint, populated-target refusal.", flush=True)
        if args.restore_only:
            return 0
        env.update(APP_ENV="test", LLM_MODE="mock", MIGRATION_DATABASE_URL=url("test_migration"),
                   MIGRATION_TEST_DATABASE_URL=url("test_migration"), MIGRATION_BACKUP_VERIFIED="true",
                   MIGRATION_TEST_BACKUP_RESTORED="true", RUNTIME_DATABASE_URL=url("test_runtime"),
                   RUNTIME_TEST_DATABASE_URL=url("test_runtime"), AUTH_TEST_DATABASE_URL=url("test_runtime"))
        for scope in ("ALL", "CA", "TX"):
            env[f"READER_{scope}_TEST_DATABASE_URL"] = url(f"test_{scope.lower()}")
            env[f"READER_{scope}_DATABASE_URL"] = url(f"test_{scope.lower()}")
        # Migration-cycle test restores reader memberships from test URLs.
        result = subprocess.run([sys.executable, "-m", "pytest", "tests/integration/test_migrations.py", "-q", "--tb=short"],
                                cwd=BACKEND, env=env)
        if result.returncode:
            return result.returncode
        with psycopg.connect(host="127.0.0.1", port=port, dbname="dashboard_test", user="test_migration",
                             password=passwords["test_migration"], autocommit=True) as connection:
            connection.execute("GRANT USAGE ON SCHEMA app TO test_runtime")
            connection.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA app TO test_runtime")
            connection.execute("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA app TO test_runtime")
        # Persist planner units for representative allowed query shapes. This
        # records calibration evidence, not a latency or monetary estimate.
        sys.path.insert(0, str(BACKEND))
        from sqlalchemy import text
        from app.analytics.compiler import compile_analysis_plan
        from app.analytics.executor import QueryExecutor, explain_cost
        from app.config import Settings
        from app.schemas.analysis import AnalysisPlan, CATALOG_VERSION, SCHEMA_VERSION
        executor = QueryExecutor(Settings(_env_file=None, app_env="test", **{
            f"reader_{scope.lower()}_database_url": url(f"test_{scope.lower()}")
            for scope in ("ALL", "CA", "TX")
        }))
        costs = []
        try:
            for scope in ("ALL", "CA", "TX"):
                for fact, kpis in {"sales": ["sales_value", "transaction_count", "average_sale_value", "sold_property_count"],
                                   "offers": ["offer_count", "accepted_offer_rate"], "repairs": ["repair_cost"]}.items():
                    for dimension in (None, "day", "month", "quarter", "year", "state", "city", "property_type", "agent"):
                        if dimension == "agent" and fact != "sales":
                            continue
                        compiled = compile_analysis_plan(AnalysisPlan(
                            schema_version=SCHEMA_VERSION, catalog_version=CATALOG_VERSION,
                            kpis=kpis, dimensions=[] if dimension is None else [dimension],
                        ), include_fact_count=True)
                        with executor._engine(scope).connect() as connection:
                            document = connection.execute(text("EXPLAIN (FORMAT JSON) " + compiled.sql), compiled.parameters).scalar_one()
                            costs.append({"scope": scope, "fact": fact, "dimension": dimension, "total_cost": str(explain_cost(document))})
        finally:
            executor.close()
        (work / "explain-costs.json").write_text(json.dumps(costs, indent=2), encoding="utf-8")
        print(f"EXPLAIN calibration: {len(costs)} plans; max planner cost {max(Decimal(item['total_cost']) for item in costs)}", flush=True)
        result = subprocess.run([sys.executable, "-m", "pytest", "tests/integration/test_auth.py",
                                "tests/integration/test_rls.py", "tests/integration/test_executor.py",
                                "tests/integration/test_dashboards.py", "tests/integration/test_budget.py",
                                "tests/integration/test_agent.py", "tests/integration/test_conversation_edits.py",
                                "tests/integration/test_checkpoint_recovery.py", "tests/integration/test_approval.py",
                                "tests/integration/test_offer_repair_kpis.py", "tests/integration/test_security_matrix.py",
                                "tests/integration/test_resource_limits.py", "-q", "--tb=short"],
                                cwd=BACKEND, env=env)
        if result.returncode:
            return result.returncode
        if args.evaluate:
            evaluation_result = run_agent_evaluation(work, env, mode="mock")
            if evaluation_result:
                return evaluation_result
        if args.live_evaluate:
            evaluation_result = run_agent_evaluation(work, env, mode="live")
            if evaluation_result:
                return evaluation_result
        if args.live_benchmark:
            return run_browser_gates(work, env, e2e=False, benchmark=True, llm_mode="live")
        if args.live_smoke:
            return run_browser_gates(
                work, env, e2e=False, benchmark=False, llm_mode="live", live_smoke=True
            )
        return run_browser_gates(
            work, env, e2e=args.e2e, benchmark=args.benchmark, llm_mode="mock"
        ) if args.e2e or args.benchmark else 0
    finally:
        if password_file.exists():
            password_file.unlink()
        if started:
            try:
                if source_before is not None:
                    source_after = source_fingerprint(port, passwords["test_admin"])
                    unchanged = source_before == source_after
                    (work / "source-integrity.json").write_text(json.dumps({"before": source_before, "after": source_after,
                        "unchanged": unchanged}, indent=2) + "\n", encoding="utf-8")
                    print(f"Source integrity: {len(source_after)} tables; unchanged={unchanged}", flush=True)
                    if not unchanged:
                        raise RuntimeError("source rows changed during test gates; inspect source-integrity.json")
            finally:
                run([binary("pg_ctl"), "-D", str(data), "-m", "fast", "-w", "stop"], env)
                print("Isolated test PostgreSQL stopped.", flush=True)


if __name__ == "__main__":
    with isolated_libpq_environment():
        raise SystemExit(main())
