"""VPS-only: merge whitelisted LLM settings into owner-only backend env."""
import json
import os
from pathlib import Path


def main():
    private = Path("deploy/secrets")
    artifact = private / "live-release-20261026.json"
    values = json.loads(artifact.read_text())
    permitted = {"LLM_PROVIDER", "LLM_ENDPOINT", "LLM_MODEL", "LLM_API_KEY", "LLM_INPUT_PRICE_PER_MILLION",
                 "LLM_OUTPUT_PRICE_PER_MILLION", "LLM_PRICING_SOURCE", "LLM_PRICING_VERIFIED_ON",
                 "LLM_MODE", "LLM_REASONING_EFFORT", "LLM_MAX_CALLS_PER_RUN", "LLM_BUDGET_PER_RUN_USD",
                 "LLM_BUDGET_PER_DAY_USD", "LLM_GLOBAL_BUDGET_PER_DAY_USD"}
    if (set(values) != permitted or any(not isinstance(v, str) or "\n" in v or "\r" in v or '"' in v for v in values.values())
            or values["LLM_GLOBAL_BUDGET_PER_DAY_USD"] != "0.10" or values["LLM_MAX_CALLS_PER_RUN"] != "1"):
        raise SystemExit("Release config does not match the reviewed profile")
    current = private / "backend.env"
    backup = private / "backend.before-live-20261026.env"
    if not backup.exists():
        with os.fdopen(os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as target:
            target.write(current.read_bytes())
    lines = [line for line in current.read_text().splitlines() if line.split("=", 1)[0].strip() not in values]
    lines.extend(f'{key}="{value}"' for key, value in values.items())
    temporary = private / "backend.live.tmp"
    with os.fdopen(os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as target:
        target.write("\n".join(lines) + "\n")
    os.replace(temporary, current)
    for path in (current, artifact, private / "public-builder-demo.json"):
        path.chmod(0o600)
    print("Production live profile applied; DB/auth settings preserved; no secrets printed.")


if __name__ == "__main__":
    main()
