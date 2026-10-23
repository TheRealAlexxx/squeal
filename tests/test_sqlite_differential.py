"""Differential tests: the same queries over the same data, squeal vs SQLite.

Python's built-in sqlite3 module is the reference engine. Every query in
STRICT_QUERIES must return the same rows on both engines, with floats
compared to 9 decimal places. A query that legitimately differs belongs
in KNOWN_DIVERGENCES with the dialect reason; a listed divergence that
starts matching is also a failure, so the list stays honest.
"""

import os
import sqlite3
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from squeal.csvload import load
from squeal.executor import execute
from squeal.parser import parse

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")
TABLES = ["people", "customers", "purchases", "readings", "edges"]

# (sql, ordered): ordered=True compares the row sequence exactly,
# ordered=False compares rows as a multiset.
STRICT_QUERIES = [
    ("select name, age from people where age is not null order by age", True),
    ("select upper(name), length(name) from people order by name", True),
    ("select name, age + 10 from people where age is not null order by name", True),
    ("select distinct city from people", False),
    ("select city, count(*) from people group by city order by city", True),
    ("select city, count(*), avg(age) from people group by city order by city", True),
    ("select count(score), count(*), min(score), max(score), sum(score) from people", False),
    ("select avg(age), sum(age) from people", False),
    ("select city from people group by city having count(*) > 1", False),
    ("select city, count(*) from people group by 1 order by 2 desc, 1", True),
    ("select name from people where age between 30 and 40 order by name", True),
    ("select name from people where city in ('London','Amsterdam') order by name", True),
    ("select name from people where city not in ('London') order by name", True),
    ("select name from people where name like 'A%' order by name", True),
    ("select name from people where name like '_d%' order by name", True),
    ("select name from people where score is null", False),
    ("select name from people where not (age > 30) and age is not null order by name", True),
    ("select name, case when age >= 40 then 'senior' when age >= 30 then 'mid' "
     "else 'junior' end from people order by name", True),
    ("select name, case when age > 100 then 'x' end from people order by name", True),
    ("select count(*) from people where age > 35 or age <= 35", False),
    ("select name || ' <' || city || '>' as label from people order by name", True),
    ("select trim('  hi  '), ltrim('  hi'), rtrim('hi  ')", False),
    ("select abs(-4), abs(4.5), sign(-2), sign(0), sign(7)", False),
    ("select coalesce(null, null, 'x'), nullif(1,1), nullif(1,2), "
     "ifnull(null,'d'), iif(1>0,'y','n')", False),
    ("select instr('hello','ll'), substr('hello',2,3), replace('aaa','a','b')", False),
    ("select round(91.54, 1), round(91.54), round(2.4), round(2.6)", False),
    ("select typeof(1), typeof('a'), typeof(null)", False),
    ("select 7.0 / 2, 9.0 / 4", False),
    ("select city, group_concat(name) from people group by city order by city", True),
    ("select sum(distinct amount), count(distinct customer_id) from purchases", False),
    ("select avg(distinct temp) from readings", False),
    ("select customers.name, purchases.item from customers "
     "join purchases on purchases.customer_id = customers.id "
     "order by customers.name, purchases.item", True),
    ("select customers.name, purchases.item from customers "
     "left join purchases on purchases.customer_id = customers.id "
     "order by customers.name, purchases.item", True),
    ("select a.name, b.name from customers a cross join customers b "
     "where a.id < b.id order by a.name, b.name", True),
    ("select a.id, b.id from readings a join readings b "
     "on b.sensor = a.sensor and b.day = a.day and b.temp = a.temp "
     "where a.id <> b.id", False),
    ("select a.id, b.id from purchases a join purchases b "
     "on a.customer_id = b.customer_id and a.amount > b.amount", False),
    ("select name from customers "
     "where id in (select customer_id from purchases) order by name", True),
    ("select name from customers "
     "where id not in (select customer_id from purchases)", False),
    ("select name from people "
     "where age > (select avg(age) from people) order by name", True),
    ("select name from customers where exists "
     "(select 1 from purchases where purchases.customer_id = customers.id) "
     "order by name", True),
    ("select name, (select sum(amount) from purchases "
     "where purchases.customer_id = customers.id) from customers order by name", True),
    ("select name from people where age in (select temp from readings)", False),
    ("select name, row_number() over (order by age) "
     "from people where age is not null", False),
    ("select sensor, temp, rank() over (partition by sensor order by temp) "
     "from readings where temp is not null", False),
    ("select sensor, temp, dense_rank() over (order by temp desc) "
     "from readings where temp is not null", False),
    ("select id, sum(amount) over (order by id) from purchases", False),
    ("select sensor, temp, avg(temp) over (partition by sensor) from readings", False),
    ("select name, count(*) over () from people", False),
    ("select name from people order by name limit 3", True),
    ("select name from people order by name limit 2 offset 3", True),
    ("select name from people order by name limit 10 offset 10", True),
    ("select 1 + 1, 'a' || 'b', 10 % 3", False),
    ("select coalesce(null, 7)", False),
    ("select customers.city, sum(purchases.amount) from customers "
     "join purchases on purchases.customer_id = customers.id "
     "group by customers.city order by customers.city", True),
    ("select customers.name, sum(purchases.amount) from customers "
     "join purchases on purchases.customer_id = customers.id "
     "group by customers.name having sum(purchases.amount) > 100 "
     "order by customers.name", True),
    ("select a.name, b.name from people a join people b "
     "on a.city = b.city and a.age < b.age", False),
    ("select * from people order by name", True),
    ("select count(*) from people where age < 'zzz'", False),
    ("select sensor, min(temp), max(temp) from readings "
     "group by sensor order by sensor", True),
    ("select day, count(*), avg(temp) from readings "
     "group by day order by day", True),
    # compound selects: UNION / UNION ALL / INTERSECT / EXCEPT
    ("select city from people union select 'Lisbon' order by 1", True),
    ("select city from people union all select 'Lisbon' order by 1", True),
    ("select city from people intersect "
     "select city from people where city = 'London'", False),
    ("select city from people except "
     "select city from people where city in ('London','Amsterdam')", False),
    ("select name from people where age > 30 union "
     "select name from people where score is not null order by name", True),
    ("select city, count(*) as n from people group by city "
     "union select 'everywhere', count(*) from people order by 1", True),
    ("select city from people union select 'Lisbon' "
     "order by 1 limit 3 offset 1", True),
    ("select name from customers where id in "
     "(select customer_id from purchases union select 999) order by name", True),
    ("select 1 as x union select 2 intersect select 2", False),
    ("select 1.0 as x union select 1", False),
    ("select age from people except select 36", False),
    # NULL ages dedupe to one; DESC puts NULLs last on both engines
    ("select age from people union select 99 order by 1 desc", True),
    # WITH clauses
    ("with high as (select name from people where age > 35) "
     "select * from high order by name", True),
    ("with a as (select city from people), "
     "b as (select city from a where city <> 'London') "
     "select distinct city from b order by 1", True),
    ("with x(town) as (select city from people) "
     "select town from x order by 1", True),
    ("with x as (select city from people union select 'Lisbon') "
     "select * from x order by city", True),
    ("with c as (select city, count(*) as n from people group by city) "
     "select * from c where n > 1 order by city", True),
    ("with ages as (select age from people where age is not null) "
     "select min(age), max(age), avg(age) from ages", False),
    ("with x as (select city from people) "
     "select a.city, b.city from x a join x b on a.city = b.city "
     "order by 1 limit 5", True),
]

