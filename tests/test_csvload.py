"""Tests for the CSV reader: row shape, blank-line handling, NULLs.

These tests carry their own expectations instead of comparing against
SQLite, because the differential and sqllogictest harnesses both build
their SQLite side from csvload's own output - a loader bug would be
invisible there.

Row policy: csv.reader yields [] only for a truly empty line, and that
is the only thing skipped. Every non-empty parsed record is data - a
quoted empty field, a line of just commas, a line of spaces - and
short records are NULL-padded to the header width. Empty fields are
NULLs.
"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from squeal.csvload import load
from squeal.errors import SquealError
from squeal.executor import execute
from squeal.parser import parse


def load_text(text):
    fd, path = tempfile.mkstemp(suffix=".csv")
    try:
        with os.fdopen(fd, "w", newline="") as fh:
            fh.write(text)
        return load(path)
    finally:
        os.unlink(path)


class RowPolicyTests(unittest.TestCase):
    def test_all_empty_field_rows_are_null_rows(self):
        # A line that is just commas is a record of empty fields, not a
        # blank line: csv.reader returns ['', ''] for it, and SQLite
        # counts it as a row of NULLs.
        table = load_text("a,b\n,\n,\n")
        self.assertEqual(table.rows, [(None, None), (None, None)])

    def test_whitespace_only_fields_become_nulls(self):
        table = load_text("a,b\n , \n1,2\n")
        self.assertEqual(table.rows, [(None, None), (1, 2)])

    def test_truly_blank_lines_are_skipped(self):
        table = load_text("a,b\n1,2\n\n3,4\n\n")
        self.assertEqual(table.rows, [(1, 2), (3, 4)])

    def test_multicolumn_unseparated_whitespace_line_is_null_row(self):
        # Deliberate: a line of spaces parses to one whitespace field,
        # which is a real record; short-row padding makes it a NULL row.
        table = load_text("a,b\n   \n1,2\n")
        self.assertEqual(table.rows, [(None, None), (1, 2)])

    def test_multicolumn_quoted_empty_is_null_row(self):
        table = load_text('a,b\n""\n1,2\n')
        self.assertEqual(table.rows, [(None, None), (1, 2)])

    def test_multicolumn_quoted_spaces_are_null_row(self):
        table = load_text('a,b\n" "\n1,2\n')
        self.assertEqual(table.rows, [(None, None), (1, 2)])

    def test_short_row_is_null_padded(self):
        table = load_text("a,b,c\n1\n2,3\n")
        self.assertEqual(table.rows, [(1, None, None), (2, 3, None)])

    def test_partial_empty_cells_are_nulls(self):
        table = load_text("a,b\n,x\ny,\n")
        self.assertEqual(table.rows, [(None, "x"), ("y", None)])

    def test_empty_file_errors(self):
        with self.assertRaises(SquealError):
            load_text("")

    def test_header_only_gives_zero_rows(self):
        table = load_text("a,b,c\n")
        self.assertEqual(table.rows, [])

    def test_too_many_fields_errors(self):
        with self.assertRaises(SquealError):
            load_text("a,b,c\n1,2,3,4\n")


class SingleColumnTests(unittest.TestCase):
    # Single-column rows have no separator, so every non-empty line is
    # a data row; only the truly empty line (csv.reader yields []) is
    # skipped.
    def test_quoted_empty_field_is_null_row(self):
        table = load_text('a\n""\nx\n')
        self.assertEqual(table.rows, [(None,), ("x",)])

    def test_whitespace_field_is_null_row(self):
        table = load_text("a\n \nx\n")
        self.assertEqual(table.rows, [(None,), ("x",)])

    def test_blank_line_skipped(self):
        table = load_text("a\n1\n\n2\n")
        self.assertEqual(table.rows, [(1,), (2,)])


class AggregateSemanticsTests(unittest.TestCase):
    # The exact regression: aggregates over rows of NULLs must see the
    # rows. SQLite ground truth for two (NULL, NULL) rows:
    # count(*) = 2, count(a) = 0, sum(a) = NULL, avg(a) = NULL.
    def test_aggregates_over_null_rows(self):
        table = load_text("a,b\n,\n,\n")
        tables = {"t": table}
        result = execute(
            parse("select count(*), count(a), sum(a), avg(a) from t"),
            lambda name: tables[name]).rows
        self.assertEqual(result, [(2, 0, None, None)])


if __name__ == "__main__":
    unittest.main()
