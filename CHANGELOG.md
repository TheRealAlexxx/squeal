# Changelog

## 0.2.0 - 2026-10-22

- INNER, LEFT [OUTER] and CROSS JOIN with ON conditions, table aliases
  and qualified column names; equijoins run as hash joins
- Subqueries: IN (SELECT ...), scalar subqueries, EXISTS; uncorrelated
  only, results cached per statement (13 s to 0.35 s on the benchmark)
- Window functions: row_number, rank, dense_rank, and windowed
  aggregates over (PARTITION BY ... ORDER BY ...)
- Aggregates accept DISTINCT; new aggregate group_concat
- Ten new scalar functions: ifnull, iif, instr, ltrim, rtrim, sign,
  floor, ceil/ceiling, power/pow, sqrt
- Positional ORDER BY and GROUP BY references
- EXPLAIN ANALYZE with measured per-stage row counts and timing
- Unknown-column errors suggest the closest matching name
- Ambiguous bare columns after a join are an error, not a coin flip
- Benchmark harness in benchmarks/ with recorded numbers in
  docs/benchmarks.md; full dialect documented in docs/sql-reference.md

## 0.1.0 - 2026-10-22

First release: lexer, parser, row-based executor over a single CSV,
EXPLAIN, CLI/REPL with table/csv/json output.
