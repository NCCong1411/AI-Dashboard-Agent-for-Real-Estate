"""Static/unit safety checks for local PostgreSQL LOGIN provisioning."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest


SCRIPT = Path(__file__).parents[2] / "scripts" / "provision_local_roles.py"
sys.path.insert(0, str(SCRIPT.parent))
SPEC = importlib.util.spec_from_file_location("provision_local_roles", SCRIPT)
assert SPEC and SPEC.loader
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


def test_default_cli_is_preflight_only() -> None:
    args = module.parser().parse_args([])
    assert args.apply is False
    assert args.reconcile is False
    assert args.confirm_database is None
    assert args.confirm_cluster is None


def test_apply_requires_both_explicit_confirmations(monkeypatch, capsys) -> None:
    monkeypatch.setenv("LOCAL_ROLE_ADMIN_DATABASE_URL", "postgresql://hidden")
    assert module.main(["--apply"]) == 2
    output = capsys.readouterr().out
    assert "hidden" not in output
    assert "confirm" in output.lower()


def test_apply_and_reconcile_are_mutually_exclusive() -> None:
    with pytest.raises(SystemExit):
        module.parser().parse_args(["--apply", "--reconcile"])


def test_operator_dsn_uses_only_explicit_override_or_ignored_audit_setting(
    monkeypatch, tmp_path
) -> None:
    backend = tmp_path / "backend"
    backend.mkdir()
    (backend / ".env").write_text(
        "AUDIT_DATABASE_URL=postgresql://audit:hidden@localhost/real_estate_db\n"
        "DATABASE_URL=postgresql://generic:wrong@remote/other\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(module, "BACKEND", backend)
    monkeypatch.delenv("LOCAL_ROLE_ADMIN_DATABASE_URL", raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgresql://environment:wrong@remote/other")
    assert module.configured_operator_dsn().startswith("postgresql://audit:")
    monkeypatch.setenv(
        "LOCAL_ROLE_ADMIN_DATABASE_URL",
        "postgresql://explicit:hidden@localhost/real_estate_db",
    )
    assert module.configured_operator_dsn().startswith("postgresql://explicit:")


def test_operator_dsn_never_falls_back_to_database_url(monkeypatch, tmp_path) -> None:
    backend = tmp_path / "backend"
    backend.mkdir()
    (backend / ".env").write_text("DATABASE_URL=postgresql://wrong\n", encoding="utf-8")
    monkeypatch.setattr(module, "BACKEND", backend)
    monkeypatch.delenv("LOCAL_ROLE_ADMIN_DATABASE_URL", raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgresql://also-wrong")
    assert module.configured_operator_dsn() == ""


@pytest.mark.parametrize("host, expected", [
    ("localhost", True), ("127.0.0.1", True), ("::1", True),
    ("db.internal", False), ("192.0.2.10", False),
])
def test_only_loopback_hosts_are_accepted(host: str, expected: bool) -> None:
    assert module._is_loopback(host) is expected


def test_admin_dsn_is_native_explicit_and_not_redirectable(monkeypatch) -> None:
    for variable in ("PGHOSTADDR", "PGSERVICE", "PGSERVICEFILE"):
        monkeypatch.delenv(variable, raising=False)
    valid = "postgresql://operator:secret@127.0.0.1:5432/real_estate_db"
    parsed = module.validate_admin_dsn(valid)
    assert parsed["host"] == "127.0.0.1"
    assert parsed["dbname"] == "real_estate_db"
    invalid = (
        "postgresql+psycopg://operator:secret@localhost/real_estate_db",
        "host=localhost dbname=real_estate_db user=operator",
        "postgresql://operator:secret@db.internal/real_estate_db",
        "postgresql://operator:secret@localhost/other",
        "postgresql://operator:secret@localhost/real_estate_db?hostaddr=127.0.0.1",
        "postgresql://operator:secret@localhost/real_estate_db?service=redirect",
    )
    for dsn in invalid:
        with pytest.raises(RuntimeError):
            module.validate_admin_dsn(dsn)


def test_libpq_redirect_environment_is_rejected(monkeypatch) -> None:
    monkeypatch.setenv("PGSERVICE", "unexpected")
    with pytest.raises(RuntimeError, match="PGSERVICE"):
        module.validate_admin_dsn(
            "postgresql://operator:secret@localhost/real_estate_db"
        )


def test_invalid_dsn_is_rejected_before_connect(monkeypatch, capsys) -> None:
    monkeypatch.setenv("LOCAL_ROLE_ADMIN_DATABASE_URL", "postgresql://operator@remote/db")
    monkeypatch.setattr(
        module.psycopg, "connect",
        lambda *_args, **_kwargs: pytest.fail("must not connect before DSN validation"),
    )
    assert module.main([]) == 1
    assert "operator" not in capsys.readouterr().out


def test_generated_passwords_and_urls_are_not_hard_coded() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    assert "secrets.token_urlsafe(48)" in source
    assert "PASSWORD {}" in source
    assert "print(password" not in source
    assert "dashboard_app_runtime:" not in source


def test_roles_are_distinct_and_map_to_exactly_one_scope_group() -> None:
    logins = [login for login, _group in module.READER_ROLES.values()]
    groups = [group for _login, group in module.READER_ROLES.values()]
    assert len(set(module.TARGET_ROLES)) == 4
    assert len(set(logins)) == len(set(groups)) == 3
    assert groups == ["analytics_reader_all", "analytics_reader_ca", "analytics_reader_tx"]
    assert module.RUNTIME_ROLE not in logins


def test_secret_output_must_stay_in_ignored_runtime_directory(tmp_path) -> None:
    with pytest.raises(RuntimeError, match="backend/.runtime"):
        module._write_secret_file(tmp_path / "credentials.env", "secret")


def test_windows_acl_is_verified_before_secret_write(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(module, "RUNTIME_DIR", tmp_path)
    monkeypatch.setenv("LOCAL_ROLE_ADMIN_DATABASE_URL", "must-not-reach-child")
    observed = {}

    def secure(command, **kwargs):
        target = Path(kwargs["env"]["ROLE_SECRET_FILE"])
        observed["empty_before_acl"] = target.read_bytes() == b""
        observed["env"] = kwargs["env"]
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(module.subprocess, "run", secure)
    output = module._write_secret_file(tmp_path / "credentials.env", "top-secret")
    assert observed["empty_before_acl"] is True
    assert "LOCAL_ROLE_ADMIN_DATABASE_URL" not in observed["env"]
    assert output.read_text(encoding="utf-8") == "top-secret"


def test_windows_acl_failure_removes_file_before_secret_write(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(module, "RUNTIME_DIR", tmp_path)
    monkeypatch.setattr(
        module.subprocess, "run", lambda *_args, **_kwargs: SimpleNamespace(returncode=31)
    )
    output = tmp_path / "credentials.env"
    with pytest.raises(RuntimeError, match="ACL"):
        module._write_secret_file(output, "must-not-be-written")
    assert not output.exists()


def test_policy_validation_is_ast_exact() -> None:
    rows = []
    for (table, name), (group, predicate) in module._expected_policies().items():
        rows.append((table, name, "r", True, [group], None, predicate))
    module._validate_policy_rows(rows)
    changed = list(rows)
    table, name, command, permissive, roles, check, predicate = changed[0]
    changed[0] = (table, name, command, permissive, roles, check, f"({predicate}) OR true")
    with pytest.raises(RuntimeError, match="not exact"):
        module._validate_policy_rows(changed)


def test_policy_normalization_accepts_only_catalog_added_casts() -> None:
    expected = module._expected_policies()[("properties", "properties_reader_ca")][1]
    assert module._canonical_expression("((state)::text = 'CA'::text)") == expected
    with pytest.raises(RuntimeError):
        module._canonical_expression("state::char(1) = 'CA'::char(1)")
    with pytest.raises(RuntimeError):
        module._canonical_expression(
            "EXISTS (SELECT 1 FROM shadow.properties AS scope_property "
            "WHERE scope_property.property_id = transactions.property_id "
            "AND scope_property.state = 'CA')"
        )


def test_security_boundary_and_effective_login_checks_run_before_commit(monkeypatch) -> None:
    class Connection:
        info = SimpleNamespace(dbname="real_estate_db")
        def execute(self, *_args, **_kwargs): return self

    checks = []
    monkeypatch.setattr(
        module, "_validate_security_boundary",
        lambda *_args, **_kwargs: checks.append("boundary"),
    )
    monkeypatch.setattr(
        module, "_verify_created_roles", lambda *_args: checks.append("logins")
    )
    module._apply_role_changes(Connection(), {role: "secret" for role in module.TARGET_ROLES})
    assert checks == ["boundary", "logins"]


class TransactionConnection:
    def __init__(self, *, fail_commit: bool = False, fail_rollback: bool = False):
        self.commands = []
        self.fail_commit = fail_commit
        self.fail_rollback = fail_rollback

    def execute(self, command, *_args):
        self.commands.append(command)
        if command == "COMMIT" and self.fail_commit:
            raise RuntimeError("simulated transport loss")
        if command == "ROLLBACK" and self.fail_rollback:
            raise RuntimeError("simulated transport loss")
        return self


def test_uncertain_commit_does_not_attempt_rollback(monkeypatch) -> None:
    connection = TransactionConnection(fail_commit=True)
    monkeypatch.setattr(module, "_apply_role_changes", lambda *_args: None)
    with pytest.raises(module.ProvisioningStateUncertain):
        module.provision(connection, {})
    assert connection.commands == ["BEGIN", "COMMIT"]


def test_confirmed_rollback_remains_a_normal_failure(monkeypatch) -> None:
    connection = TransactionConnection()

    def fail(*_args):
        raise RuntimeError("pre-commit verification failed")

    monkeypatch.setattr(module, "_apply_role_changes", fail)
    with pytest.raises(RuntimeError, match="verification failed"):
        module.provision(connection, {})
    assert connection.commands == ["BEGIN", "ROLLBACK"]


def test_failed_rollback_is_reported_as_uncertain(monkeypatch) -> None:
    connection = TransactionConnection(fail_rollback=True)
    monkeypatch.setattr(
        module, "_apply_role_changes",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("failure")),
    )
    with pytest.raises(module.ProvisioningStateUncertain):
        module.provision(connection, {})


def test_uncertain_commit_preserves_artifact_and_reports_reconciliation(
    monkeypatch, tmp_path, capsys
) -> None:
    class Context:
        info = SimpleNamespace(host="localhost", dbname="real_estate_db", user="operator")
        def __enter__(self): return self
        def __exit__(self, *_args): return False

    artifact = tmp_path / "credentials.env"
    monkeypatch.setattr(module, "ROOT", tmp_path)
    monkeypatch.setenv(
        "LOCAL_ROLE_ADMIN_DATABASE_URL",
        "postgresql://operator:hidden@localhost/real_estate_db",
    )
    for variable in ("PGHOSTADDR", "PGSERVICE", "PGSERVICEFILE"):
        monkeypatch.delenv(variable, raising=False)
    monkeypatch.setattr(module, "_load_artifacts", lambda: ({"cluster_system_identifier": "42"}, {}))
    monkeypatch.setattr(module.psycopg, "connect", lambda *_args, **_kwargs: Context())
    monkeypatch.setattr(module, "inspect_target", lambda *_args, **_kwargs: {
        "database": "real_estate_db", "cluster_system_identifier": "42",
        "migration_head": module.EXPECTED_HEAD, "provisioning_principal": "operator",
    })
    monkeypatch.setattr(module, "_credential_text", lambda *_args: "generated-secret")

    def write(_path, content):
        artifact.write_text(content, encoding="utf-8")
        return artifact

    monkeypatch.setattr(module, "_write_secret_file", write)
    monkeypatch.setattr(
        module, "provision",
        lambda *_args: (_ for _ in ()).throw(module.ProvisioningStateUncertain("unknown")),
    )
    result = module.main([
        "--apply", "--confirm-database", "real_estate_db", "--confirm-cluster", "42",
        "--output", str(artifact),
    ])
    output = capsys.readouterr().out
    assert result == 3
    assert artifact.read_text(encoding="utf-8") == "generated-secret"
    assert "reconciliation" in output
    assert "--reconcile" in output
    assert "generated-secret" not in output and "hidden" not in output


def test_provisioning_sql_has_least_privilege_guards() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    for fragment in (
        "NOSUPERUSER NOCREATEDB NOCREATEROLE",
        "NOREPLICATION NOBYPASSRLS",
        "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA app",
        "GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA app",
        "default_transaction_read_only = on",
        "statement_timeout = '5s'",
        "one or more reserved local LOGIN role names already exist",
        "pg_get_expr(p.polqual,p.polrelid)",
        "security_invoker=true",
        "WITH RECURSIVE inherited",
        "has_any_column_privilege",
        "pg_db_role_setting",
    ):
        assert fragment in source
    assert "GRANT ALL" not in source
    assert "ALTER ROLE {} SUPERUSER" not in source
