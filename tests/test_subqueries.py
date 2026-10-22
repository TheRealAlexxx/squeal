import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from squeal.csvload import load
from squeal.errors import SquealError
from squeal.executor import execute
from squeal.parser import parse

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


def resolver(name):
    path = name if os.path.exists(name) else os.path.join(FIXTURES, name + ".csv")
    return load(path)


def run(sql):
    return execute(parse(sql), resolver)


class InSubqueryTests(unittest.TestCase):
    def test_in_subquery(self):
        r = run("select name from customers where id in "
                "(select customer_id from purchases) order by name")
        self.assertEqual([row[0] for row in r.rows], ["Ada", "Alan", "Grace"])

    def test_not_in_subquery(self):
        r = run("select name from customers where id not in "
                "(select customer_id from purchases)")
        self.assertEqual(r.rows, [("Edsger",)])

    def test_in_subquery_with_filter(self):
        r = run("select name from customers where id in "
                "(select customer_id from purchases where amount > 100)")
        self.assertEqual(r.rows, [("Alan",)])

    def test_in_subquery_wrong_columns(self):
        with self.assertRaises(SquealError):
            run("select name from customers where id in (select * from purchases)")

    def test_in_subquery_aggregated(self):
        r = run("select name from customers where id in "
                "(select customer_id from purchases group by customer_id "
                "having sum(amount) > 100)")
        self.assertEqual(r.rows, [("Ada",), ("Alan",)])


class ScalarSubqueryTests(unittest.TestCase):
    def test_scalar_in_select(self):
        r = run("select name, (select count(*) from purchases) as total "
                "from customers where id = 1")
        self.assertEqual(r.rows, [("Ada", 5)])

    def test_scalar_in_where(self):
        r = run("select item from purchases where amount = (select max(amount) from purchases)")
        self.assertEqual(r.rows, [("laptop",)])

    def test_scalar_empty_is_null(self):
        r = run("select (select item from purchases where id = 999) from customers where id = 1")
        self.assertEqual(r.rows, [(None,)])

    def test_scalar_too_many_rows(self):
        with self.assertRaises(SquealError):
            run("select (select item from purchases) from customers where id = 1")

    def test_scalar_wrong_columns(self):
        with self.assertRaises(SquealError):
            run("select (select item, amount from purchases where id = 101) from customers where id = 1")


class ExistsTests(unittest.TestCase):
    def test_exists(self):
        r = run("select count(*) from customers where "
                "exists (select item from purchases)")
        self.assertEqual(r.rows[0][0], 4)

    def test_not_exists(self):
        r = run("select count(*) from customers where "
                "not exists (select item from purchases where id = 999)")
        self.assertEqual(r.rows[0][0], 4)

    def test_exists_false(self):
        r = run("select count(*) from customers where "
                "exists (select item from purchases where id = 999)")
        self.assertEqual(r.rows[0][0], 0)


if __name__ == "__main__":
    unittest.main()
