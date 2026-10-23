"""Columnar experiment benchmark (issue #4): row engine vs --engine columnar.

    PYTHONPATH=. python3 benchmarks/columnar.py [--rows N]

Times the simple-scan subset both engines can run, on the same generated
orders table benchmarks/run.py builds. The queries deliberately stay
inside the columnar subset; everything else falls back to the row
engine, so these are the shapes the experiment can win on.

Issue #4 asks for a 1M-row CSV. The development sandbox (1 GB RAM)
crawls at that size, so the default is 100k rows; scale up on a real
machine with --rows 1000000.
"""

import argparse
import os
import statistics
import sys
import tempfile
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from benchmarks.run import generate
from squeal import columnar
from squeal.csvload import load
from squeal.executor import execute
from squeal.parser import parse

QUERIES = [
    ("filter + count", "select count(*) from orders where unit_price > 500"),
    ("filter + project", "select id, quantity from orders "
                         "where unit_price > 500 and quantity >= 3"),
    ("aggregate", "select sum(quantity), avg(unit_price) from orders "
                  "where region != 'us-west'"),
    ("group by", "select region, count(*), avg(unit_price) from orders "
                 "group by region"),
    ("group by + filter", "select product, sum(quantity) from orders "
                          "where unit_price < 100 group by product"),
]


def bench(fn, repeat=3):
    samples = []
    for _ in range(repeat):
        start = time.perf_counter()
        fn()
        samples.append((time.perf_counter() - start) * 1000)
    return statistics.median(samples)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--rows", type=int, default=100_000)
    args = parser.parse_args()

    directory = tempfile.mkdtemp(prefix="squeal-columnar-bench-")
    orders, _ = generate(args.rows, directory)
    table = load(orders)
    print("dataset: %d orders (in %s)" % (args.rows, directory))

    print("| query | row engine | columnar | speedup |")
    print("| --- | --- | --- | --- |")
    for label, sql in QUERIES:
        select = parse(sql)
        # the bar: identical results before any timing matters
        expected = execute(select, table)
        got = columnar.run(select, table)
        assert got is not None, "benchmark query bailed: " + sql
        assert expected.rows == got.rows, "results differ: " + sql
        row_ms = bench(lambda: execute(parse(sql), table))
        col_ms = bench(lambda: columnar.run(parse(sql), table))
        print("| %s | %.0f ms | %.0f ms | %.1fx |"
              % (label, row_ms, col_ms, row_ms / max(col_ms, 0.001)))


if __name__ == "__main__":
    main()
