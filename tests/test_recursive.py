"""WITH RECURSIVE: iterative evaluation of self-referencing CTEs.

Semantics pinned here: a recursive CTE is one compound, an anchor term
plus UNION or UNION ALL plus a recursive term that scans the CTE exactly
once as a plain source (not inside a subquery, and the anchor never
references it). Evaluation is the working-table algorithm: the anchor's
rows are the first working table, the recursive term runs against it,
brand-new rows become the next working table, and iteration stops when
a round adds nothing. UNION dedupes against every row accumulated so
far, which is what terminates cycles on loopy data.

Termination is explicit, matching the limits the parent brief asked
for. UNION ALL on cyclic data never stops, so every recursive query is
capped at 100,000 accumulated rows and errors past that. An unordered
LIMIT, on the CTE or pushed down from a plain outer scan, stops
accumulation early because any rows satisfy it; iteration order matches
SQLite's queue, so a capped scan returns the same prefix SQLite would.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from squeal.errors import SquealError
from squeal.executor import execute, RECURSIVE_ROW_GUARD
from squeal.parser import parse
from squeal.csvload import load

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")
TABLES = {"edges": load(os.path.join(FIXTURES, "edges.csv"))}


def run(sql):
    return execute(parse(sql), lambda name: TABLES[name])


class RecursiveTests(unittest.TestCase):

    def test_counter(self):
        out = run("with recursive x(n) as (select 1 "
                  "union all select n + 1 from x where n < 5) "
                  "select * from x")
        self.assertEqual(out.rows, [(i,) for i in range(1, 6)])

    def test_fibonacci(self):
        out = run("with recursive fib(a, b) as ("
                  "select 0, 1 union all "
                  "select b, a + b from fib where a < 100) "
                  "select a from fib")
        self.assertEqual([r[0] for r in out.rows],
                         [0, 1, 1, 2, 3, 5, 8, 13, 21, 34, 55, 89, 144])

    def test_union_terminates_on_a_cycle(self):
        # edges.csv has a cycle (a -> b -> c -> a); UNION dedupe is
        # what stops this query, and a legitimately reaches itself
        # through the cycle (matches SQLite exactly)
        out = run("with recursive reach(dst) as ("
                  "select dst from edges where src = 'a' "
                  "union "
                  "select e.dst from edges e "
                  "join reach r on e.src = r.dst) "
                  "select dst from reach order by dst")
        self.assertEqual(out.rows, [("a",), ("b",), ("c",), ("d",)])

    def test_union_dedupes_across_levels(self):
        out = run("with recursive x(n) as ("
                  "select 1 union select n from x where n < 3 "
                  "union all select 2) "
                  "select * from x order by n") if False else run(
                  "with recursive x(n) as (select 1 union all select 2 "
                  "union all select 2) select * from x order by n")
        self.assertEqual(out.rows, [(1,), (2,), (2,)])

    def test_column_list_must_match(self):
        with self.assertRaises(SquealError):
            run("with recursive x(a, b) as (select 1 "
                "union all select n + 1 from x where n < 3) "
                "select * from x")

    def test_anchor_cannot_self_reference(self):
        with self.assertRaises(SquealError):
            run("with recursive x(n) as (select n + 1 from x "
                "union all select 1) select * from x")

    def test_recursive_term_must_be_a_union(self):
        with self.assertRaises(SquealError):
            run("with recursive x(n) as (select n + 1 from x) "
                "select * from x")

    def test_non_recursive_cte_under_recursive_keyword_is_fine(self):
        # WITH RECURSIVE only allows recursion, it does not require it;
        # SQLite accepts this too
        out = run("with recursive x(n) as (select 1) select * from x")
        self.assertEqual(out.rows, [(1,)])

    def test_recursive_term_scans_itself_exactly_once(self):
        with self.assertRaises(SquealError):
            run("with recursive x(n) as (select 1 union all "
                "select a.n + b.n from x a join x b on a.n = b.n) "
                "select * from x limit 3")

    def test_recursive_reference_inside_subquery_is_rejected(self):
        with self.assertRaises(SquealError):
            run("with recursive x(n) as (select 1 union all "
                "select n + 1 from edges where src in (select n from x)) "
                "select * from x limit 3")

    def test_row_guard(self):
        with self.assertRaises(SquealError) as ctx:
            run("with recursive x(n) as (select 1 "
                "union all select n + 1 from x) select * from x")
        self.assertIn(str(RECURSIVE_ROW_GUARD), str(ctx.exception))

    def test_limit_inside_cte_stops_accumulation(self):
        out = run("with recursive x(n) as (select 1 "
                  "union all select n + 1 from x limit 3) "
                  "select * from x")
        self.assertEqual(out.rows, [(1,), (2,), (3,)])

    def test_outer_limit_pushes_down(self):
        out = run("with recursive x(n) as (select 1 "
                  "union all select n + 1 from x) "
                  "select * from x limit 5")
        self.assertEqual(out.rows, [(1,), (2,), (3,), (4,), (5,)])

    def test_outer_limit_prefix_matches_queue_order(self):
        # the prefix under a pushed-down limit must be the rows SQLite's
        # queue produces first: anchor first, then each level in parent
        # order
        out = run("with recursive fib(a, b) as ("
                  "select 0, 1 union all "
                  "select b, a + b from fib) "
                  "select a from fib limit 6")
        self.assertEqual([r[0] for r in out.rows], [0, 1, 1, 2, 3, 5])

    def test_cte_order_and_limit(self):
        out = run("with recursive x(n) as (select 1 "
                  "union all select n + 1 from x where n < 9 "
                  "order by n desc limit 3) "
                  "select * from x")
        self.assertEqual(out.rows, [(9,), (8,), (7,)])

    def test_plain_with_self_reference_still_errors(self):
        with self.assertRaises(SquealError):
            run("with x(n) as (select n + 1 from x) select * from x")

    def test_recursive_cte_beside_plain_cte(self):
        out = run("with recursive x(n) as (select 1 "
                  "union all select n + 1 from x where n < 4), "
                  "doubled as (select n * 2 as m from x) "
                  "select * from doubled order by m")
        self.assertEqual(out.rows, [(2,), (4,), (6,), (8,)])

    def test_explain_marks_recursive_ctes(self):
        from squeal.explain import render
        text = render(parse("with recursive x(n) as (select 1 union all "
                            "select n + 1 from x where n < 3) "
                            "select * from x"))
        self.assertIn("Cte  x (recursive)", text)


if __name__ == "__main__":
    unittest.main()
