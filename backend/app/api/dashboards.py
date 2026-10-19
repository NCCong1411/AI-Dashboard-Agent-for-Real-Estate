"""Authenticated workspace access to published dashboards."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends

from app.analytics.executor import QueryExecutor, get_query_executor
from app.auth.dependencies import CurrentUser, Database, require_trusted_origin
from app.schemas.dashboard import DashboardDetail, DashboardQueryRequest, DashboardQueryResponse, DashboardSummary
from app.services.dashboard import get_published, list_published, query_published


router = APIRouter(prefix="/api/dashboards", tags=["dashboards"])


@router.get("", response_model=list[DashboardSummary])
def list_dashboards(user: CurrentUser, database: Database):
    return list_published(database)


@router.get("/{dashboard_id}", response_model=DashboardDetail, response_model_exclude_none=True)
def dashboard_detail(dashboard_id: UUID, user: CurrentUser, database: Database):
    return get_published(database, dashboard_id, builder=user.role == "builder", user_id=user.id)


@router.post("/{dashboard_id}/query", response_model=DashboardQueryResponse,
             dependencies=[Depends(require_trusted_origin)])
def dashboard_query(
    dashboard_id: UUID, request: DashboardQueryRequest, user: CurrentUser,
    database: Database, executor: Annotated[QueryExecutor, Depends(get_query_executor)],
):
    return query_published(database, dashboard_id, request, user.scope_profile, executor)
