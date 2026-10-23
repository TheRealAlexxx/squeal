"""Benchmarks: generate a synthetic dataset, time real queries, print a table.

    PYTHONPATH=. python3 benchmarks/run.py [--rows N] [--compare-sqlite]

Numbers land in docs/benchmarks.md. Everything is timed wall-clock with
perf_counter, median of three runs, on whatever machine runs it. No
claim here transfers to your machine; run it yourself.

--compare-sqlite runs the same queries over the same rows in SQLite
(Python's built-in sqlite3) and prints both timings side by side. SQLite
is the reference engine; the gap says honestly where squeal stands.
"""

import argparse
import csv
import os
import random
import sqlite3
import statistics
import sys
import tempfile
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from squeal.csvload import load
from squeal.executor import execute
from squeal.parser import parse

REGIONS = ["us-west", "us-east", "eu-north", "eu-south", "ap-south", "ap-east"]
PRODUCTS = ["laptop", "monitor", "keyboard", "mouse", "cable", "dock", "stand"]


def generate(rows, directory):
    rng = random.Random(42)
    orders = os.path.join(directory, "orders.csv")
    with open(orders, "w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["id", "customer_id", "region", "product", "quantity", "unit_price"])
        for i in range(1, rows + 1):
            writer.writerow([i, rng.randint(1, max(1, rows // 20)),
                             rng.choice(REGIONS), rng.choice(PRODUCTS),
                             rng.randint(1, 5), round(rng.uniform(5, 1500), 2)])
    customers = os.path.join(directory, "customers.csv")
    with open(customers, "w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["id", "name", "region"])
        for i in range(1, max(1, rows // 20) + 1):
            writer.writerow([i, "customer%d" % i, rng.choice(REGIONS)])
    return orders, customers


QUERIES = [
    ("filter + count", "select count(*) from {orders} where unit_price > 500"),
    ("group by + having", "select region, count(*), avg(unit_price) from {orders} "
                          "group by region having count(*) > 10"),
    ("hash join + group", "select c.region, sum(o.quantity) from {orders} o "
                          "join {customers} c on o.customer_id = c.id group by c.region"),
    ("window: row_number", "select id, row_number() over (partition by region "
                           "order by unit_price desc) from {orders}"),
    ("order by + limit", "select id from {orders} order by unit_price desc limit 20"),
    ("subquery in", "select count(*) from {orders} where customer_id in "
                    "(select id from {customers} where region = 'eu-north')"),
    ("recursive: 10k chain", "with recursive x(n) as (select 1 union all "
                             "select n + 1 from x where n < 10000) "
                             "select count(*) from x"),
]


def build_sqlite(orders, customers):
    """Load the generated CSVs into an in-memory SQLite with the same types."""
    con = sqlite3.connect(":memory:")
    kinds = {int: "INTEGER", float: "REAL", str: "TEXT"}
    for path in (orders, customers):
        table = load(path)
        name = os.path.basename(path)[:-4]
        cols = ", ".join("%s %s" % (c, kinds[t])
                         for c, t in zip(table.columns, table.types))
        con.execute("create table %s (%s)" % (name, cols))
        con.executemany("insert into %s values (%s)"
                        % (name, ",".join("?" * len(table.columns))), table.rows)
    return con


def bench_sqlite(sql, con, repeat=3):
    samples = []
    for _ in range(repeat):
        start = time.perf_counter()
        con.execute(sql).fetchall()
        samples.append((time.perf_counter() - start) * 1000)
    return statistics.median(samples)


def bench(sql, resolver, repeat=3):
    samples = []
    for _ in range(repeat):
        start = time.perf_counter()
        execute(parse(sql), resolver)
        samples.append((time.perf_counter() - start) * 1000)
    return statistics.median(samples)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--rows", type=int, default=100_000)
    parser.add_argument("--compare-sqlite", action="store_true",
                        help="time the same queries on SQLite next to squeal")
    args = parser.parse_args()

    directory = tempfile.mkdtemp(prefix="squeal-bench-")
    orders, customers = generate(args.rows, directory)
    print("dataset: %d orders, %d customers (in %s)"
          % (args.rows, max(1, args.rows // 20), directory))

    cache = {}
    def resolver(name):
        if name not in cache:
            cache[name] = load(name)
        return cache[name]

    names = {"orders": os.path.basename(orders)[:-4],
             "customers": os.path.basename(customers)[:-4]}
    con = build_sqlite(orders, customers) if args.compare_sqlite else None

    if con:
        print("| query | squeal | SQLite | slower by |")
        print("| --- | --- | --- | --- |")
    else:
        print("| query | median of 3 |")
        print("| --- | --- |")
    for label, sql in QUERIES:
        squeal_sql = sql.format(orders="'%s'" % orders,
                                customers="'%s'" % customers)
        ms = bench(squeal_sql, resolver)
        if con:
            sqlite_ms = bench_sqlite(sql.format(**names), con)
            print("| %s | %.0f ms | %.1f ms | %.0fx |"
                  % (label, ms, sqlite_ms, ms / max(sqlite_ms, 0.001)))
        else:
            print("| %s | %.0f ms |" % (label, ms))


if __name__ == "__main__":
    main()
