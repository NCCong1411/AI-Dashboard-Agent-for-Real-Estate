"""Canonical public state values for new planner decisions only."""

from app.schemas.agent import PlannerDecision


US_STATE_CODES = {
    "Alabama": "AL", "Alaska": "AK", "Arizona": "AZ", "Arkansas": "AR",
    "California": "CA", "Colorado": "CO", "Connecticut": "CT", "Delaware": "DE",
    "District of Columbia": "DC", "Florida": "FL", "Georgia": "GA", "Hawaii": "HI",
    "Idaho": "ID", "Illinois": "IL", "Indiana": "IN", "Iowa": "IA",
    "Kansas": "KS", "Kentucky": "KY", "Louisiana": "LA", "Maine": "ME",
    "Maryland": "MD", "Massachusetts": "MA", "Michigan": "MI", "Minnesota": "MN",
    "Mississippi": "MS", "Missouri": "MO", "Montana": "MT", "Nebraska": "NE",
    "Nevada": "NV", "New Hampshire": "NH", "New Jersey": "NJ", "New Mexico": "NM",
    "New York": "NY", "North Carolina": "NC", "North Dakota": "ND", "Ohio": "OH",
    "Oklahoma": "OK", "Oregon": "OR", "Pennsylvania": "PA", "Rhode Island": "RI",
    "South Carolina": "SC", "South Dakota": "SD", "Tennessee": "TN", "Texas": "TX",
    "Utah": "UT", "Vermont": "VT", "Virginia": "VA", "Washington": "WA",
    "West Virginia": "WV", "Wisconsin": "WI", "Wyoming": "WY",
}

_STATE_ALIASES = {name.casefold(): code for name, code in US_STATE_CODES.items()}
_STATE_ALIASES.update({code.casefold(): code for code in US_STATE_CODES.values()})


def canonical_state(value: str) -> str:
    """Map exact English names and codes; preserve every other bound value."""
    return _STATE_ALIASES.get(value.casefold(), value)


def normalize_planner_state_filters(decision: PlannerDecision) -> PlannerDecision:
    """Normalize a fresh plan/patch without touching any saved dashboard."""
    if decision.outcome != "plan":
        return decision
    data = decision.model_dump(mode="python")

    def normalize_filter(filter_spec: dict) -> None:
        if filter_spec["field"] != "state":
            return
        value = filter_spec["value"]
        filter_spec["value"] = ([canonical_state(item) for item in value]
                                if isinstance(value, list) else canonical_state(value))

    if data["dashboard"] is not None:
        for query in data["dashboard"]["queries"]:
            for filter_spec in query["plan"]["filters"]:
                normalize_filter(filter_spec)
    if data["patch"] is not None:
        for operation in data["patch"]:
            if operation["op"] == "set_filter":
                normalize_filter(operation["filter"])
    return PlannerDecision.model_validate(data)
