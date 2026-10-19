"""Destructive migration cycle against an explicitly restored test database only."""

from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.engine import make_url

BACKEND = Path(__file__).resolve().parents[2]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.config import Settings, get_settings
from app.db.connections import create_runtime_engine


SOURCE_TABLES = {
    "agencies",
    "agents",
    "agent_agencies",
    "appraisers",
    "clients",
    "inspectors",
    "neighbourhoods",
    "payment_methods",
    "property_types",
    "properties",
    "appraisals",
    "inspections",
    "offers",
    "property_neighbourhoods",
    "repairs",
    "transactions",
}
APP_TABLES = {
    "users",
    "sessions",
    "dashboards",
    "dashboard_versions",
    "conversations",
    "conversation_messages",
    "agent_checkpoints",
    "graph_checkpoint_writes",
    "usage_records",
    "usage_calls",
    "audit_events",
}
READER_TEST_URLS = {
    "analytics_reader_all": "READER_ALL_TEST_DATABASE_URL",
    "analytics_reader_ca": "READER_CA_TEST_DATABASE_URL",
    "analytics_reader_tx": "READER_TX_TEST_DATABASE_URL",
}


def integration_url() -> str:
    url = os.getenv("MIGRATION_TEST_DATABASE_URL", "")
    restored = os.getenv("MIGRATION_TEST_BACKUP_RESTORED", "").lower() == "true"
    if not url or not restored:
        pytest.skip(
            "requires MIGRATION_TEST_DATABASE_URL and MIGRATION_TEST_BACKUP_RESTORED=true"
        )
    database = make_url(url).database or ""
    if "test" not in database.lower():
        pytest.fail("refusing migration integration test: database name must contain 'test'")
    return url


def source_counts(connection) -> dict[str, int]:
    existing = set(inspect(connection).get_table_names(schema="public"))
    assert SOURCE_TABLES <= existing
    return {
        table: connection.execute(text(f'SELECT count(*) FROM public."{table}"')).scalar_one()
        for table in SOURCE_TABLES
    }


def restore_test_reader_memberships(engine) -> None:
    """Re-provision test memberships removed by DROP ROLE during downgrade."""
    memberships: list[tuple[str, str]] = []
    for group, variable in READER_TEST_URLS.items():
        value = os.getenv(variable, "")
        if not value:
            continue
        username = make_url(value).username
        if not username:
            pytest.fail(f"{variable} must contain an explicit login role")
        memberships.append((group, username))
    if not memberships:
        return
    with engine.begin() as connection:
        quote = connection.dialect.identifier_preparer.quote
        for group, username in memberships:
            connection.exec_driver_sql(f"GRANT {quote(group)} TO {quote(username)}")


def test_upgrade_downgrade_upgrade_preserves_source(monkeypatch: pytest.MonkeyPatch) -> None:
    url = integration_url()
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("MIGRATION_DATABASE_URL", url)
    monkeypatch.setenv("MIGRATION_BACKUP_VERIFIED", "true")
    get_settings.cache_clear()
    config = Config(str(BACKEND / "alembic.ini"))
    engine = create_engine(url)
    try:
        with engine.connect() as connection:
            before = source_counts(connection)
        command.upgrade(config, "head")
        command.upgrade(config, "head")
        with engine.connect() as connection:
            assert set(inspect(connection).get_table_names(schema="app")) == APP_TABLES
            assert source_counts(connection) == before
        command.downgrade(config, "base")
        with engine.connect() as connection:
            assert not inspect(connection).has_schema("app")
            assert not inspect(connection).has_schema("analytics")
            assert source_counts(connection) == before
        command.upgrade(config, "head")
        restore_test_reader_memberships(engine)
        with engine.connect() as connection:
            assert set(inspect(connection).get_table_names(schema="app")) == APP_TABLES
            assert source_counts(connection) == before
    finally:
        engine.dispose()


