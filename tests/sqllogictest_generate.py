"""Generate the committed sqllogictest-subset corpus with SQLite as oracle.

For every curated query in tests/sqllogictest_queries.py, run both squeal
and SQLite over the shared fixtures, render both results the sqllogictest
way (NULL for nulls, %.3f for floats, one row per line, values joined by a
single space), and keep only the records where the two engines agree byte
for byte. Queries that raise on both engines become `statement error`
records. Everything else is listed as a divergence for manual pinning in
tests/sqllogictest/pinned.test - never silently dropped into the corpus.

Usage: python3 tests/sqllogictest_generate.py   (rewrites tests/sqllogictest/*.test)
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tests.sqllogictest_queries import AREAS
from tests.test_sqlite_differential import (load_tables, build_sqlite,
                                            run_squeal, run_sqlite)

OUT_DIR = os.path.join(os.path.dirname(__file__), "sqllogictest")


def render(rows):
    """Rows -> (type_chars, rendered_lines). Floats get the slt %.3f form."""
    if not rows:
        return "", []
    width = len(rows[0])
    types = []
    for c in range(width):
        kind = "T"
        for row in rows:
            v = row[c]
            if v is None:
                continue
            kind = "I" if isinstance(v, int) else "R" if isinstance(v, float) else "T"
            break
        types.append(kind)
    lines = []
    for row in rows:
        parts = []
        for v in row:
            if v is None:
                parts.append("NULL")
            elif isinstance(v, float):
                parts.append(format(v, ".3f"))
            elif isinstance(v, bool):
                parts.append(str(int(v)))
            else:
                parts.append(str(v))
        lines.append(" ".join(parts))
    return "".join(types), lines


def main():
    tables = load_tables()
    con = build_sqlite(tables)
    os.makedirs(OUT_DIR, exist_ok=True)
    divergent, kept, errors = [], 0, 0
    for area, queries in AREAS.items():
        records = []
        for sql in queries:
            s_rows = run_squeal(sql, tables)
            q_rows = run_sqlite(sql, con)
            s_err = s_rows and s_rows[0][0] == "__error__"
            q_err = q_rows and q_rows[0][0] == "__error__"
            if s_err or q_err:
                if s_err and q_err:
                    records.append("statement error\n%s\n" % sql)
                    errors += 1
                else:
                    divergent.append((area, sql, "error mismatch: squeal=%s sqlite=%s"
                                      % (s_rows[0], q_rows[0])))
                continue
            s_types, s_lines = render(s_rows)
            q_types, q_lines = render(q_rows)
            mode = "nosort" if "order by" in sql.lower() else "rowsort"
            if mode == "rowsort":
                ok = sorted(s_lines) == sorted(q_lines)
            else:
                ok = s_lines == q_lines
            if not ok:
                divergent.append((area, sql, "result mismatch"))
                continue
            records.append("query %s %s\n%s\n----\n%s\n" % (s_types, mode, sql, "\n".join(q_lines)))
            kept += 1
        with open(os.path.join(OUT_DIR, area + ".test"), "w") as fh:
            fh.write("# %s: expected results verified against SQLite at generation time\n\n" % area)
            fh.write("\n".join(records))
    print("kept %d query records + %d statement-error records" % (kept, errors))
    for area, sql, why in divergent:
        print("DIVERGENT [%s] %s: %s" % (area, sql, why))
    print("%d divergent (see pinned.test for manually pinned dialect cases)" % len(divergent))


if __name__ == "__main__":
    main()
