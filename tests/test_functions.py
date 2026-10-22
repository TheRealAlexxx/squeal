import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from squeal.csvload import load
from squeal.errors import SquealError
from squeal.executor import execute
from squeal.parser import parse

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "people.csv")


def one(sql):
    return execute(parse(sql), load(FIXTURE)).rows[0][0]


class ScalarFunctionTests(unittest.TestCase):
    def test_ifnull(self):
        self.assertEqual(one("select ifnull(age, -1) from people where name = 'Katherine'"), -1)
        self.assertEqual(one("select ifnull(age, -1) from people where name = 'Ada'"), 36)

    def test_iif(self):
        self.assertEqual(one("select iif(age > 30, 'old', 'young') from people where name = 'Ada'"), "old")

    def test_instr(self):
        self.assertEqual(one("select instr(name, 'a') from people where name = 'Ada'"), 3)
        self.assertEqual(one("select instr(name, 'z') from people where name = 'Ada'"), 0)

    def test_trims(self):
        self.assertEqual(one("select ltrim('  x  ')"), "x  ")
        self.assertEqual(one("select rtrim('  x  ')"), "  x")
        self.assertEqual(one("select trim('  x  ')"), "x")

    def test_sign(self):
        self.assertEqual(one("select sign(age) from people where name = 'Ada'"), 1)
        self.assertEqual(one("select sign(0 - age) from people where name = 'Ada'"), -1)
        self.assertEqual(one("select sign(0)"), 0)

    def test_floor_ceil(self):
        self.assertEqual(one("select floor(1.7)"), 1)
        self.assertEqual(one("select ceil(1.2)"), 2)
        self.assertEqual(one("select ceiling(1.2)"), 2)

    def test_power_sqrt(self):
        self.assertEqual(one("select power(2, 10)"), 1024)
        self.assertEqual(one("select pow(3, 2)"), 9)
        self.assertEqual(one("select sqrt(16.0)"), 4.0)

    def test_sqrt_negative(self):
        with self.assertRaises(SquealError):
            one("select sqrt(-1)")

    def test_null_propagation(self):
        self.assertIsNone(one("select floor(nullif(age, age)) from people where name = 'Ada'"))
        self.assertIsNone(one("select instr(null, 'x')"))

    def test_arity_errors(self):
        with self.assertRaises(SquealError):
            one("select ifnull(1) from people")
        with self.assertRaises(SquealError):
            one("select iif(1, 2) from people")


if __name__ == "__main__":
    unittest.main()
