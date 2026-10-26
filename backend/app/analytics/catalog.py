"""Versioned phase-2 KPI catalog and semantic validation."""

from __future__ import annotations

from dataclasses import dataclass

from app.schemas.analysis import AnalysisPlan, SUPPORTED_CATALOG_VERSIONS, LEGACY_CATALOG_VERSION, DimensionId, KPIId


@dataclass(frozen=True)
class KPI:
    id: KPIId
    label_vi: str
    description_vi: str
    unit: str
    grain: str
    date_field: str
    view: str
    sql_expression: str
    source_columns: tuple[str, ...]
    dimensions: tuple[DimensionId, ...]


SALES_DIMENSIONS: tuple[DimensionId, ...] = (
    "day",
    "month",
    "quarter",
    "year",
    "state",
    "city",
    "property_type",
    "agent",
)

KPI_CATALOG: dict[KPIId, KPI] = {
    "sales_value": KPI(
        "sales_value", "Giá trị giao dịch", "Tổng giá trị giao dịch", "dataset_currency",
        "transaction", "transaction_date", "analytics.sales", "SUM(sale_price)",
        ("sale_price",), SALES_DIMENSIONS,
    ),
    "transaction_count": KPI(
        "transaction_count", "Số giao dịch", "Số giao dịch", "transaction",
        "transaction", "transaction_date", "analytics.sales", "COUNT(transaction_id)",
        ("transaction_id",), SALES_DIMENSIONS,
    ),
    "average_sale_value": KPI(
        "average_sale_value", "Giá trị giao dịch trung bình", "Giá trị trung bình trên giao dịch",
        "dataset_currency_per_transaction", "transaction", "transaction_date", "analytics.sales",
        "SUM(sale_price) / NULLIF(COUNT(transaction_id), 0)",
        ("sale_price", "transaction_id"), SALES_DIMENSIONS,
    ),
    "sold_property_count": KPI(
        "sold_property_count", "Số BĐS có giao dịch", "Số bất động sản khác nhau có giao dịch",
        "property", "filtered_transaction_set", "transaction_date", "analytics.sales",
        "COUNT(DISTINCT property_id)", ("property_id",), SALES_DIMENSIONS,
    ),
    "offer_count": KPI(
        "offer_count", "Số đề nghị", "Đếm mỗi đề nghị mua bất động sản theo offer_date, mọi trạng thái", "offer",
        "offer", "offer_date", "analytics.offers", "COUNT(offer_id)",
        ("offer_id",), SALES_DIMENSIONS[:-1],
    ),
    "accepted_offer_rate": KPI(
        "accepted_offer_rate", "Tỷ lệ đề nghị được chấp nhận", "Đề nghị accepted trên tất cả đề nghị", "ratio",
        "offer", "offer_date", "analytics.offers",
        "COUNT(offer_id) FILTER (WHERE status = 'accepted')::numeric / NULLIF(COUNT(offer_id), 0)",
        ("offer_id", "status"), SALES_DIMENSIONS[:-1],
    ),
    "repair_cost": KPI(
        "repair_cost", "Chi phí sửa chữa", "Tổng chi phí theo ngày sửa chữa", "dataset_currency",
        "repair", "repair_date", "analytics.repairs", "SUM(cost)",
        ("cost",), SALES_DIMENSIONS[:-1],
    ),
}

DIMENSION_COLUMNS: dict[DimensionId, str] = {
    "day": "transaction_date",
    "month": "date_trunc('month', transaction_date)",
    "quarter": "date_trunc('quarter', transaction_date)",
    "year": "date_trunc('year', transaction_date)",
    "state": "state",
    "city": "city",
    "property_type": "property_type",
    "agent": "agent_id",
}
FILTER_COLUMNS = {"state": "state", "city": "city", "property_type": "property_type", "agent": "agent_id"}


def validate_plan_semantics(plan: AnalysisPlan) -> None:
    if plan.catalog_version not in SUPPORTED_CATALOG_VERSIONS:
        raise ValueError("catalog version mismatch")
    if plan.catalog_version == LEGACY_CATALOG_VERSION and set(plan.kpis) - {
        "sales_value", "transaction_count", "average_sale_value", "sold_property_count",
    }:
        raise ValueError("legacy catalog supports sales KPIs only")
    if len({KPI_CATALOG[kpi_id].view for kpi_id in plan.kpis}) != 1:
        raise ValueError("each query must use exactly one fact; split mixed facts into separate queries")
    for kpi_id in plan.kpis:
        allowed = set(KPI_CATALOG[kpi_id].dimensions)
        unsupported = set(plan.dimensions) - allowed
        if unsupported:
            raise ValueError(f"unsupported dimensions for {kpi_id}: {sorted(unsupported)}")
    for filter_spec in plan.filters:
        if filter_spec.field not in FILTER_COLUMNS:
            raise ValueError(f"unsupported filter field: {filter_spec.field}")
        if any(filter_spec.field not in KPI_CATALOG[kpi_id].dimensions for kpi_id in plan.kpis):
            raise ValueError("filter is not supported by this fact")
    selected_fields = set(plan.kpis) | set(plan.dimensions)
    for sort_spec in plan.sort:
        if sort_spec.field not in selected_fields:
            raise ValueError("sort field must be selected as a KPI or dimension")
