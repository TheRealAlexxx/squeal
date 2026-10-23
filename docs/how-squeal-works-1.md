# How squeal works, part 1: from SQL text to an answer

squeal runs a SQL query end to end in about 3,500 lines of Python, no
dependencies. This post walks one real query through the whole pipeline,
using the actual code, so you can read the source afterwards and
recognize every stage.

The query, run against the CSV that ships in the repo:

```sql
select region, count(*) as n
from 'examples/orders.csv'
group by region
order by n desc
```

The answer:

```
+----------+---+
| region   | n |
+----------+---+
| eu-north | 6 |
| us-west  | 5 |
| us-east  | 5 |
| ap-south | 4 |
+----------+---+
```

Three stages, plus one deliberate gap, produced that table. Each stage is
one file, and the files are small enough to read in a sitting:

1. `squeal/lexer.py` (148 lines) turns the SQL text into tokens.
2. `squeal/parser.py` (576 lines) turns the tokens into a syntax tree.
3. The gap: there is no optimizer. This is a design decision, and it is
   the most interesting part of the tour.
4. `squeal/executor.py` (1104 lines) runs the tree and produces rows.

Supporting cast: `astnodes.py` (276 lines) defines the tree itself,
`values.py` (338 lines) defines SQL's type and three-valued-logic rules,
and `explain.py` (245 lines) prints the pipeline so you can watch it run.

## Stage 1: the lexer

The lexer is one hand-written loop. It walks the string once and emits
`Token` objects, each carrying the token's kind, text, value, line, and
column:

```python
class Token:
    __slots__ = ("kind", "text", "value", "line", "col")
```

Keywords live in a plain set, matched case insensitively. Numbers,
strings, identifiers, and two-character operators (`!=`, `<=`, `||` and
friends) each get a branch in the loop. No regular expressions, no
parser generator, no state machine library.

The line and column on every token exist for one reason: errors that
point at the exact character. Run a query with a missing expression and
you get this:

```
error: expected a value but found 'end of input'
  select region from 'examples/orders.csv' group by
                                                   ^
```

That caret comes from the position recorded back in stage 1. The
pipeline never throws away location information, so every error from
every later stage can underline the offending character in the original
SQL.

## Stage 2: the parser

The parser is recursive descent: one method per grammar rule, each
calling the ones below it. `parse_select` sits at the top and mirrors
the real shape of SQL:

```python
def parse_select(self):
    # SQL's real grammar: an optional WITH clause, then select-core
    # (compound-op select-core)*, then ORDER BY / LIMIT / OFFSET once
    # at the very end for the whole statement.
    ctes = []
    recursive = False
    if self.at_kw("with"):
        ...
```

Read that comment again, because it is the grammar. A `SELECT` is an
optional `WITH`, then one or more select-cores joined by compound
operators, then `ORDER BY` and `LIMIT` exactly once at the end. The
code is the grammar, and the grammar is readable Python.

For our query, the parser produces a `Select` node whose pieces are
plain objects:

- items: a `Column` named `region`, and a `FuncCall` for `count(*)`
  aliased as `n`
- source: a `TableRef` pointing at `examples/orders.csv`
- group_by: the `Column` `region`
- order_by: the `Column` `n`, descending

The tree is defined in `astnodes.py` and it is deliberately dumb: value
objects with fields and almost no behavior, just a `walk()` for tree
traversal and a `__repr__` for debugging. Nothing in the AST knows how
to run itself. That keeps the parser and the executor honest, since neither can
smuggle the other's job into the tree.

## The gap: the planner that isn't

Here is the part most databases spend entire subsystems on, and squeal
spends zero. There is no optimizer. No statistics, no cost model, no
plan space, no rewrites. The pipeline for any query is
fixed:

```
scan -> filter -> windows -> aggregate -> having -> project -> sort
-> offset/limit
```

`EXPLAIN` shows you this exact pipeline, because there is nothing else
to show. For our query:

