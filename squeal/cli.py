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

    def __init__(self, engine="row"):
        self.tables = {}
        self.views = {}
        self._resolving = set()
        self.engine = engine

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
            sources = _explain_sources(select)
            cte_names = {n.name for n in select.walk()
                         if isinstance(n, ast.Cte)}
            for src in sources:
                for ref in table_refs(src):
                    if ref.name not in cte_names:
                        self.resolve(ref.name)  # fail early on a bad path
            prefix = ""
            if self.engine == "columnar" and not select.analyze:
                prefix = "engine: " + self._planned_engine(select) + "\n"
            if select.analyze:
                import time
                from .explain import render_analyzed
                stats = {"table_rows": [len(self.resolve(ref.name).rows)
                                        for src in sources
                                        for ref in table_refs(src)
                                        if ref.name not in cte_names]}
                start = time.perf_counter()
                execute(select, self.resolve, stats)
                elapsed = (time.perf_counter() - start) * 1000
                body = render_analyzed(select, self.resolve, stats, elapsed)
                if self.engine == "columnar":
                    body = ("engine: row (EXPLAIN ANALYZE always measures "
                            "the row pipeline)\n" + body)
                return body
            return prefix + render_plan(select, self.resolve)
        if select.source is None and not select.compounds:
            # no FROM: one row computed from thin air. execute() makes
            # that row itself for a None source; it still needs the real
            # resolver so subqueries in the items can read actual tables
            # (the old single-row CsvTable shim made resolve() a constant
            # and every subquery table lookup hit the empty table)
            if self.engine == "columnar":
                print("columnar: query outside the subset, "
                      "using the row engine", file=sys.stderr)
            return execute(select, self.resolve)
        if self.engine == "columnar":
            note = self._columnar_note(select)
            if note is not None:
                print(note, file=sys.stderr)
            else:
                from . import columnar
                result = columnar.run(select, self.resolve(select.source.name))
                if result is not None:
                    return result
                print("columnar: query outside the subset, "
                      "using the row engine", file=sys.stderr)
        return execute(select, self.resolve)

    def _columnar_note(self, select):
        """Why the columnar engine will not take this query, or None."""
        from . import astnodes as ast
        if select.ctes:
            return "columnar: WITH clauses are outside the subset, using the row engine"
        if select.compounds:
            return "columnar: compound selects are outside the subset, using the row engine"
        if not isinstance(select.source, ast.TableRef):
            return "columnar: joins are outside the subset, using the row engine"
        if select.source.name in self.views:
            return "columnar: views are outside the subset, using the row engine"
        table = self.resolve(select.source.name)
        # views come back all-text typed; the columnar compiler trusts
        # declared types, so only real CSVs qualify
        if not table.path.endswith(".csv"):
            return "columnar: source is not a CSV, using the row engine"
        return None

    def _planned_engine(self, select):
        """Which engine a plain run of this query would use."""
        note = self._columnar_note(select)
        if note is not None or select.source is None:
            return "row (outside the columnar subset)"
        from . import columnar
        if columnar.covers(select, self.resolve(select.source.name)):
            return "columnar"
        return "row (outside the columnar subset)"


def _explain_sources(select):
    """Every core's source, in the order EXPLAIN prints them: the main
    pipeline, its compound cores, then each WITH pipeline."""
    out = []

    def visit(sel):
        out.append(sel.source)
        for _op, core in sel.compounds:
            visit(core)
        for cte in sel.ctes:
            visit(cte.select)

    visit(select)
    return out


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
        if not buf and stripped in (".tables", ".views"):
            _show_known(session, stripped)
            continue
        if not buf and stripped == ".help":
            print("commands: .tables  .views  .help  .quit - "
                  "end statements with ;")
            continue
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


def _show_known(session, command):
    """.tables lists loaded CSV tables, .views lists the session's views."""
    if command == ".tables":
        if not session.tables:
            print("(no tables loaded yet - query a CSV first)")
            return
        for name, table in session.tables.items():
            print("%s  (%d rows)" % (name, len(table.rows)))
    else:
        if not session.views:
            print("(no views yet - create one with CREATE VIEW name AS select ...)")
            return
        for name in session.views:
            print(name)


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="squeal",
        description="Run SQL queries against CSV files.")
    parser.add_argument("query", nargs="?", help="the SQL query; omit for a REPL")
    parser.add_argument("--format", choices=sorted(FORMATS), default="table",
                        help="output format (default: table)")
    parser.add_argument("--engine", choices=("row", "columnar"), default="row",
                        help="execution engine: columnar is the whole-column "
                             "experiment for simple scans (default: row)")
    parser.add_argument("--version", action="version", version="squeal " + VERSION)
    args = parser.parse_args(argv)

    session = Session(engine=args.engine)
    if args.query is None and not sys.stdin.isatty():
        # piped stdin is a script: run every statement in order
        # skip pasted REPL metacommands, nothing else: a SQL line can
        # legitimately start with a dot (e.g. the literal .5)
        meta = (".quit", ".exit", "\\q", ".tables", ".views", ".help")
        script = "\n".join(line for line in sys.stdin.read().splitlines()
                           if line.strip() not in meta)
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
    if os.path.exists(query):
        print("error: %s is a file, not a query - did you mean:" % query, file=sys.stderr)
        print('  squeal "select * from \'%s\'"' % query, file=sys.stderr)
        return 2
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
