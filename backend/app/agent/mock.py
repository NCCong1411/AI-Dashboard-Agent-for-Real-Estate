"""Deterministic local planner for development, not evidence of LLM quality."""

from datetime import datetime
import re
import unicodedata

from app.analytics.catalog import KPI_CATALOG
from app.schemas.agent import PlannerDecision
from app.schemas.analysis import AnalysisPlan, CATALOG_VERSION, SCHEMA_VERSION
from app.schemas.dashboard import DashboardSpec
from .llm import AdapterCall


def normalize(value: str) -> str:
    return "".join(character for character in unicodedata.normalize("NFD", value.lower())
                   if unicodedata.category(character) != "Mn").replace("đ", "d")


def edit_decision(prompt: str, current: DashboardSpec) -> PlannerDecision:
    text, operations = normalize(prompt), []
    if "bieu do nay" in text or "moi gioi do" in text:
        return PlannerDecision(outcome="clarification", reason_code="unresolved_reference", reason_codes=["unresolved_reference"],
                               message="Bạn muốn chỉnh widget nào? Hãy nêu tên biểu đồ hoặc KPI cụ thể.")
    if "bo loc" in text or "xoa loc" in text:
        field = "state" if any(term in text for term in ("bang", "california", "texas")) else None
        operations.append({"op": "remove_filter", "field": field} if field else {"op": "clear_filters"})
    else:
        for term, state in (("california", "CA"), ("texas", "TX")):
            if term in text:
                operations.append({"op": "set_filter", "filter": {"field": "state", "op": "eq", "value": state}})
    year = re.search(r"\b(20\d{2})\b", text)
    if year:
        number = int(year.group(1))
        operations.append({"op": "set_dates", "date_range": {"start": f"{number}-01-01", "end_exclusive": f"{number + 1}-01-01"}})
    elif "toan bo thoi gian" in text:
        operations.append({"op": "set_dates", "date_range": {"start": None, "end_exclusive": None}})
    if "them" in text:
        if "sua chua" in text:
            kpi = "repair_cost"
        elif "chap nhan" in text:
            kpi = "accepted_offer_rate"
        elif "de nghi" in text or "offer" in text:
            kpi = "offer_count"
        elif "so bat dong san" in text or "so bds" in text:
            kpi = "sold_property_count"
        elif "trung binh" in text:
            kpi = "average_sale_value"
        elif "so giao dich" in text:
            kpi = "transaction_count"
        elif "gia tri giao dich" in text:
            kpi = "sales_value"
        else:
            kpi = None
        if kpi:
            operations.append({"op": "add_widget", "kpi": kpi, "chart": "card", "dimension": None})
    chart = next((chart for phrase, chart in (("cot ngang", "horizontal_bar"), ("donut", "donut"),
        ("bieu do tron", "pie"), ("bang du lieu", "table"), ("bieu do duong", "line"), ("bieu do cot", "bar")) if phrase in text), None)
    removing = any(term in text for term in ("bo bieu do", "xoa bieu do", "bo widget", "xoa widget", "bo the", "xoa the"))
    renaming = "doi ten" in text
    if chart or removing or renaming:
        dimension = next((dimension for phrases, dimension in [(("loai bat dong san", "loai bds"), "property_type"),
                         (("theo bang",), "state"), (("theo thang",), "month"), (("thanh pho",), "city")]
                          if any(phrase in text for phrase in phrases)), None)
        explicit = [widget for widget in current.widgets if widget.id.casefold() in text]
        candidates = explicit or [widget for widget in current.widgets if dimension and widget.dimension == dimension]
        if not candidates and "so giao dich" in text:
            candidates = [widget for widget in current.widgets if widget.chart == "card" and widget.kpi == "transaction_count"]
        if renaming and "dashboard" in text:
            title = re.search(r"(?:thành|thanh)\s+(.+)", prompt, re.IGNORECASE)
            if title:
                operations.append({"op": "rename_dashboard", "title": title.group(1).strip(" '\"")})
        elif len(candidates) == 1:
            target = candidates[0]
            if removing:
                operations.append({"op": "remove_widget", "widget_id": target.id})
            elif renaming:
                title = re.search(r"(?:thành|thanh)\s+(.+)", prompt, re.IGNORECASE)
                if title:
                    operations.append({"op": "rename_widget", "widget_id": target.id, "title": title.group(1).strip(" '\"")})
            else:
                operations.append({"op": "set_chart", "widget_id": target.id, "chart": chart, "dimension": target.dimension})
        else:
            return PlannerDecision(outcome="clarification", reason_code="unresolved_reference", reason_codes=["unresolved_reference"],
                                   message="Bạn muốn chỉnh widget nào? Hãy nêu tên biểu đồ hoặc KPI cụ thể.")
    if not operations:
        return PlannerDecision(outcome="clarification", reason_code="mock_edit_not_supported",
                               message="Hãy nêu rõ bộ lọc, kỳ, tên widget hoặc KPI muốn thay đổi.")
    return PlannerDecision(outcome="plan", message="Đã lập các thay đổi cho bản nháp.", patch=operations)


