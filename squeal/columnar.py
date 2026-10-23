"""Columnar execution experiment (issue #4).

An alternate evaluation path for a narrow but common query shape: a
single-table scan whose WHERE is simple predicates, returning a plain
projection or basic aggregates, optionally grouped by one column.

The row engine walks the expression tree once per row. Here the WHERE
clause compiles to one Python list comprehension over whole column
lists, so the per-row work runs at bytecode speed instead of
interpreter-dispatch speed. Literals enter the generated code through
repr() only; no user text is spliced into it.

Anything outside the subset makes the compiler raise _Bail and the
caller falls back to the row engine. Correctness parity with the row
engine is the bar (tests/test_columnar.py compares them row for row);
the win is measured in benchmarks/columnar.py.

NULL handling follows the row engine's three-valued logic exactly
(values.py): comparisons touching NULL yield UNKNOWN, AND/OR/NOT
propagate it the SQL way, and only rows whose predicate is genuinely
TRUE survive.
"""

from . import astnodes as ast
from .executor import Result, _default_name

_CMP_OPS = {"=": "==", "!=": "!=", "<>": "!=",
            "<": "<", "<=": "<=", ">": ">", ">=": ">="}
_AGGREGATES = {"count", "sum", "avg", "min", "max"}


class _Bail(Exception):
    """The query shape is outside the columnar subset."""


def _family(kind):
    if kind in (int, float):
        return "num"
    if kind is str:
        return "str"
    raise _Bail()


def _operand(node, slots, types):
    """(source, type family, nullable) for one operand, or bail."""
    if isinstance(node, ast.Literal):
        if node.value is None or isinstance(node.value, bool):
            raise _Bail()
        return repr(node.value), _family(type(node.value)), False
    if isinstance(node, ast.Column):
        if node.name not in slots:
            raise _Bail()
        return slots[node.name], types[slots[node.name]], True
    raise _Bail()


def _predicate(node, slots, types):
    """Compile WHERE to a Python expression over v0..vn.

    The expression yields True, False, or None (SQL UNKNOWN), matching
    values.sql_and/sql_or/sql_not. Callers keep rows where it == True.
    """
    if isinstance(node, ast.Binary) and node.op in ("and", "or"):
        a = _predicate(node.left, slots, types)
        b = _predicate(node.right, slots, types)
        if node.op == "and":
            return "(False if ((%s) == False or (%s) == False) " \
                   "else (None if ((%s) is None or (%s) is None) " \
                   "else True))" % (a, b, a, b)
        return "(True if ((%s) == True or (%s) == True) " \
               "else (None if ((%s) is None or (%s) is None) " \
               "else False))" % (a, b, a, b)
    if isinstance(node, ast.Unary) and node.op == "not":
        inner = _predicate(node.expr, slots, types)
        return "(None if (%s) is None else (not (%s)))" % (inner, inner)
    if isinstance(node, ast.IsNull):
        src, _, nullable = _operand(node.expr, slots, types)
        if not nullable:
            # a literal is never NULL, so this is constant
            return "True" if node.negated else "False"
        result = "(%s is not None)" % src if node.negated \
            else "(%s is None)" % src
        # IS NULL / IS NOT NULL never yield UNKNOWN
        return "(True if %s else False)" % result
    if isinstance(node, ast.Binary) and node.op in _CMP_OPS:
        lsrc, ltype, lnull = _operand(node.left, slots, types)
        rsrc, rtype, rnull = _operand(node.right, slots, types)
        checks = ["%s is None" % s
                  for s, n in ((lsrc, lnull), (rsrc, rnull)) if n]
        guard = "(%s)" % " or ".join(checks) if checks else "False"
        if ltype != rtype:
            # squeal orders numbers before text, so a mixed-type
            # comparison has the same answer for every non-NULL row
            num_first = ltype == "num"
            constant = {"<": num_first, "<=": num_first,
                        ">": not num_first, ">=": not num_first,
                        "=": False, "!=": True, "<>": True}[node.op]
            return "(None if %s else %s)" % (guard, constant)
        return "(None if %s else (%s %s %s))" \
            % (guard, lsrc, _CMP_OPS[node.op], rsrc)
    raise _Bail()


