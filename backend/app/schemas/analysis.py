"""Structured, compiler-safe analysis plan contracts."""

from __future__ import annotations

from datetime import date
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, model_validator


LEGACY_CATALOG_VERSION = "1.0.0-import-baseline"
CATALOG_VERSION = "1.1.0-offers-repairs"
CatalogVersion = Literal["1.0.0-import-baseline", "1.1.0-offers-repairs"]
SUPPORTED_CATALOG_VERSIONS = (LEGACY_CATALOG_VERSION, CATALOG_VERSION)
SCHEMA_VERSION = "1.0.0"
KPIId = Literal["sales_value", "transaction_count", "average_sale_value", "sold_property_count", "offer_count", "accepted_offer_rate", "repair_cost"]
DimensionId = Literal["day", "month", "quarter", "year", "state", "city", "property_type", "agent"]
FilterField = Literal["state", "city", "property_type", "agent"]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DateRange(StrictModel):
    start: date | None = None
    end_exclusive: date | None = None

    @model_validator(mode="after")
    def ordered_or_all_time(self) -> "DateRange":
        if (self.start is None) != (self.end_exclusive is None):
            raise ValueError("start and end_exclusive must both be set or both be null")
        if self.start is not None and self.end_exclusive <= self.start:
            raise ValueError("end_exclusive must be after start")
        return self


class StringFilterSpec(StrictModel):
    field: Literal["state", "city", "property_type"]
    op: Literal["eq", "in"]
    value: StrictStr | list[StrictStr]

    @model_validator(mode="after")
    def operator_matches_value(self) -> Self:
        is_list = isinstance(self.value, list)
        if (self.op == "in") != is_list:
            raise ValueError("in requires a list; eq requires a scalar")
        if is_list and not self.value:
            raise ValueError("in filter list must not be empty")
        return self


class AgentFilterSpec(StrictModel):
    field: Literal["agent"]
    op: Literal["eq", "in"]
    value: StrictInt | list[StrictInt]

    @model_validator(mode="after")
    def operator_matches_value(self) -> Self:
        is_list = isinstance(self.value, list)
        if (self.op == "in") != is_list:
            raise ValueError("in requires a list; eq requires a scalar")
        if is_list and not self.value:
            raise ValueError("in filter list must not be empty")
        return self


FilterSpec = Annotated[StringFilterSpec | AgentFilterSpec, Field(discriminator="field")]


class SortSpec(StrictModel):
    field: KPIId | DimensionId
    direction: Literal["asc", "desc"] = "asc"


class AnalysisPlan(StrictModel):
    schema_version: Literal[SCHEMA_VERSION]
    catalog_version: CatalogVersion
    kpis: Annotated[list[KPIId], Field(min_length=1, max_length=4)]
    dimensions: Annotated[list[DimensionId], Field(max_length=4)] = Field(default_factory=list)
    filters: Annotated[list[FilterSpec], Field(max_length=12)] = Field(default_factory=list)
    date_range: DateRange = Field(default_factory=DateRange)
    sort: Annotated[list[SortSpec], Field(max_length=4)] = Field(default_factory=list)
    limit: int | None = Field(default=None, ge=1, le=1000)

    @model_validator(mode="after")
    def unique_fields(self) -> "AnalysisPlan":
        if len(set(self.kpis)) != len(self.kpis):
            raise ValueError("kpis must be unique")
        if len(set(self.dimensions)) != len(self.dimensions):
            raise ValueError("dimensions must be unique")
        return self