# Queries needing a SQLite built with math functions (3.35+ or a distro
# that enabled them). Skipped when the local SQLite lacks them.
RECURSIVE_QUERIES = [
    ("with recursive x(n) as (select 1 union all "
     "select n + 1 from x where n < 5) select * from x order by n", True),
    ("with recursive fib(a, b) as (select 0, 1 union all "
     "select b, a + b from fib where a < 100) "
     "select a from fib order by a", True),
    # transitive closure over edges.csv: UNION dedupe stops the a->b->c->a
    # cycle, and a reaches itself through it
    ("with recursive reach(dst) as ("
     "select dst from edges where src = 'a' union "
     "select e.dst from edges e join reach r on e.src = r.dst) "
     "select dst from reach order by dst", True),
    # UNION ALL with an explicit depth bound
    ("with recursive reach(dst, steps) as ("
     "select dst, 1 from edges where src = 'a' union all "
     "select e.dst, r.steps + 1 from edges e "
     "join reach r on e.src = r.dst where r.steps < 4) "
     "select steps, count(*) from reach group by steps order by steps", True),
    # a plain outer LIMIT pushes down; the prefix matches SQLite's queue
    ("with recursive fib(a, b) as (select 0, 1 union all "
     "select b, a + b from fib) select a from fib limit 6", True),
    ("with recursive x(s) as (select 'a' union all "
     "select s || 'a' from x where length(s) < 4) "
     "select * from x order by s", True),
]

MATH_QUERIES = [
    ("select floor(1.7), ceil(1.2), ceiling(1.2), sqrt(16.0), power(2,10), pow(3,2)", False),
    ("select floor(temp), ceil(temp) from readings where temp is not null "
     "order by id", True),
]

# Queries needing SQLite 3.39+ for RIGHT/FULL OUTER JOIN. Skipped on older.
OUTER_QUERIES = [
    ("select customers.name, purchases.item from purchases "
     "right join customers on purchases.customer_id = customers.id "
     "order by customers.name", True),
    ("select customers.name, purchases.item from customers "
     "full join purchases on purchases.customer_id = customers.id", False),
]

