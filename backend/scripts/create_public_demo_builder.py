"""Create only the dedicated public Builder; never rotate private accounts."""
import argparse
import json
import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.config import get_settings
from app.agent.llm import require_live_configuration
from app.auth.passwords import hash_password, verify_password
from app.db.connections import create_runtime_engine
from app.db.models import User, AuditEvent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--credentials", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    config = get_settings()
    require_live_configuration(config)
    if (config.app_env != "production" or config.llm_mode != "live"
            or config.llm_global_budget_per_day_usd is None
            or config.llm_global_budget_per_day_usd > Decimal("0.10") or config.llm_max_calls_per_run != 1):
        raise SystemExit("Public demo requires production live mode, one call and global daily cap <= USD 0.10")
    credentials = json.loads(args.credentials.read_text())
    if credentials["username"] != "builder_demo" or not 16 <= len(credentials["password"]) <= 128:
        raise SystemExit("Invalid dedicated public demo credentials")
    engine = create_runtime_engine(config)
    try:
        with Session(engine) as database, database.begin():
            existing = database.scalar(select(User).where(User.username == "builder_demo").with_for_update())
            if existing is not None:
                if (existing.role != "builder" or existing.scope_profile != "ALL" or not existing.is_active
                        or not verify_password(existing.password_hash, credentials["password"])):
                    raise SystemExit("Existing public account differs; operator review required")
                print("Dedicated public Builder already matches; private users unchanged.")
                return
            if not args.apply:
                print("Dry run: dedicated public Builder may be created.")
                return
            user = User(username="builder_demo", password_hash=hash_password(credentials["password"]),
                        role="builder", scope_profile="ALL")
            database.add(user)
            database.flush()
            database.add(AuditEvent(actor_user_id=user.id, action="public_demo_builder_created",
                                    target_type="user", target_id=str(user.id), details={"public_demo": True}))
        print("Dedicated public Builder created; private users unchanged; no passwords printed.")
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
