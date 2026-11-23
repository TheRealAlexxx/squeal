"""Prepared recursive-term evaluation must match the generic path.

_materialize_recursive runs each iteration through a plan built once
from the static term when the term is a bare scan of the CTE with no
stages of its own; anything else falls back to a full execute() per
iteration. These tests run a battery of recursive queries under both
settings of executor._PREPARED_RECURSIVE and require identical columns
and rows, then pin a few results outright so the prepared path is
checked against semantics, not only against itself.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import squeal.executor as executor
from squeal.csvload import load
from squeal.executor import execute
from squeal.parser import parse

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")
TABLES = {"edges": load(os.path.join(FIXTURES, "edges.csv"))}

QUERIES = [
    # plain chain, the benchmark shape
    "with recursive x(n) as (select 1 union all "
    "select n + 1 from x where n < 20) select * from x",
    # two columns, arithmetic in both
    "with recursive fib(a, b) as (select 0, 1 union all "
    "select b, a + b from fib where a < 100) select a from fib",
    # UNION dedupe terminating a cycle over real data
    "with recursive reach(dst) as ("
    "select dst from edges where src = 'a' union "
    "select e.dst from reach r join edges e on e.src = r.dst) "
    "select * from reach",
    # bare scan over real data, alias on the CTE reference
    "with recursive walk(dst) as (select dst from edges "
    "where src = 'a' union select e.dst from walk w join edges e "
    "on e.src = w.dst) select count(*) from walk",
    # NULLs flowing through the working table and the filter
    "with recursive x(n, m) as (select 1, null union all "
    "select n + 1, case when n = 2 then 7 else m end from x "
    "where n < 4) select n, m from x",
    # qualified column reference in the recursive term
    "with recursive x(n) as (select 1 union all "
    "select x.n * 2 from x where x.n < 16) select n from x",
    # DISTINCT recursive term over deduped UNION (allowed; only
    # aggregates are not)
    "with recursive x(n) as (select 1 union "
    "select distinct (n + 1) % 4 from x where n < 40) "
    "select count(*) from x",
    # uncorrelated subquery inside the recursive term
    "with recursive x(n) as (select 1 union all "
    "select n + (select count(*) from edges) from x where n < 5) "
    "select n from x",
    # ORDER BY / LIMIT on the CTE, forcing early stop
    "with recursive x(n) as (select 1 union all "
    "select n + 1 from x where n < 500 limit 7) select * from x",
    # outer ORDER BY over the accumulated rows
    "with recursive x(n) as (select 1 union all "
    "select n + 1 from x where n < 6) select n from x order by n desc",
    # generic-path shapes: ORDER BY inside the recursive term stays
    # on the full execute() loop
    "with recursive x(n) as (select 1 union all "
    "select n + 1 from x where n < 4 order by n) select * from x",
    # string accumulation
    "with recursive x(s) as (select 'a' union all "
    "select s || 'b' from x where length(s) < 5) select s from x",
]


def run(sql):
    return execute(parse(sql), lambda name: TABLES[name])


class PreparedParityTests(unittest.TestCase):

    def tearDown(self):
        executor._PREPARED_RECURSIVE = True

    def test_prepared_matches_generic(self):
        for sql in QUERIES:
            expected = run(sql)
            executor._PREPARED_RECURSIVE = False
            try:
                generic = run(sql)
            finally:
                executor._PREPARED_RECURSIVE = True
            self.assertEqual(expected.columns, generic.columns, sql)
            self.assertEqual(expected.rows, generic.rows, sql)

    def test_pinned_results(self):
        out = run("with recursive x(n) as (select 1 union all "
                  "select n + 1 from x where n < 5) select * from x")
        self.assertEqual(out.rows, [(i,) for i in range(1, 6)])

        out = run("with recursive x(n, m) as (select 1, null union all "
                  "select n + 1, case when n = 2 then 7 else m end "
                  "from x where n < 4) select n, m from x")
        self.assertEqual(out.rows, [(1, None), (2, None), (3, 7), (4, 7)])

        out = run("with recursive x(n) as (select 1 union all "
                  "select n + 1 from x where n < 500 limit 7) "
                  "select * from x")
        self.assertEqual(out.rows, [(i,) for i in range(1, 8)])

    def test_prepared_flag_off_still_works(self):
        executor._PREPARED_RECURSIVE = False
        try:
            out = run("with recursive x(n) as (select 1 union all "
                      "select n + 1 from x where n < 5) select * from x")
        finally:
            executor._PREPARED_RECURSIVE = True
        self.assertEqual(out.rows, [(i,) for i in range(1, 6)])


if __name__ == "__main__":
    unittest.main()
