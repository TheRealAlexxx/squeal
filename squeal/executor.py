"""The executor: a Select tree plus a table in, result rows out.

Pipeline: scan -> where -> group/aggregate -> having -> project ->
distinct -> sort -> limit. Rows stay tuples end to end; expression
contexts know how to resolve a column name into a slot.
"""

from . import astnodes as ast
from .errors import SquealError
from .values import (AGGREGATES, SCALAR_FUNCTIONS, arith, compare, like,
                     neg, sql_and, sql_not, sql_or, truthy, _sort_key)


class RowContext:
    """One row: column lookups come from this row only."""

    def __init__(self, columns, row):
        self.columns = columns
        self.row = row

    def get(self, name):
        if name not in self.columns:
            raise SquealError("no such column: %s" % name)
        return self.row[self.columns.index(name)]

    def rows(self):
        return [self.row]


class GroupContext:
    """A whole group of rows: aggregates see every row, anything else
    reads the first one (the SQLite convention for bare columns)."""

    def __init__(self, columns, rows):
        self.columns = columns
        self.group = rows

    def get(self, name):
        if name not in self.columns:
            raise SquealError("no such column: %s" % name)
        return self.group[0][self.columns.index(name)]

    def rows(self):
        return self.group


def eval_expr(node, ctx):
    if isinstance(node, ast.Literal):
        return node.value
    if isinstance(node, ast.Column):
        return ctx.get(node.name)
    if isinstance(node, ast.Star):
        raise SquealError("* is only valid as a bare select item or inside count(*)")
    if isinstance(node, ast.Unary):
        value = eval_expr(node.expr, ctx)
        if node.op == "not":
            return sql_not(value)
        if node.op == "-":
            return neg(value)
        return value  # unary +
    if isinstance(node, ast.Binary):
        if node.op == "and":
            return sql_and(eval_expr(node.left, ctx), eval_expr(node.right, ctx))
        if node.op == "or":
            return sql_or(eval_expr(node.left, ctx), eval_expr(node.right, ctx))
        if node.op in ("=", "!=", "<", "<=", ">", ">="):
            return compare(node.op, eval_expr(node.left, ctx), eval_expr(node.right, ctx))
        return arith(node.op, eval_expr(node.left, ctx), eval_expr(node.right, ctx))
    if isinstance(node, ast.IsNull):
        result = eval_expr(node.expr, ctx) is None
        return (not result) if node.negated else result
    if isinstance(node, ast.Like):
        result = like(eval_expr(node.expr, ctx), eval_expr(node.pattern, ctx))
        if result is not None and node.negated:
            result = not result
        return result
    if isinstance(node, ast.InList):
        value = eval_expr(node.expr, ctx)
        if value is None:
            return None
        seen_null = False
        for candidate in node.values:
            eq = compare("=", value, eval_expr(candidate, ctx))
            if eq is True:
                return (not node.negated) or False if node.negated else True
            if eq is None:
                seen_null = True
        if node.negated:
            return None if seen_null else True
        return None if seen_null else False
    if isinstance(node, ast.Between):
        value = eval_expr(node.expr, ctx)
        low = compare(">=", value, eval_expr(node.low, ctx))
        high = compare("<=", value, eval_expr(node.high, ctx))
        inside = sql_and(low, high)
        if inside is not None and node.negated:
            inside = not inside
        return inside
    if isinstance(node, ast.FuncCall):
        return eval_call(node, ctx)
    if isinstance(node, ast.Case):
        for cond, val in node.whens:
            if truthy(eval_expr(cond, ctx)) is True:
                return eval_expr(val, ctx)
        return eval_expr(node.else_val, ctx) if node.else_val is not None else None
    raise SquealError("cannot evaluate %r" % (node,))