def test_runtime_role_has_no_ddl_privileges() -> None:
    url = os.getenv("RUNTIME_TEST_DATABASE_URL", "")
    if not url:
        pytest.skip("requires RUNTIME_TEST_DATABASE_URL for the non-owner runtime role")
    if "test" not in (make_url(url).database or "").lower():
        pytest.fail("refusing privilege integration test: database name must contain 'test'")
    engine = create_engine(url)
    try:
        with engine.connect() as connection:
            role_attributes = connection.execute(
                text(
                    "SELECT rolsuper, rolcreatedb, rolcreaterole, rolreplication, rolbypassrls "
                    "FROM pg_roles "
                    "WHERE rolname = current_user"
                )
            ).one()
            assert tuple(role_attributes) == (False,) * 5
            runtime_escalation_paths = connection.execute(
                text(
                    "SELECT "
                    "has_database_privilege(current_user,current_database(),'CREATE'), "
                    "EXISTS (SELECT 1 FROM pg_namespace n WHERE n.nspname !~ '^pg_' "
                    "AND n.nspname <> 'information_schema' "
                    "AND has_schema_privilege(current_user,n.oid,'CREATE')), "
                    "EXISTS (SELECT 1 FROM pg_namespace n "
                    "WHERE pg_has_role(r.oid,n.nspowner,'USAGE') "
                    "AND n.nspname !~ '^pg_' AND n.nspname <> 'information_schema'), "
                    "EXISTS (SELECT 1 FROM pg_class c JOIN pg_namespace n "
                    "ON n.oid=c.relnamespace "
                    "WHERE pg_has_role(r.oid,c.relowner,'USAGE') "
                    "AND n.nspname !~ '^pg_' AND n.nspname <> 'information_schema') "
                    "FROM pg_roles r WHERE r.rolname=current_user"
                )
            ).one()
            assert tuple(runtime_escalation_paths) == (False,) * 4
            inherited_roles = connection.execute(
                text(
                    "SELECT r.rolname, r.rolsuper, r.rolcreatedb, r.rolcreaterole, "
                    "r.rolreplication, r.rolbypassrls, "
                    "has_database_privilege(r.oid,current_database(),'CREATE'), "
                    "EXISTS (SELECT 1 FROM pg_namespace n WHERE n.nspname !~ '^pg_' "
                    "AND n.nspname <> 'information_schema' "
                    "AND has_schema_privilege(r.oid,n.oid,'CREATE')), "
                    "EXISTS (SELECT 1 FROM pg_namespace n "
                    "WHERE pg_has_role(r.oid,n.nspowner,'USAGE') "
                    "AND n.nspname !~ '^pg_' AND n.nspname <> 'information_schema'), "
                    "EXISTS (SELECT 1 FROM pg_class c JOIN pg_namespace n "
                    "ON n.oid=c.relnamespace "
                    "WHERE pg_has_role(r.oid,c.relowner,'USAGE') "
                    "AND n.nspname !~ '^pg_' AND n.nspname <> 'information_schema') "
                    "FROM pg_roles r WHERE r.rolname <> current_user "
                    "AND pg_has_role(current_user, r.oid, 'SET')"
                )
            ).all()
            for role in inherited_roles:
                assert tuple(role[1:]) == (False,) * 9, (
                    f"runtime can SET ROLE to privileged role {role[0]}"
                )

        # A privilege introspection bug must not turn into an unnoticed DDL grant.
        probe = uuid.uuid4().hex
        probes = [f'CREATE SCHEMA "__runtime_schema_probe_{probe}"'] + [
            f'CREATE TABLE {schema}."__runtime_table_probe_{probe}" (id integer)'
            for schema in ("app", "analytics", "public")
        ]
        roles_to_probe: list[str | None] = [None] + [row[0] for row in inherited_roles]
        quote = engine.dialect.identifier_preparer.quote
        for role_name in roles_to_probe:
            for statement in probes:
                with engine.connect() as connection:
                    transaction = connection.begin()
                    try:
                        if role_name is not None:
                            connection.exec_driver_sql(f"SET LOCAL ROLE {quote(role_name)}")
                        with pytest.raises(DBAPIError) as denied:
                            connection.execute(text(statement))
                        assert getattr(denied.value.orig, "sqlstate", None) == "42501"
                    finally:
                        transaction.rollback()
    finally:
        engine.dispose()


def test_runtime_engine_audits_real_principal_and_allows_only_app_dml() -> None:
    runtime_url = os.getenv("RUNTIME_TEST_DATABASE_URL", "")
    if not runtime_url:
        pytest.skip("requires RUNTIME_TEST_DATABASE_URL for the non-owner runtime role")
    if "test" not in (make_url(runtime_url).database or "").lower():
        pytest.fail("refusing privilege integration test: database name must contain 'test'")
    runtime_user = make_url(runtime_url).username
    if not runtime_user:
        pytest.fail("RUNTIME_TEST_DATABASE_URL must contain an explicit login role")

    # Provision only the expected application grants in this disposable database.
    migration_engine = create_engine(integration_url())
    try:
        with migration_engine.begin() as connection:
            quoted_runtime_user = connection.dialect.identifier_preparer.quote(runtime_user)
            connection.exec_driver_sql(
                f"GRANT USAGE ON SCHEMA app TO {quoted_runtime_user}"
            )
            connection.exec_driver_sql(
                "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA app "
                f"TO {quoted_runtime_user}"
            )
            connection.exec_driver_sql(
                "GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA app "
                f"TO {quoted_runtime_user}"
            )
    finally:
        migration_engine.dispose()

    engine = create_runtime_engine(
        Settings(app_env="test", runtime_database_url=runtime_url, _env_file=None)
    )
    user_id = uuid.uuid4()
    username = f"runtime-audit-{user_id.hex}"
    try:
        with engine.connect() as connection:
            transaction = connection.begin()
            try:
                connection.execute(
                    text(
                        "INSERT INTO app.users "
                        "(id, username, password_hash, role, scope_profile) "
                        "VALUES (:id, :username, 'test-hash', 'viewer', 'ALL')"
                    ),
                    {"id": user_id, "username": username},
                )
                assert connection.execute(
                    text("SELECT username FROM app.users WHERE id=:id"), {"id": user_id}
                ).scalar_one() == username
                connection.execute(
                    text("UPDATE app.users SET is_active=false WHERE id=:id"),
                    {"id": user_id},
                )
                connection.execute(
                    text("DELETE FROM app.users WHERE id=:id"), {"id": user_id}
                )
            finally:
                transaction.rollback()

        for statement in (
            "SELECT property_id FROM public.properties LIMIT 1",
            "SELECT transaction_id FROM analytics.sales LIMIT 1",
        ):
            with engine.connect() as connection:
                transaction = connection.begin()
                try:
                    with pytest.raises(DBAPIError) as denied:
                        connection.execute(text(statement))
                    assert getattr(denied.value.orig, "sqlstate", None) == "42501"
                finally:
                    transaction.rollback()
    finally:
        engine.dispose()

