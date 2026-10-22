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
