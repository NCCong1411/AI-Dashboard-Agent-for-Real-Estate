"""Small semantic checks for requests whose missing meaning cannot be inferred from a KPI id."""

import re
import unicodedata

from app.schemas.agent import PlannerDecision


_METRIC_TERMS = (
    "giao dich", "doanh so", "doanh thu", "gia tri", "gia ban", "so luong",
    "bat dong san", "bds", "offer", "de nghi", "chi phi", "sua chua",
    "loi nhuan", "transaction", "sale", "property", "repair", "cost",
)
_AGENCY_FORMULA = "Catalog không có công thức hoa hồng."
_AGENCY_HISTORY = "Lịch sử môi giới–đại lý gây thiếu khớp và fan-out khi gắn giao dịch với đại lý."


def _plain(value: str) -> str:
    decomposed = unicodedata.normalize("NFD", value.casefold()).replace("đ", "d")
    return "".join(character for character in decomposed if unicodedata.category(character) != "Mn")


def guard_undefined_offer_rate(
    prompt: str, decision: PlannerDecision, *, has_context: bool = False,
) -> PlannerDecision:
    """Ask for the event/denominator before materializing an unspecified offer rate.

    The catalog has one offer-rate KPI, but its availability does not imply the
    user meant that particular numerator. Follow-up turns may carry the missing
    definition in their conversation context, so this check only covers new,
    standalone requests.
    """
    if has_context or decision.outcome != "plan":
        return decision
    words = _plain(prompt)
    if not re.search(r"\b(ty le|rate|phan tram|%)\b", words):
        return decision
    if not re.search(r"\b(offer|offers|de nghi)\b", words):
        return decision
    if re.search(r"\b(chap nhan|duoc nhan|accepted|acceptance|thanh cong)\b", words):
        return decision
    return PlannerDecision(
        outcome="clarification",
        message="Bạn muốn tính tỷ lệ nào của đề nghị? Ví dụ, số đề nghị được chấp nhận trên tổng số đề nghị?",
        reason_code="ambiguous_rate_definition",
        reason_codes=["ambiguous_rate_definition"],
    )


def include_missing_metric(prompt: str, decision: PlannerDecision, *, has_context: bool = False) -> PlannerDecision:
    """Keep a dimension question from masking the missing measure in a chart request."""
    if has_context or decision.outcome != "clarification":
        return decision
    codes = decision.reason_codes or [decision.reason_code]
    words = _plain(prompt)
    if ("ambiguous_dimension" not in codes or "missing_metric" in codes or len(codes) >= 5
            or any(term in words for term in _METRIC_TERMS)):
        return decision
    return decision.model_copy(update={
        "message": "Bạn muốn đo KPI nào? " + decision.message,
        "reason_codes": [*codes, "missing_metric"],
    })


def normalize_missing_comparison_metric(
    prompt: str, decision: PlannerDecision, *, has_context: bool = False,
) -> PlannerDecision:
    """A comparison with no measure is missing one, not choosing between named measures."""
    if has_context or decision.outcome != "clarification":
        return decision
    words = _plain(prompt)
    codes = decision.reason_codes or [decision.reason_code]
    if ("ambiguous_metric" not in codes or "missing_metric" in codes
            or not re.search(r"\b(so sanh|xep hang)\b", words)
            or any(term in words for term in _METRIC_TERMS)):
        return decision
    return decision.model_copy(update={
        "reason_code": "missing_metric" if decision.reason_code == "ambiguous_metric" else decision.reason_code,
        "reason_codes": ["missing_metric" if code == "ambiguous_metric" else code for code in codes],
    })


def explain_unsupported_agency(decision: PlannerDecision) -> PlannerDecision:
    """Give the concrete catalog and join limitations behind an agency refusal."""
    if decision.outcome != "unsupported" or decision.reason_code != "unsupported_agency_and_commission":
        return decision
    message = _plain(decision.message)
    additions = []
    if "khong co cong thuc hoa hong" not in message:
        additions.append(_AGENCY_FORMULA)
    if "lich su moi gioi–dai ly gay thieu khop va fan-out" not in message:
        additions.append(_AGENCY_HISTORY)
    if not additions:
        return decision
    expanded = " ".join([decision.message.strip(), *additions])
    if len(expanded) > 2000:
        expanded = " ".join((_AGENCY_FORMULA, _AGENCY_HISTORY))
    return decision.model_copy(update={"message": expanded})
