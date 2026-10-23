"""Provision the same verified RLS login roles on the private Compose database.

Read-only by default. This intentionally does not relax the local-only command.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets

import psycopg
from psycopg.conninfo import conninfo_to_dict

from provision_local_roles import (
    DEFAULT_OUTPUT,
    EXPECTED_DATABASE,
    ProvisioningStateUncertain,
    TARGET_ROLES,
    _credential_text,
    _write_secret_file,
    inspect_target,
    provision,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    operation = parser.add_mutually_exclusive_group()
    operation.add_argument("--apply", action="store_true")
    operation.add_argument("--reconcile", action="store_true")
    parser.add_argument("--confirm-cluster", default="")
    args = parser.parse_args()

    dsn = os.environ.get("AUDIT_DATABASE_URL", "")
    if not dsn:
        print("Production role provisioning unavailable: operator URL missing")
        return 2
    try:
        details = conninfo_to_dict(dsn)
        if (details.get("host") != "db" or details.get("dbname") != EXPECTED_DATABASE
                or details.get("user") != "postgres" or details.get("hostaddr")
                or details.get("service")):
            raise RuntimeError("operator URL must target the private Compose database")
        with psycopg.connect(dsn, autocommit=True, connect_timeout=5) as conn:
            if (conn.info.host != "db" or conn.info.dbname != EXPECTED_DATABASE
                    or conn.info.user != "postgres"):
                raise RuntimeError("connected endpoint differs from operator URL")
            cluster = conn.execute(
                "SELECT system_identifier::text FROM pg_control_system()"
            ).fetchone()[0]
            status = inspect_target(conn, cluster, reconcile=args.reconcile)
            if args.reconcile:
                print(json.dumps({**status, "reconciliation_passed": True}))
                return 0
            if not args.apply:
                print(json.dumps({**status, "preflight_passed": True}))
                return 0
            if args.confirm_cluster != cluster:
                raise RuntimeError("--confirm-cluster must match preflight output")
            passwords = {role: secrets.token_urlsafe(48) for role in TARGET_ROLES}
            output = _write_secret_file(
                DEFAULT_OUTPUT, _credential_text(conn, passwords)
            )
            try:
                provision(conn, passwords)
            except ProvisioningStateUncertain:
                print("COMMIT uncertain: retain private credentials and run --reconcile")
                return 3
            except BaseException:
                output.unlink(missing_ok=True)
                raise
        print(json.dumps({"roles_created": len(TARGET_ROLES),
                          "credential_file": str(output), "secrets_printed": False}))
        return 0
    except (OSError, ValueError, RuntimeError, psycopg.Error):
        print("Production role provisioning failed safely; no secret was printed")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
