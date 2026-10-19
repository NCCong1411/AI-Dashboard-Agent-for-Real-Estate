"""Provision least-privilege local LOGIN roles after the main migration.

The command is read-only unless ``--apply`` and both explicit confirmations are
present. Generated passwords are never written to stdout; successful runs place
the four connection URLs in the ignored ``backend/.runtime`` directory.
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import os
from pathlib import Path
import secrets
import subprocess
from typing import Any

import psycopg
from psycopg.conninfo import conninfo_to_dict
from psycopg import sql
from sqlalchemy.engine import URL
from sqlglot import exp, parse_one
from dotenv import dotenv_values

from audit_db import ROOT


BACKEND = ROOT / "backend"
RUNTIME_DIR = BACKEND / ".runtime"
PREFLIGHT_ARTIFACT = RUNTIME_DIR / "main-migration-preflight.json"
MIGRATION_ARTIFACT = RUNTIME_DIR / "main-migration-result.json"
DEFAULT_OUTPUT = RUNTIME_DIR / "local-postgres-credentials.env"
EXPECTED_DATABASE = "real_estate_db"
EXPECTED_HEAD = "0005_graph_checkpoints"
RUNTIME_ROLE = "dashboard_app_runtime"
READER_ROLES = {
    "ALL": ("dashboard_reader_all", "analytics_reader_all"),
    "CA": ("dashboard_reader_ca", "analytics_reader_ca"),
    "TX": ("dashboard_reader_tx", "analytics_reader_tx"),
}
TARGET_ROLES = (RUNTIME_ROLE, *(login for login, _group in READER_ROLES.values()))
SOURCE_TABLES = (
    "agencies", "agents", "agent_agencies", "appraisers", "clients", "inspectors",
    "neighbourhoods", "payment_methods", "property_types", "properties", "appraisals",
    "inspections", "offers", "property_neighbourhoods", "repairs", "transactions",
)
SCOPED_TABLES = ("properties", "transactions", "offers", "repairs")
ALLOWED_READER_COLUMNS = {
    "properties": {"property_id", "city", "property_type_id", "state"},
    "transactions": {"transaction_id", "agent_id", "property_id", "sale_price", "transaction_date"},
    "offers": {"offer_id", "offer_amount", "offer_date", "property_id", "status"},
    "repairs": {"repair_id", "cost", "property_id", "repair_date"},
    "property_types": {"property_type_id", "type_name"},
}


class ProvisioningStateUncertain(RuntimeError):
    """The connection failed while transaction outcome could not be confirmed."""


def configured_operator_dsn() -> str:
    """Read only the dedicated process override or ignored audit setting."""
    explicit = os.getenv("LOCAL_ROLE_ADMIN_DATABASE_URL", "")
    if explicit:
        return explicit
    env_file = BACKEND / ".env"
    if env_file.is_file():
        value = dotenv_values(env_file, interpolate=False).get("AUDIT_DATABASE_URL")
        if isinstance(value, str) and value:
            return value
    return ""


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    operation = result.add_mutually_exclusive_group()
    operation.add_argument(
        "--apply", action="store_true",
        help="create the four LOGIN roles and grants; omitted means read-only preflight",
    )
    operation.add_argument(
        "--reconcile", action="store_true",
        help="read-only verification after an uncertain COMMIT; requires all four roles",
    )
    result.add_argument(
        "--confirm-database", choices=[EXPECTED_DATABASE],
        help=f"required with --apply; must be {EXPECTED_DATABASE}",
    )
    result.add_argument(
        "--confirm-cluster", metavar="SYSTEM_IDENTIFIER",
        help="required with --apply; copy the non-secret identifier printed by preflight",
    )
    result.add_argument(
        "--output", type=Path, default=DEFAULT_OUTPUT,
        help="credential artifact path; must remain beneath backend/.runtime",
    )
    return result


def _load_artifacts() -> tuple[dict[str, Any], dict[str, Any]]:
    try:
        preflight = json.loads(PREFLIGHT_ARTIFACT.read_text(encoding="utf-8"))
        migration = json.loads(MIGRATION_ARTIFACT.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("recorded main-migration artifacts are missing or invalid") from exc
    if not preflight.get("guard_preflight_passed"):
        raise RuntimeError("recorded main-migration preflight did not pass")
    if not (
        migration.get("migration_head") == EXPECTED_HEAD
        and migration.get("reader_group_count") == 3
        and migration.get("scoped_rls_table_count") == 4
        and migration.get("source_rows_unchanged") is True
    ):
        raise RuntimeError("recorded main-migration result is incomplete or failed")
    return preflight, migration


def _is_loopback(host: str) -> bool:
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def validate_admin_dsn(dsn: str) -> dict[str, str]:
    """Reject libpq indirection and non-native/non-local DSNs before connecting."""
    if not dsn.startswith(("postgresql://", "postgres://")):
        raise RuntimeError("admin URL must be a native postgresql:// URI")
    for variable in ("PGHOSTADDR", "PGSERVICE", "PGSERVICEFILE"):
        if os.getenv(variable):
            raise RuntimeError(f"{variable} must be unset for role provisioning")
    try:
        parameters = conninfo_to_dict(dsn)
    except psycopg.Error as exc:
        raise RuntimeError("admin URL is not a valid native PostgreSQL URI") from exc
    forbidden = {"hostaddr", "service", "servicefile"}.intersection(parameters)
    if forbidden:
        raise RuntimeError("admin URL cannot use hostaddr, service, or servicefile")
    host = parameters.get("host", "")
    if not host or "," in host or not _is_loopback(host):
        raise RuntimeError("admin URL must contain one explicit loopback host")
    if parameters.get("dbname") != EXPECTED_DATABASE:
        raise RuntimeError(f"admin URL database must be {EXPECTED_DATABASE}")
    if not parameters.get("user"):
        raise RuntimeError("admin URL must contain an explicit user")
    return parameters


def _canonical_expression(expression: str) -> str:
    """Normalize only harmless catalog formatting, never semantic casts."""
    def simplify(node: exp.Expression) -> exp.Expression:
        if isinstance(node, exp.Paren):
            return node.this
        if isinstance(node, exp.Cast):
            target = node.args.get("to")
            subject = node.this
            while isinstance(subject, exp.Paren):
                subject = subject.this
            if (not isinstance(target, exp.DataType)
                    or target.this != exp.DataType.Type.TEXT
                    or not isinstance(subject, (exp.Column, exp.Literal))):
                raise RuntimeError("analytics policy contains a non-catalog cast")
            return subject
        if isinstance(node, exp.Table):
            qualifier = node.args.get("db")
            if qualifier is not None:
                if node.name != "properties" or qualifier.name != "public" or node.args.get("catalog"):
                    raise RuntimeError("analytics policy contains unexpected qualification")
                node.set("db", None)
        return node

    try:
        tree = parse_one(expression, read="postgres")
        while tree.find(exp.Cast) is not None or tree.find(exp.Paren) is not None:
            tree = tree.transform(simplify)
        return tree.transform(simplify).sql(
            dialect="postgres", normalize=True, pretty=False
        )
    except Exception as exc:
        raise RuntimeError("an analytics policy expression could not be validated") from exc


def _expected_policies() -> dict[tuple[str, str], tuple[str, str]]:
    expected: dict[tuple[str, str], tuple[str, str]] = {}
    for table in SCOPED_TABLES:
        for scope in ("all", "ca", "tx"):
            group = f"analytics_reader_{scope}"
            if scope == "all":
                predicate = "true"
            elif table == "properties":
                predicate = f"state = '{scope.upper()}'"
            else:
                predicate = (
                    "EXISTS (SELECT 1 FROM public.properties AS scope_property "
                    f"WHERE scope_property.property_id = {table}.property_id "
                    f"AND scope_property.state = '{scope.upper()}')"
                )
            expected[(table, f"{table}_reader_{scope}")] = (
                group, _canonical_expression(predicate)
            )
    return expected


def _validate_policy_rows(rows: list[tuple[Any, ...]]) -> None:
    expected = _expected_policies()
    actual: dict[tuple[str, str], tuple[str, str]] = {}
    for table, name, command, permissive, roles, with_check, predicate in rows:
        key = (table, name)
        if key in actual or command != "r" or permissive is not True or with_check is not None:
            raise RuntimeError("analytics RLS policy definitions are not exact")
        if not isinstance(roles, (list, tuple)) or len(roles) != 1:
            raise RuntimeError("analytics RLS policy role assignment is not exact")
        actual[key] = (roles[0], _canonical_expression(predicate))
    if actual != expected:
        raise RuntimeError("analytics RLS policy definitions are not exact")


def _validate_security_boundary(
    conn: psycopg.Connection[Any],
    expected_memberships: set[tuple[str, str]] | None = None,
) -> None:
    """Verify the migrated groups cannot gain effective access outside analytics."""
    groups = [group for _login, group in READER_ROLES.values()]
    ownership = conn.execute(
        "SELECT (SELECT count(*) FROM pg_class c JOIN pg_roles r ON r.oid=c.relowner "
        "WHERE r.rolname=ANY(%s)) + "
        "(SELECT count(*) FROM pg_namespace n JOIN pg_roles r ON r.oid=n.nspowner "
        "WHERE r.rolname=ANY(%s)) + "
        "(SELECT count(*) FROM pg_proc p JOIN pg_roles r ON r.oid=p.proowner "
        "WHERE r.rolname=ANY(%s)) + "
        "(SELECT count(*) FROM pg_type t JOIN pg_roles r ON r.oid=t.typowner "
        "WHERE r.rolname=ANY(%s)) + "
        "(SELECT count(*) FROM pg_database d JOIN pg_roles r ON r.oid=d.datdba "
        "WHERE r.rolname=ANY(%s))",
        (groups, groups, groups, groups, groups),
    ).fetchone()[0]
    memberships = set(conn.execute(
        "SELECT parent.rolname,member.rolname FROM pg_auth_members m "
        "JOIN pg_roles parent ON parent.oid=m.roleid "
        "JOIN pg_roles member ON member.oid=m.member "
        "WHERE parent.rolname=ANY(%s) OR member.rolname=ANY(%s)", (groups, groups),
    ).fetchall())
    public_grants = conn.execute(
        "SELECT "
        "(SELECT count(*) FROM pg_namespace n CROSS JOIN LATERAL "
        "aclexplode(COALESCE(n.nspacl,acldefault('n',n.nspowner))) a "
        "WHERE a.grantee=0 AND ((n.nspname IN ('public','analytics','app') "
        "AND a.privilege_type='CREATE') OR (n.nspname IN ('analytics','app') "
        "AND a.privilege_type='USAGE'))) + "
        "(SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
        "CROSS JOIN LATERAL aclexplode(COALESCE(c.relacl,acldefault("
        "CASE WHEN c.relkind='S' THEN 'S'::\"char\" ELSE 'r'::\"char\" END,c.relowner))) a "
        "WHERE a.grantee=0 AND ((n.nspname='public' AND c.relname=ANY(%s)) "
        "OR n.nspname IN ('analytics','app'))) + "
        "(SELECT count(*) FROM pg_attribute x JOIN pg_class c ON c.oid=x.attrelid "
        "JOIN pg_namespace n ON n.oid=c.relnamespace CROSS JOIN LATERAL "
        "aclexplode(CASE WHEN cardinality(x.attacl)>0 THEN x.attacl END) a "
        "WHERE a.grantee=0 AND ((n.nspname='public' AND c.relname=ANY(%s)) "
        "OR n.nspname IN ('analytics','app')))",
        (list(SOURCE_TABLES), list(SOURCE_TABLES)),
    ).fetchone()[0]
    # Compare every effective source-column grant with the narrow allowlist.
    allowed_rows = {
        (group, table, column)
        for group in groups for table, columns in ALLOWED_READER_COLUMNS.items()
        for column in columns
    }
    granted_rows = set(conn.execute(
        "SELECT r.rolname,c.relname,a.attname FROM pg_roles r "
        "JOIN pg_attribute a ON a.attnum>0 AND NOT a.attisdropped "
        "JOIN pg_class c ON c.oid=a.attrelid JOIN pg_namespace n ON n.oid=c.relnamespace "
        "WHERE r.rolname=ANY(%s) AND n.nspname='public' AND c.relname=ANY(%s) "
        "AND has_column_privilege(r.oid,c.oid,a.attnum,'SELECT')",
        (groups, list(SOURCE_TABLES)),
    ).fetchall())
    write_or_app = conn.execute(
        "SELECT count(*) FROM pg_roles r CROSS JOIN pg_class c "
        "JOIN pg_namespace n ON n.oid=c.relnamespace WHERE r.rolname=ANY(%s) AND ("
        "(n.nspname='public' AND c.relname=ANY(%s) AND ("
        "has_table_privilege(r.oid,c.oid,'INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER') OR "
        "has_any_column_privilege(r.oid,c.oid,'INSERT,UPDATE,REFERENCES'))) OR "
        "(n.nspname='app' AND has_table_privilege(r.oid,c.oid,'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER'))) ",
        (groups, list(SOURCE_TABLES)),
    ).fetchone()[0]
    policy_rows = conn.execute(
        "SELECT c.relname,p.polname,p.polcmd,p.polpermissive,"
        "ARRAY(SELECT r.rolname FROM unnest(p.polroles) AS role_oid(oid) "
        "JOIN pg_roles r ON r.oid=role_oid.oid ORDER BY r.rolname),"
        "pg_get_expr(p.polwithcheck,p.polrelid),pg_get_expr(p.polqual,p.polrelid) "
        "FROM pg_policy p JOIN pg_class c ON c.oid=p.polrelid "
        "JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' "
        "AND c.relname=ANY(%s) ORDER BY c.relname,p.polname", (list(SCOPED_TABLES),),
    ).fetchall()
    views = conn.execute(
        "SELECT c.relname,c.relkind,c.reloptions FROM pg_class c "
        "JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='analytics' "
        "AND c.relname IN ('sales','offers','repairs') ORDER BY c.relname"
    ).fetchall()
    indirect_paths = conn.execute(
        "SELECT (SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace "
        "WHERE n.nspname IN ('public','analytics','app') AND p.prosecdef) + "
        "(SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
        "WHERE n.nspname IN ('public','analytics','app') AND c.relkind IN ('v','m') "
        "AND NOT (n.nspname='analytics' AND c.relkind='v' "
        "AND c.relname IN ('sales','offers','repairs')))"
    ).fetchone()[0]
    if (ownership or memberships != (expected_memberships or set()) or public_grants
            or granted_rows != allowed_rows or write_or_app or indirect_paths):
        raise RuntimeError("analytics reader groups have unsafe effective privileges")
    _validate_policy_rows(policy_rows)
    if len(views) != 3 or any(
        row[1] != "v" or set(row[2] or ()) != {"security_invoker=true", "security_barrier=true"}
        for row in views
    ):
        raise RuntimeError("analytics views are missing exact security-invoker options")


def inspect_target(
    conn: psycopg.Connection[Any], recorded_cluster: str, *, reconcile: bool = False
) -> dict[str, Any]:
    """Run the fail-closed, read-only provisioning preflight."""
    conn.execute("BEGIN READ ONLY")
    try:
        conn.execute("SET LOCAL statement_timeout = '5s'")
        database, cluster, username = conn.execute(
            "SELECT current_database(), system_identifier::text, current_user "
            "FROM pg_control_system()"
        ).fetchone()
        if database != EXPECTED_DATABASE:
            raise RuntimeError(f"target database must be {EXPECTED_DATABASE}")
        if cluster != recorded_cluster:
            raise RuntimeError("target cluster does not match the recorded migration preflight")
        head = conn.execute("SELECT version_num FROM alembic_version").fetchone()
        if head is None or head[0] != EXPECTED_HEAD:
            raise RuntimeError(f"database is not migrated to {EXPECTED_HEAD}")
        schemas = conn.execute(
            "SELECT count(*) FROM pg_namespace WHERE nspname IN ('app','analytics')"
        ).fetchone()[0]
        group_rows = conn.execute(
            "SELECT rolname, rolcanlogin, rolsuper, rolcreatedb, rolcreaterole, "
            "rolreplication, rolbypassrls FROM pg_roles "
            "WHERE rolname IN ('analytics_reader_all','analytics_reader_ca','analytics_reader_tx') "
            "ORDER BY rolname"
        ).fetchall()
        target_count = conn.execute(
            "SELECT count(*) FROM pg_roles WHERE rolname = ANY(%s)",
            (list(TARGET_ROLES),),
        ).fetchone()[0]
        rls_count = conn.execute(
            "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
            "WHERE n.nspname='public' AND c.relname IN "
            "('properties','transactions','offers','repairs') AND c.relrowsecurity"
        ).fetchone()[0]
        policy_count = conn.execute(
            "SELECT count(*) FROM pg_policy p JOIN pg_class c ON c.oid=p.polrelid "
            "JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' "
            "AND c.relname IN ('properties','transactions','offers','repairs')"
        ).fetchone()[0]
        admin = conn.execute(
            "SELECT rolsuper, rolcreaterole FROM pg_roles WHERE rolname=current_user"
        ).fetchone()
        owners = conn.execute(
            "SELECT count(*) FROM pg_namespace n WHERE n.nspname IN ('app','analytics') "
            "AND pg_get_userbyid(n.nspowner)=current_user"
        ).fetchone()[0]
        if schemas != 2 or rls_count != 4 or policy_count != 12:
            raise RuntimeError("required migrated schemas, RLS tables, or policies are missing")
        if len(group_rows) != 3 or any(any(row[1:]) for row in group_rows):
            raise RuntimeError("analytics reader groups are missing or have unsafe attributes")
        if not reconcile and target_count:
            raise RuntimeError("one or more reserved local LOGIN role names already exist")
        if reconcile and target_count != len(TARGET_ROLES):
            raise RuntimeError("reconciliation found a partial or absent LOGIN role set")
        if admin is None or not (admin[0] or admin[1]) or (not admin[0] and owners != 2):
            raise RuntimeError("provisioning principal cannot safely create roles and grant schema access")
        expected_memberships = (
            {(group, login) for login, group in READER_ROLES.values()}
            if reconcile else set()
        )
        _validate_security_boundary(conn, expected_memberships)
        if reconcile:
            _verify_created_roles(conn)
        return {
            "database": database,
            "cluster_system_identifier": cluster,
            "migration_head": head[0],
            "provisioning_principal": username,
        }
    finally:
        conn.execute("ROLLBACK")


def _credential_text(conn: psycopg.Connection[Any], passwords: dict[str, str]) -> str:
    host = conn.info.host
    port = conn.info.port
    database = conn.info.dbname
    lines = []
    roles = {
        "RUNTIME_DATABASE_URL": RUNTIME_ROLE,
        **{f"READER_{scope}_DATABASE_URL": login for scope, (login, _group) in READER_ROLES.items()},
    }
    for variable, role in roles.items():
        url = URL.create(
            "postgresql+psycopg", username=role, password=passwords[role],
            host=host, port=port, database=database,
        )
        lines.append(f"{variable}={url.render_as_string(hide_password=False)}")
    return "\n".join(lines) + "\n"


WINDOWS_ACL_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
$path = $env:ROLE_SECRET_FILE
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$sid = $identity.User
$acl = Get-Acl -LiteralPath $path
$acl.SetAccessRuleProtection($true, $false)
foreach ($entry in @($acl.Access)) { [void]$acl.RemoveAccessRuleSpecific($entry) }
$rule = [Security.AccessControl.FileSystemAccessRule]::new(
  $sid, [Security.AccessControl.FileSystemRights]::FullControl,
  [Security.AccessControl.AccessControlType]::Allow)
[void]$acl.AddAccessRule($rule)
$acl.SetOwner($sid)
Set-Acl -LiteralPath $path -AclObject $acl
$check = Get-Acl -LiteralPath $path
$ownerSid = ([Security.Principal.NTAccount]$check.Owner).Translate(
  [Security.Principal.SecurityIdentifier]).Value
$rules = @($check.Access)
if ($ownerSid -ne $sid.Value -or $rules.Count -ne 1) { exit 31 }
$actualSid = $rules[0].IdentityReference.Translate(
  [Security.Principal.SecurityIdentifier]).Value
$full = [Security.AccessControl.FileSystemRights]::FullControl
if ($actualSid -ne $sid.Value -or $rules[0].IsInherited -or
    $rules[0].AccessControlType -ne [Security.AccessControl.AccessControlType]::Allow -or
    (($rules[0].FileSystemRights -band $full) -ne $full)) { exit 32 }
"""


