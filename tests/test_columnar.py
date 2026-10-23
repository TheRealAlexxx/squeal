"""The columnar experiment's bar: exact parity with the row engine.

Every PARITY query runs through both executors and must return the same
columns and the same rows in the same order. FALLBACK queries are shapes
the columnar subset deliberately refuses; they must come back None so
the row engine takes over.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from squeal import columnar
from squeal.csvload import load
from squeal.executor import execute
from squeal.parser import parse

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


def table(name):
    return load(os.path.join(FIXTURES, name + ".csv"))


PARITY = [
    "select name, age from people where age > 30",
    "select name from people where age > 30 and city = 'London'",
    "select name from people where age > 40 or score < 80",
    "select name from people where not (age > 30)",
    "select name from people where score is null",
    "select name from people where score is not null",
    "select city from people where age < 'zzz'",   # mixed types: constant-true
    "select city from people where age > 'zzz'",   # constant-false
    "select city from people where city = 5",      # mixed equality: false
    "select name, score from people where score >= 91.5 limit 2",
    "select name from people limit 3 offset 1",
    "select * from people where age <= 41",
    "select count(*) from people",
    "select count(*) from people where age > 100",
    "select count(age), count(*) from people",
    "select sum(amount), avg(amount), min(amount), max(amount) "
    "from purchases",
    "select avg(score) from people where city <> 'London'",
    "select sum(amount) from purchases where amount > 100",
    "select city, count(*) from people group by city",
    "select city, count(*), avg(age) from people group by city",
    "select sensor, min(temp), max(temp) from readings group by sensor",
    "select sensor, count(temp) from readings group by sensor",
    "select customer_id, sum(amount) as total from purchases "
    "where amount > 20 group by customer_id",
    "select count(*) from purchases where customer_id != 1 "
    "and amount >= 25 and amount < 1000",
    "select day, count(*) from readings group by day limit 2",
    "select name from people where age > 30 or score is null",
    "select name from people where age > 100 or score is null and age < 40",
    "select name from people where not (score is null)",
    "select name from people where 'x' is not null",
    "select name from people where 'x' is null",
    "select name from people where age > 1 and 'x' is null or score > 80",
]

FALLBACK = [
    "select name from people order by name",
    "select distinct city from people",
    "select upper(name) from people",
    "select name, row_number() over (order by age) from people",
    "select name || '!' from people",
    "select city, count(*) from people group by city having count(*) > 1",
    "select city, name from people group by city",  # bare non-key column
    "select a.name from people a join people b on a.name = b.name",
    "select name from people where name like 'A%'",
    "select name from people where age in (30, 40)",
    "select sum(age + 1) from people",
    "select count(distinct city) from people",
    "select 1 + 1",
]


def generated_table(path, n=1000):
    """A wider table with NULLs in every column, seeded for repeatability."""
    import random
    rng = random.Random(20261023)
    with open(path, "w") as fh:
        fh.write("id,kind,qty,price,label\n")
        for i in range(n):
            kind = rng.choice(["a", "b", "c", None])
            qty = rng.choice([rng.randint(0, 100), None])
            price = rng.choice([round(rng.uniform(0, 500), 2), None])
            label = rng.choice(["x", "yy", "zzz", None])
            fh.write("%d,%s,%s,%s,%s\n" % (
                i,
                "" if kind is None else kind,
                "" if qty is None else qty,
                "" if price is None else price,
                "" if label is None else label))
    return load(path)


GENERATED = [
    "select id from gen where qty > 50",
    "select id from gen where qty is null",
    "select id from gen where kind is null or qty is null",
    "select id, price from gen where qty >= 10 and price < 100.5",
    "select id from gen where not (qty is null)",
    "select id from gen where kind = 'a' and qty > 0 and price <= 499.99",
    "select id from gen where qty < 'zzz'",
    "select id from gen where qty = 42",
    "select id from gen where label != 'x'",
    "select count(*) from gen",
    "select count(qty), count(price), count(kind) from gen",
    "select sum(qty), avg(price), min(qty), max(price) from gen",
    "select sum(qty) from gen where kind = 'b'",
    "select kind, count(*) from gen group by kind",
    "select kind, count(*), avg(qty), sum(price) from gen "
    "where qty > 5 group by kind",
    "select kind, min(price) from gen group by kind limit 2 offset 1",
    "select id from gen where qty > 90 limit 5 offset 3",
    "select * from gen where id < 3",
]


class GeneratedParityTests(unittest.TestCase):
    def test_generated(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            t = generated_table(os.path.join(tmp, "gen.csv"))
            for sql in GENERATED:
                with self.subTest(sql=sql):
                    sql_gen = sql.replace("from gen", "from gen")
                    expected = execute(parse(sql_gen), t)
                    got = columnar.run(parse(sql_gen), t)
                    self.assertIsNotNone(got, "bail: " + sql)
                    self.assertEqual(expected.columns, got.columns, sql)
                    self.assertEqual(expected.rows, got.rows, sql)


class ParityTests(unittest.TestCase):
    def check(self, sql, fixture):
        t = table(fixture)
        expected = execute(parse(sql), t)
        got = columnar.run(parse(sql), t)
        self.assertIsNotNone(got, "columnar bailed on a parity query: " + sql)
        self.assertEqual(expected.columns, got.columns,
                         "column names differ: " + sql)
        self.assertEqual(expected.rows, got.rows, "rows differ: " + sql)

    def test_parity(self):
        for sql in PARITY:
            fixture = "purchases" if "purchases" in sql else \
                      "readings" if "readings" in sql else "people"
            with self.subTest(sql=sql):
                self.check(sql, fixture)

    def test_fallback(self):
        for sql in FALLBACK:
            with self.subTest(sql=sql):
                t = table("people")
                self.assertIsNone(columnar.run(parse(sql), t),
                                  "columnar should bail: " + sql)


class CliFlagTests(unittest.TestCase):
    """--engine columnar picks the columnar path and falls back honestly."""

    def setUp(self):
        from squeal.cli import Session
        self.cwd = os.getcwd()
        os.chdir(FIXTURES)
        self.row = Session()
        self.col = Session(engine="columnar")

    def tearDown(self):
        os.chdir(self.cwd)

    def test_same_results(self):
        for sql in PARITY:
            with self.subTest(sql=sql):
                expected = self.row.run(sql)
                got = self.col.run(sql)
                self.assertEqual(expected.columns, got.columns, sql)
                self.assertEqual(expected.rows, got.rows, sql)

    def run_noting(self, session, sql):
        import contextlib
        import io
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            result = session.run(sql)
        return result, err.getvalue()

    def test_fallback_announces_itself(self):
        result, note = self.run_noting(
            self.col, "select name from people order by name")
        self.assertIn("row engine", note)
        expected = self.row.run("select name from people order by name")
        self.assertEqual(expected.rows, result.rows)

    def test_every_fallback_announces_itself(self):
        # QA review: no query may silently run row under --engine columnar
        cases = [
            "select 1 + 1",
            "select name from people order by name",
            "select distinct city from people",
            "select a.name from people a join people b on a.name = b.name",
            "select city, count(*) from people group by city "
            "having count(*) > 1",
        ]
        self.col.run("create view londoners as "
                     "select name from people where city = 'London'")
        cases.append("select name from londoners")
        for sql in cases:
            with self.subTest(sql=sql):
                _, note = self.run_noting(self.col, sql)
                self.assertIn("row engine", note, "silent fallback: " + sql)

    def test_subset_queries_stay_quiet(self):
        _, note = self.run_noting(
            self.col, "select city, count(*) from people group by city")
        self.assertEqual("", note)

    def test_explain_names_the_engine(self):
        plan = self.col.run("explain select name from people where age > 30")
        self.assertTrue(plan.startswith("engine: columnar\n"), plan)
        plan = self.col.run("explain select name from people order by name")
        self.assertTrue(plan.startswith("engine: row"), plan)
        plan = self.col.run("explain select 1 + 1")
        self.assertTrue(plan.startswith("engine: row"), plan)
        analyzed = self.col.run(
            "explain analyze select name from people where age > 30")
        self.assertIn("EXPLAIN ANALYZE always measures the row pipeline",
                      analyzed)


if __name__ == "__main__":
    unittest.main()
