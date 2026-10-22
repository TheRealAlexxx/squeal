"""Print a Result as an aligned text table, or as CSV/JSON."""

import csv
import io
import json


def _cell(value):
    if value is None:
        return "NULL"
    if isinstance(value, float):
        return "%g" % value
    return str(value)


def as_table(result):
    rows = [[_cell(v) for v in row] for row in result.rows]
    widths = [len(c) for c in result.columns]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))
    rule = "+" + "+".join("-" * (w + 2) for w in widths) + "+"
    header = "|" + "|".join(" %s " % c.ljust(w) for c, w in zip(result.columns, widths)) + "|"
    out = [rule, header, rule]
    for row in rows:
        out.append("|" + "|".join(" %s " % cell.ljust(w) for cell, w in zip(row, widths)) + "|")
    out.append(rule)
    out.append("(%d row%s)" % (len(rows), "" if len(rows) == 1 else "s"))
    return "\n".join(out)


def as_csv(result):
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(result.columns)
    for row in result.rows:
        writer.writerow(["" if v is None else v for v in row])
    return buf.getvalue().rstrip("\n")


def as_json(result):
    return json.dumps([dict(zip(result.columns, row)) for row in result.rows],
                      indent=2)