def eval_call(node, ctx):
    name = node.name.lower()
    if name in AGGREGATES:
        if node.star and name != "count":
            raise SquealError("%s(*) is not valid, only count(*)" % name)
        if node.star:
            values = ctx.rows()
        else:
            values = [eval_expr(node.args[0], RowContext(ctx.columns, row))
                      for row in ctx.rows()]
        return AGGREGATES[name](values, node.star)
    if name in SCALAR_FUNCTIONS:
        lo, hi, fn = SCALAR_FUNCTIONS[name]
        if not (lo <= len(node.args) <= hi):
            raise SquealError("%s takes %s argument(s), got %d"
                              % (name, str(lo) if lo == hi else "%d to %d" % (lo, hi),
                                 len(node.args)))
        return fn([eval_expr(a, ctx) for a in node.args])
    raise SquealError("unknown function: %s" % node.name)


def has_aggregate(node):
    return any(isinstance(n, ast.FuncCall) and n.name.lower() in AGGREGATES
               for n in node.walk())


class Result:
    def __init__(self, columns, rows):
        self.columns = columns
        self.rows = rows


def execute(select, table):
    columns = table.columns
    rows = list(table.rows)

    if select.where is not None:
        rows = [r for r in rows
                if truthy(eval_expr(select.where, RowContext(columns, r))) is True]

    aggregated = bool(select.group_by) or any(has_aggregate(e) for e, _ in select.items) \
        or (select.having is not None and has_aggregate(select.having)) \
        or any(has_aggregate(e) for e, _ in select.order_by)

    if aggregated:
        groups = {}
        order = []
        for r in rows:
            key = tuple(eval_expr(e, RowContext(columns, r)) for e in select.group_by)
            if key not in groups:
                groups[key] = []
                order.append(key)
            groups[key].append(r)
        if not order and select.group_by:
            order = []  # empty input, grouped: no result rows
        if not select.group_by and rows:
            order = [()]  # whole table is one group
            groups[()] = rows
        elif not select.group_by and not rows:
            order = [()]  # count(*) over empty input is still one row: 0
            groups[()] = []
        contexts = [GroupContext(columns, groups[k]) for k in order]
        if select.having is not None:
            contexts = [c for c in contexts
                        if truthy(eval_expr(select.having, c)) is True]
    else:
        contexts = [RowContext(columns, r) for r in rows]
        if select.having is not None:
            raise SquealError("HAVING needs GROUP BY or an aggregate")

    out_columns, project = _projection(select, columns)
    projected = []
    order_keys = []
    for ctx in contexts:
        projected.append(tuple(project(ctx)))
        order_keys.append([eval_expr(e, ctx) for e, _ in select.order_by])

    pairs = list(zip(projected, order_keys))
    if select.order_by:
        from functools import cmp_to_key
        descs = [d for _, d in select.order_by]
        pairs.sort(key=cmp_to_key(lambda a, b: _cmp_rows(a[1], b[1], descs)))
    projected = [p for p, _ in pairs]

    if select.distinct:
        seen = set()
        unique = []
        for row in projected:
            if row not in seen:
                seen.add(row)
                unique.append(row)
        projected = unique

    start = select.offset or 0
    end = None if select.limit is None else start + select.limit
    return Result(out_columns, projected[start:end])


def _cmp_rows(a, b, descs):
    for va, vb, desc in zip(a, b, descs):
        if va is None and vb is None:
            continue
        if va is None:
            return 1  # NULLs last, whichever direction
        if vb is None:
            return -1
        ka, kb = _sort_key(va), _sort_key(vb)
        if ka == kb:
            continue
        result = -1 if ka < kb else 1
        return -result if desc else result
    return 0


def _projection(select, columns):
    names = []
    builders = []
    for expr, alias in select.items:
        if isinstance(expr, ast.Star):
            for col in columns:
                names.append(col)
                builders.append(_star_builder(col))
        else:
            names.append(alias or _default_name(expr))
            builders.append(_expr_builder(expr))
    return names, lambda ctx: [b(ctx) for b in builders]


def _star_builder(col):
    # GroupContext resolves a bare column through the group's first row.
    return lambda ctx: ctx.get(col)


def _expr_builder(expr):
    def build(ctx):
        return eval_expr(expr, ctx)
    return build


def _default_name(expr):
    from .unparse import to_sql
    if isinstance(expr, ast.Column):
        return expr.name
    return to_sql(expr)
