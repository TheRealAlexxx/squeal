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


COMPOUND_LABELS = {"union": "Union", "union_all": "Union All",
                   "intersect": "Intersect", "except": "Except"}


def _compound_plan(select, resolve=None):
    """The fixed pipeline, linearized: each core's own stages in order,
    separated by the compound operator that combines it, with the
    statement-level Sort/Limit last. Rendered with the usual chain
    indentation, so it reads top to bottom as data flow."""
    from .executor import _core_of
    lines = plan(_core_of(select), resolve)
    for op, core in select.compounds:
        lines.append((0, COMPOUND_LABELS[op]))
        lines.extend(plan(core, resolve))
    if select.order_by:
        keys = ", ".join(to_sql(e) + (" DESC" if d else "")
                         for e, d in select.order_by)
        lines.append((0, "Sort  keys: " + keys))
    if select.limit is not None or select.offset is not None:
        bits = []
        if select.limit is not None:
            bits.append("limit %d" % select.limit)
        if select.offset:
            bits.append("offset %d" % select.offset)
        lines.append((0, "Limit " + " ".join(bits)))
    return lines


def plan(select, resolve=None):
    """Return the EXPLAIN tree as a list of (depth, text) lines."""
    if select.compounds:
        lines = _compound_plan(select, resolve)
    else:
        lines = _plain_plan(select, resolve)
    for cte in select.ctes:
        # WITH definitions are separate pipelines, listed after the main
        # one, in declaration order
        lines.append((0, "Cte  " + cte.name))
        lines.extend(plan(cte.select, resolve))
    return lines


def _plain_plan(select, resolve=None):
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


# plan() used to end here; _plain_plan is the original body renamed so
# plan() can append WITH pipelines after any compound structure


def render_analyzed(select, resolve, stats, elapsed_ms):
    """The plan with the measured row counts attached to each stage."""
    lines = plan(select, resolve)
    out = []
    join_seen = False
    table_counts = iter(stats.get("table_rows", []))
    compound_counts = iter(stats.get("compound_ops", []))
    # compound plans: each core measured separately; the op line hands
    # over to the next core's counts
    core_stats = iter(stats.get("core_stats", [stats]))
    cur_core = next(core_stats)
    for i, (_, text) in enumerate(lines):
        note = ""
        if text.startswith("Scan  "):
            # a Scan over a WITH name is counted from its materialization,
            # not from a file
            base = text[6:].strip().split(" as ")[0].strip()
            cte_rows = stats.get("cte_rows", {})
            if base in cte_rows:
                note = "%d rows" % cte_rows[base]
            else:
                count = next(table_counts, None)
                if count is not None:
                    note = "%d rows" % count
        elif text.startswith(("Hash Join", "Nested Loop Join")) and not join_seen:
            join_seen = True
            note = "%d joined rows" % cur_core.get("scan", 0)
        elif text.startswith("Filter"):
            note = "%d in, %d out" % (cur_core.get("scan", 0),
                                      cur_core.get("filter", 0))
        elif text.startswith("Aggregate"):
            note = "%d groups" % cur_core.get("contexts", 0)
        elif text.startswith("Project"):
            note = "%d rows" % cur_core.get("projected", 0)
        elif text.startswith("Cte  "):
            name = text[5:].strip()
            rows = stats.get("cte_rows", {}).get(name)
            if rows is not None:
                note = "%d rows" % rows
            # stages below this line belong to the CTE's own pipeline
            cur_core = stats.get("cte_stats", {}).get(name, cur_core)
        elif text.startswith(("Union", "Intersect", "Except")):
            count = next(compound_counts, None)
            if count is not None:
                note = "%d rows" % count
            cur_core = next(core_stats, cur_core)
        elif text.startswith("Limit"):
            note = "%d rows" % stats.get("rows", 0)
        prefix = text if i == 0 else "  " * (i - 1) + "-> " + text
        out.append(prefix + ("   -- " + note if note else ""))
    out.append("elapsed: %.1f ms" % elapsed_ms)
    return "\n".join(out)


def render(select, resolve=None):
    # the pipeline is a strict chain: position in the list is the depth
    lines = plan(select, resolve)
    out = []
    for i, (_, text) in enumerate(lines):
        out.append(text if i == 0 else "  " * (i - 1) + "-> " + text)
    return "\n".join(out)
