"""Measure request-to-render in Chromium; keep mock and live gates distinct."""

from __future__ import annotations

import argparse
import ctypes
from datetime import UTC, datetime
import hashlib
from importlib.metadata import version
import json
import math
import os
from pathlib import Path
import platform
import subprocess
import sys
from urllib.parse import urlsplit
from uuid import UUID

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from sqlalchemy import select, text
from sqlalchemy.orm import Session
from app.config import get_settings
from app.analytics.executor import QueryExecutor
from app.db.connections import create_runtime_engine
from app.db.models import UsageCall, UsageRecord, User
from app.schemas.analysis import CATALOG_VERSION


def summarize(samples: list[dict], threshold_ms: int) -> dict:
    successful = [sample for sample in samples if sample["ok"]]
    def percentile(field):
        values = sorted(sample[field] for sample in successful)
        return round(values[math.ceil(0.95 * len(values)) - 1], 3) if values else None
    errors = len(samples) - len(successful)
    p95 = percentile("total_ms")
    return {"sample_count": len(samples), "success_count": len(successful), "error_count": errors,
            "error_rate": errors / len(samples) if samples else None,
            "p95_ms": p95, "p95_api_ms": percentile("api_ms"), "p95_db_ms": percentile("db_ms"),
            "p95_render_after_api_ms": percentile("render_after_api_ms"), "threshold_ms": threshold_ms,
            "timing_gate_passed": len(samples) >= 30 and errors == 0 and p95 is not None and p95 < threshold_ms}


def physical_memory_bytes():
    if os.name == "nt":
        class MemoryStatus(ctypes.Structure):
            _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong),
                        *[(name, ctypes.c_ulonglong) for name in ("total", "available", "page_total", "page_available", "virtual_total", "virtual_available", "extended")]]
        status = MemoryStatus()
        status.length = ctypes.sizeof(status)
        return status.total if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)) else None
    try:
        return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
    except (ValueError, OSError, AttributeError):
        return None


