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

    def __init__(self, schema, row, resolve=None):
        self.schema = schema
        self.row = row
        self.resolve = resolve
        self.window_values = {}  # id(WindowCall) -> value, precomputed

    def get(self, name):
        return self.row[self.schema.resolve(name)]

    def rows(self):
        return [self.row]


class GroupContext:
    """A whole group of rows: aggregates see every row, anything else
    reads the first one (the SQLite convention for bare columns)."""

    def __init__(self, schema, rows, resolve=None):
        self.schema = schema
        self.group = rows
        self.resolve = resolve

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
        candidates = []
        for candidate in node.values:
            if isinstance(candidate, ast.Subquery):
                result = _subquery_rows(candidate.select, ctx)
                if len(result.columns) != 1:
                    raise SquealError("an IN subquery must return exactly one column")
                candidates.extend(row[0] for row in result.rows)
            else:
                candidates.append(eval_expr(candidate, ctx))
        seen_null = False
        for candidate in candidates:
            eq = compare("=", value, candidate)
            if eq is True:
                return False if node.negated else True
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
    if isinstance(node, ast.WindowCall):
        if id(node) not in ctx.window_values:
            raise SquealError("window functions are only supported in the "
                              "select list for now")
        return ctx.window_values[id(node)]
    if isinstance(node, ast.Subquery):
        return _run_subquery(node.select, ctx)
    if isinstance(node, ast.Exists):
        if ctx.resolve is None:
            raise SquealError("EXISTS needs a table source")
        return len(execute(node.select, ctx.resolve).rows) > 0
    raise SquealError("cannot evaluate %r" % (node,))


def _subquery_rows(select, ctx):
    if ctx.resolve is None:
        raise SquealError("subqueries need a table source")
    return execute(select, ctx.resolve)


def _run_subquery(select, ctx):
    result = _subquery_rows(select, ctx)
    if len(result.columns) != 1:
        raise SquealError("a scalar subquery must return exactly one column")
    if not result.rows:
        return None
    if len(result.rows) > 1:
        raise SquealError("a scalar subquery returned %d rows" % len(result.rows))
    return result.rows[0][0]


def eval_call(node, ctx):
    name = node.name.lower()
    if name in AGGREGATES:
        if node.star and name != "count":
            raise SquealError("%s(*) is not valid, only count(*)" % name)
        if node.star:
            values = ctx.rows()
        else:
            values = [eval_expr(node.args[0], RowContext(ctx.schema, row, ctx.resolve))
                      for row in ctx.rows()]
        if node.distinct:
            seen = set()
            unique = []
            for v in values:
                marker = (type(v).__name__, v)
                if marker not in seen:
                    seen.add(marker)
                    unique.append(v)
            values = unique
        if name == "group_concat":
            separator = ","
            if len(node.args) > 1:
                separator = eval_expr(node.args[1], ctx)
                if separator is None:
                    separator = ""
            return AGGREGATES[name](values, node.star, separator)
        return AGGREGATES[name](values, node.star)
    if name in SCALAR_FUNCTIONS:
        lo, hi, fn = SCALAR_FUNCTIONS[name]
        if not (lo <= len(node.args) <= hi):
            raise SquealError("%s takes %s argument(s), got %d"
                              % (name, str(lo) if lo == hi else "%d to %d" % (lo, hi),
                                 len(node.args)))
        return fn([eval_expr(a, ctx) for a in node.args])
    raise SquealError("unknown function: %s" % node.name)


def _child_nodes(node):
    slots = []
    for klass in type(node).__mro__:
        slots.extend(getattr(klass, "__slots__", ()))
    for slot in slots:
        value = getattr(node, slot)
        if isinstance(value, ast.Node):
            yield value
        elif isinstance(value, (list, tuple)):
            for item in value:
                if isinstance(item, ast.Node):
                    yield item
                elif isinstance(item, (list, tuple)):
                    for sub in item:
                        if isinstance(sub, ast.Node):
                            yield sub


def has_aggregate(node):
    """True when a plain (non-window) aggregate call appears below node.

    Aggregates inside a WindowCall belong to the window; they do not
    turn the whole query into a GROUP BY query.
    """
    if isinstance(node, ast.WindowCall):
        return False
    if isinstance(node, ast.FuncCall) and node.name.lower() in AGGREGATES:
        return True
    return any(has_aggregate(child) for child in _child_nodes(node))


RANKING_FUNCTIONS = {"row_number", "rank", "dense_rank"}


def find_windows(select):
    """Every WindowCall under the select items, in order."""
    found = []
    for expr, _ in select.items:
        found.extend(n for n in expr.walk() if isinstance(n, ast.WindowCall))
    return found