def _secure_empty_secret_file(path: Path) -> int:
    """Create exclusively and verify owner-only access before any secret write."""
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        if os.name == "nt":
            system_root = os.environ.get("SystemRoot") or os.environ.get("WINDIR")
            if not system_root:
                raise RuntimeError("Windows system directory is unavailable for ACL setup")
            powershell = Path(system_root) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
            environment = {
                "SystemRoot": system_root,
                "WINDIR": system_root,
                "ROLE_SECRET_FILE": str(path),
            }
            result = subprocess.run(
                [str(powershell), "-NoLogo", "-NoProfile", "-NonInteractive",
                 "-ExecutionPolicy", "Bypass", "-Command", WINDOWS_ACL_SCRIPT],
                env=environment, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, timeout=15, check=False,
            )
            if result.returncode != 0:
                raise RuntimeError("owner-only Windows ACL could not be established and verified")
        else:
            os.chmod(path, 0o600)
            if path.stat().st_mode & 0o077:
                raise RuntimeError("owner-only credential file mode could not be verified")
        return descriptor
    except BaseException:
        os.close(descriptor)
        path.unlink(missing_ok=True)
        raise


def _write_secret_file(path: Path, content: str) -> Path:
    runtime = RUNTIME_DIR.resolve()
    resolved = path.resolve()
    if resolved.parent != runtime:
        raise RuntimeError("credential output must be a direct child of backend/.runtime")
    runtime.mkdir(parents=True, exist_ok=True)
    descriptor = _secure_empty_secret_file(resolved)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        resolved.unlink(missing_ok=True)
        raise
    return resolved


