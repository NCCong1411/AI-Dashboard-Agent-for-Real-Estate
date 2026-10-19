"""Evaluate corpus through real auth/graph/SQL on an isolated test database.

Mock and live evidence are always separate. This strict oracle grader never
counts blocked cases or a query merely executing as a pass. Refusal/question
wording is compared conservatively; paraphrases need review and may fail this
automated grader even when a human would accept them.
"""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import UTC, date, datetime
from decimal import Decimal
import json
import os
from pathlib import Path
import secrets
import sys
from time import perf_counter
import unicodedata
import uuid

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from fastapi.testclient import TestClient
from sqlalchemy import delete, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.agent.llm import create_planner
from app.analytics.catalog import KPI_CATALOG
from app.analytics.executor import QueryExecutor
from app.auth.dependencies import database_session
from app.auth.passwords import hash_password
from app.config import Settings, get_settings
from app.db.connections import create_reader_engine, create_runtime_engine
from app.db.models import AuditEvent, Conversation, UsageRecord, User
from app.services.conversation import ConversationService, get_conversation_service


CORPUS = BACKEND / "tests/evaluation/prompts.json"


def public_error_code(response: dict) -> str | None:
    """Keep the API's safe error code in evidence without logging its message/input."""
    error = response.get("error")
    return error.get("code") if isinstance(error, dict) and isinstance(error.get("code"), str) else None
FACTS = {
    "sales_value": ("transactions", "transaction_date", "transaction_id", "SUM(f.sale_price)"),
    "transaction_count": ("transactions", "transaction_date", "transaction_id", "COUNT(f.transaction_id)"),
    "average_sale_value": ("transactions", "transaction_date", "transaction_id", "SUM(f.sale_price) / NULLIF(COUNT(f.transaction_id),0)"),
    "sold_property_count": ("transactions", "transaction_date", "transaction_id", "COUNT(DISTINCT f.property_id)"),
    "offer_count": ("offers", "offer_date", "offer_id", "COUNT(f.offer_id)"),
    "accepted_offer_rate": ("offers", "offer_date", "offer_id", "SUM(CASE WHEN f.status='accepted' THEN 1 ELSE 0 END)::numeric / NULLIF(COUNT(f.offer_id),0)"),
    "repair_cost": ("repairs", "repair_date", "repair_id", "SUM(f.cost)"),
}


def normalized(value: str) -> str:
    return " ".join("".join(character for character in unicodedata.normalize("NFD", value.casefold())
                            if unicodedata.category(character) != "Mn").replace("đ", "d").split())


def canonical(value) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)


