"""Command line: squeal [options] [query], or no query for the REPL.

  squeal "select city, count(*) from 'people.csv' group by city"
  squeal --format csv "select * from 'people.csv' limit 3"
  squeal < query.sql
  squeal            # interactive prompt
"""

import argparse
import os
import sys

from . import VERSION
from .csvload import load
from .errors import SquealError
from .executor import execute
from .explain import render as render_plan
from .parser import parse, parse_many
from .render import as_csv, as_json, as_table


class Session:
    """Loaded tables, keyed by the name queries use for them."""

    def __init__(self):
        self.tables = {}

    def resolve(self, source):
        if source in self.tables:
            return self.tables[source]
        path = source
        if not os.path.exists(path) and not source.lower().endswith(".csv"):
            path = source + ".csv"
        if not os.path.exists(path):
            raise SquealError("no such table or file: %s" % source)
        table = load(path)
        self.tables[source] = table
        return table

    def run(self, sql):
        return self.run_select(parse(sql))

    def run_select(self, select):
        if select.explain:
            if select.source:
                self.resolve(select.source)  # fail early on a bad path
            return render_plan(select)
        if select.source is None:
            # no FROM: one row computed from thin air
            from .executor import Result, RowContext, _projection
            from .csvload import CsvTable
            empty = CsvTable([], [()], [], "<memory>")
            select.source = "<memory>"
            return self._render(execute(select, empty))
        return self._render(execute(select, self.resolve(select.source)))

    def _render(self, result):
        self.last_result = result
        return result


FORMATS = {"table": as_table, "csv": as_csv, "json": as_json}


def run_query(session, sql, fmt):
    result = session.run(sql)
    if isinstance(result, str):  # explain output
        return result
    return FORMATS[fmt](result)


def repl(session, fmt):
    print("squeal %s - SQL over CSV files. End statements with ; - .quit to exit" % VERSION)
    buf = ""
    while True:
        try:
            line = input("squeal> " if not buf else "   ...> ")
        except EOFError:
            print()
            break
        stripped = line.strip()
        if not buf and stripped in (".quit", ".exit", "\\q"):
            break
        buf += line + "\n"
        if not stripped.endswith(";"):
            continue
        sql = buf.strip().rstrip(";")
        buf = ""
        if not sql:
            continue
        try:
            print(run_query(session, sql, fmt))
        except SquealError as e:
            print(e.render(sql), file=sys.stderr)
        except OSError as e:
            print("error: %s" % e, file=sys.stderr)


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="squeal",
        description="Run SQL queries against CSV files.")
    parser.add_argument("query", nargs="?", help="the SQL query; omit for a REPL")
    parser.add_argument("--format", choices=sorted(FORMATS), default="table",
                        help="output format (default: table)")
    parser.add_argument("--version", action="version", version="squeal " + VERSION)
    args = parser.parse_args(argv)

    session = Session()
    if args.query is None and not sys.stdin.isatty():
        # piped stdin is a script: run every statement in order
        script = "\n".join(line for line in sys.stdin.read().splitlines()
                           if not line.strip().startswith("."))
        try:
            statements = parse_many(script)
        except SquealError as e:
            print(e.render(script), file=sys.stderr)
            return 1
        try:
            for select in statements:
                result = session.run_select(select)
                print(result if isinstance(result, str) else FORMATS[args.format](result))
        except SquealError as e:
            print(e, file=sys.stderr)
            return 1
        except OSError as e:
            print("error: %s" % e, file=sys.stderr)
            return 1
        return 0
    query = args.query
    if query is None:
        repl(session, args.format)
        return 0
    try:
        print(run_query(session, query, args.format))
    except SquealError as e:
        print(e.render(query), file=sys.stderr)
        return 1
    except OSError as e:
        print("error: %s" % e, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
