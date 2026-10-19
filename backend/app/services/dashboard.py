"""Approved dashboard reads and scope-aware re-execution; no publication API."""

from uuid import UUID

from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.analytics.compiler import compile_analysis_plan
from app.analytics.executor import QueryExecutor, result_columns
from app.db.connections import ReaderScope
from app.db.models import Dashboard, DashboardVersion
from app.schemas.analysis import AnalysisPlan, SUPPORTED_CATALOG_VERSIONS, SCHEMA_VERSION
from app.schemas.dashboard import (
    DashboardDetail, DashboardQueryRequest, DashboardQueryResponse,
    DashboardSpec, DashboardSummary, QueryResult,
)


def published_statement():
    return select(Dashboard, DashboardVersion).join(
        DashboardVersion,
        (Dashboard.current_version_id == DashboardVersion.id)
        & (Dashboard.id == DashboardVersion.dashboard_id),
    )


def load_published(database: Session, dashboard_id: UUID):
    record = database.execute(published_statement().where(Dashboard.id == dashboard_id)).one_or_none()
    if record is None:
        raise HTTPException(404, "Dashboard not found")
    dashboard, version = record
    if version.catalog_version not in SUPPORTED_CATALOG_VERSIONS or version.schema_version != SCHEMA_VERSION:
        raise HTTPException(409, "Dashboard version is incompatible with the current catalog/schema")
    try:
        spec = DashboardSpec.model_validate(version.spec)
        for query in spec.queries:
            compile_analysis_plan(query.plan)
    except (ValidationError, ValueError):
        raise HTTPException(409, "Stored dashboard definition is incompatible") from None
    return dashboard, version, spec


def list_published(database: Session) -> list[DashboardSummary]:
    return [DashboardSummary(
        id=dashboard.id, title=version.spec.get("title", dashboard.title),
        version_number=version.version_number, approved_at=version.approved_at,
    ) for dashboard, version in database.execute(
        published_statement().order_by(DashboardVersion.approved_at.desc(), Dashboard.id)
    )]


def get_published(database: Session, dashboard_id: UUID, *, builder: bool, user_id: UUID | None = None) -> DashboardDetail:
    dashboard, version, spec = load_published(database, dashboard_id)
    return DashboardDetail(
        id=dashboard.id, title=spec.title, version_number=version.version_number,
        approved_at=version.approved_at, spec=spec,
        sql={query.id: compile_analysis_plan(query.plan).sql for query in spec.queries} if builder else None,
        can_edit=builder and dashboard.owner_user_id == user_id,
    )


def apply_filters(plan: AnalysisPlan, request: DashboardQueryRequest) -> AnalysisPlan | None:
    """Intersect saved filters with request filters; never widen saved bounds."""
    value = plan.model_dump(mode="python")
    value["filters"].extend(item.model_dump(mode="python") for item in request.filters)
    requested = request.date_range
    if requested is not None and requested.start is not None:
        start = max(plan.date_range.start, requested.start) if plan.date_range.start else requested.start
        end = min(plan.date_range.end_exclusive, requested.end_exclusive) if plan.date_range.end_exclusive else requested.end_exclusive
        if start >= end:
            return None
        value["date_range"] = {"start": start, "end_exclusive": end}
    try:
        merged = AnalysisPlan.model_validate(value)
        compile_analysis_plan(merged)
        return merged
    except (ValidationError, ValueError):
        raise HTTPException(422, "Filters exceed supported limits for this dashboard") from None


def query_published(
    database: Session, dashboard_id: UUID, request: DashboardQueryRequest,
    scope: ReaderScope, executor: QueryExecutor,
) -> DashboardQueryResponse:
    _, version, spec = load_published(database, dashboard_id)
    version_number = version.version_number
    # Snapshot validated metadata only, then release its transaction before DB
    # analytics work. Scope was copied from authenticated identity by the API.
    plans = [(query.id, query.plan, apply_filters(query.plan, request)) for query in spec.queries]
    database.rollback()
    results = {
        query_id: executor.execute(merged, scope) if merged is not None else QueryResult(
            columns=result_columns(original), rows=[], latency_ms=0, status="no_data",
        )
        for query_id, original, merged in plans
    }
    return DashboardQueryResponse(
        dashboard_id=dashboard_id, version_number=version_number, scope=scope, results=results,
    )
