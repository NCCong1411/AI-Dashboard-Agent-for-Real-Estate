"""Generate private transfer artifacts; never print API keys or private logins."""
import json
import os
from pathlib import Path
import sys
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[2]


def main():
    sys.path.insert(0, str(ROOT / "backend"))
    from app.config import Settings
    from app.agent.llm import require_live_configuration
    settings = Settings(llm_mode="live")
    require_live_configuration(settings)
    if settings.llm_endpoint != "https://api.openai.com/v1" or settings.llm_model != "gpt-6-luna":
        raise SystemExit("Unexpected model/provider; review before transferring")
    output = ROOT / "deploy/secrets/live-release-20261026.json"
    if output.exists():
        raise SystemExit("Private release artifact already exists; do not overwrite blindly")
    fields = ["LLM_PROVIDER", "LLM_ENDPOINT", "LLM_MODEL", "LLM_API_KEY", "LLM_INPUT_PRICE_PER_MILLION",
              "LLM_OUTPUT_PRICE_PER_MILLION", "LLM_PRICING_SOURCE", "LLM_PRICING_VERIFIED_ON"]
    source = dotenv_values(ROOT / "backend/.env")
    values = {key: source[key] for key in fields}
    values.update(LLM_MODE="live", LLM_REASONING_EFFORT="low", LLM_MAX_CALLS_PER_RUN="1",
                  LLM_BUDGET_PER_RUN_USD="0.02", LLM_BUDGET_PER_DAY_USD="0.10",
                  LLM_GLOBAL_BUDGET_PER_DAY_USD="0.10")
    with os.fdopen(os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as target:
        json.dump(values, target)
    account = ROOT / "deploy/secrets/public-builder-demo.json"
    if not account.exists():
        with os.fdopen(os.open(account, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as target:
            json.dump({"username": "builder_demo", "password": "BuilderDemo2026!AI"}, target)
    print("Private transfer artifacts prepared; no keys or account credentials printed.")


if __name__ == "__main__":
    main()
