"""Reserve LLM usage durably before network calls; reconcile idempotently."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, ROUND_CEILING
import json
from typing import Callable
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from app.agent.llm import AdapterCall, Planner, require_live_configuration, strict_output_schema
from app.agent.prompts import SYSTEM_PROMPT, planner_input
from app.config import Settings
from app.db.models import AuditEvent, UsageCall, UsageRecord, User
from app.schemas.dashboard import DashboardSpec


class UsageLimitError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class CallPermit:
    id: UUID
    remaining_seconds: float
    expires_at: datetime


def estimate_cost(input_tokens: int, output_tokens: int, input_price: Decimal, output_price: Decimal) -> Decimal:
    return ((Decimal(input_tokens) * input_price + Decimal(output_tokens) * output_price)
            / Decimal(1000000)).quantize(Decimal("0.000001"), rounding=ROUND_CEILING)


def input_token_bound(prompt: str, current: DashboardSpec | None, now: datetime, history: list[dict] | None = None) -> int:
    # UTF-8 bytes form a conservative text-token bound. Include JSON schema
    # and instruction/context bytes, plus protocol framing allowance. This is
    # a reservation estimate, not a claim of exact tokenizer counts.
    return sum(len(value.encode("utf-8")) for value in (
        SYSTEM_PROMPT, planner_input(prompt, current, now=now, history=history),
        json.dumps(strict_output_schema(editing=current is not None), ensure_ascii=False),
    )) + 2048


class UsageService:
    def __init__(self, engine: Engine, settings: Settings, *, clock: Callable[[], datetime] | None = None):
        self.engine = engine
        self.settings = settings
        self.clock = clock or (lambda: datetime.now(UTC))

    def _now(self) -> datetime:
        value = self.clock()
        if value.tzinfo is None:
            raise ValueError("usage clock must be timezone-aware")
        return value.astimezone(UTC)

    def reserve(self, user_id: UUID, run_id: UUID, input_bound: int, output_bound: int) -> CallPermit:
        if any(isinstance(value, bool) or not isinstance(value, int) or value < 0
               for value in (input_bound, output_bound)):
            raise ValueError("token reservations require nonnegative integers")
        config = self.settings
        if config.llm_mode == "live":
            require_live_configuration(config)
            input_price, output_price = config.llm_input_price_per_million, config.llm_output_price_per_million
        else:
            input_price = output_price = Decimal(0)
            input_bound = output_bound = 0
        amount = estimate_cost(input_bound, output_bound, input_price, output_price)
        with Session(self.engine) as database, database.begin():
            # All reservations/reconciliations for a Builder acquire this same
            # lock first. Different conversations cannot race the daily budget.
            user = database.scalar(select(User).where(User.id == user_id).with_for_update())
            if user is None or not user.is_active or user.role != "builder":
                raise UsageLimitError("builder_required")
            now = self._now()  # after acquiring lock, so waits consume deadline
            day = now.astimezone(ZoneInfo("Asia/Ho_Chi_Minh")).date()
            run = database.scalar(select(UsageRecord).where(UsageRecord.run_id == run_id))
            if run is None:
                run = UsageRecord(run_id=run_id, user_id=user_id, usage_date=day, created_at=now,
                                  llm_calls=0, input_tokens=0, output_tokens=0, estimated_cost_usd=Decimal(0),
                                  query_count=0, latency_ms=0)
                database.add(run)
            elif run.user_id != user_id:
                raise UsageLimitError("run_owner_mismatch")
            remaining = config.llm_timeout_seconds - (now - run.created_at).total_seconds()
            if remaining <= 0:
                raise UsageLimitError("run_timeout")
            if run.llm_calls >= config.llm_max_calls_per_run:
                raise UsageLimitError("call_limit")
            if run.input_tokens + run.output_tokens + input_bound + output_bound > config.llm_max_tokens_per_run:
                raise UsageLimitError("token_limit")
            if run.estimated_cost_usd + amount > config.llm_budget_per_run_usd:
                raise UsageLimitError("run_budget")
            daily = database.scalar(select(func.coalesce(func.sum(UsageCall.charged_cost_usd), 0))
                                    .join(UsageRecord, UsageRecord.run_id == UsageCall.run_id)
                                    .where(UsageRecord.user_id == user_id, UsageCall.usage_date == day))
            if daily + amount > config.llm_budget_per_day_usd:
                raise UsageLimitError("daily_budget")
            run.llm_calls += 1
            run.input_tokens += input_bound
            run.output_tokens += output_bound
            run.estimated_cost_usd += amount
            call = UsageCall(run_id=run_id, usage_date=day, mode=config.llm_mode,
                             model=config.llm_model if config.llm_mode == "live" else "mock",
                             reserved_input_tokens=input_bound, reserved_output_tokens=output_bound,
                             input_price=input_price, output_price=output_price, charged_cost_usd=amount)
            database.add(call)
            database.flush()
            database.add(AuditEvent(actor_user_id=user_id, action="llm_call_reserved", target_type="usage_call",
                                    target_id=str(call.id), details={"mode": config.llm_mode, "run_id": str(run_id)}))
            permit = CallPermit(call.id, remaining, run.created_at + timedelta(seconds=config.llm_timeout_seconds))
        # Session/transaction is closed before the permit permits any network.
        return permit

    def reconcile(self, user_id: UUID, permit: CallPermit, result: AdapterCall) -> bool:
        """Return whether reported usage fits the reserved token upper bound.

        Unreported usage retains the entire reservation, even on 429/timeout;
        a crash before reconciliation likewise leaves pending usage charged.
        """
        with Session(self.engine) as database, database.begin():
            database.scalar(select(User).where(User.id == user_id).with_for_update())
            record = database.execute(select(UsageCall, UsageRecord)
                                      .join(UsageRecord, UsageRecord.run_id == UsageCall.run_id)
                                      .where(UsageCall.id == permit.id, UsageRecord.user_id == user_id)).one_or_none()
            if record is None:
                raise UsageLimitError("reservation_not_found")
            call, run = record
            if call.status != "pending":
                return (call.actual_input_tokens is None
                        or call.actual_input_tokens <= call.reserved_input_tokens
                        and call.actual_output_tokens <= call.reserved_output_tokens)
            known = all(isinstance(value, int) and not isinstance(value, bool) and value >= 0
                        for value in (result.input_tokens, result.output_tokens))
            fits = True
            if known:
                actual = estimate_cost(result.input_tokens, result.output_tokens, call.input_price, call.output_price)
                run.input_tokens += result.input_tokens - call.reserved_input_tokens
                run.output_tokens += result.output_tokens - call.reserved_output_tokens
                run.estimated_cost_usd += actual - call.charged_cost_usd
                call.actual_input_tokens, call.actual_output_tokens = result.input_tokens, result.output_tokens
                call.charged_cost_usd = actual
                call.status = "settled"
                fits = (result.input_tokens <= call.reserved_input_tokens
                        and result.output_tokens <= call.reserved_output_tokens)
            else:
                call.status = "unknown"
            call.error_code = result.error_code
            now = self._now()
            run.latency_ms = max(run.latency_ms, round((now - run.created_at).total_seconds() * 1000))
            database.add(AuditEvent(actor_user_id=user_id, action="llm_call_reconciled", target_type="usage_call",
                                    target_id=str(call.id), details={"status": call.status, "within_reservation": fits}))
        return fits

    async def generate(self, planner: Planner, prompt: str, user_id: UUID, run_id: UUID,
                       current: DashboardSpec | None = None, history: list[dict] | None = None) -> AdapterCall:
        """Count all retry/repair attempts; at most one invalid-output repair."""
        repaired = False
        last = None
        for attempt in range(self.settings.llm_max_calls_per_run):
            now = self._now()
            bound = input_token_bound(prompt, current, now, history) if self.settings.llm_mode == "live" else 0
            output_bound = (self.settings.llm_max_creation_output_tokens if current is None
                            else self.settings.llm_max_output_tokens) if self.settings.llm_mode == "live" else 0
            try:
                permit = await asyncio.to_thread(self.reserve, user_id, run_id, bound, output_bound)
            except UsageLimitError:
                if last is not None:
                    return last
                raise
            try:
                remaining = min(permit.remaining_seconds, (permit.expires_at - self._now()).total_seconds())
                async with asyncio.timeout(max(0, remaining)):
                    call = await planner.generate(prompt, current, timeout_seconds=max(0, remaining), now=now, history=history)
            except TimeoutError:
                call = AdapterCall(None, None, None, "provider_timeout", True)
            except asyncio.CancelledError:
                await asyncio.shield(asyncio.to_thread(self.reconcile, user_id, permit,
                                                       AdapterCall(None, None, None, "cancelled")))
                raise
            except Exception:
                call = AdapterCall(None, None, None, "adapter_error")
            if not await asyncio.to_thread(self.reconcile, user_id, permit, call):
                return AdapterCall(None, call.input_tokens, call.output_tokens, "usage_exceeded_reservation")
            last = call
            if call.decision is not None:
                return call
            if call.error_code == "invalid_output" and not repaired:
                repaired = True
                prompt += "\nHãy trả đúng JSON schema được cung cấp; không thêm trường hoặc KPI ngoài catalog."
                continue
            if not call.retryable:
                return call
            if attempt + 1 < self.settings.llm_max_calls_per_run:
                await asyncio.sleep(0.1)
        return last
