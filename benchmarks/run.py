"""Benchmarks: generate a synthetic dataset, time real queries, print a table.

    PYTHONPATH=. python3 benchmarks/run.py [--rows N]

Numbers land in docs/benchmarks.md. Everything is timed wall-clock with
perf_counter, median of three runs, on whatever machine runs it. No
claim here transfers to your machine; run it yourself.
"""

import argparse
import csv
import os
import random
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
    ("filter + count", "select count(*) from '{orders}' where unit_price > 500"),
    ("group by + having", "select region, count(*), avg(unit_price) from '{orders}' "
                          "group by region having count(*) > 10"),
    ("hash join + group", "select c.region, sum(o.quantity) from '{orders}' o "
                          "join '{customers}' c on o.customer_id = c.id group by c.region"),
    ("window: row_number", "select id, row_number() over (partition by region "
                           "order by unit_price desc) from '{orders}'"),
    ("order by + limit", "select id from '{orders}' order by unit_price desc limit 20"),
    ("subquery in", "select count(*) from '{orders}' where customer_id in "
                    "(select id from '{customers}' where region = 'eu-north')"),
]


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

    print("| query | median of 3 |")
    print("| --- | --- |")
    for label, sql in QUERIES:
        sql = sql.format(orders=orders, customers=customers)
        ms = bench(sql, resolver)
        print("| %s | %.0f ms |" % (label, ms))


if __name__ == "__main__":
    main()
