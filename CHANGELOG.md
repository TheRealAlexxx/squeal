# Changelog

## Unreleased

- Fixed silent row loss in the CSV reader: a line of just commas, a
  quoted empty field, or a line of spaces now loads as a row of NULLs
  instead of being skipped. Only truly empty lines are skipped; short
  rows stay NULL-padded to the header width

## 0.3.0 - 2026-10-23

- sqllogictest-subset corpus in tests/sqllogictest: 80 records,
  feature files verified against the SQLite oracle at generation time,
  pinned.test holding hand-verified dialect differences; the README
  count is asserted by the test suite

- Correlated subqueries: the inner query may reference outer columns;
  uncorrelated results stay cached per statement, correlated ones run
  per outer row with the correlation verdict memoized
- Views: CREATE VIEW name AS SELECT and DROP VIEW [IF EXISTS];
  session-level named queries, validated at creation, materialized
  fresh on each use; views may reference other views, self-reference
  is an error
- Columnar engine (issue #4): an alternate evaluation path for simple
  single-table scans that compiles the WHERE clause into list
  comprehensions over whole columns; anything outside the subset
  bails to the row engine, and tests/test_columnar.py holds the two
  engines row for row equal

- Compound selects: UNION, UNION ALL, INTERSECT, EXCEPT, with SQLite's
  equal-precedence left-to-right rule
- WITH clauses: named statement-scoped subqueries, materialized on
  first use, shadowing tables and views
- WITH RECURSIVE: working-table iteration; UNION dedupe terminates
  cycles; 100,000-row guard; early termination under an unordered
  LIMIT on the CTE or a plain outer scan
- Aggregates, window functions, GROUP BY and HAVING are rejected in
  recursive terms, matching SQLite; DISTINCT stays allowed
- Fix: FROM-less queries can use subqueries that read real tables
  (scalar, EXISTS, NOT EXISTS); the CLI's single-row shim used to
  swallow every table lookup inside them
- EXPLAIN lists each WITH pipeline and marks self-referencing CTEs
  "Cte <name> (recursive)"

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
