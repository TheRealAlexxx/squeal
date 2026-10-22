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

