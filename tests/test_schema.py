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