def reference_rows(settings: Settings, scope: str, oracle: dict, widget: dict) -> dict:
    """Independent direct-table SQL built from oracle fields, not agent plans."""
    kpis = widget.get("kpis") or [widget["kpi"]]
    kpi = kpis[0]
    table, day, key, expression = FACTS[kpi]
    if any(FACTS[item][0] != table for item in kpis):
        raise ValueError("reference query cannot combine facts")
    selected_dimensions = widget.get("dimensions") or ([widget["dimension"]] if widget.get("dimension") else [])
    dimensions = {"day": f"f.{day}", "state": "p.state", "city": "p.city", "property_type": "pt.type_name", "agent": "f.agent_id"}
    dimensions.update({period: f"date_trunc('{period}', f.{day})::date" for period in ("month", "quarter", "year")})
    if "agent" in selected_dimensions and table != "transactions":
        raise ValueError("oracle uses an unsupported fact/dimension relationship")
    projections = [f"{dimensions[item]} AS {item}" for item in selected_dimensions]
    projections += [f"{FACTS[item][3]} AS {item}" for item in kpis] + [f"COUNT(f.{key}) AS fact_count"]
    sql = "SELECT " + ", ".join(projections) + f" FROM public.{table} f JOIN public.properties p ON p.property_id=f.property_id JOIN public.property_types pt ON pt.property_type_id=p.property_type_id"
    predicates, parameters = [], {}
    time = oracle["time"]
    if time["mode"] != "all_time":
        predicates.extend((f"f.{day} >= :start", f"f.{day} < :end"))
        parameters.update(start=date.fromisoformat(time["start"]), end=date.fromisoformat(time["end_exclusive"]))
    fields = {"state": "p.state", "city": "p.city", "property_type": "pt.type_name", "agent": "f.agent_id"}
    for index, item in enumerate(oracle["filters"]):
        if item["field"] == "agent" and table != "transactions":
            raise ValueError("reference filter does not exist on this fact")
        column = fields[item["field"]]
        values = item["value"] if item["op"] == "in" else [item["value"]]
        binds = []
        for offset, value in enumerate(values):
            name = f"v_{index}_{offset}"
            parameters[name] = value
            binds.append(":" + name)
        predicates.append(column + " IN (" + ",".join(binds) + ")")
    if predicates:
        sql += " WHERE " + " AND ".join(predicates)
    if selected_dimensions:
        sql += " GROUP BY " + ",".join(dimensions[item] for item in selected_dimensions)
    if oracle["sort"]:
        order = []
        for item in oracle["sort"]:
            field = item["field"]
            if field not in set(selected_dimensions) | set(kpis):
                raise ValueError("oracle sort is not in this widget projection")
            order.append(field + (" DESC" if item["direction"] == "desc" else " ASC"))
        sql += " ORDER BY " + ",".join(order)
    if oracle["limit"] is not None:
        sql += " LIMIT :limit"
        parameters["limit"] = oracle["limit"]
    engine = create_reader_engine(settings, scope)
    try:
        with engine.connect() as connection, connection.begin():
            connection.execute(text("SET TRANSACTION READ ONLY"))
            connection.execute(text("SET LOCAL statement_timeout='5s'"))
            records = connection.execute(text(sql), parameters).mappings().all()
        no_data = not records or all(row["fact_count"] == 0 for row in records)
        rows = [{key: str(value) if isinstance(value, (Decimal, date, datetime)) else value
                 for key, value in row.items() if key != "fact_count"} for row in records]
        return {"status": "no_data" if no_data else "ok", "rows": rows}
    finally:
        engine.dispose()


def compare_rows(actual: list[dict], expected: list[dict], kpi: str, dimension: str | None, ordered: bool) -> bool:
    fields = [kpi] + ([dimension] if dimension else [])
    projected = [{field: row.get(field) for field in fields} for row in actual]
    expected = [{field: row.get(field) for field in fields} for row in expected]
    if not ordered:
        projected.sort(key=lambda row: canonical({key: row[key] for key in fields if key != kpi}))
        expected.sort(key=lambda row: canonical({key: row[key] for key in fields if key != kpi}))
    if len(projected) != len(expected):
        return False
    for actual_row, expected_row in zip(projected, expected, strict=True):
        if dimension and actual_row[dimension] != expected_row[dimension]:
            return False
        a, e = actual_row[kpi], expected_row[kpi]
        if a is None or e is None:
            if a != e:
                return False
        elif kpi in {"transaction_count", "sold_property_count", "offer_count"}:
            if type(a) is not int or a != e:
                return False
        else:
            tolerance = Decimal("0.000000000001") if kpi == "accepted_offer_rate" else Decimal("0.005")
            if not isinstance(a, str) or not Decimal(a).is_finite() or abs(Decimal(a) - Decimal(e)) >= tolerance:
                return False
    return True


