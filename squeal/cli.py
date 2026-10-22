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
    """Loaded tables and views, keyed by the name queries use for them."""

    def __init__(self):
        self.tables = {}
        self.views = {}
        self._resolving = set()

    def resolve(self, source):
        if source in self.views:
            if source in self._resolving:
                raise SquealError("view %s references itself" % source)
            self._resolving.add(source)
            try:
                result = execute(self.views[source], self.resolve)
            finally:
                self._resolving.discard(source)
            from .csvload import CsvTable
            return CsvTable(result.columns, result.rows, [str] * len(result.columns),
                            "<view %s>" % source)
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
        return self.run_statement(parse(sql))

    def run_statement(self, stmt):
        from . import astnodes as ast
        if isinstance(stmt, ast.CreateView):
            execute(stmt.select, self.resolve)  # fail now on a broken view
            self.views[stmt.name] = stmt.select
            self.tables.pop(stmt.name, None)
            return "view %s created" % stmt.name
        if isinstance(stmt, ast.DropView):
            if stmt.name not in self.views:
                if stmt.if_exists:
                    return "view %s did not exist" % stmt.name
                raise SquealError("no such view: %s" % stmt.name)
            del self.views[stmt.name]
            return "view %s dropped" % stmt.name
        return self.run_select(stmt)

    def run_select(self, select):
        from . import astnodes as ast
        if not isinstance(select, ast.Select):
            return self.run_statement(select)
        if select.explain:
            for ref in table_refs(select.source):
                self.resolve(ref.name)  # fail early on a bad path
            if select.analyze:
                import time
                from .explain import render_analyzed
                stats = {"table_rows": [len(self.resolve(ref.name).rows)
                                        for ref in table_refs(select.source)]}
                start = time.perf_counter()
                execute(select, self.resolve, stats)
                elapsed = (time.perf_counter() - start) * 1000
                return render_analyzed(select, self.resolve, stats, elapsed)
            return render_plan(select, self.resolve)
        if select.source is None:
            # no FROM: one row computed from thin air
            from .csvload import CsvTable
            return execute(select, CsvTable([], [()], [], "<memory>"))
        return execute(select, self.resolve)


def table_refs(node):
    from . import astnodes as ast
    if node is None:
        return
    if isinstance(node, ast.TableRef):
        yield node
    else:
        yield from table_refs(node.left)
        yield from table_refs(node.right)


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
