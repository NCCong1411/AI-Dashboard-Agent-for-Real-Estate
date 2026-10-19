"""Audit credential resolution must not require application runtime roles."""

from __future__ import annotations

import importlib.util
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "audit_db.py"


def test_audit_dsn_from_ignored_env_file(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("audit_db_configuration", SCRIPT)
    assert spec and spec.loader
    audit = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(audit)
    backend = tmp_path / "backend"
    backend.mkdir()
    (backend / ".env").write_text("AUDIT_DATABASE_URL=postgresql://audit:secret@localhost/audit\n", encoding="utf-8")
    monkeypatch.setattr(audit, "ROOT", tmp_path)
    monkeypatch.delenv("AUDIT_DATABASE_URL", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert audit.configured_audit_dsn(None) == "postgresql://audit:secret@localhost/audit"
    monkeypatch.setenv("AUDIT_DATABASE_URL", "postgresql://explicit/audit")
    assert audit.configured_audit_dsn(None) == "postgresql://explicit/audit"
    assert audit.configured_audit_dsn("postgresql://cli/audit") == "postgresql://cli/audit"
