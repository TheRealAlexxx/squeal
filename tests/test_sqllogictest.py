"""Run the sqllogictest-subset corpus as one unittest, and pin the README count.

The corpus (tests/sqllogictest/*.test) is the published correctness claim:
feature files verified against the SQLite oracle at generation time,
pinned.test holding hand-verified dialect differences. The README's
"sqllogictest subset: N records" line must equal the live passing count so
the published number cannot drift from reality.
"""

import glob
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from squeal.csvload import load
from tests.run_sqllogictest import run_file, CORPUS, FIXTURES, TABLES


class SltTests(unittest.TestCase):
    def test_corpus_and_readme_count(self):
        tables = {name: load(os.path.join(FIXTURES, name + ".csv")) for name in TABLES}
        total = 0
        failures = []
        for path in sorted(glob.glob(os.path.join(CORPUS, "*.test"))):
            p, f, why = run_file(path, tables)
            total += p + f
            failures.extend("%s: %s" % (os.path.basename(path), w) for w in why)
        self.assertEqual(failures, [])
        readme = open(os.path.join(os.path.dirname(__file__), "..", "README.md")).read()
        m = re.search(r"sqllogictest subset: (\d+) records", readme)
        self.assertIsNotNone(m, "README is missing the sqllogictest count line")
        self.assertEqual(int(m.group(1)), total,
                         "README count drifted: update the README line to %d" % total)


class SltFaultInjectionTests(unittest.TestCase):
    """The harness must be able to fail: corrupt records are caught."""

    def tables(self):
        return {name: load(os.path.join(FIXTURES, name + ".csv")) for name in TABLES}

    def write_tmp(self, body):
        import tempfile
        fd, path = tempfile.mkstemp(suffix=".test")
        with os.fdopen(fd, "w") as fh:
            fh.write(body)
        self.addCleanup(os.unlink, path)
        return path

    def test_wrong_expected_rows_fail(self):
        path = self.write_tmp("query I nosort\nselect 1 + 1\n----\n3\n")
        p, f, why = run_file(path, self.tables())
        self.assertEqual((p, f), (0, 1))

    def test_missing_error_fails(self):
        path = self.write_tmp("statement error\nselect 1 + 1\n")
        p, f, why = run_file(path, self.tables())
        self.assertEqual((p, f), (0, 1))

    def test_wrong_error_text_fails(self):
        # the query raises, but not with this message: a different bug
        # must not keep a pin green
        path = self.write_tmp("statement error no such table\nselect cast('42' as integer)\n")
        p, f, why = run_file(path, self.tables())
        self.assertEqual((p, f), (0, 1))
        self.assertIn("does not contain", why[0])


if __name__ == "__main__":
    unittest.main()
