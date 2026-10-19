"""Execute compiler-owned plans with scoped readers and bounded DB resources."""

from __future__ import annotations

import logging
from decimal import Decimal, InvalidOperation
from functools import lru_cache
from threading import Lock
from time import perf_counter
from typing import Any

from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError, SQLAlchemyError

from app.analytics.catalog import KPI_CATALOG
from app.analytics.compiler import compile_analysis_plan
from app.config import Settings, get_settings
from app.db.connections import READER_URL_FIELDS, ReaderScope, create_reader_engine
from app.schemas.analysis import AnalysisPlan
from app.schemas.dashboard import QueryResult, ResultColumn


logger = logging.getLogger(__name__)
_MESSAGES = {
    "timeout": "Truy vấn vượt thời gian cho phép. Hãy thu hẹp bộ lọc.",
    "row_limit": "Kết quả vượt giới hạn dòng. Hãy giảm độ chi tiết hoặc thu hẹp bộ lọc.",
    "cost_limit": "Chi phí kế hoạch truy vấn vượt ngưỡng. Hãy thu hẹp bộ lọc.",
    "database_error": "Không thể truy vấn dữ liệu. Vui lòng thử lại.",
    "configuration_error": "Kết nối dữ liệu cho phạm vi này chưa sẵn sàng.",
}


class QueryRejected(Exception):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def explain_cost(document: Any) -> Decimal:
    """Read only PostgreSQL's root total cost; malformed plans fail closed."""
    try:
        if not isinstance(document, list) or len(document) != 1:
            raise ValueError("expected one JSON plan")
        value = document[0]["Plan"]["Total Cost"]
        if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
            raise ValueError("expected numeric planner cost")
        cost = Decimal(str(value))
        if not cost.is_finite() or cost < 0:
            raise ValueError("invalid planner cost")
        return cost
    except (KeyError, TypeError, ValueError, InvalidOperation) as error:
        raise QueryRejected("database_error") from error


def result_columns(plan: AnalysisPlan) -> list[ResultColumn]:
    columns = [
        ResultColumn(
            name=name,
            type="date" if name in {"day", "month", "quarter", "year"}
            else "integer" if name == "agent" else "string",
        )
        for name in plan.dimensions
    ]
    columns.extend(
        ResultColumn(
            name=name,
            type="integer" if name in {"transaction_count", "sold_property_count", "offer_count"} else "decimal",
            unit=KPI_CATALOG[name].unit,
        )
        for name in plan.kpis
    )
    return columns


class QueryExecutor:
    """One reader pool per server-authorized scope; no shared result cache.

    The caller must obtain scope from authenticated server identity. There is
    deliberately no SQL entry point or default scope in this executor.
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._engines: dict[ReaderScope, Engine] = {}
        self._lock = Lock()

    def _engine(self, scope: ReaderScope) -> Engine:
        with self._lock:
            if scope not in self._engines:
                self._engines[scope] = create_reader_engine(self.settings, scope)
            return self._engines[scope]

    def close(self) -> None:
        with self._lock:
            for engine in self._engines.values():
                engine.dispose()
            self._engines.clear()

    def execute(self, plan: AnalysisPlan, scope: ReaderScope) -> QueryResult:
        started = perf_counter()
        if scope not in READER_URL_FIELDS:
            raise ValueError("reader scope must be one of ALL, CA, or TX")
        # Validate even model_construct input before inspecting or connecting.
        compile_analysis_plan(plan)
        plan = AnalysisPlan.model_validate(plan.model_dump(mode="python"))
        columns = result_columns(plan)
        # A count in the same aggregate distinguishes no rows from real zeros
        # and all-null prices, even if the caller requested only SUM/AVG.
        compiled = compile_analysis_plan(plan, include_fact_count=True)
        error_code = None
        rows: list[dict[str, Any]] = []
        no_data = False
        try:
            with self._engine(scope).connect() as connection:
                with connection.begin():
                    connection.execute(text("SET TRANSACTION READ ONLY"))
                    connection.execute(
                        text("SELECT set_config('statement_timeout', :timeout, true)"),
                        {"timeout": f"{self.settings.analytics_statement_timeout_ms}ms"},
                    )
                    document = connection.execute(
                        text("EXPLAIN (FORMAT JSON) " + compiled.sql), compiled.parameters
                    ).scalar_one()
                    if explain_cost(document) > Decimal(str(self.settings.analytics_max_query_cost)):
                        raise QueryRejected("cost_limit")
                    # Server cursor bounds client buffering. Fetch one extra
                    # row to detect overflow instead of returning partial data.
                    with connection.execution_options(
                        stream_results=True, max_row_buffer=self.settings.analytics_max_rows + 1
                    ).execute(text(compiled.sql), compiled.parameters) as result:
                        records = result.mappings().fetchmany(self.settings.analytics_max_rows + 1)
                    if len(records) > self.settings.analytics_max_rows:
                        raise QueryRejected("row_limit")
                    no_data = not records or all(row["_fact_count"] == 0 for row in records)
                    rows = [{column.name: row[column.name] for column in columns} for row in records]
        except QueryRejected as error:
            error_code = error.code
        except DBAPIError as error:
            sqlstate = getattr(error.orig, "sqlstate", None) or getattr(error.orig, "pgcode", None)
            error_code = "timeout" if sqlstate == "57014" else "database_error"
        except SQLAlchemyError:
            error_code = "database_error"
        except RuntimeError:
            # Missing URLs and rejected reader principals are intentionally not
            # echoed: DB errors can contain credentials, SQL or filter values.
            error_code = "configuration_error"
        elapsed = max(0, round((perf_counter() - started) * 1000))
        logger.info("analytics_query", extra={"scope": scope, "latency_ms": elapsed, "error_code": error_code})
        if error_code is not None:
            return QueryResult(
                columns=columns, rows=[], latency_ms=elapsed, status="error",
                error_code=error_code, warnings=[_MESSAGES[error_code]],
            )
        return QueryResult(
            columns=columns, rows=rows, latency_ms=elapsed,
            status="no_data" if no_data else "ok",
        )


@lru_cache
def get_query_executor() -> QueryExecutor:
    return QueryExecutor(get_settings())
