"""Dashboard, widget, and precision-safe query result contracts."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from datetime import datetime
from uuid import UUID
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .analysis import AnalysisPlan, CatalogVersion, DateRange, DimensionId, FilterSpec, KPIId, SCHEMA_VERSION


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class QuerySpec(StrictModel):
    id: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    plan: AnalysisPlan


class WidgetSpec(StrictModel):
    id: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")
    title: str = Field(min_length=1, max_length=160)
    chart: Literal["card", "line", "bar", "horizontal_bar", "pie", "donut", "table"]
    query_id: str
    kpi: KPIId
    dimension: DimensionId | None = None


class LayoutItem(StrictModel):
    widget_id: str
    x: int = Field(ge=0, le=11)
    y: int = Field(ge=0)
    width: int = Field(ge=1, le=12)
    height: int = Field(ge=1, le=12)


class DashboardSpec(StrictModel):
    schema_version: Literal[SCHEMA_VERSION]
    catalog_version: CatalogVersion
    title: str = Field(min_length=1, max_length=255)
    queries: Annotated[list[QuerySpec], Field(min_length=1, max_length=6)]
    widgets: Annotated[list[WidgetSpec], Field(min_length=1, max_length=8)]
    layout: Annotated[list[LayoutItem], Field(min_length=1, max_length=8)]

    @model_validator(mode="after")
    def references_are_valid(self) -> "DashboardSpec":
        if any(query.plan.catalog_version != self.catalog_version for query in self.queries):
            raise ValueError("dashboard and query catalog versions must match")
        queries = {query.id: query.plan for query in self.queries}
        if len(queries) != len(self.queries):
            raise ValueError("query ids must be unique")
        if len({widget.id for widget in self.widgets}) != len(self.widgets):
            raise ValueError("widget ids must be unique")
        layout_ids = [item.widget_id for item in self.layout]
        if len(set(layout_ids)) != len(layout_ids) or set(layout_ids) != {
            widget.id for widget in self.widgets
        }:
            raise ValueError("layout must reference each widget exactly once")
        for index, item in enumerate(self.layout):
            if item.x + item.width > 12:
                raise ValueError("layout must fit the 12-column grid")
            for other in self.layout[:index]:
                if (item.x < other.x + other.width and other.x < item.x + item.width
                        and item.y < other.y + other.height and other.y < item.y + item.height):
                    raise ValueError("widget layout must not overlap")
        for widget in self.widgets:
            plan = queries.get(widget.query_id)
            if plan is None or widget.kpi not in plan.kpis:
                raise ValueError("widget must reference a query containing its KPI")
            if widget.dimension is not None and widget.dimension not in plan.dimensions:
                raise ValueError("widget dimension must be present in its query")
            if widget.chart == "card" and (plan.dimensions or widget.dimension is not None):
                raise ValueError("card must use an ungrouped query")
            if widget.chart not in {"card", "table"} and plan.dimensions != [widget.dimension]:
                raise ValueError("chart requires exactly its declared dimension")
        return self


class ResultColumn(StrictModel):
    name: str
    type: Literal["string", "integer", "decimal", "date", "datetime"]
    unit: str | None = None


class QueryResult(StrictModel):
    columns: list[ResultColumn]
    rows: list[dict[str, Any]]
    latency_ms: int = Field(ge=0)
    status: Literal["ok", "no_data", "error"]
    warnings: list[str] = Field(default_factory=list)
    error_code: Literal["timeout", "row_limit", "cost_limit", "database_error", "configuration_error"] | None = None

    @field_validator("rows", mode="before")
    @classmethod
    def encode_decimal_as_exact_string(cls, rows: Any) -> Any:
        if not isinstance(rows, list):
            return rows
        return [
            {key: format(value, "f") if isinstance(value, Decimal) else value for key, value in row.items()}
            for row in rows
        ]

    @model_validator(mode="after")
    def validate_shape_and_decimal_encoding(self) -> "QueryResult":
        column_names = {column.name for column in self.columns}
        if len(column_names) != len(self.columns):
            raise ValueError("result column names must be unique")
        decimal_names = {column.name for column in self.columns if column.type == "decimal"}
        for row in self.rows:
            if set(row) != column_names:
                raise ValueError("each row must exactly match result columns")
            for name in decimal_names:
                value = row[name]
                if value is not None:
                    if not isinstance(value, str):
                        raise ValueError("decimal cells must use base-10 JSON strings")
                    try:
                        if not Decimal(value).is_finite():
                            raise ValueError("decimal cells must be finite")
                    except InvalidOperation as error:
                        raise ValueError("invalid decimal cell") from error
        return self


class DashboardSummary(StrictModel):
    id: UUID
    title: str
    version_number: int
    approved_at: datetime


class DashboardDetail(DashboardSummary):
    spec: DashboardSpec
    sql: dict[str, str] | None = None
    can_edit: bool = False


class DashboardQueryRequest(StrictModel):
    filters: Annotated[list[FilterSpec], Field(max_length=12)] = Field(default_factory=list)
    date_range: DateRange | None = None


class DashboardQueryResponse(StrictModel):
    dashboard_id: UUID
    version_number: int
    scope: Literal["ALL", "CA", "TX"]
    results: dict[str, QueryResult]
