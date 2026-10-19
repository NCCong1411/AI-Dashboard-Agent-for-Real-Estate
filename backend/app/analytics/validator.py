"""Fail-closed validation for SQL emitted by the analytics compiler.

This module is deliberately independent from the compiler.  It accepts SQL
text only, parses it with the PostgreSQL dialect, and proves that the tree is
one of the fixed read-only query forms for sales, offers, or repairs.
"""

from __future__ import annotations
from functools import lru_cache

from sqlglot import Dialect, exp, parse
from sqlglot.errors import SqlglotError
from sqlglot.tokens import TokenType


class SQLValidationError(ValueError):
    """Raised when SQL is outside the analytics query allowlist."""


_SOURCE_COLUMNS = {
    "transaction_date",
    "state",
    "city",
    "property_type",
    "agent_id",
    "sale_price",
    "transaction_id",
    "property_id",
}
_FILTER_COLUMNS = {"state", "city", "property_type", "agent_id"}
_DIMENSION_EXPRESSIONS = {
    "day": "s.transaction_date",
    "month": "date_trunc('month', s.transaction_date)::date",
    "quarter": "date_trunc('quarter', s.transaction_date)::date",
    "year": "date_trunc('year', s.transaction_date)::date",
    "state": "s.state",
    "city": "s.city",
    "property_type": "s.property_type",
    "agent": "s.agent_id",
}
_KPI_EXPRESSIONS = {
    "sales_value": "SUM(s.sale_price)",
    "transaction_count": "COUNT(s.transaction_id)",
    "average_sale_value": "SUM(s.sale_price) / NULLIF(COUNT(s.transaction_id), 0)",
    "sold_property_count": "COUNT(DISTINCT s.property_id)",
}

# Independent allowlist: never derive these expressions from planner output or
# the compiler. Each source has its own columns, date, metrics, and count key.
_FACTS = {
    "sales": ("transaction_date", "transaction_id", _SOURCE_COLUMNS, _KPI_EXPRESSIONS),
    "offers": ("offer_date", "offer_id", {"offer_date", "offer_id", "status", "property_id", "state", "city", "property_type"}, {
        "offer_count": "COUNT(s.offer_id)",
        "accepted_offer_rate": "COUNT(s.offer_id) FILTER (WHERE s.status = 'accepted')::numeric / NULLIF(COUNT(s.offer_id), 0)",
    }),
    "repairs": ("repair_date", "repair_id", {"repair_date", "repair_id", "cost", "property_id", "state", "city", "property_type"}, {
        "repair_cost": "SUM(s.cost)",
    }),
}


@lru_cache(maxsize=3)
def _projections(fact):
    day, key, _, kpis = _FACTS[fact]
    dimensions = {name: sql.replace("transaction_date", day) for name, sql in _DIMENSION_EXPRESSIONS.items()
                  if fact == "sales" or name != "agent"}
    return {name: _expression_key(sql) for name, sql in (dimensions | kpis | {"_fact_count": f"COUNT(s.{key})"}).items()}


def _expression_key(sql: str) -> str:
    statement = parse(f"SELECT {sql}", read="postgres")[0]
    return statement.expressions[0].sql(dialect="postgres")


def _reject_forbidden_structure(statement: exp.Select) -> None:
    forbidden = (
        exp.Command,
        exp.Create,
        exp.Delete,
        exp.Drop,
        exp.Insert,
        exp.Join,
        exp.Merge,
        exp.SetOperation,
        exp.Subquery,
        exp.TableSample,
        exp.Update,
        exp.Window,
        exp.With,
        exp.WithFill,
    )
    for node_type in forbidden:
        if statement.find(node_type) is not None:
            raise SQLValidationError(f"forbidden SQL structure: {node_type.__name__}")

    allowed_arguments = {"expressions", "from_", "where", "group", "order", "limit"}
    for argument, value in statement.args.items():
        if argument not in allowed_arguments and value:
            raise SQLValidationError(f"forbidden SELECT clause: {argument}")

    # SQLGlot stores several SQL modifiers below the top-level Select.  Check
    # the exact argument surface of every clause the compiler can emit so a
    # nested decoration cannot hide behind an otherwise allowed clause.
    exact_arguments: tuple[tuple[type[exp.Expression], set[str]], ...] = (
        (exp.From, {"this"}),
        (exp.Where, {"this"}),
        (exp.Group, {"expressions"}),
        (exp.Order, {"expressions"}),
        (exp.Ordered, {"this", "desc", "nulls_first"}),
        (exp.Limit, {"expression"}),
        (exp.TableAlias, {"this"}),
    )
    for node_type, allowed in exact_arguments:
        for node in statement.find_all(node_type):
            decorated = {
                name
                for name, value in node.args.items()
                if name not in allowed and value is not None
            }
            if decorated:
                raise SQLValidationError(
                    f"forbidden {node_type.__name__} decoration: {sorted(decorated)}"
                )