def compute_windows(calls, schema, rows, resolve):
    """Precompute every window call: {id(node): [value per input row]}.

    Frame semantics: with an ORDER BY the frame is the partition start
    through the current row (a running total); without one it is the
    whole partition.
    """
    results = {}
    for call in calls:
        name = call.call.name
        if name not in RANKING_FUNCTIONS and name not in AGGREGATES:
            raise SquealError("%s() cannot be used as a window function" % name)
        if name in RANKING_FUNCTIONS and call.call.args:
            raise SquealError("%s() takes no arguments" % name)
        if call.call.distinct:
            raise SquealError("DISTINCT is not supported in window functions")
        ctxs = [RowContext(schema, r, resolve) for r in rows]
        pkeys = [tuple(eval_expr(e, c) for e in call.partition_by) for c in ctxs]
        okeys = [[eval_expr(e, c) for e, _ in call.order_by] for c in ctxs]
        descs = [d for _, d in call.order_by]

        partitions = {}
        for i, key in enumerate(pkeys):
            partitions.setdefault(key, []).append(i)

        values = [None] * len(rows)
        for members in partitions.values():
            if call.order_by:
                from functools import cmp_to_key
                members = sorted(members, key=cmp_to_key(
                    lambda a, b: _cmp_rows(okeys[a], okeys[b], descs)))
            if name in ("rank", "dense_rank"):
                for i, r in _assign_ranks(name, members, okeys):
                    values[i] = r
                continue
            for pos, i in enumerate(members):
                if name == "row_number":
                    values[i] = pos + 1
                else:
                    frame = members[:pos + 1] if call.order_by else members
                    values[i] = _window_aggregate(call.call, ctxs, frame)
        results[id(call)] = values
    return results


def _assign_ranks(name, members, okeys):
    """rank(): ties share a rank, then gaps. dense_rank(): no gaps."""
    ranks = []
    for pos, i in enumerate(members):
        if pos == 0:
            ranks.append((i, 1))
            continue
        prev = members[pos - 1]
        tied = _cmp_rows(okeys[prev], okeys[i],
                         [False] * len(okeys[i])) == 0
        if tied:
            ranks.append((i, ranks[-1][1]))
        elif name == "rank":
            ranks.append((i, pos + 1))
        else:
            ranks.append((i, ranks[-1][1] + 1))
    return ranks


def _window_aggregate(call, ctxs, frame):
    if call.star:
        return AGGREGATES[call.name](frame, True)
    values = [eval_expr(call.args[0], ctxs[i]) for i in frame]
    return AGGREGATES[call.name](values, False)


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


def execute(select, source, stats=None):
    # stats, when given, collects per-stage row counts for EXPLAIN ANALYZE
    if hasattr(source, "columns"):
        # a bare table: single-source queries only
        resolve = lambda _name, t=source: t
    else:
        resolve = source
    if select.source is None:
        schema, rows = Schema([]), [()]
    else:
        schema, rows = scan_source(select.source, resolve)
    if stats is not None:
        stats["scan"] = len(rows)
    columns = schema.names

    # positional references: ORDER BY 2 / GROUP BY 1 mean select items
    def positional(expr):
        if isinstance(expr, ast.Literal) and isinstance(expr.value, int) \
                and not isinstance(expr.value, bool):
            if 1 <= expr.value <= len(select.items):
                return select.items[expr.value - 1][0]
            raise SquealError("positional reference %d is out of range: "
                              "the query selects %d item(s)"
                              % (expr.value, len(select.items)))
        return expr
    select_group_by = [positional(e) for e in select.group_by]
    select_order_by = [(positional(e), d) for e, d in select.order_by]

    if select.where is not None:
        rows = [r for r in rows
                if truthy(eval_expr(select.where, RowContext(schema, r, resolve))) is True]
    if stats is not None:
        stats["filter"] = len(rows)

    windows = find_windows(select)
    aggregated = bool(select.group_by) or any(has_aggregate(e) for e, _ in select.items) \
        or (select.having is not None and has_aggregate(select.having)) \
        or any(has_aggregate(e) for e, _ in select.order_by)
    if windows and aggregated:
        raise SquealError("window functions cannot be mixed with GROUP BY "
                          "or aggregates yet")

    if aggregated:
        groups = {}
        order = []
        for r in rows:
            key = tuple(eval_expr(e, RowContext(schema, r, resolve)) for e in select_group_by)
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
        contexts = [GroupContext(schema, groups[k], resolve) for k in order]
        if select.having is not None:
            contexts = [c for c in contexts
                        if truthy(eval_expr(select.having, c)) is True]
    else:
        contexts = [RowContext(schema, r, resolve) for r in rows]
        if select.having is not None:
            raise SquealError("HAVING needs GROUP BY or an aggregate")
        if windows:
            computed = compute_windows(windows, schema, rows, resolve)
            for i, ctx in enumerate(contexts):
                ctx.window_values = {node_id: vals[i]
                                     for node_id, vals in computed.items()}

    if stats is not None:
        stats["contexts"] = len(contexts)
    out_columns, project = _projection(select, columns)
    projected = []
    order_keys = []
    for ctx in contexts:
        row = tuple(project(ctx))
        projected.append(row)
        keys = []
        for expr, _ in select_order_by:
            # ORDER BY may name a select alias or output column
            if isinstance(expr, ast.Column) and expr.name in out_columns:
                keys.append(row[out_columns.index(expr.name)])
            else:
                keys.append(eval_expr(expr, ctx))
        order_keys.append(keys)

    pairs = list(zip(projected, order_keys))
    if select_order_by:
        from functools import cmp_to_key
        descs = [d for _, d in select_order_by]
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

    if stats is not None:
        stats["projected"] = len(projected)
    start = select.offset or 0
    end = None if select.limit is None else start + select.limit
    result = Result(out_columns, projected[start:end])
    if stats is not None:
        stats["rows"] = len(result.rows)
    return result


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
