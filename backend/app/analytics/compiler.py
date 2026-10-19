"""Compile a validated AnalysisPlan to one parameterized PostgreSQL SELECT."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import TypeAlias

from app.analytics.catalog import FILTER_COLUMNS, KPI_CATALOG, validate_plan_semantics
from app.analytics.validator import validate_compiled_sql
from app.schemas.analysis import AnalysisPlan, DimensionId, KPIId


BindValue: TypeAlias = str | int | date


@dataclass(frozen=True)
class CompiledQuery:
    sql: str
    parameters: dict[str, BindValue]


_KPI_SQL: dict[KPIId, str] = {
    "sales_value": "SUM(s.sale_price)",
    "transaction_count": "COUNT(s.transaction_id)",
    "average_sale_value": "SUM(s.sale_price) / NULLIF(COUNT(s.transaction_id), 0)",
    "sold_property_count": "COUNT(DISTINCT s.property_id)",
    "offer_count": "COUNT(s.offer_id)",
    "accepted_offer_rate": "COUNT(s.offer_id) FILTER (WHERE s.status = 'accepted')::numeric / NULLIF(COUNT(s.offer_id), 0)",
    "repair_cost": "SUM(s.cost)",
}

_DIMENSION_SQL: dict[DimensionId, str] = {
    "day": "s.transaction_date",
    "month": "date_trunc('month', s.transaction_date)::date",
    "quarter": "date_trunc('quarter', s.transaction_date)::date",
    "year": "date_trunc('year', s.transaction_date)::date",
    "state": "s.state",
    "city": "s.city",
    "property_type": "s.property_type",
    "agent": "s.agent_id",
}

_FILTER_SQL = {field: f"s.{column}" for field, column in FILTER_COLUMNS.items()}


def compile_analysis_plan(plan: AnalysisPlan, *, include_fact_count: bool = False) -> CompiledQuery:
    """Compile a Pydantic-validated plan using only internal SQL mappings."""

    if not isinstance(plan, AnalysisPlan):
        raise TypeError("compiler accepts AnalysisPlan instances only")
    # Re-run schema validation so a model produced with Pydantic's low-level
    # model_construct() escape hatch cannot bypass the compiler boundary.
    # A forged model_construct may contain invalid nested values. Serializer
    # warnings echo those values; validate silently, then let callers return
    # their sanitized validation error instead of logging untrusted contents.
    plan = AnalysisPlan.model_validate(plan.model_dump(mode="python", warnings=False))
    validate_plan_semantics(plan)
    fact = KPI_CATALOG[plan.kpis[0]]
    dimension_sql = {key: value.replace("transaction_date", fact.date_field) for key, value in _DIMENSION_SQL.items()}
    projections = [f"{dimension_sql[item]} AS {item}" for item in plan.dimensions]
    projections.extend(f"{_KPI_SQL[item]} AS {item}" for item in plan.kpis)
    if include_fact_count:
        key = {"analytics.sales": "transaction_id", "analytics.offers": "offer_id", "analytics.repairs": "repair_id"}[fact.view]
        projections.append(f"COUNT(s.{key}) AS _fact_count")
    sql_parts = ["SELECT", "    " + ",\n    ".join(projections), f"FROM {fact.view} AS s"]

    parameters: dict[str, BindValue] = {}
    predicates: list[str] = []
    if plan.date_range.start is not None:
        predicates.extend(
            (
                f"s.{fact.date_field} >= :start_date",
                f"s.{fact.date_field} < :end_date_exclusive",
            )
        )
        parameters["start_date"] = plan.date_range.start
        parameters["end_date_exclusive"] = plan.date_range.end_exclusive  # type: ignore[assignment]

    for filter_index, filter_spec in enumerate(plan.filters):
        column = _FILTER_SQL[filter_spec.field]
        if filter_spec.op == "eq":
            parameter = f"filter_{filter_index}"
            predicates.append(f"{column} = :{parameter}")
            parameters[parameter] = filter_spec.value  # type: ignore[assignment]
        else:
            values = filter_spec.value
            if not isinstance(values, list):  # defensive: FilterSpec already proves this
                raise ValueError("in filter requires a list")
            parameter_names = [f"filter_{filter_index}_{item_index}" for item_index in range(len(values))]
            placeholders = ", ".join(f":{name}" for name in parameter_names)
            predicates.append(f"{column} IN ({placeholders})")
            parameters.update(zip(parameter_names, values, strict=True))

    if predicates:
        sql_parts.extend(("WHERE", "    " + "\n    AND ".join(predicates)))
    if plan.dimensions:
        sql_parts.extend(("GROUP BY", "    " + ",\n    ".join(dimension_sql[item] for item in plan.dimensions)))
    if plan.sort:
        sql_parts.extend(
            (
                "ORDER BY",
                "    " + ",\n    ".join(f"{item.field} {item.direction.upper()}" for item in plan.sort),
            )
        )
    if plan.limit is not None:
        sql_parts.append("LIMIT :limit")
        parameters["limit"] = plan.limit

    sql = "\n".join(sql_parts)
    validate_compiled_sql(sql)
    return CompiledQuery(sql=sql, parameters=parameters)
