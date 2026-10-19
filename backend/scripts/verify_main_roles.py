"""Read-only end-to-end verification of provisioned local PostgreSQL logins.

Uses the ignored credential artifact, never prints URLs or query rows. This is
not a substitute for the disposable-cluster security regression suite.
"""

from __future__ import annotations

import json
from pathlib import Path
import sys

from dotenv import dotenv_values
from pydantic import ValidationError
from sqlalchemy import text

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.config import Settings
from app.db.connections import create_reader_engine, create_runtime_engine


CREDENTIALS = BACKEND / ".runtime" / "local-postgres-credentials.env"
EXPECTED_COUNTS = {"ALL": 1000, "CA": 135, "TX": 132}


def main() -> int:
    if not CREDENTIALS.is_file():
        print("role verification unavailable: ignored credential artifact is missing")
        return 2
    values = dotenv_values(CREDENTIALS, interpolate=False)
    names = ("RUNTIME_DATABASE_URL", "READER_ALL_DATABASE_URL",
             "READER_CA_DATABASE_URL", "READER_TX_DATABASE_URL")
    if any(not isinstance(values.get(name), str) or not values[name] for name in names):
        print("role verification unavailable: one or more local URLs are missing")
        return 2
    settings = Settings(_env_file=None, app_env="development", **{
        name.lower(): values[name] for name in names
    })
    result = {"runtime_principal_valid": False, "reader_scope_valid": {},
              "sales_transaction_count": {}, "all_checks_passed": False}
    runtime = create_runtime_engine(settings)
    try:
        with runtime.connect() as connection:
            principal = connection.scalar(text("SELECT current_user"))
            result["runtime_principal_valid"] = principal == "dashboard_app_runtime"
    finally:
        runtime.dispose()
    for scope in EXPECTED_COUNTS:
        reader = create_reader_engine(settings, scope)
        try:
            with reader.connect() as connection:
                count = connection.scalar(text("SELECT count(*) FROM analytics.sales"))
                states = [row[0] for row in connection.execute(
                    text("SELECT DISTINCT state FROM public.properties ORDER BY state")
                )]
                result["sales_transaction_count"][scope] = count
                result["reader_scope_valid"][scope] = (
                    count == EXPECTED_COUNTS[scope]
                    and (scope == "ALL" or states == [scope])
                )
        finally:
            reader.dispose()
    result["all_checks_passed"] = (result["runtime_principal_valid"]
                                   and all(result["reader_scope_valid"].values()))
    print(json.dumps(result))
    return 0 if result["all_checks_passed"] else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ValidationError as error:
        print(json.dumps({"configuration_error_fields": [
            {"field": str(item["loc"][0]), "type": item["type"]}
            for item in error.errors(include_url=False)
        ]}))
        raise SystemExit(1) from None
    except Exception as error:
        print(f"role verification failed ({type(error).__name__}); credential and database details suppressed")
        raise SystemExit(1) from None
