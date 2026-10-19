"""P1-T01 configuration and health endpoint smoke tests."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.config import Settings, get_settings


def test_required_config_fails_clearly(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("APP_ENV", raising=False)
    with pytest.raises(ValidationError, match="app_env"):
        Settings(_env_file=None)


def test_scaffold_needs_no_database_or_llm_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    settings = Settings(_env_file=None)
    assert settings.app_env == "test"
    assert settings.llm_mode == "mock"
    assert settings.llm_api_key is None


def test_health_endpoint_without_database_or_llm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    get_settings.cache_clear()
    main = importlib.import_module("app.main")
    main = importlib.reload(main)
    with TestClient(main.app) as client:
        response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": "AI Dashboard Agent API",
        "environment": "test",
    }

