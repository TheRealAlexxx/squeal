"""WITH clauses: named, statement-scoped subqueries.

Semantics pinned here: a CTE is materialized on first use and shared
for the rest of the statement; later CTEs may reference earlier ones;
a CTE name shadows a table or view of the same name; a self-reference
is an error under plain WITH (WITH RECURSIVE lives in its own tests);
an unused CTE is never evaluated.
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


class WithTests(unittest.TestCase):
    def test_basic(self):
        r = run("with high as (select name from people where age > 35) "
                "select * from high order by name")
        self.assertEqual([r[0] for r in r.rows], ["Ada", "Alan", "Barbara"])

    def test_chained_ctes(self):
        r = run("with a as (select city from people), "
                "b as (select city from a where city <> 'London') "
                "select distinct city from b order by 1")
        self.assertEqual(r.rows, [("Amsterdam",), ("New York",)])

    def test_column_aliases(self):
        r = run("with x(town) as (select city from people) "
                "select town from x where town = 'London'")
        self.assertEqual(r.columns, ["town"])
        self.assertEqual(len(r.rows), 3)

    def test_alias_arity_mismatch(self):
        with self.assertRaises(SquealError) as cm:
            run("with x(a, b) as (select city from people) select * from x")
        self.assertIn("names 2 column(s) but its query returns 1",
                      str(cm.exception))

    def test_duplicate_name(self):
        with self.assertRaises(SquealError) as cm:
            run("with x as (select 1), x as (select 2) select * from x")
        self.assertIn("duplicate WITH name: x", str(cm.exception))

    def test_self_reference_is_an_error(self):
        with self.assertRaises(SquealError) as cm:
            run("with loop as (select * from loop) select * from loop")
        self.assertIn("references itself", str(cm.exception))

    def test_cte_shadows_table(self):
        r = run("with people as (select 'impostor' as name) "
                "select * from people")
        self.assertEqual(r.rows, [("impostor",)])

    def test_unused_cte_is_never_evaluated(self):
        # lazy materialization: a broken but unused CTE does not fail
        r = run("with broken as (select * from nosuchtable) select 1 as ok")
        self.assertEqual(r.rows, [(1,)])

    def test_cte_in_subquery(self):
        r = run("select name from customers where id in "
                "(with buyers as (select customer_id from purchases) "
                "select * from buyers) order by name")
        self.assertTrue(len(r.rows) >= 1)

    def test_with_over_compound(self):
        r = run("with x as (select city from people union select 'Lisbon') "
                "select * from x order by city")
        self.assertEqual(r.rows[0], ("Amsterdam",))
        self.assertEqual(r.rows[-1], ("New York",))
        self.assertIn(("Lisbon",), r.rows)

    def test_recursive_keyword_parses(self):
        # WITH RECURSIVE exists now; its semantics live in
        # tests/test_recursive.py. A non-recursive CTE under the
        # keyword just works.
        r = run("with recursive r as (select 1) select * from r")
        self.assertEqual(r.rows, [(1,)])

    def test_explain_lists_cte_pipeline(self):
        from squeal.cli import Session
        out = Session().run(
            "explain with high as (select city from '%s' group by city) "
            "select * from high" % os.path.join(FIXTURES, "people.csv"))
        self.assertIn("Cte  high", out)
        self.assertLess(out.index("Cte  high"),
                        out.index("Aggregate", out.index("Cte  high")))

    def test_explain_analyze_counts_cte_pipeline_separately(self):
        from squeal.cli import Session
        out = Session().run(
            "explain analyze with c as (select city, count(*) as n "
            "from '%s' group by city) select * from c where n > 1"
            % os.path.join(FIXTURES, "people.csv"))
        self.assertIn("Cte  c   -- 3 rows", out)
        self.assertIn("-- 3 groups", out)   # three cities
        self.assertIn("-- 3 in, 2 out", out)  # the outer n > 1 filter


if __name__ == "__main__":
    unittest.main()