def _validate_source(statement: exp.Select) -> str:
    tables = list(statement.find_all(exp.Table))
    if len(tables) != 1:
        raise SQLValidationError("query must read exactly one table")
    table = tables[0]
    if table.catalog or table.db != "analytics" or table.name not in _FACTS or table.alias != "s":
        raise SQLValidationError("only allowlisted analytics views AS s are allowed")
    if set(table.args) - {"this", "db", "catalog", "alias"}:
        raise SQLValidationError("source table decorations are not allowed")
    alias = table.args.get("alias")
    if not isinstance(alias, exp.TableAlias) or alias.args.get("columns") is not None:
        raise SQLValidationError("source must use the undecorated alias s")
    return table.name


def _validate_functions_and_columns(statement: exp.Select, fact: str) -> None:
    # User-defined and extension functions parse as Anonymous.  Built-ins are
    # constrained more tightly below by exact, catalog-owned projection ASTs.
    function = statement.find(exp.Anonymous)
    if function is not None:
        raise SQLValidationError(f"function is not allowed: {function.name}")

    if statement.find(exp.Star) is not None:
        raise SQLValidationError("star projections are not allowed")

    for column in statement.find_all(exp.Column):
        if column.table:
            if column.table != "s" or column.name not in _FACTS[fact][2]:
                raise SQLValidationError(f"source column is not allowed: {column.sql()}")
        elif column.name not in _projections(fact):
            raise SQLValidationError(f"unqualified column is not an output alias: {column.name}")


def _validate_projections_and_grouping(statement: exp.Select, fact: str) -> set[str]:
    aliases: set[str] = set()
    selected_dimensions: list[str] = []
    for projection in statement.expressions:
        if not isinstance(projection, exp.Alias):
            raise SQLValidationError("every projection must have a fixed output alias")
        alias = projection.alias
        expected = _projections(fact).get(alias)
        if alias in aliases or expected is None:
            raise SQLValidationError(f"projection alias is not allowed or duplicated: {alias}")
        if projection.this.sql(dialect="postgres") != expected:
            raise SQLValidationError(f"projection expression does not match catalog: {alias}")
        aliases.add(alias)
        if alias in _DIMENSION_EXPRESSIONS:
            selected_dimensions.append(alias)

    group = statement.args.get("group")
    group_keys = [] if group is None else [item.sql(dialect="postgres") for item in group.expressions]
    expected_group_keys = [_projections(fact)[name] for name in selected_dimensions]
    if group_keys != expected_group_keys:
        raise SQLValidationError("GROUP BY must exactly match selected dimensions")
    if not aliases.intersection(_FACTS[fact][3]):
        raise SQLValidationError("query must select at least one catalog KPI")
    return aliases


def _flatten_and(expression: exp.Expression) -> list[exp.Expression]:
    if isinstance(expression, exp.And):
        return _flatten_and(expression.this) + _flatten_and(expression.expression)
    return [expression]


def _is_source_column(node: exp.Expression, name: str | None = None) -> bool:
    return (
        isinstance(node, exp.Column)
        and node.table == "s"
        and node.name in (_SOURCE_COLUMNS | {"offer_date", "repair_date"})
        and (name is None or node.name == name)
    )


def _placeholder_name(node: exp.Expression) -> str | None:
    return node.name if isinstance(node, exp.Placeholder) else None


