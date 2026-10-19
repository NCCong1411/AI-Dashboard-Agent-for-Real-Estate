"""Read-only check of conflicts before touching the existing PostgreSQL DB."""

from __future__ import annotations

import json
from pathlib import Path

import psycopg

from audit_db import ROOT, configured_audit_dsn, isolated_libpq_environment


def main() -> int:
    dsn = configured_audit_dsn(None)
    if not dsn:
        print("Migration preflight unavailable: AUDIT_DATABASE_URL is missing")
        return 2
    source_tables = (
        "agencies", "agents", "agent_agencies", "appraisers", "clients", "inspectors",
        "neighbourhoods", "payment_methods", "property_types", "properties",
        "appraisals", "inspections", "offers", "property_neighbourhoods", "repairs", "transactions",
    )
    scoped = ("properties", "transactions", "offers", "repairs")
    with psycopg.connect(dsn, autocommit=True, connect_timeout=5) as conn:
        conn.execute("BEGIN READ ONLY")
        if conn.execute("SHOW transaction_read_only").fetchone()[0] != "on":
            raise RuntimeError("read-only transaction was not confirmed")
        conn.execute("SET LOCAL statement_timeout = '5s'")
        database = conn.execute("SELECT current_database()").fetchone()[0]
        cluster = str(conn.execute("SELECT system_identifier FROM pg_control_system()").fetchone()[0])
        if database != "real_estate_db":
            raise RuntimeError("AUDIT_DATABASE_URL does not target real_estate_db")
        schemas = conn.execute("SELECT nspname FROM pg_namespace WHERE nspname IN ('app','analytics')").fetchall()
        groups = conn.execute("SELECT rolname FROM pg_roles WHERE rolname IN "
                              "('analytics_reader_all','analytics_reader_ca','analytics_reader_tx')").fetchall()
        existing_rls = conn.execute("""
            SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE n.nspname='public' AND c.relname=ANY(%s)
              AND (c.relrowsecurity OR c.relforcerowsecurity OR EXISTS
                   (SELECT 1 FROM pg_policy p WHERE p.polrelid=c.oid))
        """, (list(scoped),)).fetchone()[0]
        public_schema_create = conn.execute("""
            SELECT count(*) FROM pg_namespace n
            CROSS JOIN LATERAL aclexplode(COALESCE(n.nspacl,acldefault('n',n.nspowner))) acl
            WHERE n.nspname IN ('public','app','analytics')
              AND acl.grantee=0 AND acl.privilege_type='CREATE'
        """).fetchone()[0]
        public_source_grants = conn.execute("""
            SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
            CROSS JOIN LATERAL aclexplode(COALESCE(c.relacl,acldefault('r',c.relowner))) acl
            WHERE n.nspname='public' AND c.relname=ANY(%s)
              AND acl.grantee=0 AND acl.privilege_type IN
                  ('SELECT','INSERT','UPDATE','DELETE','TRUNCATE','REFERENCES','TRIGGER')
        """, (list(source_tables),)).fetchone()[0]
        public_column_grants = conn.execute("""
            SELECT count(*) FROM pg_attribute a JOIN pg_class c ON c.oid=a.attrelid
            JOIN pg_namespace n ON n.oid=c.relnamespace
            CROSS JOIN LATERAL aclexplode(CASE WHEN cardinality(a.attacl)>0 THEN a.attacl END) acl
            WHERE n.nspname='public' AND c.relname=ANY(%s)
              AND a.attnum>0 AND NOT a.attisdropped AND acl.grantee=0
        """, (list(source_tables),)).fetchone()[0]
        other_consumers = conn.execute("""
            SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
            CROSS JOIN LATERAL aclexplode(COALESCE(c.relacl,acldefault('r',c.relowner))) acl
            WHERE n.nspname='public' AND c.relname=ANY(%s)
              AND acl.grantee NOT IN (0,c.relowner,
                  (SELECT oid FROM pg_roles WHERE rolname=current_user))
        """, (list(scoped),)).fetchone()[0]
        other_column_consumers = conn.execute("""
            SELECT count(*) FROM pg_attribute a JOIN pg_class c ON c.oid=a.attrelid
            JOIN pg_namespace n ON n.oid=c.relnamespace
            CROSS JOIN LATERAL aclexplode(CASE WHEN cardinality(a.attacl)>0 THEN a.attacl END) acl
            WHERE n.nspname='public' AND c.relname=ANY(%s)
              AND a.attnum>0 AND NOT a.attisdropped
              AND acl.grantee NOT IN (0,c.relowner,
                  (SELECT oid FROM pg_roles WHERE rolname=current_user))
        """, (list(scoped),)).fetchone()[0]
        security_snapshot = {
            "source_table_owners_and_acls": [
                {"table": row[0], "owner": row[1], "acl": row[2]}
                for row in conn.execute("""
                    SELECT c.relname, pg_get_userbyid(c.relowner),
                           COALESCE(c.relacl::text, '<default>')
                    FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
                    WHERE n.nspname='public' AND c.relname=ANY(%s)
                    ORDER BY c.relname
                """, (list(source_tables),)).fetchall()
            ],
            "schema_owners_and_acls": [
                {"schema": row[0], "owner": row[1], "acl": row[2]}
                for row in conn.execute("""
                    SELECT n.nspname, pg_get_userbyid(n.nspowner),
                           COALESCE(n.nspacl::text, '<default>')
                    FROM pg_namespace n WHERE n.nspname='public'
                """).fetchall()
            ],
            "default_acls": [
                {"owner": row[0], "schema": row[1], "object_type": row[2], "acl": row[3]}
                for row in conn.execute("""
                    SELECT pg_get_userbyid(d.defaclrole),
                           COALESCE(n.nspname, '<global>'), d.defaclobjtype,
                           d.defaclacl::text
                    FROM pg_default_acl d LEFT JOIN pg_namespace n ON n.oid=d.defaclnamespace
                    ORDER BY 1, 2, 3
                """).fetchall()
            ],
            "role_memberships": [
                {"role": row[0], "member": row[1], "grantor": row[2]}
                for row in conn.execute("""
                    SELECT parent.rolname, member.rolname, grantor.rolname
                    FROM pg_auth_members m
                    JOIN pg_roles parent ON parent.oid=m.roleid
                    JOIN pg_roles member ON member.oid=m.member
                    JOIN pg_roles grantor ON grantor.oid=m.grantor
                    ORDER BY 1, 2, 3
                """).fetchall()
            ],
        }
        conn.execute("ROLLBACK")
    counts = {"existing_app_schemas": len(schemas), "existing_reader_groups": len(groups),
              "existing_scoped_rls": existing_rls, "public_schema_create_grants": public_schema_create,
              "public_source_table_grants": public_source_grants,
              "public_source_column_grants": public_column_grants,
              "other_scoped_table_consumers": other_consumers,
              "other_scoped_column_consumers": other_column_consumers}
    report = {"database": database, "cluster_system_identifier": cluster,
              "read_only": True, "counts": counts,
              "security_snapshot": security_snapshot,
              "guard_preflight_passed": all(value == 0 for value in counts.values())}
    destination = ROOT / "backend" / ".runtime" / "main-migration-preflight.json"
    destination.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: value for key, value in report.items() if key != "security_snapshot"}))
    return 0 if report["guard_preflight_passed"] else 1


if __name__ == "__main__":
    with isolated_libpq_environment():
        raise SystemExit(main())
