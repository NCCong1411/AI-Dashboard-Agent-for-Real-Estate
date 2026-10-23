"""Credential rotation changes only the intended Viewer and revokes its sessions."""

from datetime import UTC, datetime, timedelta
from pathlib import Path
import sys

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.auth.passwords import hash_password, verify_password
from app.db.models import User, Session as LoginSession
from scripts.update_viewer_credentials import update_credentials
from scripts.rotate_builder_password import rotate


@pytest.fixture
def database():
    engine = create_engine("sqlite://", execution_options={"schema_translate_map": {"app": None}})
    User.__table__.create(engine)
    LoginSession.__table__.create(engine)
    with Session(engine) as db:
        viewer = User(username="viewer_all", role="viewer", scope_profile="ALL",
                      password_hash=hash_password("original-password"), is_active=True)
        builder = User(username="builder_all", role="builder", scope_profile="ALL",
                       password_hash=hash_password("builder-password"), is_active=True)
        db.add_all([viewer, builder])
        db.flush()
        for index, user in enumerate([viewer, builder]):
            db.add(LoginSession(user_id=user.id, token_hash=str(index),
                                expires_at=datetime.now(UTC) + timedelta(hours=1)))
        db.commit()
        yield db
    engine.dispose()


def test_rotation_preserves_privileges_and_revokes_only_target(database):
    update_credentials(database, "viewer_all", "new_viewer", "replacement-password")
    database.commit()
    user = database.scalar(select(User).where(User.username == "new_viewer"))
    assert (user.role, user.scope_profile, user.is_active) == ("viewer", "ALL", True)
    assert verify_password(user.password_hash, "replacement-password")
    assert not verify_password(user.password_hash, "original-password")
    sessions = database.scalars(select(LoginSession).order_by(LoginSession.token_hash)).all()
    assert sessions[0].revoked_at is not None
    assert sessions[1].revoked_at is None


@pytest.mark.parametrize("old,new,password", [
    ("builder_all", "renamed", "replacement-password"),
    ("viewer_all", "builder_all", "replacement-password"),
    ("viewer_all", "new_viewer", "short"),
])
def test_rejects_wrong_role_collision_and_weak_password(database, old, new, password):
    with pytest.raises(ValueError):
        update_credentials(database, old, new, password)
    database.rollback()
    viewer = database.scalar(select(User).where(User.username == "viewer_all"))
    assert verify_password(viewer.password_hash, "original-password")
    assert all(s.revoked_at is None for s in database.scalars(select(LoginSession)))


def test_builder_rotation_preserves_viewer_and_revokes_only_builder(database):
    viewer = database.scalar(select(User).where(User.username == "viewer_all"))
    original_viewer_hash = viewer.password_hash
    rotate(database, "replacement-builder-password")
    database.commit()
    builder = database.scalar(select(User).where(User.username == "builder_all"))
    assert verify_password(builder.password_hash, "replacement-builder-password")
    assert (builder.role, builder.scope_profile) == ("builder", "ALL")
    assert viewer.password_hash == original_viewer_hash
    sessions = database.scalars(select(LoginSession).order_by(LoginSession.token_hash)).all()
    assert sessions[0].revoked_at is None
    assert sessions[1].revoked_at is not None


@pytest.mark.parametrize("password", ["short", "x" * 1025, "long-password\n"])
def test_builder_rejects_invalid_password(database, password):
    with pytest.raises(ValueError):
        rotate(database, password)
    database.rollback()
    builder = database.scalar(select(User).where(User.username == "builder_all"))
    assert verify_password(builder.password_hash, "builder-password")


def test_builder_private_file_update_preserves_other_secrets(tmp_path, monkeypatch):
    import runpy
    from dotenv import dotenv_values
    project = Path(__file__).resolve().parents[3]
    module = runpy.run_path(str(project / "deploy/scripts/rotate_lightsail_builder.py"))
    compile(module["HOST_FINISH"], "host_finish", "exec")
    private = tmp_path / "deploy/secrets"
    private.mkdir(parents=True)
    (private / "accounts.env").write_text(
        "SEED_BUILDER_ALL_PASSWORD=before\nSEED_VIEWER_CA_PASSWORD=unchanged\n")
    desired = 'a new "quoted" password'
    (private / "builder-password-update.txt").write_text(desired, encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    exec(module["HOST_FINISH"].split('origin = "https://"')[0], {})
    values = dotenv_values(private / "accounts.env", interpolate=False)
    assert values["SEED_BUILDER_ALL_PASSWORD"] == desired
    assert values["SEED_VIEWER_CA_PASSWORD"] == "unchanged"
    assert not (private / "builder-password-update.txt").exists()