# Documented dialect differences: sql -> why it differs. Each must keep
# diverging; if one starts matching, squeal grew SQLite-compatible there
# and the list must be updated.
KNOWN_DIVERGENCES = {
    "with recursive x(n) as (select 1 union all "
    "select n + 1 from x where n < 9 order by n desc limit 3) "
    "select * from x":
        "ORDER BY in SQLite's recursive-select is a queue-extraction "
        "priority, not a result sort (positional terms only; a named "
        "term errors on SQLite 3.37.2), so it changes traversal order "
        "but never sorts the output; squeal instead applies ORDER BY "
        "and LIMIT on the CTE to the accumulated rows after iteration",
    "select 7 / 2, 9 / 4":
        "SQLite truncates integer division; squeal always does true division",
    "select 1 / 0":
        "SQLite returns NULL for division by zero; squeal raises an error",
    "select 10 % -3":
        "SQLite's % keeps the sign of the dividend; Python's keeps the divisor's",
    "select round(2.5), round(-2.5), round(3.5)":
        "SQLite rounds halves away from zero; squeal uses Python banker's rounding",
    "select typeof(1.5)":
        "SQLite calls floats 'real'; squeal calls them 'float'",
    "select name, age from people order by age":
        "SQLite sorts NULLs first ascending; squeal always puts NULLs last",
    "select name, max(age) from people":
        "SQLite reads a bare column from the row that won max(); "
        "squeal reads the group's first row",
}


def load_tables():
    return {name: load(os.path.join(FIXTURES, name + ".csv")) for name in TABLES}


def build_sqlite(tables):
    kinds = {int: "INTEGER", float: "REAL", str: "TEXT"}
    con = sqlite3.connect(":memory:")
    con.execute("pragma case_sensitive_like = on")  # squeal's LIKE is case sensitive
    for name, table in tables.items():
        cols = ", ".join('"%s" %s' % (c, kinds[t])
                         for c, t in zip(table.columns, table.types))
        con.execute('create table "%s" (%s)' % (name, cols))
        con.executemany('insert into "%s" values (%s)'
                        % (name, ",".join("?" * len(table.columns))), table.rows)
    return con


def run_squeal(sql, tables):
    try:
        return execute(parse(sql), lambda name: tables[name]).rows
    except Exception as exc:  # an engine error is a result too
        return [("__error__", exc.__class__.__name__)]


def run_sqlite(sql, con):
    try:
        return con.execute(sql).fetchall()
    except Exception as exc:
        return [("__error__", exc.__class__.__name__)]


def close(a, b):
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return abs(a - b) <= 1e-9
    return a == b


def rows_match(expected, got, ordered):
    if len(expected) != len(got):
        return False
    pairs = zip(expected, got) if ordered else _multiset_pairs(expected, got)
    if pairs is None:
        return False
    return all(len(x) == len(y) and all(close(u, v) for u, v in zip(x, y))
               for x, y in pairs)


def _multiset_pairs(expected, got):
    pool = [tuple(row) for row in got]
    pairs = []
    for row in expected:
        match = None
        for i, candidate in enumerate(pool):
            if len(candidate) == len(row) and \
                    all(close(u, v) for u, v in zip(row, candidate)):
                match = i
                break
        if match is None:
            return None
        pairs.append((row, pool.pop(match)))
    return pairs


class DifferentialTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tables = load_tables()
        cls.con = build_sqlite(cls.tables)
        try:
            cls.con.execute("select sqrt(4.0)").fetchone()
            cls.has_math = True
        except sqlite3.OperationalError:
            cls.has_math = False
        try:
            cls.con.execute("select 1 from people a right join people b on 1=0").fetchall()
            cls.has_right_full = True
        except sqlite3.OperationalError:
            cls.has_right_full = False

    def check(self, sql, ordered):
        expected = run_sqlite(sql, self.con)
        got = run_squeal(sql, self.tables)
        return expected, got, rows_match(expected, got, ordered)

    def test_strict_queries_match_sqlite(self):
        queries = list(STRICT_QUERIES) + RECURSIVE_QUERIES
        if self.has_math:
            queries += MATH_QUERIES
        if self.has_right_full:
            queries += OUTER_QUERIES
        failures = []
        for sql, ordered in queries:
            with self.subTest(sql=sql):
                expected, got, ok = self.check(sql, ordered)
                if not ok:
                    failures.append(
                        "\n  %s\n    sqlite: %r\n    squeal: %r" % (sql, expected, got))
        self.assertEqual(failures, [],
                         "queries diverging from SQLite:" + "".join(failures))

    def test_known_divergences_still_diverge(self):
        for sql, reason in KNOWN_DIVERGENCES.items():
            with self.subTest(sql=sql):
                expected = run_sqlite(sql, self.con)
                got = run_squeal(sql, self.tables)
                self.assertFalse(
                    rows_match(expected, got, True),
                    "listed divergence now matches SQLite; update the list. "
                    "reason was: %s\n  sqlite: %r\n  squeal: %r" % (reason, expected, got))


if __name__ == "__main__":
    unittest.main()
