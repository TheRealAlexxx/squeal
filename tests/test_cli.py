import os
import subprocess
import sys
import unittest

ROOT = os.path.join(os.path.dirname(__file__), "..")
FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "people.csv")
ENV = dict(os.environ, PYTHONPATH=ROOT)


def cli(*args, stdin=None):
    return subprocess.run([sys.executable, "-m", "squeal.cli", *args],
                          capture_output=True, text=True, env=ENV, cwd=ROOT,
                          input=stdin)


class CliTests(unittest.TestCase):
    def test_query(self):
        r = cli("select name from '%s' where age > 40 order by name" % FIXTURE)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("Alan", r.stdout)
        self.assertIn("Barbara", r.stdout)
        self.assertIn("(2 rows)", r.stdout)

    def test_csv_format(self):
        r = cli("--format", "csv", "select name from '%s' limit 1" % FIXTURE)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip().splitlines(), ["name", "Ada"])

    def test_json_format(self):
        r = cli("--format", "json", "select name from '%s' where city = 'Amsterdam'" % FIXTURE)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('"Edsger"', r.stdout)

    def test_explain(self):
        r = cli("explain select name from '%s' where age > 30" % FIXTURE)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("Scan", r.stdout)
        self.assertIn("Filter", r.stdout)

    def test_stdin_script(self):
        r = cli(stdin="select 1 + 1;\nselect 2 + 2;\n")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.count("(1 row)"), 2)

    def test_error_exit_code(self):
        r = cli("select from")
        self.assertEqual(r.returncode, 1)
        self.assertIn("^", r.stderr)

    def test_missing_file(self):
        r = cli("select * from 'nope.csv'")
        self.assertEqual(r.returncode, 1)
        self.assertIn("no such table or file", r.stderr)

    def test_no_from(self):
        r = cli("select 6 * 7 as answer")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("42", r.stdout)

    def test_version(self):
        r = cli("--version")
        self.assertEqual(r.returncode, 0)
        self.assertIn("squeal", r.stdout)


if __name__ == "__main__":
    unittest.main()


class ExplainAnalyzeTests(unittest.TestCase):
    def test_explain_analyze(self):
        r = cli("explain analyze select name from '%s' where age > 30" % FIXTURE)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("6 in, 4 out", r.stdout)
        self.assertIn("elapsed:", r.stdout)

    def test_explain_still_static(self):
        r = cli("explain select name from '%s' where age > 30" % FIXTURE)
        self.assertNotIn("elapsed:", r.stdout)



class ViewTests(unittest.TestCase):
    def test_create_query_drop(self):
        script = (
            "create view londoners as select * from '%s' where city = 'London';\n"
            "select name from londoners order by age;\n"
            "drop view londoners;\n"
            "select 1 from londoners;\n" % FIXTURE)
        r = cli(stdin=script)
        self.assertEqual(r.returncode, 1)  # last select fails after the drop
        self.assertIn("view londoners created", r.stdout)
        self.assertIn("Ada", r.stdout)
        self.assertIn("no such table or file: londoners", r.stderr)

    def test_view_over_view(self):
        script = (
            "create view londoners as select * from '%s' where city = 'London';\n"
            "create view old_londoners as select * from londoners where age > 35;\n"
            "select name from old_londoners order by name;\n" % FIXTURE)
        r = cli(stdin=script)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("Ada", r.stdout)
        self.assertIn("Alan", r.stdout)
        self.assertNotIn("Katherine", r.stdout)

    def test_broken_view_fails_at_create(self):
        r = cli(stdin="create view bad as select nope from '%s';\n" % FIXTURE)
        self.assertEqual(r.returncode, 1)
        self.assertIn("no such column", r.stderr)

    def test_drop_missing_view(self):
        r = cli(stdin="drop view ghost;\n")
        self.assertEqual(r.returncode, 1)
        self.assertIn("no such view", r.stderr)

    def test_drop_if_exists(self):
        r = cli(stdin="drop view if exists ghost;\nselect 1;\n")
        self.assertEqual(r.returncode, 0, r.stderr)



class ReplMetaCommandTests(unittest.TestCase):
    def test_show_known_empty(self):
        import io
        from contextlib import redirect_stdout
        from squeal.cli import Session, _show_known
        out = io.StringIO()
        with redirect_stdout(out):
            _show_known(Session(), ".tables")
            _show_known(Session(), ".views")
        self.assertIn("no tables", out.getvalue())
        self.assertIn("no views", out.getvalue())

    def test_show_known_populated(self):
        import io
        from contextlib import redirect_stdout
        from squeal.cli import Session, _show_known
        from squeal.parser import parse
        session = Session()
        session.run_statement(parse(
            "create view londoners as select * from '%s' where city = 'London'" % FIXTURE))
        out = io.StringIO()
        with redirect_stdout(out):
            _show_known(session, ".tables")
            _show_known(session, ".views")
        self.assertIn("people.csv  (6 rows)", out.getvalue())
        self.assertIn("londoners", out.getvalue())