def build_mock_dashboard(title: str, kpis: list, dimensions: list, filters: list, date_range: dict,
                         *, overview: bool, chart: str | None) -> DashboardSpec:
    queries, widgets = [], []
    labels = {"day": "ngày", "month": "tháng", "quarter": "quý", "year": "năm", "state": "bang",
              "city": "thành phố", "property_type": "loại BĐS", "agent": "mã môi giới"}

    groups = {}
    for kpi in kpis:
        groups.setdefault(KPI_CATALOG[kpi].view.rsplit(".", 1)[1], []).append(kpi)

    def query(query_id, dims, selected):
        queries.append({"id": query_id, "plan": AnalysisPlan(
            schema_version=SCHEMA_VERSION, catalog_version=CATALOG_VERSION,
            kpis=selected, dimensions=dims, filters=filters, date_range=date_range,
            sort=[{"field": dims[0], "direction": "asc"}] if dims else [],
        ).model_dump(mode="json")})

    if overview or not dimensions:
        for fact, selected in groups.items():
            query_id = "totals" if len(groups) == 1 else f"totals_{fact}"
            query(query_id, [], selected)
            for kpi in selected:
                widgets.append({"id": f"card-{kpi}", "title": KPI_CATALOG[kpi].label_vi,
                                "chart": "card", "query_id": query_id, "kpi": kpi})
    for dimension in dimensions:
        for fact, selected in groups.items():
            query_id = f"by_{dimension}" if len(groups) == 1 else f"by_{dimension}_{fact}"
            query(query_id, [dimension], selected)
            selected_chart = chart or ("line" if dimension in {"day", "month", "quarter"} else "bar")
            for kpi in selected[:1] if overview else selected:
                widget_id = f"chart-{dimension}" if len(groups) == 1 and (overview or len(selected) == 1) else f"chart-{dimension}-{kpi}"
                widgets.append({"id": widget_id, "title": f"{KPI_CATALOG[kpi].label_vi} theo {labels[dimension]}",
                                "chart": selected_chart, "query_id": query_id, "kpi": kpi, "dimension": dimension})
    layout, y = [], 0
    for start in range(0, len(widgets), 3):
        group = widgets[start:start + 3]
        for index, widget in enumerate(group):
            layout.append({"widget_id": widget["id"], "x": index * 4, "y": y, "width": 4,
                           "height": 2 if widget["chart"] == "card" else 4})
        y += max(item["height"] for item in layout[-len(group):])
    return DashboardSpec.model_validate({
        "schema_version": SCHEMA_VERSION, "catalog_version": CATALOG_VERSION,
        "title": title[:255], "queries": queries, "widgets": widgets,
        "layout": layout,
    })