def verify_executed_queries(response: dict, settings: Settings) -> dict:
    """Check every query/metric, including queries not attached to a widget.

    This checks numerical execution of the actual structured plan. assess()
    separately checks whether that plan answers the question's oracle.
    """
    differences, checked = [], 0
    for query in (response.get("draft") or {}).get("queries", []):
        plan, identifier = query["plan"], query["id"]
        period = plan["date_range"]
        oracle = {"time": {"mode": "all_time" if period["start"] is None else "custom", **period},
                  "filters": plan["filters"], "sort": plan["sort"], "limit": plan["limit"]}
        expected = reference_rows(settings, response["scope"], oracle, {"kpis": plan["kpis"], "dimensions": plan["dimensions"]})
        actual = response.get("results", {}).get(identifier)
        checked += 1
        if not actual or actual.get("status") != expected["status"]:
            differences.append({"query_id": identifier, "field": "status"})
            continue
        names = set(plan["kpis"]) | set(plan["dimensions"])
        if any(set(row) != names for row in actual["rows"]):
            differences.append({"query_id": identifier, "field": "columns"})
            continue
        def project(rows):
            return [dict(row, _dimensions=tuple(row[name] for name in plan["dimensions"])) for row in rows]
        for kpi in plan["kpis"]:
            if not compare_rows(project(actual["rows"]), project(expected["rows"]), kpi,
                                "_dimensions" if plan["dimensions"] else None, bool(plan["sort"])):
                differences.append({"query_id": identifier, "field": kpi + ".rows"})
    return {"checked_queries": checked, "all_correct": not differences, "differences": differences}


def assess(case: dict, response: dict, http_status: int, query_count: int, settings: Settings) -> list[dict]:
    oracle, differences = case["oracle"], []
    def check(field, actual, expected):
        if actual != expected:
            differences.append({"field": field, "actual": actual, "expected": expected})
    auth = oracle["authorization"]
    if not auth["agent_allowed"]:
        check("http_status", http_status, auth["http_status"])
        check("query_count", query_count, 0)
        check("draft", response.get("draft"), None)
        check("results", response.get("results", {}), {})
        detail = response.get("detail", {})
        check("reason_code", detail.get("code") if isinstance(detail, dict) else None, oracle["reason_code"])
        for phrase in oracle["explanation_must_include"]:
            check("authorization_explanation", normalized(phrase) in normalized(detail.get("message", "") if isinstance(detail, dict) else ""), True)
        return differences
    check("http_status", http_status, 200)
    outcome = "unsupported" if oracle["outcome"] == "refusal" else oracle["outcome"]
    check("outcome", response.get("outcome"), outcome)
    check("scope", response.get("scope"), auth["session_profile"])
    if outcome != "plan":
        check("query_count", query_count, 0)
        check("draft", response.get("draft"), None)
        check("results", response.get("results", {}), {})
        if outcome == "clarification":
            actual_codes = set(response.get("reason_codes", [])) | {response.get("reason_code")}
            check("reason_codes", sorted(code for code in actual_codes if code), sorted(oracle["reason_codes"]))
            check("required_question", normalized(response.get("message", "")), normalized(oracle["required_question"]))
        else:
            check("reason_code", response.get("reason_code"), oracle["reason_code"])
            for phrase in oracle["explanation_must_include"]:
                check("explanation_contains", normalized(phrase) in normalized(response.get("message", "")), True)
        return differences
    draft = response.get("draft")
    if not draft:
        return differences + [{"field": "draft", "actual": None, "expected": "validated dashboard"}]
    queries = {query["id"]: query["plan"] for query in draft["queries"]}
    check("kpis", sorted({kpi for plan in queries.values() for kpi in plan["kpis"]}), sorted(oracle["kpis"]))
    check("dimensions", sorted({dimension for plan in queries.values() for dimension in plan["dimensions"]}), sorted(oracle["dimensions"]))
    signature = lambda widget: (widget["kpi"], widget.get("dimension"), widget["chart"])
    actual_widgets = Counter(signature(widget) for widget in draft["widgets"])
    expected_widgets = Counter(signature(widget) for widget in oracle["widgets"])
    check("widgets", sorted(map(str, actual_widgets.elements())), sorted(map(str, expected_widgets.elements())))
    for query_id, plan in queries.items():
        check(f"{query_id}.filters", sorted(map(canonical, plan["filters"])), sorted(map(canonical, oracle["filters"])))
        expected_time = {"start": None, "end_exclusive": None} if oracle["time"]["mode"] == "all_time" else {
            "start": oracle["time"]["start"], "end_exclusive": oracle["time"]["end_exclusive"]}
        check(f"{query_id}.time", plan["date_range"], expected_time)
        check(f"{query_id}.sort", plan["sort"], oracle["sort"])
        check(f"{query_id}.limit", plan["limit"], oracle["limit"])
    # Verify every matched widget against independent oracle SQL even if plan
    # comparison already failed. Mismatched widgets remain failures above.
    for widget in draft["widgets"]:
        matches = [item for item in oracle["widgets"] if signature(item) == signature(widget)]
        if not matches:
            continue
        result = response.get("results", {}).get(widget["query_id"])
        if not result:
            differences.append({"field": "query_result", "actual": None, "expected": widget["id"]})
            continue
        expected = reference_rows(settings, auth["session_profile"], oracle, matches[0])
        check(f"{widget['id']}.data_status", result["status"], expected["status"])
        if not compare_rows(result["rows"], expected["rows"], widget["kpi"], widget.get("dimension"), bool(oracle["sort"])):
            differences.append({"field": f"{widget['id']}.rows", "actual": result["rows"], "expected": expected["rows"]})
    return differences


