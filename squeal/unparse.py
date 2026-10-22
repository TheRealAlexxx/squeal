"""Turn an expression tree back into SQL text, for EXPLAIN and errors."""

from . import astnodes as ast


def to_sql(node):
    if isinstance(node, ast.Literal):
        if node.value is None:
            return "NULL"
        if isinstance(node.value, str):
            return "'%s'" % node.value.replace("'", "''")
        return repr(node.value)
    if isinstance(node, ast.Column):
        return node.name
    if isinstance(node, ast.Star):
        return "*"
    if isinstance(node, ast.Unary):
        return "%s %s" % (node.op.upper(), to_sql(node.expr)) \
            if node.op == "not" else "%s%s" % (node.op, to_sql(node.expr))
    if isinstance(node, ast.Binary):
        return "(%s %s %s)" % (to_sql(node.left), node.op.upper(), to_sql(node.right))
    if isinstance(node, ast.IsNull):
        return "%s IS %sNULL" % (to_sql(node.expr), "NOT " if node.negated else "")
    if isinstance(node, ast.Like):
        return "%s %sLIKE %s" % (to_sql(node.expr),
                                 "NOT " if node.negated else "", to_sql(node.pattern))
    if isinstance(node, ast.InList):
        return "%s %sIN (%s)" % (to_sql(node.expr), "NOT " if node.negated else "",
                                 ", ".join(to_sql(v) for v in node.values))
    if isinstance(node, ast.Between):
        return "%s %sBETWEEN %s AND %s" % (
            to_sql(node.expr), "NOT " if node.negated else "",
            to_sql(node.low), to_sql(node.high))
    if isinstance(node, ast.FuncCall):
        if node.star:
            return "%s(*)" % node.name
        prefix = "DISTINCT " if node.distinct else ""
        return "%s(%s%s)" % (node.name, prefix,
                             ", ".join(to_sql(a) for a in node.args))
    if isinstance(node, ast.Subquery):
        return "(subquery)"
    if isinstance(node, ast.Exists):
        return "EXISTS (subquery)"
    if isinstance(node, ast.WindowCall):
        parts = []
        if node.partition_by:
            parts.append("PARTITION BY " + ", ".join(to_sql(e) for e in node.partition_by))
        if node.order_by:
            parts.append("ORDER BY " + ", ".join(
                to_sql(e) + (" DESC" if d else "") for e, d in node.order_by))
        return "%s OVER (%s)" % (to_sql(node.call), " ".join(parts))
    if isinstance(node, ast.Case):
        parts = ["CASE"]
        for cond, val in node.whens:
            parts.append("WHEN %s THEN %s" % (to_sql(cond), to_sql(val)))
        if node.else_val is not None:
            parts.append("ELSE %s" % to_sql(node.else_val))
        parts.append("END")
        return " ".join(parts)
    return repr(node)
