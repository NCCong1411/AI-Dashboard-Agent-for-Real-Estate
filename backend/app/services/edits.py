"""Apply allowlisted edits, preserving unrelated widget IDs/content/layout."""

import hashlib
import json

from app.analytics.catalog import KPI_CATALOG
from app.analytics.compiler import compile_analysis_plan
from app.schemas.analysis import AnalysisPlan, CATALOG_VERSION, LEGACY_CATALOG_VERSION
from app.schemas.dashboard import DashboardSpec
from app.schemas.edits import DraftChanges, EditOperation, WidgetChange


def query_fingerprint(plan: AnalysisPlan, scope: str) -> str:
    value = plan.model_dump(mode="json")
    value["filters"] = sorted(value["filters"], key=lambda item: json.dumps(item, sort_keys=True))
    return hashlib.sha256(json.dumps({"scope": scope, "plan": value}, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def changes_between(previous: DashboardSpec, current: DashboardSpec) -> DraftChanges:
    old = {widget.id: widget for widget in previous.widgets}
    new = {widget.id: widget for widget in current.widgets}
    old_layout = {item.widget_id: item for item in previous.layout}
    new_layout = {item.widget_id: item for item in current.layout}
    changes = []
    for widget_id in old.keys() | new.keys():
        if widget_id not in old:
            changes.append(WidgetChange(widget_id=widget_id, title=new[widget_id].title, change="added"))
        elif widget_id not in new:
            changes.append(WidgetChange(widget_id=widget_id, title=old[widget_id].title, change="removed"))
        elif old[widget_id] != new[widget_id] or old_layout[widget_id] != new_layout[widget_id]:
            changes.append(WidgetChange(widget_id=widget_id, title=new[widget_id].title, change="updated"))
    before = {query.id: query.plan for query in previous.queries}
    after = {query.id: query.plan for query in current.queries}
    changed_queries = sorted(query_id for query_id in before.keys() | after.keys() if before.get(query_id) != after.get(query_id))
    return DraftChanges(widgets=sorted(changes, key=lambda item: item.widget_id), changed_queries=changed_queries,
                        title_changed=previous.title != current.title, presentation_only=not changed_queries)


def apply_edits(current: DashboardSpec, operations: list[EditOperation]) -> DashboardSpec:
    value = current.model_dump(mode="python")
    if current.catalog_version == LEGACY_CATALOG_VERSION and any(
        operation.op == "add_widget" and operation.kpi in {"offer_count", "accepted_offer_rate", "repair_cost"}
        for operation in operations
    ):
        # An explicit request to add a new KPI upgrades the draft only. Reading
        # or filtering a published legacy dashboard leaves its version intact.
        value["catalog_version"] = CATALOG_VERSION
        for query in value["queries"]:
            query["plan"]["catalog_version"] = CATALOG_VERSION

    def widget(widget_id):
        found = next((item for item in value["widgets"] if item["id"] == widget_id), None)
        if found is None:
            raise ValueError("edit references a missing widget")
        return found

    def unique_id(prefix, existing):
        suffix, proposed = 1, prefix
        while proposed in existing:
            proposed = f"{prefix}_{suffix}"
            suffix += 1
        return proposed

    def query_for(kpi, dimensions, base):
        for query in value["queries"]:
            plan = query["plan"]
            if (plan["dimensions"] == dimensions and plan["filters"] == base["filters"]
                    and plan["date_range"] == base["date_range"]):
                candidate = dict(plan, kpis=plan["kpis"] if kpi in plan["kpis"] else plan["kpis"] + [kpi])
                try:
                    compile_analysis_plan(AnalysisPlan.model_validate(candidate))
                except ValueError:
                    continue
                query["plan"] = candidate
                return query["id"]
        query_id = unique_id("query_" + kpi, {query["id"] for query in value["queries"]})
        candidate = dict(base, kpis=[kpi], dimensions=dimensions,
                         sort=[item for item in base["sort"] if item["field"] in [kpi, *dimensions]])
        value["queries"].append({"id": query_id, "plan": candidate})
        return query_id

    for operation in operations:
        if operation.op in {"set_filter", "remove_filter", "clear_filters", "set_dates"}:
            for query in value["queries"]:
                plan = query["plan"]
                if operation.op == "set_dates":
                    plan["date_range"] = operation.date_range.model_dump(mode="python")
                elif operation.op == "clear_filters":
                    plan["filters"] = []
                else:
                    field = operation.filter.field if operation.op == "set_filter" else operation.field
                    plan["filters"] = [item for item in plan["filters"] if item["field"] != field]
                    if operation.op == "set_filter":
                        plan["filters"].append(operation.filter.model_dump(mode="python"))
        elif operation.op == "set_chart":
            target = widget(operation.widget_id)
            if operation.chart == target["chart"] and operation.dimension is None:
                continue
            target["chart"] = operation.chart
            dimension = None if operation.chart == "card" else operation.dimension or target["dimension"]
            if operation.chart not in {"card", "table"} and dimension is None:
                raise ValueError("chart edit requires a dimension")
            query = next(query for query in value["queries"] if query["id"] == target["query_id"])
            dimensions = [] if dimension is None else [dimension]
            if query["plan"]["dimensions"] != dimensions:
                target["query_id"] = query_for(target["kpi"], dimensions, query["plan"])
            target["dimension"] = dimension
        elif operation.op == "add_widget":
            dimensions = [] if operation.dimension is None else [operation.dimension]
            if operation.chart == "card" and dimensions or operation.chart not in {"card", "table"} and not dimensions:
                raise ValueError("new chart must declare compatible dimensions")
            base = value["queries"][0]["plan"]
            query_id = query_for(operation.kpi, dimensions, base)
            widget_id = unique_id("widget-" + operation.kpi, {item["id"] for item in value["widgets"]})
            value["widgets"].append({"id": widget_id, "title": KPI_CATALOG[operation.kpi].label_vi,
                                     "query_id": query_id, "kpi": operation.kpi, "dimension": operation.dimension, "chart": operation.chart})
            height = 2 if operation.chart == "card" else 4
            found = None
            bottom = max((item["y"] + item["height"] for item in value["layout"]), default=0)
            for y in range(bottom + 1):
                for x in (0, 4, 8):
                    if all(not (x < item["x"] + item["width"] and item["x"] < x + 4
                                and y < item["y"] + item["height"] and item["y"] < y + height)
                           for item in value["layout"]):
                        found = {"widget_id": widget_id, "x": x, "y": y, "width": 4, "height": height}
                        break
                if found:
                    break
            value["layout"].append(found)
        elif operation.op == "remove_widget":
            widget(operation.widget_id)
            value["widgets"] = [item for item in value["widgets"] if item["id"] != operation.widget_id]
            value["layout"] = [item for item in value["layout"] if item["widget_id"] != operation.widget_id]
        elif operation.op == "rename_widget":
            widget(operation.widget_id)["title"] = operation.title
        elif operation.op == "rename_dashboard":
            value["title"] = operation.title
        else:
            raise ValueError("unsupported edit operation")
    used_queries = {item["query_id"] for item in value["widgets"]}
    value["queries"] = [query for query in value["queries"] if query["id"] in used_queries]
    result = DashboardSpec.model_validate(value)
    for query in result.queries:
        compile_analysis_plan(query.plan)
    return result
