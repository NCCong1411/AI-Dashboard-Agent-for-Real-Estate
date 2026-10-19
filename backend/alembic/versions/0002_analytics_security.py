"""Add scoped analytics readers, row security, and invoker views.

Revision ID: 0002_analytics_security
Revises: 0001_app_schema
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op


revision: str = "0002_analytics_security"
down_revision: str | None = "0001_app_schema"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


READERS = (
    "analytics_reader_all",
    "analytics_reader_ca",
    "analytics_reader_tx",
)
SCOPED_TABLES = ("properties", "transactions", "offers", "repairs")
SOURCE_TABLES = (
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
)


def _policy_predicate(table: str, scope: str) -> str:
    if scope == "all":
        return "true"
    state = scope.upper()
    if table == "properties":
        return f"state = '{state}'"
    return (
        "EXISTS (SELECT 1 FROM public.properties AS scope_property "
        f"WHERE scope_property.property_id = {table}.property_id "
        f"AND scope_property.state = '{state}')"
    )


def upgrade() -> None:
    # Privileges are additive. Fail closed rather than silently leaking through
    # PUBLIC/other roles or default-denying an existing source consumer.
    source_names = ",".join(f"'{table}'" for table in SOURCE_TABLES)
    scoped_names = ",".join(f"'{table}'" for table in SCOPED_TABLES)
    op.execute(
        f"""
        DO $guard$
        BEGIN
          IF EXISTS (
            SELECT 1 FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = 'public'
              AND c.relname IN ('properties','transactions','offers','repairs')
              AND (c.relrowsecurity OR c.relforcerowsecurity)
          ) OR EXISTS (
            SELECT 1 FROM pg_policy p
            JOIN pg_class c ON c.oid = p.polrelid
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = 'public'
              AND c.relname IN ('properties','transactions','offers','repairs')
          ) THEN
            RAISE EXCEPTION 'P1-T03 refuses to replace existing RLS configuration';
          END IF;
          IF EXISTS (
            SELECT 1 FROM pg_roles
            WHERE rolname IN ('analytics_reader_all','analytics_reader_ca','analytics_reader_tx')
          ) THEN
            RAISE EXCEPTION 'P1-T03 reserved analytics reader role already exists';
          END IF;

          IF EXISTS (
            SELECT 1
            FROM pg_namespace n
            CROSS JOIN LATERAL aclexplode(
              COALESCE(n.nspacl, acldefault('n', n.nspowner))
            ) acl
            WHERE n.nspname IN ('public','analytics','app')
              AND acl.grantee = 0
              AND acl.privilege_type = 'CREATE'
          ) THEN
            RAISE EXCEPTION 'P1-T03 refuses unsafe PUBLIC schema CREATE privilege';
          END IF;

          IF EXISTS (
            SELECT 1
            FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
            CROSS JOIN LATERAL aclexplode(
              COALESCE(c.relacl, acldefault('r', c.relowner))
            ) acl
            WHERE n.nspname = 'public'
              AND c.relname IN ({source_names})
              AND acl.grantee = 0
              AND acl.privilege_type IN
                  ('SELECT','INSERT','UPDATE','DELETE','TRUNCATE','REFERENCES','TRIGGER')
          ) OR EXISTS (
            SELECT 1
            FROM pg_attribute a
            JOIN pg_class c ON c.oid = a.attrelid
            JOIN pg_namespace n ON n.oid = c.relnamespace
            CROSS JOIN LATERAL aclexplode(
              CASE WHEN cardinality(a.attacl) > 0 THEN a.attacl END
            ) acl
            WHERE n.nspname = 'public'
              AND c.relname IN ({source_names})
              AND a.attnum > 0 AND NOT a.attisdropped
              AND acl.grantee = 0
          ) THEN
            RAISE EXCEPTION 'P1-T03 refuses unsafe PUBLIC source privileges';
          END IF;

          IF EXISTS (
            SELECT 1
            FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
            CROSS JOIN LATERAL aclexplode(
              COALESCE(c.relacl, acldefault('r', c.relowner))
            ) acl
            WHERE n.nspname = 'public'
              AND c.relname IN ({scoped_names})
              AND acl.grantee NOT IN (0, c.relowner,
                    (SELECT oid FROM pg_roles WHERE rolname = current_user))
          ) OR EXISTS (
            SELECT 1
            FROM pg_attribute a
            JOIN pg_class c ON c.oid = a.attrelid
            JOIN pg_namespace n ON n.oid = c.relnamespace
            CROSS JOIN LATERAL aclexplode(
              CASE WHEN cardinality(a.attacl) > 0 THEN a.attacl END
            ) acl
            WHERE n.nspname = 'public'
              AND c.relname IN ({scoped_names})
              AND a.attnum > 0 AND NOT a.attisdropped
              AND acl.grantee NOT IN (0, c.relowner,
                    (SELECT oid FROM pg_roles WHERE rolname = current_user))
          ) THEN
            RAISE EXCEPTION 'P1-T03 requires review/removal of existing scoped-table consumers';
          END IF;
        END
        $guard$
        """
    )

    for role in READERS:
        op.execute(
            f"CREATE ROLE {role} NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE "
            "NOINHERIT NOREPLICATION NOBYPASSRLS"
        )

    for table in SCOPED_TABLES:
        op.execute(f"ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY")
        for scope, role in zip(("all", "ca", "tx"), READERS, strict=True):
            predicate = _policy_predicate(table, scope)
            op.execute(
                f"CREATE POLICY {table}_reader_{scope} ON public.{table} "
                f"FOR SELECT TO {role} USING ({predicate})"
            )

    op.execute("REVOKE ALL ON TABLE public.properties FROM " + ", ".join(READERS))
    op.execute("REVOKE ALL ON TABLE public.transactions FROM " + ", ".join(READERS))
    op.execute("REVOKE ALL ON TABLE public.offers FROM " + ", ".join(READERS))
    op.execute("REVOKE ALL ON TABLE public.repairs FROM " + ", ".join(READERS))
    op.execute("REVOKE ALL ON TABLE public.property_types FROM " + ", ".join(READERS))
    op.execute("GRANT USAGE ON SCHEMA public, analytics TO " + ", ".join(READERS))
    op.execute(
        "GRANT SELECT (property_id, city, property_type_id, state) "
        "ON public.properties TO " + ", ".join(READERS)
    )
    op.execute(
        "GRANT SELECT (transaction_id, agent_id, property_id, sale_price, transaction_date) "
        "ON public.transactions TO " + ", ".join(READERS)
    )
    op.execute(
        "GRANT SELECT (offer_id, offer_amount, offer_date, property_id, status) "
        "ON public.offers TO " + ", ".join(READERS)
    )
    op.execute(
        "GRANT SELECT (repair_id, cost, property_id, repair_date) "
        "ON public.repairs TO " + ", ".join(READERS)
    )
    op.execute(
        "GRANT SELECT (property_type_id, type_name) ON public.property_types TO "
        + ", ".join(READERS)
    )

    op.execute(
        """
        CREATE VIEW analytics.sales WITH (security_invoker = true, security_barrier = true) AS
        SELECT t.transaction_id, t.agent_id, t.property_id, t.sale_price,
               t.transaction_date, p.state, p.city, p.property_type_id,
               pt.type_name AS property_type
        FROM public.transactions AS t
        JOIN public.properties AS p ON p.property_id = t.property_id
        JOIN public.property_types AS pt ON pt.property_type_id = p.property_type_id
        """
    )
    op.execute(
        """
        CREATE VIEW analytics.offers WITH (security_invoker = true, security_barrier = true) AS
        SELECT o.offer_id, o.property_id, o.offer_amount, o.offer_date, o.status,
               p.state, p.city, p.property_type_id, pt.type_name AS property_type
        FROM public.offers AS o
        JOIN public.properties AS p ON p.property_id = o.property_id
        JOIN public.property_types AS pt ON pt.property_type_id = p.property_type_id
        """
    )
    op.execute(
        """
        CREATE VIEW analytics.repairs WITH (security_invoker = true, security_barrier = true) AS
        SELECT r.repair_id, r.property_id, r.cost, r.repair_date,
               p.state, p.city, p.property_type_id, pt.type_name AS property_type
        FROM public.repairs AS r
        JOIN public.properties AS p ON p.property_id = r.property_id
        JOIN public.property_types AS pt ON pt.property_type_id = p.property_type_id
        """
    )
    op.execute(
        "GRANT SELECT ON analytics.sales, analytics.offers, analytics.repairs TO "
        + ", ".join(READERS)
    )


def downgrade() -> None:
    op.execute("REVOKE SELECT ON analytics.sales, analytics.offers, analytics.repairs FROM " + ", ".join(READERS))
    op.execute("DROP VIEW analytics.repairs")
    op.execute("DROP VIEW analytics.offers")
    op.execute("DROP VIEW analytics.sales")
    op.execute("REVOKE ALL ON TABLE public.property_types FROM " + ", ".join(READERS))
    for table in SCOPED_TABLES:
        op.execute(f"REVOKE ALL ON TABLE public.{table} FROM " + ", ".join(READERS))
        for scope in ("all", "ca", "tx"):
            op.execute(f"DROP POLICY {table}_reader_{scope} ON public.{table}")
        op.execute(f"ALTER TABLE public.{table} DISABLE ROW LEVEL SECURITY")
    op.execute("REVOKE USAGE ON SCHEMA public, analytics FROM " + ", ".join(READERS))
    for role in reversed(READERS):
        op.execute(f"DROP ROLE {role}")
