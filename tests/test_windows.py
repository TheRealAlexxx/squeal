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


class WindowTests(unittest.TestCase):
    def test_row_number_partitioned(self):
        r = run("select name, row_number() over "
                "(partition by city order by age desc) as rn "
                "from people order by city, rn")
        self.assertEqual(r.rows, [("Edsger", 1), ("Alan", 1), ("Ada", 2),
                                  ("Katherine", 3), ("Barbara", 1), ("Grace", 2)])

    def test_row_number_unpartitioned(self):
        r = run("select row_number() over (order by name) from people order by name")
        self.assertEqual([row[0] for row in r.rows], [1, 2, 3, 4, 5, 6])

    def test_rank_with_ties(self):
        r = run("select name, rank() over (order by city) as r from people order by r, name")
        self.assertEqual(r.rows, [("Edsger", 1), ("Ada", 2), ("Alan", 2),
                                  ("Katherine", 2), ("Barbara", 5), ("Grace", 5)])

    def test_dense_rank_with_ties(self):
        r = run("select name, dense_rank() over (order by city) as r "
                "from people order by r, name")
        self.assertEqual(r.rows, [("Edsger", 1), ("Ada", 2), ("Alan", 2),
                                  ("Katherine", 2), ("Barbara", 3), ("Grace", 3)])

    def test_sum_partition(self):
        r = run("select name, sum(score) over (partition by city) as total "
                "from people where city = 'London' order by name")
        self.assertEqual([row[1] for row in r.rows], [175.0, 175.0, 175.0])

    def test_running_avg(self):
        r = run("select name, round(avg(score) over (order by name), 2) as running "
                "from people order by name")
        self.assertEqual(r.rows[0], ("Ada", 91.5))
        self.assertEqual(r.rows[1], ("Alan", 91.5))  # NULL score skipped
        self.assertEqual(r.rows[2], ("Barbara", 93.25))

    def test_count_star_window(self):
        r = run("select name, count(*) over (partition by city) as n "
                "from people where city = 'Amsterdam'")
        self.assertEqual(r.rows, [("Edsger", 1)])

    def test_whole_table_partition(self):
        r = run("select distinct max(score) over () from people")
        self.assertEqual(r.rows, [(95.0,)])

    def test_windows_see_post_where_rows(self):
        # SQL semantics: windowing happens after WHERE, so the whole-table
        # partition here is just Ada's row
        r = run("select max(score) over () from people where name = 'Ada'")
        self.assertEqual(r.rows, [(91.5,)])

    def test_window_inside_expression(self):
        r = run("select name, round(avg(score) over (order by name), 1) as running "
                "from people order by name")
        self.assertEqual(r.rows[2], ("Barbara", 93.2))  # 93.25 rounds half-even

    def test_count_star_window_parse(self):
        r = run("select name, count(*) over (partition by city) as n "
                "from people where city = 'London' order by name")
        self.assertEqual([row[1] for row in r.rows], [3, 3, 3])

    def test_window_with_where(self):
        r = run("select name, row_number() over (order by age desc) as rn "
                "from people where age > 30 order by rn")
        self.assertEqual([row[0] for row in r.rows],
                         ["Barbara", "Alan", "Ada", "Edsger"])

    def test_window_mixed_with_group_by_fails(self):
        with self.assertRaises(SquealError):
            run("select city, row_number() over (order by age) from people group by city")

    def test_window_nested_in_expression(self):
        r = run("select name, row_number() over (order by age desc) + 100 as rn "
                "from people where age is not null order by rn limit 1")
        self.assertEqual(r.rows, [("Barbara", 101)])

    def test_ranking_takes_no_args(self):
        with self.assertRaises(SquealError):
            run("select row_number(age) over (order by age) from people")

    def test_plain_aggregate_still_works(self):
        # the pruning in has_aggregate must not hide a real GROUP BY query
        r = run("select city, count(*) from people group by city order by city")
        self.assertEqual(len(r.rows), 3)


if __name__ == "__main__":
    unittest.main()