```
Scan  examples/orders.csv
-> Aggregate  group by: region
  -> Project  region, count(*)
    -> Sort  keys: n DESC
```

This is a real design decision, not a gap. An optimizer exists to pick
between alternative plans, and alternatives only pay off when data is
large and storage is smart. squeal's inputs are CSV files and Python
lists, and its execution is a tuple-at-a-time loop in interpreted
Python. At that scale the fixed pipeline is almost always within a small
constant of optimal, and the entire surface area of planner bugs (wrong
plan, stale statistics, regression after an upgrade) simply does not
exist. Correctness you can read beats cleverness you can't.

There is one real planning decision, and it is honest about what it is:
joins. When the executor's `scan_source` meets a join, `equijoin_keys`
checks whether the `ON` condition is an equality between one column from
each side, possibly combined with other conditions. If so,
squeal builds a hash table on the right side's key and probes it
with the left, one pass over each input instead of a quadratic
nested loop. If not, it falls back to a nested loop. (`join_strategy`
wraps the same check so `EXPLAIN` can print the chosen label.) Two
plans, chosen by a shape check on the syntax, not by estimated cost.
That is the whole planner.

## Stage 3: the executor

The executor is a tree-walk over the fixed pipeline. `execute()` takes
the `Select` node, resolves the `FROM` source into a schema and a list
of rows, then applies each stage in order, counting rows per stage when
`EXPLAIN ANALYZE` asks for statistics:

```
Scan  examples/orders.csv   -- 20 rows
-> Aggregate  group by: region   -- 4 groups
  -> Project  region, count(*)   -- 4 rows
    -> Sort  keys: n DESC
elapsed: 0.2 ms
```

Expressions are evaluated by `eval_expr`, one function that pattern
matches on the node type: a `Literal` returns its value, a `Column`
looks up the name in a context, a `Binary` evaluates both sides and
applies the operator. Fourteen node types, fourteen cases, no visitor
framework.

The subtle part of SQL lives in `values.py`. SQL booleans are not
Python booleans: they are three-valued, because `NULL` makes every
comparison unknown. `null = null` is not true, it is unknown, and
`where unknown` filters the row out. squeal keeps that logic in one
place (`sql_and`, `sql_or`, `compare`) instead of scattering it through
the executor, which is why `and` and `or` in `eval_expr` call those
helpers rather than Python's own operators. The arithmetic rules are worth a
look too: division always yields a float (`7/2` is `3.5`, not `3`),
mixing an integer and a float promotes to float, and arithmetic on
non-numbers raises instead of guessing. Comparisons go the other way:
mixed types never raise, because numbers always sort before text, so a
CSV column that degraded to text compares sanely instead of exploding.

The executor also caches work when the cache is provably safe. An
uncorrelated subquery in an `IN` list is constant for the whole
statement, so squeal materializes it once and probes a hash set per row
instead of re-running the subquery every time. The comment in the code
says exactly why it is allowed: "subquery candidates are constant for
the statement: materialize once, and probe a hash set instead of
scanning per outer row". This is the executor's version of the
planner's lesson: do the optimization only where correctness is a
syntax check away.

## Reading the source yourself

If you want to follow along, the shortest path through the code matches
the stages above:

1. `lexer.py` top to bottom; it is short and self contained.
2. `astnodes.py`, just to see every node the pipeline can produce.
3. `parser.py`, starting at `parse_select`.
4. `executor.py`, starting at `execute`, with `values.py` open on the
   side.

Part 2 covers the stages that did not fit here: how window
functions are precomputed in a separate pass over the filtered rows
before projection (and why mixing them with GROUP BY is an error, for
now), how recursive CTEs iterate until an iteration adds nothing, and
how the opt-in columnar engine recompiles the WHERE clause of a narrow
query shape into list comprehensions over whole columns, checked row
for row against the row engine it runs alongside.

[Read part 2: windows, recursion, and a second
engine](how-squeal-works-2.md).
