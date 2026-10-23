# squeal SQL reference

One page, everything squeal speaks. Where squeal differs from the SQL
standard, the difference is called out.

## Statements

```
[WITH name [(col, ...)] AS (select), ...]
SELECT [DISTINCT] select_item [, ...]
  [FROM source]
  [WHERE expr]
  [GROUP BY expr [, ...]]
  [HAVING expr]
  [compound-op SELECT ...]
  [ORDER BY expr [ASC|DESC] [, ...]]
  [LIMIT n [OFFSET n]]

compound-op: UNION [ALL|DISTINCT] | INTERSECT | EXCEPT

EXPLAIN [ANALYZE] SELECT ...

CREATE VIEW name AS SELECT ...
DROP VIEW [IF EXISTS] name
```

Views are session-level named queries. They validate when created,
materialize fresh on every use, and may reference other views; a view
that references itself is an error.

A select item is `expr [AS alias]`, `*`, or a window call.

### Compound selects

`UNION`, `INTERSECT` and `EXCEPT` deduplicate rows; `UNION ALL` keeps
duplicates. Dedupe treats NULL = NULL, like SELECT DISTINCT. The
operators share one precedence and fold left to right (the SQLite
rule), and every side must return the same number of columns; column
names come from the first side. ORDER BY / LIMIT / OFFSET appear once,
at the very end, and ORDER BY over a compound names an output column
or a position, nothing else.

### WITH

A WITH clause names a query for the rest of the statement:

```
with high as (select name from people where age > 35),
     senior(town) as (select city from people where age > 40)
select * from high
```

Each name materializes on first use and is shared for the rest of the
statement, including across sides of a compound and inside subqueries.
Later CTEs may use earlier ones; a CTE name shadows a table or
view of the same name. An unused CTE is never evaluated. Under plain
WITH, a CTE that reaches itself is an error, like a self-referencing
view; WITH RECURSIVE allows exactly that.

### WITH RECURSIVE

```
with recursive reach(dst) as (
  select dst from edges where src = 'a'
  union
  select e.dst from edges e join reach r on e.src = r.dst
)
select dst from reach
```

A recursive CTE is one compound: an anchor term, UNION or UNION ALL,
and a recursive term that scans the CTE exactly once as a plain source
(not inside a subquery, and the anchor never references it). Evaluation
is the working-table algorithm: the anchor's rows are the first working
table, the recursive term runs against it, and brand-new rows become
the next working table until a round adds nothing. Under UNION (not
ALL) dedupe checks every row accumulated so far, which is what stops
cycles on loopy data, as in the closure query above.

Termination is explicit, because UNION ALL on cyclic data never stops
on its own. Every recursive query is capped at 100,000 accumulated
rows and errors past that ("exceeded the 100000 row guard; a LIMIT
bounds it only on the CTE itself or on a plain outer scan - with
WHERE, DISTINCT or aggregates the recursive term itself must be
tighter"). An unordered LIMIT stops
accumulation early, either written on the CTE itself or pushed down
from a plain outer scan like `select * from x limit 5`. Rows come out
in SQLite's queue order - anchor first, then each level in the order
its parents were produced - so a capped scan returns the same prefix
SQLite would. ORDER BY and LIMIT on the CTE itself apply to the
accumulated rows after iteration. The recursive term computes new
rows from one working table at a time, so it cannot use aggregate
functions, window functions, GROUP BY, or HAVING - those have no
honest meaning across levels and error (SQLite rejects them too);
DISTINCT stays allowed.

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
- A positional ORDER BY or GROUP BY reference must point at a real
  expression; `select * from t order by 1` fails because `*` is not
  one.
- ORDER BY in SQLite's recursive-select is not a result sort: it is a
  queue-extraction priority that changes traversal order (breadth
  first by default, ORDER BY ... DESC gives depth first), and only
  positional terms are accepted - a named term errors ("1st ORDER BY
  term does not match any column in the result set"). Verified against
  SQLite 3.37.2 with branching-graph probes, per
  https://sqlite.org/lang_with.html. squeal instead applies ORDER BY
  and LIMIT on a recursive CTE to the accumulated rows after
  iteration, so the same query can return different (and, squeal
  would argue, the promised) rows.
- There is no CAST. Types come from the CSV, so `cast(age as text)`
  fails at parse time with a pointer at the `as`. If you need a number
  as text, export and convert, or use || with an empty string:
  `select age || '' from people`.
