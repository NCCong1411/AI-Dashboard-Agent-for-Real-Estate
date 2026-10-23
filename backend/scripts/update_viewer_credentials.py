"""Explicitly rotate one ALL-scope Viewer and revoke its existing sessions."""

import argparse
from datetime import UTC, datetime
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import dotenv_values
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.auth.passwords import hash_password
from app.config import get_settings
from app.db.connections import create_runtime_engine
from app.db.models import User, Session as LoginSession


def update_credentials(database, old_username, username, password):
    if not username or username != username.strip() or len(username) > 128:
        raise ValueError("Invalid username")
    if not password or not 12 <= len(password) <= 1024:
        raise ValueError("Invalid password length")
    user = database.scalar(select(User).where(
        User.username == old_username).with_for_update())
    if (user is None or user.role != "viewer" or user.scope_profile != "ALL"
            or not user.is_active):
        raise ValueError("Original active ALL Viewer not found")
    duplicate = database.scalar(select(User).where(
        User.username == username, User.id != user.id))
    if duplicate is not None:
        raise ValueError("Username already exists")
    user.username = username
    user.password_hash = hash_password(password)
    database.execute(update(LoginSession).where(
        LoginSession.user_id == user.id, LoginSession.revoked_at.is_(None)
    ).values(revoked_at=datetime.now(UTC)))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True)
    parser.add_argument("--old-username", default="viewer_all")
    args = parser.parse_args()
    path = Path(args.input)
    engine = None
    try:
        values = dotenv_values(path, interpolate=False, encoding="utf-8-sig")
        engine = create_runtime_engine(get_settings())
        with Session(engine) as database, database.begin():
            update_credentials(database, args.old_username,
                               values.get("DEMO_USERNAME"), values.get("DEMO_PASSWORD"))
    except Exception:
        raise SystemExit("Update failed; no secret was printed. Do not retry blindly if connection was lost.")
    finally:
        path.unlink(missing_ok=True)
        if engine is not None:
            engine.dispose()
    print("Viewer account updated; previous sessions revoked.")


if __name__ == "__main__":
    main()