def _verify_created_roles(conn: psycopg.Connection[Any]) -> None:
    attributes = conn.execute(
            "SELECT rolname, rolcanlogin, rolsuper, rolcreatedb, rolcreaterole, "
            "rolreplication, rolbypassrls, rolinherit FROM pg_roles "
            "WHERE rolname = ANY(%s)", (list(TARGET_ROLES),),
        ).fetchall()
    if len(attributes) != 4 or any(
        row[1] is not True or any(row[2:7]) or row[7] is not True
        for row in attributes
    ):
        raise RuntimeError("created LOGIN role attributes failed verification")
    memberships = set(conn.execute(
            "WITH RECURSIVE inherited(login_oid,role_oid) AS ("
            "SELECT m.member,m.roleid FROM pg_auth_members m JOIN pg_roles login ON login.oid=m.member "
            "WHERE login.rolname=ANY(%s) UNION "
            "SELECT i.login_oid,m.roleid FROM inherited i JOIN pg_auth_members m ON m.member=i.role_oid) "
            "SELECT login.rolname,parent.rolname FROM inherited i "
            "JOIN pg_roles login ON login.oid=i.login_oid "
            "JOIN pg_roles parent ON parent.oid=i.role_oid",
            (list(TARGET_ROLES),),
        ).fetchall())
    expected_memberships = {
        (login, group) for login, group in READER_ROLES.values()
    }
    if memberships != expected_memberships:
        raise RuntimeError("created LOGIN role transitive memberships failed verification")
    unsafe = conn.execute(
            "SELECT count(*) FROM pg_roles r WHERE r.rolname=ANY(%s) AND ("
            "has_database_privilege(r.oid,current_database(),'CREATE') OR "
            "has_schema_privilege(r.oid,'public','CREATE') OR "
            "has_schema_privilege(r.oid,'analytics','CREATE') OR "
            "has_schema_privilege(r.oid,'app','CREATE') OR EXISTS ("
            "SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
            "WHERE c.relowner=r.oid AND n.nspname IN ('public','analytics','app')))",
            (list(TARGET_ROLES),),
        ).fetchone()[0]
    if unsafe:
        raise RuntimeError("created LOGIN role DDL denial failed verification")
    readers = [login for login, _group in READER_ROLES.values()]
    reader_columns = set(conn.execute(
            "SELECT r.rolname,c.relname,a.attname FROM pg_roles r "
            "JOIN pg_attribute a ON a.attnum>0 AND NOT a.attisdropped "
            "JOIN pg_class c ON c.oid=a.attrelid JOIN pg_namespace n ON n.oid=c.relnamespace "
            "WHERE r.rolname=ANY(%s) AND n.nspname='public' AND c.relname=ANY(%s) "
            "AND has_column_privilege(r.oid,c.oid,a.attnum,'SELECT')",
            (readers, list(SOURCE_TABLES)),
        ).fetchall())
    expected_columns = {
        (reader, table, column) for reader in readers
        for table, columns in ALLOWED_READER_COLUMNS.items() for column in columns
    }
    unsafe_data = conn.execute(
            "SELECT count(*) FROM pg_roles r CROSS JOIN pg_class c "
            "JOIN pg_namespace n ON n.oid=c.relnamespace WHERE r.rolname=ANY(%s) AND ("
            "(n.nspname='public' AND c.relname=ANY(%s) AND ("
            "has_table_privilege(r.oid,c.oid,'INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER') OR "
            "has_any_column_privilege(r.oid,c.oid,'INSERT,UPDATE,REFERENCES'))) OR "
            "(r.rolname=ANY(%s) AND n.nspname='app' AND "
            "has_table_privilege(r.oid,c.oid,'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')) OR "
            "(r.rolname=%s AND n.nspname IN ('public','analytics') AND "
            "has_table_privilege(r.oid,c.oid,'SELECT'))) ",
            (list(TARGET_ROLES), list(SOURCE_TABLES), readers, RUNTIME_ROLE),
        ).fetchone()[0]
    app_table_mismatch = conn.execute(
            "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
            "WHERE n.nspname='app' AND c.relkind IN ('r','p') AND NOT ("
            "has_table_privilege(%s,c.oid,'SELECT') AND has_table_privilege(%s,c.oid,'INSERT') "
            "AND has_table_privilege(%s,c.oid,'UPDATE') AND has_table_privilege(%s,c.oid,'DELETE'))",
            (RUNTIME_ROLE, RUNTIME_ROLE, RUNTIME_ROLE, RUNTIME_ROLE),
        ).fetchone()[0]
    view_grants = conn.execute(
            "SELECT count(*) FROM pg_roles r CROSS JOIN pg_class c "
            "JOIN pg_namespace n ON n.oid=c.relnamespace WHERE r.rolname=ANY(%s) "
            "AND n.nspname='analytics' AND c.relname IN ('sales','offers','repairs') "
            "AND has_table_privilege(r.oid,c.oid,'SELECT')", (readers,),
        ).fetchone()[0]
    settings = set(conn.execute(
            "SELECT r.rolname,unnest(s.setconfig) FROM pg_db_role_setting s "
            "JOIN pg_roles r ON r.oid=s.setrole WHERE s.setdatabase=("
            "SELECT oid FROM pg_database WHERE datname=current_database()) "
            "AND r.rolname=ANY(%s)", (list(TARGET_ROLES),),
        ).fetchall())
    expected_settings = {
        (reader, setting) for reader in readers
        for setting in ("default_transaction_read_only=on", "statement_timeout=5s")
    }
    if (reader_columns != expected_columns or unsafe_data or app_table_mismatch
            or view_grants != 9 or settings != expected_settings):
        raise RuntimeError("created LOGIN role effective privileges failed verification")