def _compile_mask(where, table):
    """A list of bool, one per row: True keeps the row."""
    slots = {}
    types = {}
    for i, name in enumerate(table.columns):
        slots[name] = "v%d" % i
        types["v%d" % i] = _family(table.types[i])
        base = table.path.rsplit("/", 1)[-1]
        if base.endswith(".csv"):
            slots[base[:-4] + "." + name] = "v%d" % i
            types["v%d" % i] = _family(table.types[i])
    expr = _predicate(where, slots, types)
    used = sorted({int(v[1:]) for v in _vars(expr)})
    if not used:
        # constant predicate (e.g. 'x' is null): same answer for every row
        keep = eval("(%s) == True" % expr)
        return [keep] * len(table.rows)
    names = ["v%d" % i for i in used]
    if len(used) == 1:
        src = "lambda cols: [(%s) == True for %s in cols[%d]]" % (
            expr, names[0], used[0])
    else:
        src = "lambda cols: [(%s) == True for %s in zip(%s)]" % (
            expr, ", ".join(names),
            ", ".join("cols[%d]" % i for i in used))
    fn = eval(src)  # built from the validated AST; literals via repr()
    cols = [list(col) for col in zip(*table.rows)] if table.rows else \
           [[] for _ in table.columns]
    return fn(cols)


def _vars(expr):
    import re
    return set(re.findall(r"v\d+", expr))


def _aggregate(call, table, mask, idxs=None):
    name = call.name.lower()
    if name not in _AGGREGATES or call.distinct:
        raise _Bail()
    if idxs is None:
        idxs = [i for i, keep in enumerate(mask) if keep]
    if name == "count" and call.star:
        return len(idxs)
    if call.star:
        raise _Bail()
    col = _value_column(call.args[0], table)
    values = [col[i] for i in idxs if col[i] is not None]
    if name == "count":
        return len(values)
    if not values:
        return None
    if name == "sum":
        return sum(values)
    if name == "avg":
        return sum(values) / len(values)
    if name == "min":
        return min(values)
    return max(values)


def _same_column(a, b):
    return isinstance(b, ast.Column) and \
        (a.name == b.name or a.name.endswith("." + b.name)
         or b.name.endswith("." + a.name))


def _value_column(expr, table):
    """The whole column for a bare column reference, or bail."""
    if not isinstance(expr, ast.Column):
        raise _Bail()
    for i, name in enumerate(table.columns):
        if name == expr.name or expr.name.endswith("." + name):
            return [row[i] for row in table.rows]
    raise _Bail()


def run(select, table):
    """Execute the subset on whole columns, or return None to fall back."""
    try:
        if select.distinct or select.order_by or select.source is None:
            return None
        if not isinstance(select.source, ast.TableRef):
            return None
        if select.group_by and len(select.group_by) > 1:
            return None
        if select.having is not None:
            return None
        for item, _alias in select.items:
            if isinstance(item, ast.WindowCall):
                return None

        mask = [True] * len(table.rows)
        if select.where is not None:
            mask = _compile_mask(select.where, table)

        key_expr = None
        group_keys = None
        if select.group_by:
            key_expr = select.group_by[0]
            group_keys = _value_column(key_expr, table)

        if any(isinstance(item, ast.FuncCall) for item, _ in select.items):
            return _run_aggregates(select, table, mask, key_expr, group_keys)
        if group_keys is not None:
            return None  # GROUP BY without aggregates: row engine's job
        return _run_projection(select, table, mask)
    except _Bail:
        return None


def _limit_offset(select, rows):
    rows = rows[select.offset or 0:]
    if select.limit is not None:
        rows = rows[:select.limit]
    return rows


def _run_projection(select, table, mask):
    if len(select.items) == 1 and isinstance(select.items[0][0], ast.Star):
        value_cols = [[row[i] for row in table.rows]
                      for i in range(len(table.columns))]
        columns = list(table.columns)
    else:
        columns = [alias or _default_name(item)
                   for item, alias in select.items]
        value_cols = [_value_column(item, table)
                      for item, _ in select.items]
    rows = [tuple(col[i] for col in value_cols)
            for i, keep in enumerate(mask) if keep]
    return Result(columns, _limit_offset(select, rows))


def _run_aggregates(select, table, mask, key_expr, group_keys):
    columns = [alias or _default_name(item) for item, alias in select.items]
    if group_keys is None:
        row = []
        for item, _ in select.items:
            if isinstance(item, ast.FuncCall):
                row.append(_aggregate(item, table, mask))
            else:
                raise _Bail()  # bare columns in aggregates: row engine's job
        return Result(columns, _limit_offset(select, [tuple(row)]))
    groups = {}
    order = []
    for i, keep in enumerate(mask):
        if keep:
            key = group_keys[i]
            if key not in groups:
                groups[key] = []
                order.append(key)
            groups[key].append(i)
    rows = []
    for key in order:
        idxs = groups[key]
        row = []
        for item, _ in select.items:
            if isinstance(item, ast.FuncCall):
                row.append(_aggregate(item, table, mask, idxs))
            elif isinstance(item, ast.Column) and _same_column(item, key_expr):
                row.append(key)
            else:
                raise _Bail()
        rows.append(tuple(row))
    return Result(columns, _limit_offset(select, rows))
