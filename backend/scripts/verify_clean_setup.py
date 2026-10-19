"""Rehearse the documented setup in a fresh snapshot and virtual environment.

Copies git-visible source (including uncommitted changes) but no local env,
dependencies or runtime artifacts. Logs stay in an ignored directory. Nothing
is installed into the caller's environment and no existing database is used.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from time import perf_counter


ROOT = Path(__file__).resolve().parents[2]


def setup_environment(values: dict[str, str]) -> dict[str, str]:
    """Keep registry/proxy policy, but prevent caller install destinations."""
    blocked = {
        "VIRTUAL_ENV", "PYTHONPATH", "PYTHONHOME", "PYTHONUSERBASE", "PYTHONSTARTUP",
        "PIP_TARGET", "PIP_PREFIX", "PIP_USER", "PIP_ROOT", "PIP_PYTHON", "PIP_CONFIG_FILE",
        "DATABASE_URL", "AUDIT_DATABASE_URL", "RESTORE_TEST_DATABASE_URL",
    }
    environment = {key: value for key, value in values.items()
                   if not key.upper().startswith(("LLM_", "SEED_", "PG", "READER_", "MIGRATION_", "RUNTIME_", "AUTH_TEST_", "E2E_"))
                   and key.upper() not in blocked}
    environment.update(APP_ENV="test", BACKEND_URL="http://127.0.0.1:8000", LLM_MODE="mock",
                       PIP_CONFIG_FILE=os.devnull, PIP_REQUIRE_VIRTUALENV="true", PYTHONNOUSERSITE="1")
    return environment


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--e2e", action="store_true", help="also run the full isolated PostgreSQL/browser journey")
    args = parser.parse_args()
    if os.name != "nt":
        parser.error("the documented local workflow currently targets Windows")
    runtime = ROOT / "backend/.runtime"
    runtime.mkdir(exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="clean-setup-", dir=runtime))
    snapshot = work / "project"
    snapshot.mkdir()
    names = subprocess.check_output(["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"], cwd=ROOT)
    for name in set(names.decode("utf-8").split("\0")) - {""}:
        relative = Path(name)
        if relative.is_absolute() or ".." in relative.parts:
            raise RuntimeError("git source path escapes snapshot")
        if any(part in {".git", ".venv", "node_modules", ".runtime", ".next"} for part in relative.parts):
            continue
        if any(part.startswith(".env") and part != ".env.example" for part in relative.parts):
            continue
        source = ROOT / relative
        if not source.is_file():
            continue
        if source.is_symlink():
            raise RuntimeError("snapshot does not follow source symlinks")
        target = snapshot / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    environment = setup_environment(dict(os.environ))
    steps = []
    print(f"Clean setup artifacts: {work.relative_to(ROOT)}", flush=True)

    def run(label, command, cwd=snapshot):
        started = perf_counter()
        with (work / f"{len(steps):02d}-{label}.log").open("w", encoding="utf-8") as log:
            result = subprocess.run(command, cwd=cwd, env=environment, stdout=log, stderr=subprocess.STDOUT,
                                    creationflags=subprocess.CREATE_NO_WINDOW)
        steps.append({"step": label, "exit_code": result.returncode, "seconds": round(perf_counter() - started, 3)})
        (work / "setup-report.json").write_text(json.dumps({"steps": steps, "e2e_requested": args.e2e}, indent=2) + "\n", encoding="utf-8")
        print(f"{label}: exit {result.returncode}", flush=True)
        if result.returncode:
            raise RuntimeError(f"clean setup failed at {label}; inspect ignored log")

    run("create-venv", [sys.executable, "-m", "venv", ".venv"])
    python = str(snapshot / ".venv/Scripts/python.exe")
    run("python-install", [python, "-m", "pip", "install", "-r", "backend/requirements.lock"])
    run("pip-check", [python, "-m", "pip", "check"])
    npm = shutil.which("npm.cmd")
    if npm is None:
        raise RuntimeError("npm.cmd is required")
    frontend = snapshot / "frontend"
    run("npm-ci", [npm, "ci"], frontend)
    # Only example config is copied; never copy caller's local secrets.
    shutil.copy2(snapshot / "backend/.env.example", snapshot / "backend/.env")
    shutil.copy2(frontend / ".env.example", frontend / ".env.local")
    run("api-generation", [npm, "run", "generate:api"], frontend)
    run("unit-static", [python, "-m", "pytest", "backend/tests", "--ignore=backend/tests/integration", "-q"])
    run("lint", [npm, "run", "lint"], frontend)
    # next build creates the route type declarations used by typecheck.
    run("production-build", [npm, "run", "build"], frontend)
    run("typecheck", [npm, "run", "typecheck"], frontend)
    if args.e2e:
        run("browser-install", ["node", "node_modules/@playwright/test/cli.js", "install", "chromium"], frontend)
        run("postgres-browser", [python, "backend/scripts/test_local_postgres.py", "--e2e"])
    print("Clean setup rehearsal passed; live-provider acceptance remains separate.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
