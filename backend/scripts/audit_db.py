#!/usr/bin/env python3
"""Read-only audit of the real-estate PostgreSQL database or its SQL import.

The import-file audit has no third-party dependencies. Live database inspection
requires psycopg 3 and a DSN supplied through --dsn, AUDIT_DATABASE_URL,
backend/.env, or DATABASE_URL. Connection strings are never included in output.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import json
import os
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SQL = ROOT / "data" / "real-estate-sales-sample-database-v3-sql-postgres.sql"


@contextmanager
def isolated_libpq_environment():
    """Do not let inherited service/hostaddr settings redirect explicit URLs."""
    keys = ("PGHOSTADDR", "PGSERVICE", "PGSERVICEFILE")
    previous = {key: os.environ.pop(key, None) for key in keys}
    try:
        yield
    finally:
        for key, value in previous.items():
            if value is not None:
                os.environ[key] = value


def configured_audit_dsn(explicit: str | None) -> str | None:
    """Resolve audit credentials without loading unrelated application secrets."""
    if explicit:
        return explicit
    direct = os.getenv("AUDIT_DATABASE_URL")
    if direct:
        return direct
    env_file = ROOT / "backend" / ".env"
    if env_file.is_file():
        try:
            from dotenv import dotenv_values
        except ImportError:
            pass  # Import-only audit still works without project dependencies.
        else:
            local = dotenv_values(env_file, interpolate=False).get("AUDIT_DATABASE_URL")
            if local:
                return local
    return os.getenv("DATABASE_URL") or None


def identifier_fingerprint(value: str) -> str:
    """Make role/owner identifiers comparable without writing them to audit logs."""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]


def split_statements(text: str) -> Iterable[str]:
    """Split SQL on semicolons outside quoted strings."""
    start = 0
    quoted = False
    i = 0
    while i < len(text):
        char = text[i]
        if char == "'":
            if quoted and i + 1 < len(text) and text[i + 1] == "'":
                i += 2
                continue
            quoted = not quoted
        elif char == ";" and not quoted:
            yield text[start:i].strip()
            start = i + 1
        i += 1
    tail = text[start:].strip()
    if tail:
        yield tail


def split_csv(text: str) -> list[str]:
    """Split one SQL tuple/column list on commas outside quotes/parens."""
    parts: list[str] = []
    start = 0
    quoted = False
    depth = 0
    i = 0
    while i < len(text):
        char = text[i]
        if char == "'":
            if quoted and i + 1 < len(text) and text[i + 1] == "'":
                i += 2
                continue
            quoted = not quoted
        elif not quoted:
            if char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
            elif char == "," and depth == 0:
                parts.append(text[start:i].strip())
                start = i + 1
        i += 1
    parts.append(text[start:].strip())
    return parts


def parse_tuples(text: str) -> list[list[str]]:
    tuples: list[list[str]] = []
    quoted = False
    depth = 0
    start: int | None = None
    i = 0
    while i < len(text):
        char = text[i]
        if char == "'":
            if quoted and i + 1 < len(text) and text[i + 1] == "'":
                i += 2
                continue
            quoted = not quoted
        elif not quoted:
            if char == "(":
                if depth == 0:
                    start = i + 1
                depth += 1
            elif char == ")":
                depth -= 1
                if depth == 0 and start is not None:
                    tuples.append(split_csv(text[start:i]))
                    start = None
                if depth < 0:
                    raise ValueError("unbalanced closing parenthesis in INSERT")
        i += 1
    if quoted or depth:
        raise ValueError("unterminated quote or tuple in INSERT")
    return tuples


def scalar(token: str) -> Any:
    token = token.strip()
    if token.upper() == "NULL":
        return None
    if token.startswith("'") and token.endswith("'"):
        return token[1:-1].replace("''", "'")
    if re.fullmatch(r"[-+]?\d+", token):
        return int(token)
    if re.fullmatch(r"[-+]?(?:\d+\.\d*|\d*\.\d+)", token):
        return Decimal(token)
    return token


def clean_identifier(raw: str) -> str:
    return raw.strip().strip('"').replace('""', '"')


def parse_import(path: Path) -> dict[str, Any]:
    raw = path.read_bytes()
    text = raw.decode("utf-8-sig")
    tables: dict[str, dict[str, Any]] = {}
    foreign_keys: list[dict[str, str]] = []

    for statement in split_statements(text):
        create = re.search(
            r'CREATE\s+TABLE\s+"([^"]+)"\s*\((.*)\)\s*$',
            statement,
            re.IGNORECASE | re.DOTALL,
        )
        if create:
            name, body = create.groups()
            columns: list[dict[str, Any]] = []
            primary_key: list[str] = []
            unique: list[list[str]] = []
            for definition in split_csv(body):
                column_match = re.match(r'^"([^"]+)"\s+(.+)$', definition, re.DOTALL)
                if not column_match:
                    continue
                col_name, remainder = column_match.groups()
                type_match = re.match(
                    r"(.+?)(?=\s+(?:GENERATED|NOT\s+NULL|NULL|PRIMARY\s+KEY|UNIQUE|DEFAULT|CHECK|REFERENCES)\b|$)",
                    remainder.strip(),
                    re.IGNORECASE | re.DOTALL,
                )
                data_type = " ".join((type_match.group(1) if type_match else remainder).split())
                nullable = not bool(re.search(r"\bNOT\s+NULL\b|\bPRIMARY\s+KEY\b", remainder, re.I))
                is_pk = bool(re.search(r"\bPRIMARY\s+KEY\b", remainder, re.I))
                is_unique = bool(re.search(r"\bUNIQUE\b", remainder, re.I))
                columns.append(
                    {
                        "name": col_name,
                        "data_type": data_type.lower(),
                        "nullable": nullable,
                        "primary_key": is_pk,
                        "unique": is_unique,
                    }
                )
                if is_pk:
                    primary_key.append(col_name)
                if is_unique:
                    unique.append([col_name])
            tables[name] = {
                "columns": columns,
                "primary_key": primary_key,
                "unique_constraints": unique,
                "rows": [],
            }
            continue

        insert = re.search(
            r'INSERT\s+INTO\s+"([^"]+)"\s*\((.*?)\)\s*VALUES\s*(.*)$',
            statement,
            re.IGNORECASE | re.DOTALL,
        )
        if insert:
            name, raw_columns, values = insert.groups()
            if name not in tables:
                raise ValueError(f"INSERT targets unknown table: {name}")
            columns = [clean_identifier(item) for item in split_csv(raw_columns)]
            for tuple_values in parse_tuples(values):
                if len(columns) != len(tuple_values):
                    raise ValueError(f"column/value mismatch in {name}")
                tables[name]["rows"].append(dict(zip(columns, map(scalar, tuple_values))))
            continue

        fk = re.search(
            r'ALTER\s+TABLE\s+"([^"]+)"\s+ADD\s+CONSTRAINT\s+"([^"]+)"\s+'
            r'FOREIGN\s+KEY\s*\("([^"]+)"\)\s+REFERENCES\s+"([^"]+)"\s*\("([^"]+)"\)',
            statement,
            re.IGNORECASE | re.DOTALL,
        )
        if fk:
            table, constraint, column, ref_table, ref_column = fk.groups()
            foreign_keys.append(
                {
                    "table": table,
                    "constraint": constraint,
                    "column": column,
                    "referenced_table": ref_table,
                    "referenced_column": ref_column,
                }
            )

    if not tables:
        raise ValueError("no CREATE TABLE statements found")

    result: dict[str, Any] = {
        "source": {
            "kind": "sql_import",
            "path": str(path.relative_to(ROOT)).replace("\\", "/") if path.is_relative_to(ROOT) else str(path),
            "bytes": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest(),
        },
        "schema": "public (implicit in import)",
        "ddl_inventory": {
            "explicit_index_statement_count": len(
                re.findall(r"(?im)^\s*CREATE\s+(?:UNIQUE\s+)?INDEX\b", text)
            ),
            "grant_statement_count": len(re.findall(r"(?im)^\s*GRANT\b", text)),
            "revoke_statement_count": len(re.findall(r"(?im)^\s*REVOKE\b", text)),
        },
        "tables": {},
        "foreign_keys": foreign_keys,
    }

    for name, table in tables.items():
        rows = table["rows"]
        null_counts = {
            column["name"]: sum(row.get(column["name"]) is None for row in rows)
            for column in table["columns"]
        }
        date_ranges = {}
        for column in table["columns"]:
            if column["data_type"].startswith(("date", "timestamp")):
                values = [row[column["name"]] for row in rows if row.get(column["name"]) is not None]
                date_ranges[column["name"]] = {
                    "min": min(values) if values else None,
                    "max": max(values) if values else None,
                }
        duplicate_keys = 0
        pk = table["primary_key"]
        if pk:
            keys = [tuple(row.get(column) for column in pk) for row in rows]
            duplicate_keys = sum(count - 1 for count in Counter(keys).values() if count > 1)
        duplicate_unique_keys: dict[str, int] = {}
        for constraint_columns in table["unique_constraints"]:
            keys = [
                tuple(row.get(column) for column in constraint_columns)
                for row in rows
                if all(row.get(column) is not None for column in constraint_columns)
            ]
            label = ",".join(constraint_columns)
            duplicate_unique_keys[label] = sum(
                count - 1 for count in Counter(keys).values() if count > 1
            )
        result["tables"][name] = {
            "columns": table["columns"],
            "primary_key": pk,
            "unique_constraints": table["unique_constraints"],
            "row_count": len(rows),
            "null_counts": null_counts,
            "date_ranges": date_ranges,
            "duplicate_primary_key_rows": duplicate_keys,
            "duplicate_unique_key_rows": duplicate_unique_keys,
        }

    orphan_counts: list[dict[str, Any]] = []
    for fk in foreign_keys:
        parent_values = {
            row.get(fk["referenced_column"])
            for row in tables[fk["referenced_table"]]["rows"]
        }
        count = sum(
            row.get(fk["column"]) is not None and row.get(fk["column"]) not in parent_values
            for row in tables[fk["table"]]["rows"]
        )
        orphan_counts.append({**fk, "orphan_rows": count})
    result["foreign_key_orphans"] = orphan_counts
    result["relationship_analysis"] = relationship_analysis(tables)
    result["baselines"] = transaction_baselines(tables)
    result["summary"] = {
        "table_count": len(tables),
        "primary_key_count": sum(bool(t["primary_key"]) for t in tables.values()),
        "foreign_key_count": len(foreign_keys),
        "unique_constraint_count": sum(
            len(table["unique_constraints"]) for table in tables.values()
        ),
        "duplicate_primary_key_rows": sum(
            table["duplicate_primary_key_rows"] for table in result["tables"].values()
        ),
        "duplicate_unique_key_rows": sum(
            sum(table["duplicate_unique_key_rows"].values())
            for table in result["tables"].values()
        ),
        "foreign_key_orphan_rows": sum(item["orphan_rows"] for item in orphan_counts),
    }
    result["_rows"] = {name: value["rows"] for name, value in tables.items()}
    return result


def relationship_analysis(tables: dict[str, dict[str, Any]]) -> dict[str, Any]:
    rows = {name: table["rows"] for name, table in tables.items()}
    agent_memberships: dict[Any, list[dict[str, Any]]] = defaultdict(list)
    for membership in rows.get("agent_agencies", []):
        agent_memberships[membership["agent_id"]].append(membership)
    unmatched = 0
    multiple = 0
    match_histogram: Counter[int] = Counter()
    for transaction in rows.get("transactions", []):
        transaction_date = transaction["transaction_date"]
        matches = 0
        for membership in agent_memberships.get(transaction["agent_id"], []):
            if membership["start_date"] <= transaction_date and (
                membership["end_date"] is None or transaction_date <= membership["end_date"]
            ):
                matches += 1
        match_histogram[matches] += 1
        unmatched += matches == 0
        multiple += matches > 1

    property_links = Counter(
        row["property_id"] for row in rows.get("property_neighbourhoods", [])
    )
    duplicated_properties = sum(count > 1 for count in property_links.values())
    extra_rows = sum(max(0, count - 1) for count in property_links.values())
    return {
        "agent_agencies": {
            "bridge_rows": len(rows.get("agent_agencies", [])),
            "distinct_agents": len(agent_memberships),
            "transactions_with_no_date_valid_agency": unmatched,
            "transactions_with_multiple_date_valid_agencies": multiple,
            "date_valid_match_count_histogram": dict(sorted(match_histogram.items())),
            "risk": "Joining transactions to agency history is not safe for additive KPIs.",
        },
        "property_neighbourhoods": {
            "bridge_rows": len(rows.get("property_neighbourhoods", [])),
            "distinct_properties": len(property_links),
            "properties_with_multiple_links": duplicated_properties,
            "extra_rows_if_joined_at_property_grain": extra_rows,
            "maximum_links_per_property": max(property_links.values(), default=0),
            "risk": "A property-to-neighbourhood join can fan out property or transaction facts.",
        },
    }


def transaction_baselines(tables: dict[str, dict[str, Any]]) -> dict[str, Any]:
    rows = {name: table["rows"] for name, table in tables.items()}
    property_state = {row["property_id"]: row["state"] for row in rows.get("properties", [])}
    output = {}
    for scope in ("ALL", "CA", "TX"):
        selected = [
            row
            for row in rows.get("transactions", [])
            if scope == "ALL" or property_state.get(row["property_id"]) == scope
        ]
        output[scope] = {
            "transaction_count": len(selected),
            "sale_price_sum": str(sum((row["sale_price"] for row in selected), Decimal("0"))),
            "distinct_property_count": len({row["property_id"] for row in selected}),
        }
    return output


def audit_database(dsn: str, expected: dict[str, Any]) -> dict[str, Any]:
    try:
        import psycopg
        from psycopg import sql
    except ImportError as exc:
        raise RuntimeError("live audit requires psycopg 3 (`python -m pip install psycopg[binary]`)") from exc

    expected_tables = sorted(expected["tables"])
    result: dict[str, Any] = {"source": {"kind": "live_postgresql"}, "schema": "public", "tables": {}}
    conn = psycopg.connect(dsn, autocommit=True, connect_timeout=5)
    try:
        conn.execute("BEGIN READ ONLY")
        result["read_only_transaction"] = conn.execute("SHOW transaction_read_only").fetchone()[0]
        if result["read_only_transaction"] != "on":
            raise RuntimeError("database did not confirm a read-only transaction")
        version, database, user = conn.execute(
            "SELECT version(), current_database(), current_user"
        ).fetchone()
        result.update(
            {
                "version": version,
                "database": database,
                "current_user_fingerprint": identifier_fingerprint(user),
            }
        )
        superuser, bypass_rls = conn.execute(
            "SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user"
        ).fetchone()
        result["current_user_attributes"] = {
            "superuser": superuser,
            "bypass_rls": bypass_rls,
        }
        catalog_rows = conn.execute(
            """
            SELECT table_name, column_name, data_type, is_nullable, ordinal_position
            FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = ANY(%s)
            ORDER BY table_name, ordinal_position
            """,
            (expected_tables,),
        ).fetchall()
        columns_by_table: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for table, column, data_type, nullable, _position in catalog_rows:
            columns_by_table[table].append(
                {"name": column, "data_type": data_type, "nullable": nullable == "YES"}
            )

        pk_rows = conn.execute(
            """
            SELECT tc.table_name, tc.constraint_name, kcu.column_name, kcu.ordinal_position
            FROM information_schema.table_constraints tc
            JOIN information_schema.key_column_usage kcu
              ON tc.constraint_schema = kcu.constraint_schema
             AND tc.constraint_name = kcu.constraint_name
            WHERE tc.table_schema = 'public' AND tc.constraint_type = 'PRIMARY KEY'
              AND tc.table_name = ANY(%s)
            ORDER BY tc.table_name, kcu.ordinal_position
            """,
            (expected_tables,),
        ).fetchall()
        pks: dict[str, list[str]] = defaultdict(list)
        for table, _constraint, column, _position in pk_rows:
            pks[table].append(column)

        unique_rows = conn.execute(
            """
            SELECT tc.table_name, tc.constraint_name, kcu.column_name, kcu.ordinal_position
            FROM information_schema.table_constraints tc
            JOIN information_schema.key_column_usage kcu
              ON tc.constraint_schema = kcu.constraint_schema
             AND tc.constraint_name = kcu.constraint_name
            WHERE tc.table_schema = 'public' AND tc.constraint_type = 'UNIQUE'
              AND tc.table_name = ANY(%s)
            ORDER BY tc.table_name, tc.constraint_name, kcu.ordinal_position
            """,
            (expected_tables,),
        ).fetchall()
        uniques: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
        for table, constraint, column, _position in unique_rows:
            uniques[table][constraint].append(column)

        fk_rows = conn.execute(
            """
            SELECT tc.table_name, tc.constraint_name, kcu.column_name,
                   ccu.table_name, ccu.column_name
            FROM information_schema.table_constraints tc
            JOIN information_schema.key_column_usage kcu
              ON tc.constraint_schema = kcu.constraint_schema
             AND tc.constraint_name = kcu.constraint_name
            JOIN information_schema.constraint_column_usage ccu
              ON tc.constraint_schema = ccu.constraint_schema
             AND tc.constraint_name = ccu.constraint_name
            WHERE tc.table_schema = 'public' AND tc.constraint_type = 'FOREIGN KEY'
              AND tc.table_name = ANY(%s)
            ORDER BY tc.table_name, tc.constraint_name
            """,
            (expected_tables,),
        ).fetchall()
        result["foreign_keys"] = [
            {
                "table": table,
                "constraint": constraint,
                "column": column,
                "referenced_table": ref_table,
                "referenced_column": ref_column,
            }
            for table, constraint, column, ref_table, ref_column in fk_rows
        ]

        index_rows = conn.execute(
            """
            SELECT tablename, indexname, indexdef
            FROM pg_indexes
            WHERE schemaname = 'public' AND tablename = ANY(%s)
            ORDER BY tablename, indexname
            """,
            (expected_tables,),
        ).fetchall()
        indexes: dict[str, list[dict[str, str]]] = defaultdict(list)
        for table, name, definition in index_rows:
            indexes[table].append({"name": name, "definition": definition})

        privilege_rows = conn.execute(
            """
            SELECT table_name, privilege_type
            FROM information_schema.role_table_grants
            WHERE table_schema = 'public' AND grantee = current_user
              AND table_name = ANY(%s)
            ORDER BY table_name, privilege_type
            """,
            (expected_tables,),
        ).fetchall()
        privileges: dict[str, list[str]] = defaultdict(list)
        for table, privilege in privilege_rows:
            privileges[table].append(privilege)

        table_security_rows = conn.execute(
            """
            SELECT c.relname, pg_get_userbyid(c.relowner), c.relrowsecurity,
                   c.relforcerowsecurity
            FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p')
              AND c.relname = ANY(%s)
            ORDER BY c.relname
            """,
            (expected_tables,),
        ).fetchall()
        table_security = {
            table: {
                "owner_fingerprint": identifier_fingerprint(owner),
                "row_security_enabled": row_security,
                "row_security_forced": force_row_security,
            }
            for table, owner, row_security, force_row_security in table_security_rows
        }
        schema_privileges = {
            privilege: conn.execute(
                "SELECT has_schema_privilege(current_user, 'public', %s)",
                (privilege,),
            ).fetchone()[0]
            for privilege in ("USAGE", "CREATE")
        }
        result["current_user_schema_privileges"] = schema_privileges

        for table in expected_tables:
            if table not in columns_by_table:
                result["tables"][table] = {"missing": True}
                continue
            count = conn.execute(
                sql.SQL("SELECT count(*) FROM {}.{}").format(
                    sql.Identifier("public"), sql.Identifier(table)
                )
            ).fetchone()[0]
            actual_columns = columns_by_table[table]
            null_exprs = [
                sql.SQL("count(*) FILTER (WHERE {} IS NULL)").format(sql.Identifier(column["name"]))
                for column in actual_columns
            ]
            null_values = conn.execute(
                sql.SQL("SELECT {} FROM {}.{}").format(
                    sql.SQL(", ").join(null_exprs),
                    sql.Identifier("public"),
                    sql.Identifier(table),
                )
            ).fetchone()
            null_counts = {
                column["name"]: value for column, value in zip(actual_columns, null_values)
            }
            date_ranges: dict[str, dict[str, Any]] = {}
            for column in actual_columns:
                if column["data_type"].startswith(("date", "timestamp")):
                    minimum, maximum = conn.execute(
                        sql.SQL("SELECT min({}), max({}) FROM {}.{}").format(
                            sql.Identifier(column["name"]),
                            sql.Identifier(column["name"]),
                            sql.Identifier("public"),
                            sql.Identifier(table),
                        )
                    ).fetchone()
                    date_ranges[column["name"]] = {"min": minimum, "max": maximum}
            duplicate_keys = 0
            if pks.get(table):
                pk_identifiers = sql.SQL(", ").join(map(sql.Identifier, pks[table]))
                duplicate_keys = conn.execute(
                    sql.SQL(
                        "SELECT COALESCE(sum(n - 1), 0) FROM "
                        "(SELECT count(*) AS n FROM {}.{} GROUP BY {} HAVING count(*) > 1) d"
                    ).format(sql.Identifier("public"), sql.Identifier(table), pk_identifiers)
                ).fetchone()[0]
            duplicate_unique_keys: dict[str, int] = {}
            actual_unique_constraints = list(uniques.get(table, {}).values())
            for constraint_columns in actual_unique_constraints:
                if not set(constraint_columns).issubset(
                    {column["name"] for column in actual_columns}
                ):
                    continue
                unique_identifiers = sql.SQL(", ").join(map(sql.Identifier, constraint_columns))
                non_null = sql.SQL(" AND ").join(
                    sql.SQL("{} IS NOT NULL").format(sql.Identifier(column))
                    for column in constraint_columns
                )
                duplicate_unique_keys[",".join(constraint_columns)] = conn.execute(
                    sql.SQL(
                        "SELECT COALESCE(sum(n - 1), 0) FROM "
                        "(SELECT count(*) AS n FROM {}.{} WHERE {} "
                        "GROUP BY {} HAVING count(*) > 1) d"
                    ).format(
                        sql.Identifier("public"),
                        sql.Identifier(table),
                        non_null,
                        unique_identifiers,
                    )
                ).fetchone()[0]
            result["tables"][table] = {
                "columns": columns_by_table[table],
                "primary_key": pks.get(table, []),
                "unique_constraints": actual_unique_constraints,
                "indexes": indexes.get(table, []),
                "current_user_direct_role_grants": privileges.get(table, []),
                "current_user_effective_privileges": [
                    privilege
                    for privilege in (
                        "SELECT",
                        "INSERT",
                        "UPDATE",
                        "DELETE",
                        "TRUNCATE",
                        "REFERENCES",
                        "TRIGGER",
                    )
                    if conn.execute(
                        "SELECT has_table_privilege(current_user, %s, %s)",
                        (f"public.{table}", privilege),
                    ).fetchone()[0]
                ],
                "security": table_security.get(table, {}),
                "row_count": count,
                "null_counts": null_counts,
                "date_ranges": date_ranges,
                "duplicate_primary_key_rows": duplicate_keys,
                "duplicate_unique_key_rows": duplicate_unique_keys,
            }

        orphan_counts = []
        for fk in result["foreign_keys"]:
            orphan_count = conn.execute(
                sql.SQL(
                    "SELECT count(*) FROM {}.{} child "
                    "LEFT JOIN {}.{} parent ON child.{} = parent.{} "
                    "WHERE child.{} IS NOT NULL AND parent.{} IS NULL"
                ).format(
                    sql.Identifier("public"),
                    sql.Identifier(fk["table"]),
                    sql.Identifier("public"),
                    sql.Identifier(fk["referenced_table"]),
                    sql.Identifier(fk["column"]),
                    sql.Identifier(fk["referenced_column"]),
                    sql.Identifier(fk["column"]),
                    sql.Identifier(fk["referenced_column"]),
                )
            ).fetchone()[0]
            orphan_counts.append({**fk, "orphan_rows": orphan_count})
        result["foreign_key_orphans"] = orphan_counts

        all_count, all_sum, all_properties = conn.execute(
            "SELECT count(*), COALESCE(sum(sale_price), 0), count(DISTINCT property_id) FROM public.transactions"
        ).fetchone()
        baselines = {
            "ALL": {
                "transaction_count": all_count,
                "sale_price_sum": str(all_sum),
                "distinct_property_count": all_properties,
            }
        }
        for scope in ("CA", "TX"):
            count, total, property_count = conn.execute(
                """
                SELECT count(*), COALESCE(sum(t.sale_price), 0), count(DISTINCT t.property_id)
                FROM public.transactions t
                JOIN public.properties p ON p.property_id = t.property_id
                WHERE p.state = %s
                """,
                (scope,),
            ).fetchone()
            baselines[scope] = {
                "transaction_count": count,
                "sale_price_sum": str(total),
                "distinct_property_count": property_count,
            }
        result["baselines"] = baselines

        unmatched, multiple = conn.execute(
            """
            SELECT count(*) FILTER (WHERE matches = 0), count(*) FILTER (WHERE matches > 1)
            FROM (
                SELECT t.transaction_id, count(aa.agent_agency_id) AS matches
                FROM public.transactions t
                LEFT JOIN public.agent_agencies aa
                  ON aa.agent_id = t.agent_id
                 AND aa.start_date <= t.transaction_date
                 AND (aa.end_date IS NULL OR t.transaction_date <= aa.end_date)
                GROUP BY t.transaction_id
            ) matched
            """
        ).fetchone()
        bridge_rows, distinct_agents = conn.execute(
            "SELECT count(*), count(DISTINCT agent_id) FROM public.agent_agencies"
        ).fetchone()
        pn_rows, pn_properties, pn_multi, pn_extra, pn_max = conn.execute(
            """
            SELECT COALESCE(sum(n), 0), count(*), count(*) FILTER (WHERE n > 1),
                   COALESCE(sum(GREATEST(n - 1, 0)), 0), COALESCE(max(n), 0)
            FROM (SELECT property_id, count(*) AS n
                  FROM public.property_neighbourhoods GROUP BY property_id) links
            """
        ).fetchone()
        result["relationship_analysis"] = {
            "agent_agencies": {
                "bridge_rows": bridge_rows,
                "distinct_agents": distinct_agents,
                "transactions_with_no_date_valid_agency": unmatched,
                "transactions_with_multiple_date_valid_agencies": multiple,
                "risk": "Joining transactions to agency history is not safe for additive KPIs.",
            },
            "property_neighbourhoods": {
                "bridge_rows": pn_rows,
                "distinct_properties": pn_properties,
                "properties_with_multiple_links": pn_multi,
                "extra_rows_if_joined_at_property_grain": pn_extra,
                "maximum_links_per_property": pn_max,
                "risk": "A property-to-neighbourhood join can fan out property or transaction facts.",
            },
        }
        result["summary"] = {
            "table_count": sum(not value.get("missing", False) for value in result["tables"].values()),
            "primary_key_count": sum(bool(columns) for columns in pks.values()),
            "foreign_key_count": len(result["foreign_keys"]),
            "unique_constraint_count": sum(
                len(constraints) for constraints in uniques.values()
            ),
            "duplicate_primary_key_rows": sum(
                value.get("duplicate_primary_key_rows", 0) for value in result["tables"].values()
            ),
            "duplicate_unique_key_rows": sum(
                sum(value.get("duplicate_unique_key_rows", {}).values())
                for value in result["tables"].values()
            ),
            "foreign_key_orphan_rows": sum(item["orphan_rows"] for item in orphan_counts),
        }
        import_foreign_keys = {
            (
                item["table"],
                item["column"],
                item["referenced_table"],
                item["referenced_column"],
            )
            for item in expected["foreign_keys"]
        }
        database_foreign_keys = {
            (
                item["table"],
                item["column"],
                item["referenced_table"],
                item["referenced_column"],
            )
            for item in result["foreign_keys"]
        }
        result["comparison_to_import"] = {
            "missing_tables": [
                table for table in expected_tables if result["tables"][table].get("missing", False)
            ],
            "column_differences": {
                table: {
                    "import": [
                        {"name": column["name"], "nullable": column["nullable"]}
                        for column in expected["tables"][table]["columns"]
                    ],
                    "database": [
                        {"name": column["name"], "nullable": column["nullable"]}
                        for column in result["tables"][table].get("columns", [])
                    ],
                }
                for table in expected_tables
                if [
                    {"name": column["name"], "nullable": column["nullable"]}
                    for column in expected["tables"][table]["columns"]
                ]
                != [
                    {"name": column["name"], "nullable": column["nullable"]}
                    for column in result["tables"][table].get("columns", [])
                ]
            },
            "primary_key_differences": {
                table: {
                    "import": expected["tables"][table]["primary_key"],
                    "database": result["tables"][table].get("primary_key", []),
                }
                for table in expected_tables
                if expected["tables"][table]["primary_key"]
                != result["tables"][table].get("primary_key", [])
            },
            "unique_constraint_differences": {
                table: {
                    "import": expected["tables"][table]["unique_constraints"],
                    "database": result["tables"][table].get("unique_constraints", []),
                }
                for table in expected_tables
                if sorted(expected["tables"][table]["unique_constraints"])
                != sorted(result["tables"][table].get("unique_constraints", []))
            },
            "foreign_key_differences": {
                "missing_from_database": [list(item) for item in sorted(import_foreign_keys - database_foreign_keys)],
                "extra_in_database": [list(item) for item in sorted(database_foreign_keys - import_foreign_keys)],
            },
            "row_count_differences": {
                table: {
                    "import": expected["tables"][table]["row_count"],
                    "database": result["tables"][table].get("row_count"),
                }
                for table in expected_tables
                if result["tables"][table].get("row_count")
                != expected["tables"][table]["row_count"]
            },
            "baseline_differences": {
                scope: {"import": expected["baselines"][scope], "database": baselines[scope]}
                for scope in ("ALL", "CA", "TX")
                if expected["baselines"][scope] != baselines[scope]
            },
        }
        conn.execute("ROLLBACK")
    finally:
        conn.close()
    return result


def public_result(result: dict[str, Any]) -> dict[str, Any]:
    """Remove parsed raw rows before serialization."""
    return {key: value for key, value in result.items() if key != "_rows"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sql", type=Path, default=DEFAULT_SQL, help="SQL import to audit")
    parser.add_argument("--dsn", help="PostgreSQL DSN; preferably use AUDIT_DATABASE_URL in backend/.env")
    parser.add_argument("--require-db", action="store_true", help="fail if no live DSN is available")
    parser.add_argument("--output", type=Path, help="write JSON report to this file")
    args = parser.parse_args()

    import_audit = parse_import(args.sql.resolve())
    dsn = configured_audit_dsn(args.dsn)
    report: dict[str, Any] = {
        "audit_timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "import_audit": public_result(import_audit),
    }
    exit_code = 0
    if dsn:
        try:
            report["database_audit"] = audit_database(dsn, import_audit)
        except Exception as exc:  # keep import evidence even if DB inspection fails
            report["database_audit"] = {
                "status": "blocked",
                "reason": (
                    f"{type(exc).__name__}: live database audit failed; "
                    "verify the read-only credential and psycopg installation."
                ),
            }
            exit_code = 2
    else:
        report["database_audit"] = {
            "status": "blocked",
            "reason": "No DSN supplied via --dsn, AUDIT_DATABASE_URL, backend/.env, or DATABASE_URL.",
        }
        if args.require_db:
            exit_code = 2

    rendered = json.dumps(report, ensure_ascii=False, indent=2, default=str)
    if args.output:
        args.output.write_text(rendered + "\n", encoding="utf-8")
    else:
        print(rendered)
    return exit_code


if __name__ == "__main__":
    with isolated_libpq_environment():
        sys.exit(main())