def evaluate(settings: Settings, output: Path, *, phase: str = "sales") -> dict:
    if settings.runtime_database_url is None or "test" not in (make_url(settings.runtime_database_url.get_secret_value()).database or "").lower():
        raise RuntimeError("evaluation requires an isolated runtime database whose name contains 'test'")
    planner = create_planner(settings)  # missing live config fails before users/calls
    engine = create_runtime_engine(settings)
    executor = QueryExecutor(settings)
    service = ConversationService(engine, settings, executor, planner)
    suffix, password = uuid.uuid4().hex[:12], secrets.token_urlsafe(24)
    actors, records = {}, []
    corpus = json.loads(CORPUS.read_text(encoding="utf-8"))
    from app.main import app
    previous = app.dependency_overrides.copy()
    def db_session():
        with Session(engine) as database:
            yield database
    app.dependency_overrides.update({database_session: db_session, get_settings: lambda: settings, get_conversation_service: lambda: service})
    try:
        with Session(engine) as database, database.begin():
            for role, scope in {(case["oracle"]["authorization"]["actor_role"], case["oracle"]["authorization"]["session_profile"]) for case in corpus["cases"]}:
                user = User(username=f"evaluation_{suffix}_{role}_{scope}", password_hash=hash_password(password), role=role, scope_profile=scope)
                database.add(user)
                database.flush()
                actors[(role, scope)] = (user.id, user.username)
        with TestClient(app) as client:
            headers = {"Origin": settings.frontend_origin.rstrip("/")}
            for case in corpus["cases"]:
                result = {"case_id": case["id"], "category": case["category"], "mode": settings.llm_mode,
                          "provider_model_config": {"provider": settings.llm_provider, "model": settings.llm_model if settings.llm_mode == "live" else "mock",
                                                    "reasoning_effort": settings.llm_reasoning_effort},
                          "status": "NOT_RUN", "plan_diff": [], "sql_result": None, "latency_ms": 0}
                if phase == "sales" and case["phase_tag"] == "phase_4":
                    result.update(status="BLOCKED", reason="phase_4_catalog_not_enabled")
                    records.append(result)
                    continue
                auth = case["oracle"]["authorization"]
                user_id, username = actors[(auth["actor_role"], auth["session_profile"])]
                assert client.post("/api/auth/login", headers=headers, json={"username": username, "password": password}).status_code == 200
                start = perf_counter()
                created = client.post("/api/conversations", headers=headers, json={})
                if created.status_code == 201:
                    response = client.post(f"/api/conversations/{created.json()['id']}/messages", headers=headers, json={"message": case["prompt"]})
                else:
                    response = created
                actual = response.json()
                with Session(engine) as database:
                    usage = database.scalar(select(UsageRecord).where(UsageRecord.run_id == uuid.UUID(actual["run_id"]))) if actual.get("run_id") else None
                    query_count = usage.query_count if usage else 0
                    result["usage"] = {"llm_calls": usage.llm_calls, "input_tokens": usage.input_tokens, "output_tokens": usage.output_tokens,
                                       "estimated_cost_usd": str(usage.estimated_cost_usd)} if usage else None
                result["latency_ms"] = round((perf_counter() - start) * 1000)
                result["actual_outcome"] = actual.get("outcome", "authorization_denied" if response.status_code == 403 else "error")
                result["actual_message"] = actual.get("message") or (actual.get("detail", {}).get("message") if isinstance(actual.get("detail"), dict) else None)
                result["actual_reason_code"] = actual.get("reason_code") or (actual.get("detail", {}).get("code") if isinstance(actual.get("detail"), dict) else None)
                result["actual_reason_codes"] = actual.get("reason_codes", [])
                result["actual_error_code"] = public_error_code(actual)
                result["scope_authorization"] = {"actor_role": auth["actor_role"], "session_profile": auth["session_profile"],
                                                  "response_scope": actual.get("scope"), "http_status": response.status_code, "query_count": query_count}
                result["actual_plan"] = actual.get("draft")
                result["sql_result"] = actual.get("results")
                try:
                    differences = assess(case, actual, response.status_code, query_count, settings)
                    result["execution_accuracy"] = verify_executed_queries(actual, settings)
                    if not result["execution_accuracy"]["all_correct"]:
                        differences.append({"field": "execution_accuracy", **result["execution_accuracy"]})
                    result.update(status="FAIL" if differences else "PASS", plan_diff=differences)
                except Exception as error:
                    result.update(status="BLOCKED", reason="oracle_comparison_failed", error_type=type(error).__name__)
                records.append(result)
                print(json.dumps({"case_id": result["case_id"], "mode": settings.llm_mode,
                                  "status": result["status"], "outcome": result["actual_outcome"],
                                  "latency_ms": result["latency_ms"]}), flush=True)
        report = {"mode": settings.llm_mode, "phase": phase, "created_at": datetime.now(UTC).isoformat(),
                  "catalog_version": corpus["catalog_version"], "results": records,
                  "summary": dict(Counter(record["status"] for record in records)),
                  "is_live_quality_evidence": settings.llm_mode == "live",
                  "text_grading": "strict normalized oracle wording; paraphrases may require manual review"}
        passed_valid = sum(record["status"] == "PASS" and record["category"] == "valid" for record in records)
        passed_other = sum(record["status"] == "PASS" and record["category"] != "valid" for record in records)
        report["release_gate_passed"] = (settings.llm_mode == "live" and phase == "full"
                                         and passed_valid >= 18 and passed_other == 10
                                         and not report["summary"].get("BLOCKED")
                                         and all(record.get("execution_accuracy", {}).get("all_correct", False) for record in records))
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return report
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous)
        executor.close()
        ids = [actor[0] for actor in actors.values()]
        if ids:
            with Session(engine) as database, database.begin():
                database.execute(delete(AuditEvent).where(AuditEvent.actor_user_id.in_(ids)))
                database.execute(delete(Conversation).where(Conversation.owner_user_id.in_(ids)))
                database.execute(delete(UsageRecord).where(UsageRecord.user_id.in_(ids)))
                database.execute(delete(User).where(User.id.in_(ids)))
        engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", required=True, choices=("mock", "live"))
    parser.add_argument("--phase", choices=("sales", "full"), default="sales")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    settings = get_settings()
    if args.mode != settings.llm_mode:
        parser.error("--mode must match configured LLM_MODE")
    report = evaluate(settings, args.output, phase=args.phase)
    print(json.dumps({"mode": report["mode"], "summary": report["summary"]}))
    if any(record["status"] == "BLOCKED" and record.get("reason") != "phase_4_catalog_not_enabled" for record in report["results"]):
        return 2
    if report["summary"].get("FAIL"):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
