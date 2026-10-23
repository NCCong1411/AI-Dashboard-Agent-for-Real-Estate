"""Create private, fresh demo secrets on the VPS; never prints their values."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import re
import secrets


ROOT = Path(__file__).resolve().parents[2]
PRIVATE = ROOT / "deploy" / "secrets"
ROLE_NAMES = (
    "RUNTIME_DATABASE_URL",
    "READER_ALL_DATABASE_URL",
    "READER_CA_DATABASE_URL",
    "READER_TX_DATABASE_URL",
)
ACCOUNT_NAMES = (
    "SEED_BUILDER_ALL_PASSWORD",
    "SEED_VIEWER_ALL_PASSWORD",
    "SEED_VIEWER_CA_PASSWORD",
    "SEED_BUILDER_TX_PASSWORD",
)


def exclusive_secret(path: Path, value: str) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(path, 0o600)
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def parse_roles(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        key, separator, value = line.partition("=")
        if not separator or key in values or key not in ROLE_NAMES or not value:
            raise ValueError("role credential file is invalid")
        values[key] = value
    if set(values) != set(ROLE_NAMES):
        raise ValueError("role credential file is incomplete")
    return values


def initialize(domain: str) -> None:
    if not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.duckdns\.org", domain):
        raise ValueError("domain must be one DuckDNS hostname")
    PRIVATE.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(PRIVATE, 0o700)
    roles = PRIVATE / "roles"
    roles.mkdir(mode=0o700, exist_ok=True)
    os.chmod(roles, 0o700)
    targets = ("postgres_password.txt", "operator.env", "site.env")
    if any((PRIVATE / name).exists() for name in targets):
        raise FileExistsError("production initialization already exists")
    password = secrets.token_hex(32)
    native = f"postgresql://postgres:{password}@db:5432/real_estate_db"
    migration = f"postgresql+psycopg://postgres:{password}@db:5432/real_estate_db"
    exclusive_secret(PRIVATE / "postgres_password.txt", password + "\n")
    exclusive_secret(PRIVATE / "operator.env", (
        "APP_ENV=production\n"
        f"AUDIT_DATABASE_URL={native}\n"
        f"MIGRATION_DATABASE_URL={migration}\n"
    ))
    exclusive_secret(PRIVATE / "site.env", f"PUBLIC_DOMAIN={domain}\n")
    print("Created private database/operator/site files; no values printed")


def finish() -> None:
    if not (PRIVATE / "site.env").is_file():
        raise RuntimeError("run init first")
    roles = parse_roles(PRIVATE / "roles" / "local-postgres-credentials.env")
    if any((PRIVATE / name).exists() for name in ("backend.env", "accounts.env")):
        raise FileExistsError("production backend/account files already exist")
    site = (PRIVATE / "site.env").read_text(encoding="utf-8").strip()
    if not site.startswith("PUBLIC_DOMAIN="):
        raise RuntimeError("site configuration is invalid")
    domain = site.partition("=")[2]
    backend = (
        "APP_ENV=production\n"
        f"FRONTEND_ORIGIN=https://{domain}\n"
        "LLM_MODE=mock\n"
        "LOG_LEVEL=INFO\n"
        + "".join(f"{name}={roles[name]}\n" for name in ROLE_NAMES)
    )
    accounts = "".join(
        f"{name}={secrets.token_urlsafe(32)}\n" for name in ACCOUNT_NAMES
    )
    exclusive_secret(PRIVATE / "backend.env", backend)
    exclusive_secret(PRIVATE / "accounts.env", accounts)
    print("Created private backend and demo-account files; no values printed")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init")
    init.add_argument("domain")
    commands.add_parser("finish")
    args = parser.parse_args()
    if args.command == "init":
        initialize(args.domain)
    else:
        finish()


if __name__ == "__main__":
    main()
