"""Bounded structured edits applied by the backend to an existing draft."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from .analysis import DateRange, DimensionId, FilterField, FilterSpec, KPIId


ChartType = Literal["card", "line", "bar", "horizontal_bar", "pie", "donut", "table"]


class EditModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SetFilter(EditModel):
    op: Literal["set_filter"]
    filter: FilterSpec


class RemoveFilter(EditModel):
    op: Literal["remove_filter"]
    field: FilterField


class ClearFilters(EditModel):
    op: Literal["clear_filters"]


class SetDates(EditModel):
    op: Literal["set_dates"]
    date_range: DateRange


class SetChart(EditModel):
    op: Literal["set_chart"]
    widget_id: str = Field(max_length=64)
    chart: ChartType
    dimension: DimensionId | None = None


class AddWidget(EditModel):
    op: Literal["add_widget"]
    kpi: KPIId
    chart: ChartType = "card"
    dimension: DimensionId | None = None


class RemoveWidget(EditModel):
    op: Literal["remove_widget"]
    widget_id: str = Field(max_length=64)


class RenameWidget(EditModel):
    op: Literal["rename_widget"]
    widget_id: str = Field(max_length=64)
    title: str = Field(min_length=1, max_length=160)


class RenameDashboard(EditModel):
    op: Literal["rename_dashboard"]
    title: str = Field(min_length=1, max_length=255)


EditOperation = Annotated[SetFilter | RemoveFilter | ClearFilters | SetDates | SetChart | AddWidget | RemoveWidget | RenameWidget | RenameDashboard,
                          Field(discriminator="op")]


class WidgetChange(EditModel):
    widget_id: str
    title: str
    change: Literal["added", "removed", "updated"]


class DraftChanges(EditModel):
    widgets: list[WidgetChange] = Field(default_factory=list)
    changed_queries: list[str] = Field(default_factory=list)
    title_changed: bool = False
    presentation_only: bool = False
