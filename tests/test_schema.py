import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from squeal.errors import SquealError
from squeal.schema import Schema


class SchemaTests(unittest.TestCase):
    def setUp(self):
        self.schema = Schema([("orders", "id"), ("orders", "region"),
                              ("customers", "id"), ("customers", "name")])

    def test_bare_unique(self):
        self.assertEqual(self.schema.resolve("region"), 1)
        self.assertEqual(self.schema.resolve("name"), 3)

    def test_qualified(self):
        self.assertEqual(self.schema.resolve("orders.id"), 0)
        self.assertEqual(self.schema.resolve("customers.id"), 2)

    def test_ambiguous_bare(self):
        with self.assertRaises(SquealError) as ctx:
            self.schema.resolve("id")
        self.assertIn("ambiguous", str(ctx.exception))
        self.assertIn("orders.id", str(ctx.exception))

    def test_unknown(self):
        with self.assertRaises(SquealError):
            self.schema.resolve("nope")

    def test_unknown_qualified(self):
        with self.assertRaises(SquealError):
            self.schema.resolve("orders.nope")

    def test_merge(self):
        a = Schema([(None, "x")])
        b = Schema([("t", "y")])
        merged = a.merge(b)
        self.assertEqual(len(merged), 2)
        self.assertEqual(merged.names, ["x", "y"])


if __name__ == "__main__":
    unittest.main()


class SuggestionTests(unittest.TestCase):
    def test_typo_suggests_column(self):
        schema = Schema([(None, "name"), (None, "score")])
        with self.assertRaises(SquealError) as ctx:
            schema.resolve("nmae")
        self.assertIn("did you mean name", str(ctx.exception))

    def test_distant_name_no_suggestion(self):
        schema = Schema([(None, "name")])
        with self.assertRaises(SquealError) as ctx:
            schema.resolve("zzzzz")
        self.assertNotIn("did you mean", str(ctx.exception))

    def test_qualified_typo_suggests_qualified(self):
        schema = Schema([("orders", "region")])
        with self.assertRaises(SquealError) as ctx:
            schema.resolve("orders.regoin")
        self.assertIn("orders.region", str(ctx.exception))



class CsvLoadRobustnessTests(unittest.TestCase):
    def _write(self, text, name="tmp.csv"):
        import tempfile
        path = os.path.join(tempfile.mkdtemp(), name)
        with open(path, "wb") as fh:
            fh.write(text)
        return path

    def test_ragged_extra_fields_are_an_error_not_silent_loss(self):
        from squeal.csvload import load
        from squeal.errors import SquealError
        path = self._write(b"a,b\n1,2\n3,4,5\n")
        with self.assertRaises(SquealError) as ctx:
            load(path)
        self.assertIn("row 3 has 3 fields but the header has 2",
                      str(ctx.exception))

    def test_short_rows_still_pad_with_nulls(self):
        from squeal.csvload import load
        path = self._write(b"a,b\n1,\n")
        self.assertEqual(load(path).rows, [(1, None)])

    def test_non_utf8_is_a_clean_error(self):
        from squeal.csvload import load
        from squeal.errors import SquealError
        path = self._write(b"\xff\xfe\xa0\x00bad")
        with self.assertRaises(SquealError) as ctx:
            load(path)
        self.assertIn("not UTF-8", str(ctx.exception))

    def test_empty_file_is_a_clean_error(self):
        from squeal.csvload import load
        from squeal.errors import SquealError
        path = self._write(b"")
        with self.assertRaises(SquealError) as ctx:
            load(path)
        self.assertIn("empty", str(ctx.exception))


class DuplicateHeaderTests(unittest.TestCase):
    def test_duplicate_header_names_the_real_problem(self):
        from squeal.csvload import load
        from squeal.executor import execute
        from squeal.parser import parse
        import tempfile
        path = os.path.join(tempfile.mkdtemp(), "dup.csv")
        with open(path, "w") as fh:
            fh.write("a,a\n1,2\n")
        table = load(path)
        with self.assertRaises(SquealError) as ctx:
            execute(parse("select a from dup"), table)
        self.assertIn("repeats the column name", str(ctx.exception))

    def test_genuine_join_ambiguity_still_hints_qualifiers(self):
        from squeal.csvload import CsvTable
        from squeal.executor import execute
        from squeal.parser import parse
        a = CsvTable(["id"], [(1,)], [int], "a.csv")
        b = CsvTable(["id"], [(1,)], [int], "b.csv")
        tables = {"a": a, "b": b}
        with self.assertRaises(SquealError) as ctx:
            execute(parse("select id from a join b on a.id = b.id"),
                    lambda name: tables[name])
        self.assertIn("ambiguous column", str(ctx.exception))
        self.assertIn("a.id", str(ctx.exception))


class DeepNestingTests(unittest.TestCase):
    def test_deep_parens_get_a_clean_error(self):
        from squeal.parser import parse
        with self.assertRaises(SquealError) as ctx:
            parse("select " + "(" * 3000 + "1" + ")" * 3000)
        self.assertIn("nested too deeply", str(ctx.exception))