def _validate_predicates(statement: exp.Select, fact: str) -> None:
    where = statement.args.get("where")
    if where is None:
        return
    seen_date_bounds: set[str] = set()
    filter_index = 0
    for predicate in _flatten_and(where.this):
        if isinstance(predicate, exp.GTE) and _is_source_column(predicate.this, _FACTS[fact][0]):
            parameter = _placeholder_name(predicate.expression)
            if parameter != "start_date" or parameter in seen_date_bounds:
                raise SQLValidationError("invalid or duplicate inclusive date bound")
            seen_date_bounds.add(parameter)
            continue
        if isinstance(predicate, exp.LT) and _is_source_column(predicate.this, _FACTS[fact][0]):
            parameter = _placeholder_name(predicate.expression)
            if parameter != "end_date_exclusive" or parameter in seen_date_bounds:
                raise SQLValidationError("invalid or duplicate exclusive date bound")
            seen_date_bounds.add(parameter)
            continue
        if isinstance(predicate, exp.EQ) and _is_source_column(predicate.this):
            if predicate.this.name not in _FILTER_COLUMNS:
                raise SQLValidationError("filter column is not allowed")
            parameter = _placeholder_name(predicate.expression)
            if parameter != f"filter_{filter_index}":
                raise SQLValidationError("filter values must use compiler bind parameters")
            filter_index += 1
            continue
        if isinstance(predicate, exp.In) and _is_source_column(predicate.this):
            if predicate.this.name not in _FILTER_COLUMNS or not predicate.expressions:
                raise SQLValidationError("IN filter column/list is not allowed")
            names = [_placeholder_name(item) for item in predicate.expressions]
            expected_names = [
                f"filter_{filter_index}_{item_index}"
                for item_index in range(len(predicate.expressions))
            ]
            if names != expected_names:
                raise SQLValidationError("IN filter values must use compiler bind parameters")
            filter_index += 1
            continue
        raise SQLValidationError(f"predicate is outside the compiler allowlist: {predicate.sql()}")

    if seen_date_bounds and seen_date_bounds != {"start_date", "end_date_exclusive"}:
        raise SQLValidationError("date bounds must be supplied as a half-open pair")


def _validate_order_and_limit(statement: exp.Select, aliases: set[str]) -> None:
    order = statement.args.get("order")
    if order is not None:
        for item in order.expressions:
            column = item.this
            if not isinstance(column, exp.Column) or column.table or column.name not in aliases:
                raise SQLValidationError("ORDER BY must use a selected output alias")
            if item.args.get("nulls_first") != bool(item.args.get("desc")):
                raise SQLValidationError("ORDER BY must use PostgreSQL default NULL placement")

    limit = statement.args.get("limit")
    if limit is not None and _placeholder_name(limit.expression) != "limit":
        raise SQLValidationError("LIMIT must use the compiler bind parameter")


def validate_compiled_sql(sql: str) -> exp.Select:
    """Parse and validate one compiler-generated PostgreSQL SELECT.

    The returned AST is safe to inspect, but callers should execute the
    original SQL with the compiler's separate parameter mapping.
    """

    if not isinstance(sql, str) or not sql.strip():
        raise SQLValidationError("SQL must be a non-empty string")
    try:
        # SQLGlot accepts == as equality, but PostgreSQL treats it as a
        # distinct operator. Validate the actual operator spelling before
        # normalization because execution uses the original SQL text.
        tokens = Dialect.get_or_raise("postgres").tokenize(sql)
        if any(token.token_type == TokenType.EQ and token.text != "=" for token in tokens):
            raise SQLValidationError("non-PostgreSQL equality operator")
        statements = parse(sql, read="postgres")
    except SqlglotError as exc:
        raise SQLValidationError("invalid PostgreSQL SQL") from exc
    if len(statements) != 1 or not isinstance(statements[0], exp.Select):
        raise SQLValidationError("exactly one SELECT statement is required")

    statement = statements[0]
    _reject_forbidden_structure(statement)
    fact = _validate_source(statement)
    _validate_functions_and_columns(statement, fact)
    aliases = _validate_projections_and_grouping(statement, fact)
    _validate_predicates(statement, fact)
    _validate_order_and_limit(statement, aliases)
    return statement
