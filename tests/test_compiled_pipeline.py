"""The compiled row pipeline must match the RowContext pipeline.

For plain scans (no GROUP BY, no windows) execute() can closure-compile
the projection and ORDER BY keys and skip building a RowContext per
row; with a LIMIT and a pure column projection it also sorts first and
projects only the survivors. These tests run a battery of queries under
both settings of executor._COMPILED_ROW_PIPELINE and require identical
columns, rows and error behavior, then pin a few results outright.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import squeal.executor as executor
from squeal.csvload import load
from squeal.errors import SquealError
from squeal.executor import execute
from squeal.parser import parse

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")
TABLES = {name: load(os.path.join(FIXTURES, name + ".csv"))
          for name in ("people", "customers", "purchases", "readings",
                       "edges")}

QUERIES = [
    # plain column scans
    "select name from people",
    "select * from people",
    "select name, age from people order by age",
    # the benchmark shape
    "select name from people order by score desc limit 2",
    "select name from people order by score desc limit 2 offset 1",
    # alias and positional keys
    "select name as n, score as s from people order by s",
    "select name, score from people order by 2 desc",
    "select score as s from people order by s desc limit 3",
    # expression projection and keys
    "select name, age + 1 from people order by age + 1",
    "select name || '!' from people order by 1",
    "select name from people order by score * 2, name",
    # multi-key, mixed direction, NULLs in keys
    "select name from people order by city, score desc",
    "select name from people order by score desc, age",
    "select name, age from people order by age desc",
    # distinct (blocks deferral)
    "select distinct city from people order by city limit 2",
    "select distinct city from people",
    # where + order + limit
    "select name from people where city = 'London' order by score desc "
    "limit 2",
    "select name from people where age is null",
    "select name from people where age is not null order by age",
    # limit/offset edges
    "select name from people limit 0",
    "select name from people limit 3 offset 100",
    "select name from people order by name limit 100",
    # no source
    "select 1",
    "select 1 + 2 as three",
    # interpreter-only shapes fall back to RowContext: subquery, case,
    # function call in projection or key
    "select name, (select count(*) from purchases) from people "
    "order by 2 limit 2",
    "select name, case when age > 30 then 'old' else 'young' end "
    "from people order by 2, name",
    "select name from people order by lower(name) limit 3",
    # correlated subquery in projection
    "select name, (select count(*) from purchases p "
    "where p.customer_id = people.rowid) from people limit 2",
    # join (schema from two sources)
    "select c.name, p.item from customers c join purchases p "
    "on p.customer_id = c.id order by p.amount desc limit 3",
    # order by over computed floats
    "select id, temp from readings order by temp limit 4",
    "select id from readings order by temp desc limit 4 offset 2",
]

# (sql, expected error substring): a failing projection and a failing
# key must compete in baseline evaluation order - row by row, project
# then keys - not pipeline stage by stage.
ERROR_QUERIES = [
    # projection type error beats the ORDER BY division
    ("select age + name from people order by 1 / 0", "cannot apply +"),
    # reversed: the projection division beats the key's type error
    ("select 1 / 0 from people order by age + name", "division by zero"),
    # row 1's projection fails before any key runs
    ("select 10 / (age - 36) from people order by 10 / (age - 41)",
     "division by zero"),
    # row 1 projects clean, so row 1's key fails before row 2's
    # projection would
    ("select 10 / (age - 41) from people order by 10 / (age - 36)",
     "division by zero"),
    # positional reference out of range
    ("select name from people order by 5", "out of range"),
    # HAVING without grouping or aggregates
    ("select name from people having 1 > 0", "HAVING"),
]


def run(sql):
    return execute(parse(sql), lambda name: TABLES[name])


class CompiledPipelineParityTests(unittest.TestCase):

    def tearDown(self):
        executor._COMPILED_ROW_PIPELINE = True

    def run_both(self, sql):
        executor._COMPILED_ROW_PIPELINE = True
        try:
            fast = ("ok",) + (lambda r: (r.columns, r.rows))(run(sql))
        except SquealError as e:
            fast = ("err", type(e).__name__, str(e))
        executor._COMPILED_ROW_PIPELINE = False
        try:
            slow = ("ok",) + (lambda r: (r.columns, r.rows))(run(sql))
        except SquealError as e:
            slow = ("err", type(e).__name__, str(e))
        executor._COMPILED_ROW_PIPELINE = True
        return fast, slow

    def test_compiled_matches_context(self):
        for sql in QUERIES:
            fast, slow = self.run_both(sql)
            self.assertEqual(fast, slow, sql)

    def test_errors_match(self):
        for sql, expected in ERROR_QUERIES:
            fast, slow = self.run_both(sql)
            self.assertEqual(fast, slow, sql)
            self.assertEqual(fast[0], "err", sql)
            self.assertIn(expected, fast[-1], sql)

    def test_pinned_results(self):
        out = run("select name from people order by score desc limit 2")
        self.assertEqual(out.rows, [("Barbara",), ("Ada",)])

        out = run("select name from people order by score desc "
                  "limit 2 offset 1")
        self.assertEqual(out.rows, [("Ada",), ("Grace",)])

        out = run("select name, age from people order by age desc")
        self.assertEqual(out.rows[0], ("Barbara", 42))
        # NULLs last in both directions
        self.assertEqual(out.rows[-1], ("Katherine", None))

        out = run("select name as n, score as s from people "
                  "order by s limit 1")
        self.assertEqual(out.columns, ["n", "s"])
        self.assertEqual(out.rows, [("Edsger", 77.25)])

    def test_flag_off_still_works(self):
        executor._COMPILED_ROW_PIPELINE = False
        try:
            out = run("select name from people order by score desc "
                      "limit 2")
        finally:
            executor._COMPILED_ROW_PIPELINE = True
        self.assertEqual(out.rows, [("Barbara",), ("Ada",)])


if __name__ == "__main__":
    unittest.main()
