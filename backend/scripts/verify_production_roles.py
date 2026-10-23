"""Read-only verification of the private production runtime and reader logins."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from sqlalchemy import text

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.config import get_settings
from app.db.connections import create_reader_engine, create_runtime_engine


EXPECTED_COUNTS = {"ALL": 1000, "CA": 135, "TX": 132}


def main() -> int:
    settings = get_settings()
    if settings.app_env != "production":
        raise RuntimeError("production role verifier requires APP_ENV=production")
    result: dict[str, object] = {"runtime_valid": False, "reader_valid": {}}
    runtime = create_runtime_engine(settings)
    try:
        with runtime.connect() as connection:
            result["runtime_valid"] = connection.scalar(text("SELECT current_user")) == "dashboard_app_runtime"
    finally:
        runtime.dispose()
    for scope, expected in EXPECTED_COUNTS.items():
        reader = create_reader_engine(settings, scope)
        try:
            with reader.connect() as connection:
                count = connection.scalar(text("SELECT count(*) FROM analytics.sales"))
                states = [row[0] for row in connection.execute(
                    text("SELECT DISTINCT state FROM public.properties ORDER BY state")
                )]
                result["reader_valid"][scope] = (
                    count == expected and (scope == "ALL" or states == [scope])
                )
        finally:
            reader.dispose()
    result["all_checks_passed"] = result["runtime_valid"] and all(result["reader_valid"].values())
    print(json.dumps(result))
    return 0 if result["all_checks_passed"] else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"Production role verification failed ({type(error).__name__}); secrets suppressed")
        raise SystemExit(1) from None
