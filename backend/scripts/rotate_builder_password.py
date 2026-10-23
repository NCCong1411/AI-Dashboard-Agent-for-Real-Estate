"""Rotate builder_all's password without changing role, scope, or other users."""

import argparse
from datetime import UTC, datetime
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.auth.passwords import hash_password
from app.config import get_settings
from app.db.connections import create_runtime_engine
from app.db.models import User, Session as LoginSession


def rotate(database, password):
    if not password or not 12 <= len(password) <= 1024 or "\n" in password or "\r" in password:
        raise ValueError("Invalid password length or newline")
    user = database.scalar(select(User).where(
        User.username == "builder_all").with_for_update())
    if (user is None or user.role != "builder" or user.scope_profile != "ALL"
            or not user.is_active):
        raise ValueError("Expected active ALL Builder not found")
    user.password_hash = hash_password(password)
    database.execute(update(LoginSession).where(
        LoginSession.user_id == user.id, LoginSession.revoked_at.is_(None)
    ).values(revoked_at=datetime.now(UTC)))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True)
    args = parser.parse_args()
    path = Path(args.input)
    engine = None
    try:
        password = path.read_text(encoding="utf-8")
        engine = create_runtime_engine(get_settings())
        with Session(engine) as database, database.begin():
            rotate(database, password)
    except Exception:
        raise SystemExit("Builder rotation failed; no secret was printed.")
    finally:
        path.unlink(missing_ok=True)
        if engine is not None:
            engine.dispose()
    print("Builder password updated; previous sessions revoked.")


if __name__ == "__main__":
    main()
