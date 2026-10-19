"""Opt-in first-time seed for four local demo users with private passwords.

The ignored artifacts remain local; no password or database URL is printed.
Existing demo identities are never reset by this bootstrap wrapper.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import secrets
import sys

from dotenv import dotenv_values
from sqlalchemy import text


BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.config import get_settings
from app.db.connections import create_runtime_engine
from provision_local_roles import _write_secret_file
from seed_users import ACCOUNTS, seed


RUNTIME_DIR = BACKEND / ".runtime"
ROLE_CREDENTIALS = RUNTIME_DIR / "local-postgres-credentials.env"
PASSWORD_ARTIFACT = RUNTIME_DIR / "local-demo-accounts.env"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="insert the four first-time demo users")
    args = parser.parse_args()
    if not ROLE_CREDENTIALS.is_file():
        print("demo seed unavailable: provisioned runtime credential is missing")
        return 2
    values = dotenv_values(ROLE_CREDENTIALS, interpolate=False)
    runtime_url = values.get("RUNTIME_DATABASE_URL")
    if not isinstance(runtime_url, str) or not runtime_url:
        print("demo seed unavailable: provisioned runtime URL is missing")
        return 2
    if PASSWORD_ARTIFACT.exists():
        print("demo seed refused: private demo password artifact already exists")
        return 2
    prior = os.environ.get("RUNTIME_DATABASE_URL")
    os.environ["RUNTIME_DATABASE_URL"] = runtime_url
    get_settings.cache_clear()
    try:
        engine = create_runtime_engine(get_settings())
        try:
            with engine.connect() as connection:
                existing = connection.scalar(text(
                    "SELECT count(*) FROM app.users WHERE username IN "
                    "('builder_all','viewer_all','viewer_ca','builder_tx')"
                ))
        finally:
            engine.dispose()
        if existing:
            print("demo seed refused: one or more demo usernames already exist")
            return 2
        if not args.apply:
            print("demo seed preflight passed; no users or passwords created")
            return 0
        passwords = {variable: secrets.token_urlsafe(24) for *_, variable in ACCOUNTS}
        content = "\n".join(f"{variable}={value}" for variable, value in passwords.items()) + "\n"
        _write_secret_file(PASSWORD_ARTIFACT, content)
        try:
            for variable, value in passwords.items():
                os.environ[variable] = value
            get_settings.cache_clear()
            seed()
        finally:
            for variable in passwords:
                os.environ.pop(variable, None)
            get_settings.cache_clear()
        print("seeded four demo users; passwords saved only in ignored owner-only local artifact")
        return 0
    finally:
        if prior is None:
            os.environ.pop("RUNTIME_DATABASE_URL", None)
        else:
            os.environ["RUNTIME_DATABASE_URL"] = prior
        get_settings.cache_clear()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"demo seed failed ({type(error).__name__}); credentials and DB details suppressed")
        raise SystemExit(1) from None
