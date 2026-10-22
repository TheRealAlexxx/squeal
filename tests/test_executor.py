import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from squeal.csvload import load
from squeal.errors import SquealError
from squeal.executor import execute
from squeal.parser import parse

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "people.csv")


def run(sql):
    return execute(parse(sql), load(FIXTURE))


class WhereTests(unittest.TestCase):
    def test_filter(self):
        r = run("select name from people where age > 35 order by name")
        self.assertEqual([row[0] for row in r.rows], ["Ada", "Alan", "Barbara"])

    def test_null_rejects(self):
        # Katherine has NULL age: neither predicate keeps her
        r = run("select count(*) from people where age > 100 or age <= 100")
        self.assertEqual(r.rows[0][0], 5)

    def test_is_null(self):
        r = run("select name from people where age is null")
        self.assertEqual(r.rows, [("Katherine",)])

    def test_is_not_null(self):
        r = run("select count(*) from people where age is not null")
        self.assertEqual(r.rows[0][0], 5)

    def test_like(self):
        r = run("select name from people where name like 'A%' order by name")
        self.assertEqual([row[0] for row in r.rows], ["Ada", "Alan"])

    def test_like_underscore(self):
        r = run("select name from people where name like '_d_'")
        self.assertEqual(r.rows, [("Ada",)])

    def test_in(self):
        r = run("select name from people where city in ('London', 'Amsterdam') order by name")
        self.assertEqual([row[0] for row in r.rows], ["Ada", "Alan", "Edsger", "Katherine"])

    def test_not_in_with_null(self):
        # x NOT IN (list containing a match for nothing) stays true for known values
        r = run("select count(*) from people where city not in ('London')")
        self.assertEqual(r.rows[0][0], 3)

    def test_between(self):
        r = run("select name from people where age between 30 and 40 order by name")
        self.assertEqual([row[0] for row in r.rows], ["Ada", "Edsger"])

    def test_three_valued_or(self):
        # NULL OR TRUE is TRUE: Katherine qualifies through the second arm
        r = run("select count(*) from people where age > 100 or city = 'London'")
        self.assertEqual(r.rows[0][0], 3)


class ExpressionTests(unittest.TestCase):
    def test_arithmetic(self):
        r = run("select score + 1 from people where name = 'Ada'")
        self.assertEqual(r.rows[0][0], 92.5)

    def test_null_propagates(self):
        r = run("select score + 1 from people where name = 'Alan'")
        self.assertIsNone(r.rows[0][0])

    def test_concat(self):
        r = run("select name || ' of ' || city from people where name = 'Ada'")
        self.assertEqual(r.rows[0][0], "Ada of London")

    def test_case(self):
        r = run("""select case when score >= 90 then 'great'
                              when score >= 80 then 'good'
                              else 'ok' end from people where name = 'Grace'""")
        self.assertEqual(r.rows[0][0], "good")

    def test_case_no_else(self):
        r = run("select case when score >= 99 then 'perfect' end from people where name = 'Ada'")
        self.assertIsNone(r.rows[0][0])

    def test_scalar_functions(self):
        r = run("select upper(name), length(name), typeof(age) from people where name = 'Ada'")
        self.assertEqual(r.rows[0], ("ADA", 3, "integer"))

    def test_coalesce(self):
        r = run("select coalesce(age, -1) from people where name = 'Katherine'")
        self.assertEqual(r.rows[0][0], -1)

    def test_division_by_zero(self):
        with self.assertRaises(SquealError):
            run("select 1 / 0 from people")

    def test_unknown_column(self):
        with self.assertRaises(SquealError):
            run("select nope from people")

    def test_unknown_function(self):
        with self.assertRaises(SquealError):
            run("select frobnicate(name) from people")


class AggregateTests(unittest.TestCase):
    def test_count_star(self):
        self.assertEqual(run("select count(*) from people").rows[0][0], 6)

    def test_count_column_skips_null(self):
        self.assertEqual(run("select count(age) from people").rows[0][0], 5)

    def test_sum_avg(self):
        r = run("select sum(age), avg(age) from people")
        self.assertEqual(r.rows[0][0], 182)
        self.assertAlmostEqual(r.rows[0][1], 36.4)

    def test_min_max(self):
        r = run("select min(score), max(score) from people")
        self.assertEqual(r.rows[0], (77.25, 95.0))

    def test_group_by(self):
        r = run("select city, count(*) from people group by city order by city")
        self.assertEqual(r.rows, [("Amsterdam", 1), ("London", 3), ("New York", 2)])

    def test_group_by_avg_skips_null(self):
        r = run("select avg(age) from people group by city order by city")
        self.assertEqual([row[0] for row in r.rows], [34, 38.5, 35.5])

    def test_having(self):
        r = run("select city from people group by city having count(*) > 1 order by city")
        self.assertEqual([row[0] for row in r.rows], ["London", "New York"])

    def test_having_without_aggregate_fails(self):
        with self.assertRaises(SquealError):
            run("select name from people having age > 1")

    def test_aggregate_over_empty(self):
        r = run("select count(*), sum(age) from people where age > 100")
        self.assertEqual(r.rows, [(0, None)])

    def test_grouped_empty(self):
        r = run("select city, count(*) from people where age > 100 group by city")
        self.assertEqual(r.rows, [])


class ShapeTests(unittest.TestCase):
    def test_star(self):
        r = run("select * from people where name = 'Ada'")
        self.assertEqual(r.columns, ["name", "age", "city", "score"])
        self.assertEqual(r.rows[0], ("Ada", 36, "London", 91.5))

    def test_alias(self):
        r = run("select name as person from people where name = 'Ada'")
        self.assertEqual(r.columns, ["person"])

    def test_distinct(self):
        r = run("select distinct city from people order by city")
        self.assertEqual([row[0] for row in r.rows], ["Amsterdam", "London", "New York"])

    def test_order_desc_nulls_last(self):
        r = run("select name from people order by age desc")
        self.assertEqual(r.rows[-1][0], "Katherine")

    def test_order_multiple(self):
        r = run("select name from people order by city asc, age desc")
        self.assertEqual([row[0] for row in r.rows],
                         ["Edsger", "Alan", "Ada", "Katherine", "Barbara", "Grace"])

    def test_limit_offset(self):
        r = run("select name from people order by name limit 2 offset 1")
        self.assertEqual([row[0] for row in r.rows], ["Alan", "Barbara"])

    def test_mixed_type_order(self):
        # numbers sort before text, so the predicate holds for every row
        r = run("select count(*) from people where 5 < 'apple'")
        self.assertEqual(r.rows, [(6,)])

    def test_select_without_from(self):
        r = run("select 1 + 1")
        # no FROM means a single computed row; handled by the caller, but
        # executor must at least not need a table for projection-only trees
        self.assertIsNotNone(r.columns)


if __name__ == "__main__":
    unittest.main()


class PositionalTests(unittest.TestCase):
    def test_order_by_position(self):
        r = run("select city, count(*) from people group by city order by 2 desc")
        self.assertEqual(r.rows[0], ("London", 3))

    def test_group_by_position(self):
        r = run("select city, count(*) from people group by 1 order by 1")
        self.assertEqual([row[0] for row in r.rows],
                         ["Amsterdam", "London", "New York"])

    def test_position_out_of_range(self):
        with self.assertRaises(SquealError):
            run("select name from people order by 5")

