# How squeal works, part 2: windows, recursion, and a second engine

Part 1 walked a plain query through squeal's fixed pipeline: lexer,
parser, no optimizer, executor. Three features in the dialect do not
fit that plain pipeline, and each earned its own machinery. This post
opens all three, again straight from the source: window functions,
recursive CTEs, and the opt-in columnar engine.

## Window functions are a precompute pass

A window function breaks the executor's basic deal. `eval_expr`
evaluates one expression against one row, but `rank() over (order by
unit_price desc)` needs every other row in the partition before it can
answer for any one of them. squeal's answer is to not evaluate windows
inside `eval_expr` at all.

`find_windows` walks the select items and collects every `WindowCall`
node. Before projection, `compute_windows` evaluates each call once,
for every filtered input row, and returns a map from the node's
identity to a list of values, one per row:

```python
def compute_windows(calls, schema, rows, resolve, exec_cache=None):
    """Precompute every window call: {id(node): [value per input row]}.

    Frame semantics: with an ORDER BY the frame is the partition start
    through the current row (a running total); without one it is the
    whole partition.
    """
```

Later, when the projection walks the expression tree and reaches a
`WindowCall`, `eval_expr` just looks the row's precomputed value up by
`id(node)` in the row context. The tree-walker never learns what a
partition is.

Inside `compute_windows` the work is honest and simple: evaluate the
partition keys and order keys per row, bucket row indexes into
partitions, sort each partition's members with the same ordering rules, then walk the members in order. For
`row_number` the value is the position. For a windowed aggregate like
`sum(quantity) over (order by order_id)`, the frame is the members from
the partition start through the current position, and the aggregate
runs over that frame. Real output, frame by frame:

```
+----------+---------+
| region   | running |
+----------+---------+
| us-west  | 1       |
| eu-north | 3       |
| us-east  | 6       |
| us-west  | 8       |
+----------+---------+
```

Ranking has the one subtle rule in the file, and `_assign_ranks` says
it in two lines of its docstring: "rank(): ties share a rank, then
gaps. dense_rank(): no gaps." The orders CSV has five laptops at the
same price, which makes the difference visible:

```
+---------+------------+---+---+
| product | unit_price | r | d |
+---------+------------+---+---+
| laptop  | 1299       | 1 | 1 |
| laptop  | 1299       | 1 | 1 |
| laptop  | 1299       | 1 | 1 |
| laptop  | 1299       | 1 | 1 |
| laptop  | 1299       | 1 | 1 |
| monitor | 349        | 6 | 2 |
+---------+------------+---+---+
```

Five ties at rank 1, then rank skips to 6 while dense_rank steps to 2.
Exactly what the docstring promises.

Two honest limits come straight out of the design. Because windows are
precomputed over the filtered rows before projection, a window call in
a WHERE clause has no values to look up:

```
error: window functions are only supported in the select list for now
```

And because the precompute runs on the pre-aggregation row set, mixing
windows with grouping would need two different row universes at once,
so the executor refuses:

```
error: window functions cannot be mixed with GROUP BY or aggregates yet
```

Note the "yet". Both are pinned, documented errors, not silent wrong
answers. One more honesty note: a running aggregate re-aggregates its
frame at every position, which is quadratic per partition. At CSV
scale nobody notices, and the code stays the kind you can read.

## Recursive CTEs are a loop with a conscience

`WITH RECURSIVE` looks declarative and runs as a plain loop.
`_materialize_recursive` implements the textbook working-table
algorithm, and its docstring does not pretend otherwise:

```
1. run the anchor term; its rows are the first working table
2. run the recursive term with the CTE bound to the working table
3. brand-new rows become the next working table; repeat until an
   iteration adds nothing
```

Before the loop starts, the function runs a validation gauntlet, and
the error messages are the documentation. The anchor term cannot
reference the CTE itself. The recursive term must scan the CTE exactly
once, as a direct scan rather than through a subquery. And the
recursive term cannot use GROUP BY, HAVING, aggregates, or window
functions. The comment in the code gives the reason: the recursive
term computes new rows from one working table at a time, "so anything
that would aggregate or rank across levels has no honest meaning here.
SQLite rejects these too". Run it and you get the same answer SQLite
would give:

```
error: recursive WITH query n: the recursive term cannot use GROUP BY or HAVING
```

Termination is engineered, not hoped for. Under UNION (not ALL) every
new row is checked against everything produced so far, so a cycle on
loopy data stops when an iteration adds nothing new. Under either
keyword a hard guard caps the whole result at 100,000 rows, and the
error message tells you exactly what your options are:

```
error: recursive WITH query n exceeded the 100000 row guard; a LIMIT bounds it only on the CTE itself or on a plain outer scan - with WHERE, DISTINCT or aggregates the recursive term itself must be tighter
```

That message earned its length. A LIMIT does stop the loop early, but
only when it is an unordered limit on the CTE itself, or a plain outer
scan of it: any rows satisfy an unordered limit, so accumulation can
stop as soon as enough rows exist. Wrap the scan in a WHERE, DISTINCT,
or an aggregate and the limit no longer bounds the loop, because the
query might need rows beyond the prefix. The classic counting query
shows both halves: with `limit 5` the chain stops at five rows, and
wrapped in `count(*)` with no limit it runs into the guard above.

Iteration order matches SQLite's queue: anchor rows first, then each
level in the order its parents were produced. That is what makes the
early-stop prefix deterministic instead of whatever order a set
happened to produce.

## The columnar engine is an opt-in second opinion

The row engine walks the expression tree once per row, and most of its
time goes to dispatch, not data. Issue #4 asked the obvious question:
for a simple single-table scan with simple predicates, what if the
WHERE clause ran at bytecode speed over whole columns instead?

`columnar.py` is the answer, and it is disciplined about being one.
The module docstring states the bar up front: "Correctness parity with
the row engine is the bar (tests/test_columnar.py compares them row for
row); the win is measured in benchmarks/columnar.py." You opt in with
`--engine columnar`. Anything outside the narrow subset (joins,
ordering, DISTINCT, HAVING, window calls, more than one grouping
column, non-bare-column aggregate arguments) falls back to the
row engine, and the CLI says so out loud:

```
columnar: query outside the subset, using the row engine
```

Inside the subset, the WHERE clause compiles to one Python expression
string over column slots, evaluated as a single list comprehension.
The subtle part is that the generated code preserves SQL semantics
exactly: NULL guards yield UNKNOWN where the row engine would, AND/OR
propagate three-valued logic the same way, and a mixed-type comparison
folds to a constant, because squeal orders numbers before text, so the
answer is the same for every non-NULL row. Literals enter the
generated code through `repr()` only; no user text is spliced into it.
The docstring's "no user text is spliced into it" is the first
thing to check in any compiler that calls `eval` (this one does).

The payoff, from the recorded benchmarks on 100k rows: filter plus
count drops from 111 ms to 40 ms, and a single-column group by from
451 ms to 71 ms. EXPLAIN names the engine it picked, so you never have
to guess which path ran:

```
engine: columnar
Scan  examples/orders.csv
-> Filter  (unit_price > 20)
  -> Aggregate  group by: region
    -> Project  region, count(*)
```

(EXPLAIN ANALYZE always measures the row pipeline, so it labels itself
`engine: row` even under `--engine columnar`; the plain plan names the
real pick.)

And parity is not a figure of speech. The same grouped query returns
identical rows under both engines, and the test suite holds the two
engines row for row equal across the subset, including NULL
three-valued logic, mixed-type comparisons, and NULL group keys. It is
still roughly 6x per row behind SQLite on similar shapes. The win is a
constant factor inside a pure-Python interpreter, and
docs/benchmarks.md says so.

## Reading order for part 2

1. `executor.py`, `compute_windows` and `_assign_ranks`, about 65
   lines together.
2. `executor.py`, `_materialize_recursive`, 116 lines including its
   docstring, about 95 of them code.
3. `columnar.py` top to bottom, 276 lines, with
   `tests/test_columnar.py` open beside it.

Everything quoted above comes from those files, docs/benchmarks.md
and squeal/cli.py, and from queries run against the checkout this
article was written in. That is the whole
point of the exercise: in a codebase this size, the documentation can
afford to be the truth.