def _apply_role_changes(conn: psycopg.Connection[Any], passwords: dict[str, str]) -> None:
    database = conn.info.dbname
    for role in TARGET_ROLES:
        conn.execute(sql.SQL(
            "CREATE ROLE {} LOGIN INHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE "
            "NOREPLICATION NOBYPASSRLS CONNECTION LIMIT 10 PASSWORD {}"
        ).format(sql.Identifier(role), sql.Literal(passwords[role])))
    conn.execute(sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
        sql.Identifier(database), sql.SQL(", ").join(map(sql.Identifier, TARGET_ROLES))))
    conn.execute(sql.SQL("GRANT USAGE ON SCHEMA app TO {}").format(sql.Identifier(RUNTIME_ROLE)))
    conn.execute(sql.SQL(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA app TO {}"
    ).format(sql.Identifier(RUNTIME_ROLE)))
    conn.execute(sql.SQL(
        "GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA app TO {}"
    ).format(sql.Identifier(RUNTIME_ROLE)))
    conn.execute(sql.SQL("REVOKE CREATE ON SCHEMA public, app, analytics FROM {}").format(
        sql.SQL(", ").join(map(sql.Identifier, TARGET_ROLES))))
    for _scope, (login, group) in READER_ROLES.items():
        conn.execute(sql.SQL("GRANT {} TO {}").format(sql.Identifier(group), sql.Identifier(login)))
        conn.execute(sql.SQL(
            "ALTER ROLE {} IN DATABASE {} SET default_transaction_read_only = on"
        ).format(sql.Identifier(login), sql.Identifier(database)))
        conn.execute(sql.SQL(
            "ALTER ROLE {} IN DATABASE {} SET statement_timeout = '5s'"
        ).format(sql.Identifier(login), sql.Identifier(database)))
    _validate_security_boundary(
        conn, {(group, login) for login, group in READER_ROLES.values()}
    )
    _verify_created_roles(conn)


