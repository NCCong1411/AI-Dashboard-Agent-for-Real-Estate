"""Typed runtime configuration for the local backend."""

from functools import lru_cache
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


BACKEND_DIR = Path(__file__).resolve().parents[1]


class Settings(BaseSettings):
    """Runtime settings. Database URLs remain optional for the health-only app."""

    app_env: Literal["development", "test", "production"]
    app_name: str = "AI Dashboard Agent API"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = Field(
        default="INFO"
    )
    frontend_origin: str = "http://localhost:3000"
    session_cookie_name: str = "dashboard_session"
    session_ttl_seconds: int = Field(default=28800, ge=300, le=604800)
    # Accepted from the shared .env so app startup can coexist with the
    # audit-only credential. Runtime connections never use this field.
    audit_database_url: SecretStr | None = None
    runtime_database_url: SecretStr | None = None
    migration_database_url: SecretStr | None = None
    reader_all_database_url: SecretStr | None = None
    reader_ca_database_url: SecretStr | None = None
    reader_tx_database_url: SecretStr | None = None
    seed_builder_all_password: SecretStr | None = None
    seed_viewer_all_password: SecretStr | None = None
    seed_viewer_ca_password: SecretStr | None = None
    seed_builder_tx_password: SecretStr | None = None
    migration_backup_verified: bool = False
    database_connect_timeout_seconds: int = Field(default=3, ge=2, le=5)
    analytics_statement_timeout_ms: int = Field(default=5000, ge=1, le=5000)
    analytics_max_rows: int = Field(default=1000, ge=1, le=1000)
    # Planner units, not milliseconds or money. Calibrate on the restored DB.
    analytics_max_query_cost: float = Field(default=10000, gt=0, allow_inf_nan=False)
    llm_mode: Literal["mock", "live"] = "mock"
    llm_provider: Literal["openai"] | None = None
    llm_endpoint: str | None = None
    llm_model: str | None = None
    llm_api_key: SecretStr | None = None
    llm_input_price_per_million: Decimal | None = Field(default=None, ge=0, max_digits=18, decimal_places=6, allow_inf_nan=False)
    llm_output_price_per_million: Decimal | None = Field(default=None, ge=0, max_digits=18, decimal_places=6, allow_inf_nan=False)
    llm_pricing_source: str | None = None
    llm_pricing_verified_on: date | None = None
    llm_reasoning_effort: Literal["low", "medium", "high", "xhigh", "max"] = "low"
    llm_max_output_tokens: int = Field(default=4096, ge=256, le=24000)
    llm_max_creation_output_tokens: int = Field(default=6144, ge=256, le=8192)
    llm_max_calls_per_run: int = Field(default=3, ge=1, le=3)
    llm_max_tokens_per_run: int = Field(default=24000, ge=1, le=24000)
    llm_timeout_seconds: int = Field(default=60, ge=1, le=60)
    agent_run_timeout_seconds: int = Field(default=120, ge=90, le=180)
    llm_budget_per_run_usd: Decimal = Field(default=Decimal("0.10"), ge=0, max_digits=12, decimal_places=6, allow_inf_nan=False)
    llm_budget_per_day_usd: Decimal = Field(default=Decimal("1.00"), ge=0, max_digits=12, decimal_places=6, allow_inf_nan=False)
    llm_global_budget_per_day_usd: Decimal | None = Field(default=None, ge=0, max_digits=12, decimal_places=6, allow_inf_nan=False)

    @field_validator(
        "audit_database_url",
        "runtime_database_url",
        "migration_database_url",
        "reader_all_database_url",
        "reader_ca_database_url",
        "reader_tx_database_url",
        "seed_builder_all_password",
        "seed_viewer_all_password",
        "seed_viewer_ca_password",
        "seed_builder_tx_password",
        "llm_api_key",
        mode="before",
    )
    @classmethod
    def blank_database_url_is_unset(cls, value: object) -> object:
        """Treat blank example-file values as absent credentials."""
        if isinstance(value, SecretStr):
            return None if not value.get_secret_value().strip() else value
        if isinstance(value, str) and not value.strip():
            return None
        return value

    model_config = SettingsConfigDict(
        env_file=BACKEND_DIR / ".env",
        env_file_encoding="utf-8",
        extra="forbid",
        case_sensitive=False,
    )


@lru_cache
def get_settings() -> Settings:
    """Load and validate configuration once per process."""

    return Settings()

