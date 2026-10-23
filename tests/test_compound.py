"""Compound selects: UNION, UNION ALL, INTERSECT, EXCEPT.

Semantics pinned here, beyond the SQLite differential coverage:
equal precedence folded left to right, non-ALL operators deduplicate
(NULL = NULL for dedupe, as with SELECT DISTINCT), column names come
from the first core, and ORDER BY over a compound is restricted to
output columns and positions.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from squeal.errors import SquealError
from squeal.executor import execute
from squeal.parser import parse
from squeal.csvload import load

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")
TABLES = {name: load(os.path.join(FIXTURES, name + ".csv"))
          for name in ("people", "customers", "purchases", "readings")}


def run(sql):
    return execute(parse(sql), lambda name: TABLES[name])


class CompoundTests(unittest.TestCase):
    def test_union_dedupes(self):
        r = run("select 1 as x union select 1 union select 2")
        self.assertEqual(r.rows, [(1,), (2,)])

    def test_union_distinct_keyword(self):
        r = run("select 1 as x union distinct select 1")
        self.assertEqual(r.rows, [(1,)])

    def test_union_all_keeps_duplicates_in_order(self):
        r = run("select 2 as x union all select 1 union all select 2")
        self.assertEqual(r.rows, [(2,), (1,), (2,)])

    def test_union_nulls_dedupe(self):
        r = run("select null as x union select null")
        self.assertEqual(r.rows, [(None,)])

    def test_number_and_text_stay_distinct(self):
        r = run("select 1 as x union select '1'")
        self.assertEqual(len(r.rows), 2)

    def test_numeric_dedupe_matches_comparison(self):
        # 1 and 1.0 compare equal, so they are one row under UNION
        r = run("select 1.0 as x union select 1")
        self.assertEqual(len(r.rows), 1)

    def test_intersect(self):
        r = run("select 1 as x union select 2 intersect select 2")
        # equal precedence, left to right: (1 U 2) n 2 = {2}
        self.assertEqual(r.rows, [(2,)])

    def test_except(self):
        r = run("select 1 as x union select 2 except select 2")
        self.assertEqual(r.rows, [(1,)])

    def test_except_everything(self):
        r = run("select 1 as x except select 1")
        self.assertEqual(r.rows, [])

    def test_column_names_from_first_core(self):
        r = run("select 1 as first_name union select 2 as second_name")
        self.assertEqual(r.columns, ["first_name"])

    def test_order_by_output_column(self):
        r = run("select 2 as x union select 1 order by x")
        self.assertEqual(r.rows, [(1,), (2,)])

    def test_order_by_position(self):
        r = run("select 2 as x, 'b' as y union select 1, 'a' order by 1")
        self.assertEqual(r.rows, [(1, "a"), (2, "b")])

    def test_order_by_nulls_last(self):
        r = run("select null as x union select 1 order by x")
        self.assertEqual(r.rows, [(1,), (None,)])

    def test_limit_offset(self):
        r = run("select 1 as x union select 2 union select 3 "
                "order by x limit 1 offset 1")
        self.assertEqual(r.rows, [(2,)])

    def test_cores_may_group_and_filter(self):
        r = run("select city, count(*) as n from people group by city "
                "union select 'everywhere', count(*) from people order by 1")
        self.assertEqual(r.rows[0], ("Amsterdam", 1))
        self.assertEqual(r.rows[-1], ("everywhere", 6))

    def test_compound_inside_subquery(self):
        r = run("select name from customers where id in "
                "(select customer_id from purchases union select 999) "
                "order by name")
        self.assertTrue(len(r.rows) >= 1)

    def test_empty_core(self):
        r = run("select name from people where age > 999 "
                "union select 'ghost' order by 1")
        self.assertEqual(r.rows, [("ghost",)])

    def test_column_count_mismatch(self):
        with self.assertRaises(SquealError) as cm:
            run("select 1, 2 union select 1")
        self.assertIn("same number of columns", str(cm.exception))
        self.assertIn("2 vs 1", str(cm.exception))

    def test_misplaced_order_by(self):
        with self.assertRaises(SquealError) as cm:
            run("select 1 as x order by x union select 2")
        self.assertIn("very end", str(cm.exception))

    def test_order_by_expression_rejected(self):
        with self.assertRaises(SquealError) as cm:
            run("select 1 as x union select 2 order by x + 1")
        self.assertIn("output column", str(cm.exception))

    def test_order_by_position_out_of_range(self):
        with self.assertRaises(SquealError) as cm:
            run("select 1 as x union select 2 order by 5")
        self.assertIn("out of range", str(cm.exception))

    def test_explain_lists_compound_stage(self):
        from squeal.cli import Session
        out = Session().run("explain select city from '%s' "
                            "union select 'x' as city order by city"
                            % os.path.join(FIXTURES, "people.csv"))
        self.assertIn("Union", out)
        self.assertLess(out.index("Scan"), out.index("Union"))
        self.assertLess(out.index("Union"), out.index("Sort"))

    def test_explain_analyze_measures_each_core(self):
        from squeal.cli import Session
        out = Session().run("explain analyze select 1 as x union select 2")
        self.assertIn("Union", out)
        self.assertIn("-- 2 rows", out)


    def test_explain_compound_arms_and_statement_tail(self):
        # the op divides pipelines at column 0 with the arm it combines
        # chained beneath; statement ORDER BY/LIMIT dedent back out so
        # they read as the tail of the whole statement
        from squeal.cli import Session
        out = Session().run(
            "explain select 1 as i union all select 2 as i "
            "order by i limit 2")
        self.assertEqual(out, """\
Compute  (no table)
-> Project  1
Union All
-> Compute  (no table)
  -> Project  2
Sort  keys: i
-> Limit limit 2""")


if __name__ == "__main__":
    unittest.main()
