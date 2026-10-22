# squeal

A tiny SQL engine for CSV files, written from scratch in pure Python.
Zero dependencies, one installable package, and a real pipeline:
lexer, recursive descent parser, expression evaluator, row-based
executor, EXPLAIN, and a CLI/REPL.

squeal exists to answer a question: what is the smallest honest SQL
engine you can still learn something from? Every stage is a single
small module you can read in one sitting.

## Install

```
pip install git+https://github.com/TheRealAlexxx/squeal.git
```

Or run it from a checkout with no install at all:

```
PYTHONPATH=. python3 -m squeal.cli "select 1 + 1"
```

## Try it

There is a sample dataset in `examples/orders.csv`: twenty orders with
a couple of missing prices.

```
$ squeal "select region, count(*) as orders, sum(quantity) as items,
          round(sum(quantity * unit_price), 2) as revenue
          from 'examples/orders.csv'
          group by region having count(*) >= 4
          order by revenue desc"
+----------+--------+-------+---------+
| region   | orders | items | revenue |
+----------+--------+-------+---------+
| eu-north | 6      | 13    | 4339.95 |
| us-east  | 5      | 14    | 3709.43 |
| us-west  | 5      | 8     | 2230.97 |
| ap-south | 4      | 9     | 1797.94 |
+----------+--------+-------+---------+
(4 rows)
```

Note what did not happen: the two orders with a missing `unit_price`
did not crash the multiplication and did not count as zero. They
became NULL, and NULL sums skip them, the way SQL says they should.

## What it speaks

- `SELECT` with expressions, `AS` aliases, `*`, and `DISTINCT`
- `FROM 'file.csv'` (or a bare name, resolved to `name.csv`)
- `WHERE` with full precedence: `or`, `and`, `not`, comparisons,
  `IS [NOT] NULL`, `[NOT] LIKE`, `[NOT] IN (...)`, `[NOT] BETWEEN`
- `GROUP BY` and `HAVING`, with `count`, `sum`, `avg`, `min`, `max`
- `ORDER BY` (multiple keys, `ASC`/`DESC`, select aliases allowed,
  NULLs always last)
- `LIMIT` and `OFFSET`
- Arithmetic `+ - * / %`, string concat `||`, `CASE WHEN ... END`
- Scalar functions: `upper`, `lower`, `length`, `trim`, `abs`,
  `round`, `coalesce`, `nullif`, `substr`, `replace`, `typeof`
- `EXPLAIN <query>` prints the pipeline stages a query runs through
- Three output formats: `--format table|csv|json`
- A REPL when you run `squeal` with no query, and script mode when you
  pipe a file of statements into it
- Errors point at the exact character that confused the parser:

```
$ squeal "select from"
error: expected a value but found 'from'
  select from
         ^
```

## The dialect, honestly

- **NULLs are real.** Comparison or arithmetic with NULL yields NULL.
  `WHERE` keeps a row only when the predicate is TRUE, so NULL and
  FALSE both reject. `AND`/`OR`/`NOT` follow the SQL truth tables.
- **CSV types are inferred per column** from the whole file: all
  integers means int, all numeric means float, anything else means
  text. Empty cells are NULL. One stray word in a number column
  demotes the whole column to text instead of crashing.
- **Mixed-type comparisons never raise.** Numbers always sort before
  text, so `5 < 'apple'` is true and consistent.
- **`LIKE` is case sensitive.** `%` matches any run, `_` one character.
- **No FROM** computes a single row: `select 6 * 7 as answer`.
- **Strings are 1-based** in `substr`, like the standard says.
- One query reads one file. No joins yet; see the roadmap.

## How it works inside

```
SQL text
  -> squeal/lexer.py     tokens with line/column positions
  -> squeal/parser.py    recursive descent, one Select tree out
  -> squeal/astnodes.py  dumb value objects, zero behavior
  -> squeal/executor.py  scan, filter, group, having, project,
                         distinct, sort, limit
  -> squeal/values.py    NULL logic, comparisons, functions
  -> squeal/render.py    table, csv or json output
```

The executor is deliberately row-based. There is no query optimizer;
EXPLAIN shows the fixed pipeline with the stages your query activates.
That keeps the whole execution model in your head, which is the point.

## Develop and test

```
PYTHONPATH=. python3 -m unittest discover -s tests
```

The suite covers the lexer, parser, value semantics, executor, and CLI
end to end. CI runs it on Python 3.9 through 3.12.

## Roadmap

Tracked as GitHub issues. The honest next steps: joins (the big one),
subqueries in `IN`, `CREATE VIEW`-style named queries in the REPL,
and a columnar execution experiment behind a flag.

## License

MIT. Do what you like; credit is nice.
