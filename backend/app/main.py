"""FastAPI application entry point for the local MVP."""

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse
from pydantic.json_schema import models_json_schema

from .api.auth import router as auth_router
from .api.catalog import router as catalog_router
from .api.dashboards import router as dashboards_router
from .api.conversations import router as conversations_router
from .config import get_settings
from .schemas.agent import AgentResponse, PlannerDecision
from .schemas.analysis import AnalysisPlan
from .schemas.dashboard import DashboardSpec, QueryResult, WidgetSpec


settings = get_settings()

app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    docs_url="/api/docs" if settings.app_env != "production" else None,
    openapi_url="/api/openapi.json" if settings.app_env != "production" else None,
)
app.include_router(auth_router)
app.include_router(catalog_router)
app.include_router(dashboards_router)
app.include_router(conversations_router)


@app.middleware("http")
async def private_api_response(request: Request, call_next):
    response = await call_next(request)
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    return response


def versioned_openapi() -> dict:
    """Include non-endpoint contracts in the generated client schema."""
    if app.openapi_schema is not None:
        return app.openapi_schema
    schema = get_openapi(title=app.title, version=app.version, routes=app.routes)
    _, contract_schema = models_json_schema(
        [
            (AnalysisPlan, "validation"),
            (DashboardSpec, "validation"),
            (WidgetSpec, "validation"),
            (QueryResult, "validation"),
            (AgentResponse, "validation"),
            (PlannerDecision, "validation"),
        ],
        ref_template="#/components/schemas/{model}",
    )
    schema.setdefault("components", {}).setdefault("schemas", {}).update(
        contract_schema.get("$defs", {})
    )
    app.openapi_schema = schema
    return schema


app.openapi = versioned_openapi


@app.exception_handler(RequestValidationError)
async def sanitized_validation_error(
    _request: Request, exception: RequestValidationError
) -> JSONResponse:
    """Return useful locations/types without echoing submitted secrets."""
    errors = [
        {key: value for key, value in error.items() if key not in {"input", "ctx"}}
        for error in exception.errors()
    ]
    return JSONResponse(status_code=422, content={"detail": jsonable_encoder(errors)})


@app.get("/api/health", tags=["system"])
def health() -> dict[str, str]:
    """Report process health without requiring database or LLM credentials."""

    return {
        "status": "ok",
        "service": settings.app_name,
        "environment": settings.app_env,
    }

