"""One small OpenAI Responses adapter with explicit configuration and no DB access."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from urllib.parse import urlparse

from openai import APIConnectionError, APIStatusError, APITimeoutError, AsyncOpenAI
from pydantic import ValidationError

from app.config import Settings
from app.schemas.agent import PlannerDecision
from app.schemas.dashboard import DashboardSpec
from .prompts import SYSTEM_PROMPT, planner_input


class AdapterConfigurationError(ValueError):
    pass


@dataclass(frozen=True)
class AdapterCall:
    decision: PlannerDecision | None
    input_tokens: int | None
    output_tokens: int | None
    error_code: str | None = None
    retryable: bool = False


class Planner(Protocol):
    async def generate(self, prompt: str, current: DashboardSpec | None = None, *,
                       timeout_seconds: float = 60, now: datetime | None = None,
                       history: list[dict] | None = None) -> AdapterCall: ...


def strict_output_schema(*, editing: bool = False) -> dict:
    """Pydantic schema with the strict structured-output subset.

    The discriminated filter alternatives are disjoint by `field`, so anyOf
    preserves their validation semantics while avoiding provider-unsupported
    oneOf/discriminator. Pydantic still validates all semantic invariants locally.
    """
    def convert(value):
        if isinstance(value, list):
            return [convert(item) for item in value]
        if not isinstance(value, dict):
            return value
        result = {key: convert(item) for key, item in value.items()
                  if key not in {"default", "discriminator"}
                  and not (key == "title" and isinstance(item, str))}
        if "oneOf" in result:
            result["anyOf"] = result.pop("oneOf")
        if result.get("type") == "object":
            result["additionalProperties"] = False
            result["required"] = list(result.get("properties", {}))
        return result
    schema = PlannerDecision.model_json_schema()
    schema["properties"]["dashboard" if editing else "patch"] = {"type": "null"}
    definitions = schema.pop("$defs", {})
    needed = {}
    def visit(value):
        if isinstance(value, dict):
            reference = value.get("$ref", "")
            if reference.startswith("#/$defs/"):
                name = reference.split("/")[-1]
                if name not in needed:
                    needed[name] = definitions[name]
                    visit(needed[name])
            for item in value.values():
                visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)
    visit(schema)
    if needed:
        schema["$defs"] = needed
    return convert(schema)


def require_live_configuration(settings: Settings) -> None:
    required = (settings.llm_provider, settings.llm_endpoint, settings.llm_model,
                settings.llm_api_key, settings.llm_pricing_source, settings.llm_pricing_verified_on)
    if (not all(required) or settings.llm_input_price_per_million is None
            or settings.llm_output_price_per_million is None):
        raise AdapterConfigurationError("live mode requires provider, endpoint, model, key and verified pricing")
    if any(not value.strip() for value in (settings.llm_endpoint, settings.llm_model, settings.llm_pricing_source)):
        raise AdapterConfigurationError("live configuration must not contain blank identifiers")
    endpoint = urlparse(settings.llm_endpoint)
    if (endpoint.scheme != "https" or not endpoint.hostname or endpoint.username
            or endpoint.password or endpoint.query or endpoint.fragment):
        raise AdapterConfigurationError("LLM_ENDPOINT must be an HTTPS base URL without credentials/query/fragment")


class OpenAIPlanner:
    def __init__(self, settings: Settings) -> None:
        require_live_configuration(settings)
        self.settings = settings

    async def generate(self, prompt: str, current: DashboardSpec | None = None, *,
                       timeout_seconds: float = 60, now: datetime | None = None,
                       history: list[dict] | None = None) -> AdapterCall:
        if not prompt.strip() or len(prompt) > 12000:
            raise ValueError("prompt must contain 1–12000 characters")
        require_live_configuration(self.settings)
        # No automatic SDK retries: each call/retry must be reserved and counted
        # by the usage service. The outer asyncio deadline also bounds wall time.
        try:
            async with asyncio.timeout(min(timeout_seconds, 60)):
                async with AsyncOpenAI(
                    api_key=self.settings.llm_api_key.get_secret_value(),
                    base_url=self.settings.llm_endpoint,
                    max_retries=0, timeout=min(timeout_seconds, 60),
                ) as client:
                    response = await client.responses.create(
                        model=self.settings.llm_model, instructions=SYSTEM_PROMPT,
                        input=planner_input(prompt, current, now=now, history=history), store=False,
                        service_tier="default",
                        reasoning={"effort": self.settings.llm_reasoning_effort},
                        max_output_tokens=(self.settings.llm_max_creation_output_tokens
                                           if current is None else self.settings.llm_max_output_tokens),
                        text={"format": {"type": "json_schema", "name": "dashboard_plan",
                                         "strict": True, "schema": strict_output_schema(editing=current is not None)}},
                    )
        except (APITimeoutError, TimeoutError):
            return AdapterCall(None, None, None, "provider_timeout", True)
        except APIStatusError as error:
            code = {401: "provider_authentication_failed", 403: "provider_permission_denied",
                    429: "provider_rate_limit"}.get(error.status_code, "provider_error")
            return AdapterCall(None, None, None, code, error.status_code == 429 or error.status_code >= 500)
        except APIConnectionError:
            return AdapterCall(None, None, None, "provider_unavailable", True)
        usage = response.usage
        input_tokens = usage.input_tokens if usage is not None else None
        output_tokens = usage.output_tokens if usage is not None else None
        if response.status != "completed":
            return AdapterCall(None, input_tokens, output_tokens, "incomplete_output")
        if any(item.type == "message" and any(part.type == "refusal" for part in item.content)
               for item in response.output):
            return AdapterCall(None, input_tokens, output_tokens, "provider_refusal")
        try:
            decision = PlannerDecision.model_validate_json(response.output_text)
        except (ValidationError, ValueError):
            return AdapterCall(None, input_tokens, output_tokens, "invalid_output")
        return AdapterCall(decision, input_tokens, output_tokens)


def create_planner(settings: Settings) -> Planner:
    if settings.llm_mode == "mock":
        from .mock import MockPlanner
        return MockPlanner()
    return OpenAIPlanner(settings)