def provision(conn: psycopg.Connection[Any], passwords: dict[str, str]) -> None:
    """Create and verify roles, preserving secrets if COMMIT outcome is uncertain."""
    conn.execute("BEGIN")
    try:
        _apply_role_changes(conn, passwords)
    except BaseException as exc:
        try:
            conn.execute("ROLLBACK")
        except BaseException as rollback_exc:
            raise ProvisioningStateUncertain(
                "transaction rollback could not be confirmed"
            ) from rollback_exc
        raise
    try:
        conn.execute("COMMIT")
    except BaseException as exc:
        raise ProvisioningStateUncertain("transaction commit outcome is uncertain") from exc


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    dsn = configured_operator_dsn()
    if not dsn:
        print("Role provisioning not started: dedicated local operator URL is missing")
        return 2
    if args.apply and (
        args.confirm_database != EXPECTED_DATABASE or not args.confirm_cluster
    ):
        print("Role provisioning not started: --apply requires database and cluster confirmations")
        return 2
    try:
        expected_connection = validate_admin_dsn(dsn)
        preflight_artifact, _migration_artifact = _load_artifacts()
        recorded_cluster = str(preflight_artifact["cluster_system_identifier"])
        with psycopg.connect(dsn, autocommit=True, connect_timeout=5) as conn:
            if (not _is_loopback(conn.info.host)
                    or (getattr(conn.info, "hostaddr", "")
                        and not _is_loopback(conn.info.hostaddr))
                    or conn.info.dbname != expected_connection["dbname"]
                    or conn.info.user != expected_connection["user"]):
                raise RuntimeError("connected PostgreSQL endpoint does not match the validated URL")
            status = inspect_target(conn, recorded_cluster, reconcile=args.reconcile)
            if args.reconcile:
                print(json.dumps({
                    **status, "reconciliation_passed": True, "changes_applied": False,
                }))
                return 0
            if not args.apply:
                print(json.dumps({**status, "preflight_passed": True, "changes_applied": False}))
                return 0
            if args.confirm_cluster != status["cluster_system_identifier"]:
                raise RuntimeError("--confirm-cluster does not match the connected cluster")
            passwords = {role: secrets.token_urlsafe(48) for role in TARGET_ROLES}
            output = _write_secret_file(args.output, _credential_text(conn, passwords))
            try:
                provision(conn, passwords)
            except ProvisioningStateUncertain:
                print(json.dumps({
                    "state": "uncertain",
                    "credential_artifact_preserved": str(output.relative_to(ROOT)),
                    "secrets_printed": False,
                    "reconciliation": (
                        "keep this artifact and run this command again with --reconcile "
                        "before retrying or removing it"
                    ),
                }))
                return 3
            except BaseException:
                output.unlink(missing_ok=True)
                raise
        print(json.dumps({
            "database": EXPECTED_DATABASE,
            "cluster_system_identifier": status["cluster_system_identifier"],
            "roles_created": len(TARGET_ROLES),
            "credential_artifact": str(output.relative_to(ROOT)),
            "secrets_printed": False,
        }))
        return 0
    except (KeyError, OSError, RuntimeError, psycopg.Error):
        print("Role provisioning failed safely; no credential values were printed")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
