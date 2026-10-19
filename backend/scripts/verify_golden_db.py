"""Run independent golden SQL against an existing DB in a read-only transaction.

Uses AUDIT_DATABASE_URL from backend/.env; never connects via runtime or reader
credentials and never logs the DSN or database rows.
"""

from __future__ import annotations

from decimal import Decimal
import json
from pathlib import Path

import psycopg
from psycopg import sql as psql

from audit_db import ROOT, configured_audit_dsn, isolated_libpq_environment


FIXTURES = ROOT / "backend" / "tests" / "fixtures"
FIELDS = {
    "sales": ("sales_value", "transaction_count", "average_sale_value", "sold_property_count"),
    "offers": ("offer_count", "accepted_offer_count", "accepted_offer_rate"),
    "repairs": ("repair_row_count", "repair_cost"),
}
STATEMENTS = {"sales": "golden_sales", "offers": "golden_offers", "repairs": "golden_repairs"}


def matches(actual: object, expected: object, field: str) -> bool:
    if actual is None or expected is None:
        return actual is None and expected is None
    if field in {"average_sale_value", "accepted_offer_rate"}:
        return abs(Decimal(str(actual)) - Decimal(str(expected))) <= Decimal("0.000000000001")
    if isinstance(expected, str):
        return Decimal(str(actual)) == Decimal(expected)
    return actual == expected


def main() -> int:
    dsn = configured_audit_dsn(None)
    if not dsn:
        print("Golden DB verification unavailable: AUDIT_DATABASE_URL is missing")
        return 2
    cases = json.loads((FIXTURES / "golden_results.json").read_text(encoding="utf-8"))["cases"]
    sql = (FIXTURES / "golden_queries.sql").read_text(encoding="utf-8")
    results: list[dict[str, object]] = []
    try:
        with psycopg.connect(dsn, connect_timeout=5, autocommit=True) as conn:
            conn.execute("BEGIN READ ONLY")
            if conn.execute("SHOW transaction_read_only").fetchone()[0] != "on":
                raise RuntimeError("read-only transaction was not confirmed")
            conn.execute("SET LOCAL statement_timeout = '5s'")
            # The remaining statements are dimension examples; KPI baseline
            # verification uses the three prepared fact statements only.
            fact_sql = sql[sql.index("PREPARE golden_sales("):sql.index("-- Dimension reference queries")]
            for statement in fact_sql.split(";"):
                if statement.strip():
                    conn.execute(statement)
            for case in cases:
                mismatches = []
                args = (case["scope"], case["start_date"], case["end_date_exclusive"])
                for fact, name in STATEMENTS.items():
                    expected = case.get("expected", {}).get(fact, case.get(f"expected_{fact}"))
                    if expected is None:
                        continue
                    row = conn.execute(
                        psql.SQL("EXECUTE {} ({}, {}, {})").format(
                            psql.Identifier(name), *(psql.Literal(value) for value in args)
                        )
                    ).fetchone()
                    for index, field in enumerate(FIELDS[fact]):
                        if not matches(row[index], expected[field], field):
                            mismatches.append(f"{fact}.{field}")
                    count_field = FIELDS[fact][1] if fact == "sales" else FIELDS[fact][0]
                    status = "no_data" if row[FIELDS[fact].index(count_field)] == 0 else "ok"
                    if status != expected["status"]:
                        mismatches.append(f"{fact}.status")
                results.append({"case_id": case["id"], "pass": not mismatches, "mismatches": mismatches})
            conn.execute("ROLLBACK")
    except Exception as exc:
        print(f"Golden DB verification failed: {type(exc).__name__}; connection or SQL requires review")
        return 2
    print(json.dumps({"case_count": len(results), "passed": sum(item["pass"] for item in results),
                      "failed_cases": [item for item in results if not item["pass"]]}, ensure_ascii=False))
    return 0 if all(item["pass"] for item in results) else 1


if __name__ == "__main__":
    with isolated_libpq_environment():
        raise SystemExit(main())
