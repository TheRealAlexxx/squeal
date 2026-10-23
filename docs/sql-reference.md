# squeal SQL reference

One page, everything squeal speaks. Where squeal differs from the SQL
standard, the difference is called out.

## Statements

```
SELECT [DISTINCT] select_item [, ...]
  [FROM source]
  [WHERE expr]
  [GROUP BY expr [, ...]]
  [HAVING expr]
  [ORDER BY expr [ASC|DESC] [, ...]]
  [LIMIT n [OFFSET n]]

EXPLAIN [ANALYZE] SELECT ...

CREATE VIEW name AS SELECT ...
DROP VIEW [IF EXISTS] name
```

Views are session-level named queries. They validate when created,
materialize fresh on every use, and may reference other views; a view
that references itself is an error.

A select item is `expr [AS alias]`, `*`, or a window call.

## Sources

- `'path/to/file.csv'` - a CSV file, optionally with an alias
- `name` - resolved to `name.csv` in the working directory
- `source [INNER] JOIN source ON expr`
- `source LEFT|RIGHT|FULL [OUTER] JOIN source ON expr`
- `source CROSS JOIN source`
- Every source may carry an alias: `FROM orders o` or `FROM orders AS o`

Equijoins (`ON a.x = b.y`) run as hash joins, including multi-key
equijoins (`ON a.x = b.x AND a.y = b.y`), which hash on the tuple;
leftover non-equality conjuncts become residual filters. Anything
else falls back to a nested loop. EXPLAIN names the strategy.

## Expressions

- Literals: numbers, 'strings' (quote with ''), TRUE, FALSE, NULL
- Columns: `name` or `qualifier.name`
- Arithmetic: `+ - * / %`, string concat `||`, unary `-`
- Comparisons: `= != <> < <= > >=`
- `expr IS [NOT] NULL`
- `expr [NOT] LIKE 'pattern'` - % any run, _ one char, case sensitive
- `expr [NOT] IN (value, ...)` or `expr [NOT] IN (SELECT one_column ...)`
- `expr [NOT] BETWEEN low AND high`
- `CASE WHEN cond THEN val ... [ELSE val] END`
- `EXISTS (SELECT ...)`
- Scalar subquery: `(SELECT one_column ...)` returning at most one row

Precedence, weakest first: OR, AND, NOT, comparisons, `+ - ||`, `* / %`,
unary, primary.

Positional references: `ORDER BY 2` and `GROUP BY 1` point at select
items; out of range is an error.

## Functions

Aggregates: `count(*)`, `count(x)`, `sum`, `avg`, `min`, `max`,
`group_concat(x [, sep])`. All accept `DISTINCT` except `count(*)`.

Scalar: `upper`, `lower`, `length`, `trim`, `ltrim`, `rtrim`, `abs`,
`round(x [, digits])`, `coalesce`, `nullif`, `ifnull`, `iif`, `instr`,
`substr` (1-based), `replace`, `sign`, `floor`, `ceil`/`ceiling`,
`power`/`pow`, `sqrt`, `typeof`.

Window: `row_number()`, `rank()`, `dense_rank()`, and any aggregate,
all with `OVER ([PARTITION BY exprs] [ORDER BY items])`. With an ORDER
BY the frame is partition start through current row (running totals);
without one it is the whole partition. Windows run over the post-WHERE
row set and cannot mix with GROUP BY.

## The dialect, honestly

- NULLs follow the SQL three-valued logic everywhere: comparisons and
  arithmetic with NULL yield NULL; WHERE keeps only TRUE.
- Column types are inferred per CSV column from the whole file: int,
  float, or text. Empty cells are NULL.
- Numbers always compare less than text, so mixed columns never crash
  a comparison.
- ORDER BY puts NULLs last in both directions.
- Subqueries may be correlated (the inner query can reference outer
  columns). Uncorrelated ones are executed once and cached for the
  statement; correlated ones run per outer row, with memoized
  correlation checks and per-statement probe sets to keep the common
  cases cheap.
- A bare column in a grouped query reads the group's first row, the
  SQLite convention.
- There is no CAST. Types come from the CSV, so `cast(age as text)`
  fails at parse time with a pointer at the `as`. If you need a number
  as text, export and convert, or use || with an empty string:
  `select age || '' from people`.