class MockPlanner:
    def __init__(self, decision: PlannerDecision | None = None) -> None:
        self.decision = decision

    async def generate(self, prompt: str, current: DashboardSpec | None = None, *,
                       timeout_seconds: float = 60, now: datetime | None = None,
                       history: list[dict] | None = None) -> AdapterCall:
        if not prompt.strip() or len(prompt) > 12000:
            raise ValueError("prompt must contain 1–12000 characters")
        if self.decision is not None:
            result = PlannerDecision.model_validate(self.decision.model_dump(mode="python"))
            return AdapterCall(result, 0, 0)
        text = normalize(prompt)
        forbidden = ("drop table", "select ", "bo qua phan quyen", "doi scope", "loi nhuan", "hoa hong", "du bao", "dai ly", "neighbourhood")
        if any(term in text for term in forbidden):
            decision = PlannerDecision(outcome="unsupported", reason_code="outside_catalog",
                                       message="Yêu cầu này không nằm trong phạm vi KPI được hỗ trợ.")
        elif "doanh thu" in text or "hieu qua" in text:
            decision = PlannerDecision(outcome="clarification", reason_code="ambiguous_kpi",
                                       message="Bạn muốn đo giá trị giao dịch hay một chỉ tiêu nào khác?")
        elif any(term in text for term in ("nam nay", "nam truoc", "thang nay", "thang truoc", "hom qua", "gan nhat", "so sanh")):
            decision = PlannerDecision(outcome="clarification", reason_code="mock_time_not_supported",
                                       message="Mock cần một khoảng ngày hoặc năm cụ thể để tạo bản xem trước.")
        elif current is not None:
            decision = edit_decision(prompt, current)
        else:
            overview = "tong quan" in text or "dashboard" in text and "gia tri giao dich" in text and "trung binh" not in text
            if any(term in text for term in ("offer", "de nghi", "chao gia", "sua chua")):
                overview, kpis = False, []
                if "gia tri giao dich" in text:
                    kpis.append("sales_value")
                if "so giao dich" in text:
                    kpis.append("transaction_count")
                if "so de nghi" in text or ("offer" in text or "chao gia" in text) and "ty le" not in text:
                    kpis.append("offer_count")
                if "chap nhan" in text and "ty le" in text:
                    kpis.append("accepted_offer_rate")
                if "sua chua" in text:
                    kpis.append("repair_cost")
                if not kpis:
                    return AdapterCall(PlannerDecision(outcome="clarification", reason_code="ambiguous_rate_definition",
                        message="Bạn muốn số đề nghị hay tỷ lệ đề nghị được chấp nhận?"), 0, 0)
            elif overview:
                kpis = ["sales_value", "transaction_count", "average_sale_value"]
            elif "trung binh" in text:
                kpis = ["average_sale_value"]
            elif "so bat dong san" in text or "so bds" in text:
                kpis = ["sold_property_count"]
            elif "so giao dich" in text or "dem" in text:
                kpis = ["transaction_count"]
            elif "gia tri giao dich" in text:
                kpis = ["sales_value"]
            else:
                decision = PlannerDecision(outcome="clarification", reason_code="missing_kpi",
                                           message="Bạn muốn xem giá trị giao dịch, số giao dịch hay số bất động sản?")
                return AdapterCall(decision, 0, 0)
            dimensions = []
            for phrases, dimension in [(("theo thang", "tung thang"), "month"), (("theo nam", "tung nam"), "year"),
                                       (("theo bang", ", bang"), "state"), (("thanh pho",), "city"),
                                       (("loai bat dong san", "loai bds"), "property_type")]:
                if any(phrase in text for phrase in phrases):
                    dimensions.append(dimension)
            if overview and not dimensions:
                dimensions = ["month", "state", "property_type"]
            filters = []
            for phrase, value in [("california", "CA"), ("texas", "TX")]:
                if phrase in text:
                    filters.append({"field": "state", "op": "eq", "value": value})
            for property_type in ("condo", "duplex", "townhouse", "single_family", "co_op"):
                if property_type in text:
                    filters.append({"field": "property_type", "op": "eq", "value": property_type})
            year = re.search(r"\b(20\d{2})\b", text)
            date_range = {}
            if year:
                number = int(year.group(1))
                quarter = re.search(r"quy\s*([1-4])", text)
                if quarter:
                    month = (int(quarter.group(1)) - 1) * 3 + 1
                    date_range = {"start": f"{number}-{month:02d}-01", "end_exclusive": f"{number + (month == 10)}-{(month + 2) % 12 + 1:02d}-01"}
                else:
                    date_range = {"start": f"{number}-01-01", "end_exclusive": f"{number + 1}-01-01"}
            chart = "horizontal_bar" if "cot ngang" in text else "pie" if "bieu do tron" in text else "donut" if "donut" in text else "table" if "bang du lieu" in text else None
            dashboard = build_mock_dashboard("Dashboard thử nghiệm", kpis, dimensions, filters, date_range,
                                             overview=overview, chart=chart)
            decision = PlannerDecision(outcome="plan", message="Đã tạo bản xem trước bằng mock; chưa phát hành dashboard.", dashboard=dashboard)
        return AdapterCall(decision, 0, 0)