def benchmark(args) -> dict:
    parsed = urlsplit(args.base_url)
    if parsed.scheme not in {"http", "https"} or parsed.hostname not in {"localhost", "127.0.0.1", "::1"} or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in {"", "/"}:
        raise ValueError("benchmark requires a loopback app origin without credentials/path/query")
    groups = ["saved", "agent"] if args.group == "both" else [args.group]
    if "agent" in groups and args.expected_mode is None:
        raise ValueError("agent benchmark requires --expected-mode mock or live")
    saved_user = os.getenv("BENCHMARK_SAVED_USERNAME") or os.getenv("BENCHMARK_USERNAME")
    agent_user = os.getenv("BENCHMARK_AGENT_USERNAME") or os.getenv("BENCHMARK_USERNAME")
    if not os.getenv("BENCHMARK_PASSWORD") or "saved" in groups and not saved_user or "agent" in groups and not agent_user:
        raise ValueError("set BENCHMARK_PASSWORD and required BENCHMARK_*_USERNAME variables")
    prompt = os.getenv("BENCHMARK_PROMPT", "Tổng quan giao dịch năm 2025")
    configuration = {"base_url": args.base_url.rstrip("/"), "groups": groups, "saved_user": saved_user, "agent_user": agent_user,
                     "samples": args.samples, "warmup": args.warmup, "expected_mode": args.expected_mode,
                     "dashboard_id": args.dashboard_id, "prompt": prompt}
    worker = subprocess.run(["node", str(ROOT / "frontend/scripts/benchmark.mjs")], input=json.dumps(configuration),
        capture_output=True, text=True, encoding="utf-8", cwd=ROOT, env=os.environ.copy(),
        timeout=(args.samples + args.warmup) * len(groups) * 150 + 60,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    try:
        report = json.loads(worker.stdout)
    except ValueError:
        raise RuntimeError("browser benchmark did not produce metrics; no raw output is logged") from None
    settings = get_settings()
    source = {}
    executor = QueryExecutor(settings)
    try:
        with executor._engine("ALL").connect() as connection:
            source["postgres_version"] = connection.scalar(text("SELECT current_setting('server_version')"))
            source["fact_rows"] = {view: connection.scalar(text(f"SELECT COUNT({key}) FROM analytics.{view}"))
                                   for view, key in (("sales", "transaction_id"), ("offers", "offer_id"), ("repairs", "repair_id"))}
    except Exception as error:
        source["metadata_error_type"] = type(error).__name__
    finally:
        executor.close()
    records = [sample for group in report["groups"].values() for phase in ("warmup", "samples") for sample in group[phase]]
    runs = [UUID(record["run_id"]) for record in records if record.get("run_id")]
    # Read only this benchmark actor's runs. Keep all usage/audit records, even
    # warmup and failures; deleting them would undermine the daily budget.
    if runs and settings.runtime_database_url is not None:
        engine = create_runtime_engine(settings)
        try:
            with Session(engine) as database:
                usage = {str(row.run_id): row for row in database.scalars(select(UsageRecord).join(User, User.id == UsageRecord.user_id)
                    .where(UsageRecord.run_id.in_(runs), User.username == agent_user))}
                calls = list(database.scalars(select(UsageCall).where(UsageCall.run_id.in_([row.run_id for row in usage.values()]))))
                for record in records:
                    row = usage.get(record.get("run_id"))
                    if row:
                        record["usage"] = {"llm_calls": row.llm_calls, "input_tokens": row.input_tokens,
                            "output_tokens": row.output_tokens, "estimated_cost_usd": str(row.estimated_cost_usd),
                            "backend_run_ms": row.latency_ms, "query_count": row.query_count}
                        record["provider_calls"] = [{"mode": call.mode, "model": call.model, "status": call.status,
                            "input_price": str(call.input_price), "output_price": str(call.output_price)}
                            for call in calls if call.run_id == row.run_id]
        finally:
            engine.dispose()
    for name, group in report["groups"].items():
        group["summary"] = summarize(group["samples"], 3000 if name == "saved" else 45000)
    dataset = ROOT / "data/real-estate-sales-sample-database-v3-sql-postgres.sql"
    report.update(created_at=datetime.now(UTC).isoformat(), expected_mode=args.expected_mode, catalog_version=CATALOG_VERSION,
        warmup_excluded=True, prompt_sha256=hashlib.sha256(prompt.encode()).hexdigest(),
        dataset_import_sha256=hashlib.sha256(dataset.read_bytes()).hexdigest(),
        environment={"os": platform.platform(), "architecture": platform.machine(), "cpu": platform.processor(),
            "logical_cpu_count": os.cpu_count(), "physical_memory_bytes": physical_memory_bytes(), "python": platform.python_version(),
            "frontend_mode": args.frontend_mode, "source": source,
            "packages": {name: version(name) for name in ("fastapi", "SQLAlchemy", "psycopg", "langgraph", "openai")}},
        model={"provider": settings.llm_provider, "model": settings.llm_model,
            "input_price_per_million": str(settings.llm_input_price_per_million) if settings.llm_input_price_per_million is not None else None,
            "output_price_per_million": str(settings.llm_output_price_per_million) if settings.llm_output_price_per_million is not None else None,
            "pricing_source": settings.llm_pricing_source, "pricing_verified_on": str(settings.llm_pricing_verified_on) if settings.llm_pricing_verified_on else None},
        measurement={"saved": "before page navigation through metadata/query and two animation frames after canvas readiness",
            "agent": "before send click through preview and two animation frames after canvas readiness; conversation setup excluded",
            "db_ms": "sum of executor timings, including connection/EXPLAIN/fetch", "api_ms": "browser request responseEnd",
            "render_after_api_ms": "API body finished to observed DOM/canvas readiness", "percentile": "nearest rank ceil(0.95*n), successes only; failures retained and fail gate"})
    report["live_performance_gate_passed"] = (not report.get("fatal") and args.expected_mode == "live" and set(groups) == {"saved", "agent"}
        and all(group["summary"]["timing_gate_passed"] for group in report["groups"].values()))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--group", choices=("saved", "agent", "both"), default="both")
    parser.add_argument("--expected-mode", choices=("mock", "live"))
    parser.add_argument("--samples", type=int, default=30)
    parser.add_argument("--warmup", type=int, default=3)
    parser.add_argument("--dashboard-id", type=UUID)
    parser.add_argument("--frontend-mode", choices=("production", "development", "unknown"), default="unknown")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.samples < 30 or args.warmup < 1:
        parser.error("at least 30 measured samples and one warmup are required")
    if args.dashboard_id:
        args.dashboard_id = str(args.dashboard_id)
    report = benchmark(args)
    print(json.dumps({"groups": {name: group["summary"] for name, group in report["groups"].items()},
                      "live_performance_gate_passed": report["live_performance_gate_passed"]}))
    return 1 if report.get("fatal") or any(not group["summary"]["timing_gate_passed"] for group in report["groups"].values()) else 0


if __name__ == "__main__":
    raise SystemExit(main())
