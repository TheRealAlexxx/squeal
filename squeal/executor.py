"""The executor: a Select tree plus a table in, result rows out.

Pipeline: scan -> where -> group/aggregate -> having -> project ->
distinct -> sort -> limit. Rows stay tuples end to end; expression
contexts know how to resolve a column name into a slot.
"""

from . import astnodes as ast
from .errors import SquealError
from .schema import Schema
from .values import (AGGREGATES, SCALAR_FUNCTIONS, arith, compare, like,
                     neg, sql_and, sql_not, sql_or, truthy, _sort_key)


class RowContext:
    """One row: column lookups come from this row only."""

    def __init__(self, schema, row):
        self.schema = schema
        self.row = row

    def get(self, name):
        return self.row[self.schema.resolve(name)]

    def rows(self):
        return [self.row]


class GroupContext:
    """A whole group of rows: aggregates see every row, anything else
    reads the first one (the SQLite convention for bare columns)."""

    def __init__(self, schema, rows):
        self.schema = schema
        self.group = rows

    def get(self, name):
        return self.group[0][self.schema.resolve(name)]

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
            values = [eval_expr(node.args[0], RowContext(ctx.schema, row))
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


def _qualifier_for(name):
    import os
    base = os.path.basename(name)
    if base.lower().endswith(".csv"):
        base = base[:-4]
    return base


def scan_schema(node, resolve):
    """The schema a source tree produces, without reading any rows."""
    if isinstance(node, ast.TableRef):
        table = resolve(node.name)
        qual = node.alias or _qualifier_for(node.name)
        return Schema([(qual, c) for c in table.columns])
    if isinstance(node, ast.Join):
        return scan_schema(node.left, resolve).merge(scan_schema(node.right, resolve))
    raise SquealError("cannot scan %r" % (node,))


def _refs_within(expr, schema):
    """True when every column under expr resolves inside schema."""
    cols = [n for n in expr.walk() if isinstance(n, ast.Column)]
    if not cols:
        return False
    for col in cols:
        try:
            schema.resolve(col.name)
        except SquealError:
            return False
    return True


def equijoin_keys(on, left_schema, right_schema):
    """Split `a = b` into (left-side expr, right-side expr), or None."""
    if not isinstance(on, ast.Binary) or on.op != "=":
        return None
    if _refs_within(on.left, left_schema) and _refs_within(on.right, right_schema):
        return on.left, on.right
    if _refs_within(on.left, right_schema) and _refs_within(on.right, left_schema):
        return on.right, on.left
    return None


def _hashable(value):
    if isinstance(value, bool):
        return int(value)
    return value


def join_strategy(node, left_schema, right_schema):
    if node.kind == "cross" or node.on is None:
        return "nested loop"
    return "hash" if equijoin_keys(node.on, left_schema, right_schema) else "nested loop"


def scan_source(node, resolve):
    """Turn a TableRef/Join tree into a (Schema, rows) pair."""
    if isinstance(node, ast.TableRef):
        table = resolve(node.name)
        qual = node.alias or _qualifier_for(node.name)
        return Schema([(qual, c) for c in table.columns]), list(table.rows)
    if isinstance(node, ast.Join):
        left_schema, left_rows = scan_source(node.left, resolve)
        right_schema, right_rows = scan_source(node.right, resolve)
        schema = left_schema.merge(right_schema)
        keys = None if node.kind == "cross" else             equijoin_keys(node.on, left_schema, right_schema)
        if keys is not None:
            return schema, _hash_join(node, keys, schema,
                                      left_schema, left_rows,
                                      right_schema, right_rows)
        rows = []
        for lrow in left_rows:
            matched = False
            for rrow in right_rows:
                combined = lrow + rrow
                if node.on is None or truthy(
                        eval_expr(node.on, RowContext(schema, combined))) is True:
                    matched = True
                    rows.append(combined)
            if not matched and node.kind == "left":
                rows.append(lrow + (None,) * len(right_schema))
        return schema, rows
    raise SquealError("cannot scan %r" % (node,))


def _hash_join(node, keys, schema, left_schema, left_rows,
               right_schema, right_rows):
    """Build a hash of the right side on the join key, probe with the left.

    NULL keys never match, matching SQL's refusal to call NULL = NULL.
    """
    lexpr, rexpr = keys
    buckets = {}
    for rrow in right_rows:
        key = _hashable(eval_expr(rexpr, RowContext(right_schema, rrow)))
        if key is None:
            continue
        buckets.setdefault(key, []).append(rrow)
    rows = []
    for lrow in left_rows:
        key = _hashable(eval_expr(lexpr, RowContext(left_schema, lrow)))
        matches = buckets.get(key, []) if key is not None else []
        matched = False
        for rrow in matches:
            matched = True
            rows.append(lrow + rrow)
        if not matched and node.kind == "left":
            rows.append(lrow + (None,) * len(right_schema))
    return rows


def execute(select, source):
    if hasattr(source, "columns"):
        # a bare table: single-source queries only
        resolve = lambda _name, t=source: t
    else:
        resolve = source
    if select.source is None:
        schema, rows = Schema([]), [()]
    else:
        schema, rows = scan_source(select.source, resolve)
    columns = schema.names

    if select.where is not None:
        rows = [r for r in rows
                if truthy(eval_expr(select.where, RowContext(schema, r))) is True]

    aggregated = bool(select.group_by) or any(has_aggregate(e) for e, _ in select.items) \
        or (select.having is not None and has_aggregate(select.having)) \
        or any(has_aggregate(e) for e, _ in select.order_by)

    if aggregated:
        groups = {}
        order = []
        for r in rows:
            key = tuple(eval_expr(e, RowContext(schema, r)) for e in select.group_by)
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
        contexts = [GroupContext(schema, groups[k]) for k in order]
        if select.having is not None:
            contexts = [c for c in contexts
                        if truthy(eval_expr(select.having, c)) is True]
    else:
        contexts = [RowContext(schema, r) for r in rows]
        if select.having is not None:
            raise SquealError("HAVING needs GROUP BY or an aggregate")

    out_columns, project = _projection(select, columns)
    projected = []
    order_keys = []
    for ctx in contexts:
        row = tuple(project(ctx))
        projected.append(row)
        keys = []
        for expr, _ in select.order_by:
            # ORDER BY may name a select alias or output column
            if isinstance(expr, ast.Column) and expr.name in out_columns:
                keys.append(row[out_columns.index(expr.name)])
            else:
                keys.append(eval_expr(expr, ctx))
        order_keys.append(keys)

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
            for slot, col in enumerate(columns):
                names.append(col)
                builders.append(_slot_builder(slot))
        else:
            names.append(alias or _default_name(expr))
            builders.append(_expr_builder(expr))
    return names, lambda ctx: [b(ctx) for b in builders]


def _slot_builder(slot):
    # a star column reads its own slot directly: joined rows can carry
    # two columns with the same bare name, and * wants both
    return lambda ctx: ctx.rows()[0][slot] if isinstance(ctx, GroupContext)         else ctx.row[slot]


def _expr_builder(expr):
    def build(ctx):
        return eval_expr(expr, ctx)
    return build


def _default_name(expr):
    from .unparse import to_sql
    if isinstance(expr, ast.Column):
        return expr.name
    return to_sql(expr)
