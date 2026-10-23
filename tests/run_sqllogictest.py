"""Run the sqllogictest-subset corpus against squeal.

Format subset (per .test file): `query <types> <sortmode>` records with the
SQL, a `----` line and the expected rows, and `statement error` records,
optionally with a substring the error message must contain.
Values render the sqllogictest way: NULL for nulls, %.3f for floats, one row
per line, values joined by single spaces. `rowsort` sorts expected and actual
lines before comparing; `nosort` compares in order. Expected values in the
feature files were verified against SQLite at generation time
(tests/sqllogictest_generate.py); pinned.test holds hand-verified dialect
differences.

Usage: python3 tests/run_sqllogictest.py [--check-readme README.md]
Exit 1 on any mismatch, 0 when every record passes.
"""

import glob
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from squeal.csvload import load
from squeal.executor import execute
from squeal.parser import parse

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")
CORPUS = os.path.join(os.path.dirname(__file__), "sqllogictest")
TABLES = ["people", "customers", "purchases", "readings", "edges"]


def render_rows(rows):
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
    return lines


def run_file(path, tables):
    """Returns (passed, failed, failures) record counts for one .test file."""
    with open(path) as fh:
        text = fh.read()
    passed = failed = 0
    failures = []
    # records are separated by blank lines
    for block in re.split(r"\n\s*\n", text):
        lines = [ln for ln in block.splitlines() if ln.strip() and not ln.startswith("#")]
        if not lines:
            continue
        head = lines[0]
        if head.startswith("statement error"):
            want = head[len("statement error"):].strip()  # optional error-text substring
            sql = "\n".join(lines[1:])
            try:
                execute(parse(sql), lambda name: tables[name])
                failed += 1
                failures.append("expected an error but got rows:\n" + sql)
            except Exception as exc:
                if want and want not in str(exc):
                    failed += 1
                    failures.append("%s\nerror %r does not contain %r" % (sql, str(exc), want))
                else:
                    passed += 1
            continue
        m = re.match(r"query\s+(\S+)\s+(\S+)", head)
        if not m:
            failed += 1
            failures.append("unparseable record head: " + head)
            continue
        mode = m.group(2)
        sep = lines.index("----")
        sql = "\n".join(lines[1:sep])
        expected = lines[sep + 1:]
        try:
            got = render_rows(execute(parse(sql), lambda name: tables[name]).rows)
        except Exception as exc:
            failed += 1
            failures.append("%s\nraised %s: %s" % (sql, exc.__class__.__name__, exc))
            continue
        ok = sorted(expected) == sorted(got) if mode == "rowsort" else expected == got
        if ok:
            passed += 1
        else:
            failed += 1
            failures.append("%s\nexpected:\n%s\ngot:\n%s" % (sql, "\n".join(expected), "\n".join(got)))
    return passed, failed, failures


def main():
    tables = {name: load(os.path.join(FIXTURES, name + ".csv")) for name in TABLES}
    total_pass = total_fail = 0
    for path in sorted(glob.glob(os.path.join(CORPUS, "*.test"))):
        p, f, failures = run_file(path, tables)
        total_pass += p
        total_fail += f
        print("%s: %d passed, %d failed" % (os.path.basename(path), p, f))
        for why in failures:
            print("---\n" + why)
    print("total: %d records, %d passed, %d failed" % (total_pass + total_fail, total_pass, total_fail))
    if "--check-readme" in sys.argv:
        readme_path = sys.argv[sys.argv.index("--check-readme") + 1]
        with open(readme_path) as fh:
            readme = fh.read()
        m = re.search(r"sqllogictest subset: (\d+) records", readme)
        if not m:
            print("README has no 'sqllogictest subset: N records' line")
            return 1
        if int(m.group(1)) != total_pass:
            print("README claims %s records but the corpus has %d passing - update the README" % (m.group(1), total_pass))
            return 1
        print("README count matches: %d records" % total_pass)
    return 1 if total_fail else 0


if __name__ == "__main__":
    sys.exit(main())
