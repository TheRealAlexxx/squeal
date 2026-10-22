"""EXPLAIN: print the pipeline a query will run through, as a tree.

squeal has no planner worth the name yet: every query is the same fixed
pipeline and EXPLAIN shows which stages are active and with what. That is
still worth printing, because it makes the execution model legible.
"""

from . import astnodes as ast
from .executor import has_aggregate
from .unparse import to_sql


def source_sql(node):
    if isinstance(node, ast.TableRef):
        return node.name + (" as " + node.alias if node.alias else "")
    if node.kind == "cross":
        return "%s CROSS JOIN %s" % (source_sql(node.left), source_sql(node.right))
    return "%s %s JOIN %s ON %s" % (source_sql(node.left), node.kind.upper(),
                                    source_sql(node.right), to_sql(node.on))


def scan_stages(node, resolve=None):
    """Innermost-first Scan/Join stages for the source tree."""
    if isinstance(node, ast.TableRef):
        return ["Scan  " + source_sql(node)]
    stages = scan_stages(node.left, resolve)
    if node.kind == "cross":
        stages.append("Nested Loop Join  %s (cross)" % source_sql(node.right))
        return stages
    strategy = "Nested Loop"
    if resolve is not None:
        from .executor import scan_schema, join_strategy
        if join_strategy(node, scan_schema(node.left, resolve),
                         scan_schema(node.right, resolve)) == "hash":
            strategy = "Hash"
    stages.append("%s Join  %s on %s" % (strategy, source_sql(node.right),
                                         to_sql(node.on)))
    return stages


def plan(select, resolve=None):
    """Return the EXPLAIN tree as a list of (depth, text) lines."""
    lines = []
    depth = 0

    def stage(text):
        lines.append((depth, text))

    if select.limit is not None or select.offset is not None:
        bits = []
        if select.limit is not None:
            bits.append("limit %d" % select.limit)
        if select.offset:
            bits.append("offset %d" % select.offset)
        stage("Limit " + " ".join(bits))
        depth += 1
    if select.order_by:
        keys = ", ".join(to_sql(e) + (" DESC" if d else "")
                         for e, d in select.order_by)
        stage("Sort  keys: " + keys)
        depth += 1
    if select.distinct:
        stage("Distinct")
        depth += 1
    items = ", ".join(to_sql(e) for e, _ in select.items)
    aggregated = bool(select.group_by) or any(has_aggregate(e) for e, _ in select.items)
    # appended outermost-first; reversed at the end
    stage("Project  " + items)
    depth += 1
    if aggregated:
        if select.having is not None:
            stage("Having  " + to_sql(select.having))
            depth += 1
        keys = ", ".join(to_sql(e) for e in select.group_by)
        stage("Aggregate  group by: " + (keys or "(whole table)"))
        depth += 1
    if select.where is not None:
        stage("Filter  " + to_sql(select.where))
        depth += 1
    if select.source:
        # scan_stages is innermost-first; we append outermost-first and
        # reverse everything at the end
        for text in reversed(scan_stages(select.source, resolve)):
            stage(text)
            depth += 1
    else:
        stage("Compute  (no table)")
    lines.reverse()
    return lines


def render(select, resolve=None):
    # the pipeline is a strict chain: position in the list is the depth
    lines = plan(select, resolve)
    out = []
    for i, (_, text) in enumerate(lines):
        out.append(text if i == 0 else "  " * (i - 1) + "-> " + text)
    return "\n".join(out)
