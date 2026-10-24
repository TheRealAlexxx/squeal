"""Load a CSV file into rows, guessing a type for each column.

A column becomes int when every value looks like an integer, float when
every value is numeric, otherwise it stays text. Empty cells are NULL.
The guess uses the whole file, not just the first row, so one stray
"pending" in a sea of numbers demotes the column to text rather than
crashing the query. Only truly empty lines are skipped; every
other record is a row, NULL-padded to the header width.
"""

import csv
import re

from .errors import SquealError

INT_RE = re.compile(r"^[+-]?\d+$")
FLOAT_RE = re.compile(r"^[+-]?(\d+\.\d*|\.\d+|\d+)([eE][+-]?\d+)?$")


def _infer(values):
    """Pick int, float or str for one column of raw strings (None = empty)."""
    present = [v for v in values if v is not None]
    if present and all(INT_RE.match(v) for v in present):
        return int
    if present and all(FLOAT_RE.match(v) for v in present):
        return float
    return str


class CsvTable:
    def __init__(self, columns, rows, types, path):
        self.columns = columns
        self.rows = rows
        self.types = types  # per column: int, float or str
        self.path = path


def load(path):
    try:
        with open(path, newline="", encoding="utf-8-sig") as fh:
            raw = list(csv.reader(fh))
    except UnicodeDecodeError:
        raise SquealError(
            "%s is not UTF-8 text; convert it first "
            "(e.g. iconv -f UTF-16 -t UTF-8)" % path)
    if not raw:
        raise SquealError("%s is empty: no header row" % path)
    columns = [c.strip() or "column%d" % (i + 1) for i, c in enumerate(raw[0])]
    width = len(columns)
    body = []
    for line_no, record in enumerate(raw[1:], start=2):
        # csv.reader yields [] only for a truly empty line; that is the
        # only thing skipped. Every non-empty parsed record is data,
        # with short records NULL-padded to the header width below.
        if not record:
            continue
        if len(record) > width:
            raise SquealError(
                "%s row %d has %d fields but the header has %d"
                % (path, line_no, len(record), width))
        record = record + [""] * (width - len(record))
        body.append([cell.strip() or None for cell in record])
    types = [_infer([row[i] for row in body]) for i in range(width)]
    rows = []
    for row in body:
        rows.append(tuple(types[i](row[i]) if row[i] is not None else None
                          for i in range(width)))
    return CsvTable(columns, rows, types, path)
