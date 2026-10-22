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
    return load(os.path.join(FIXTURES, name + ".csv"))


def run(sql):
    return execute(parse(sql), resolver)


class JoinTests(unittest.TestCase):
    def test_inner_join(self):
        r = run("select customers.name, purchases.item "
                "from customers join purchases on customers.id = purchases.customer_id "
                "order by purchases.id")
        self.assertEqual([row[0] for row in r.rows],
                         ["Ada", "Ada", "Alan", "Grace"])

    def test_join_filters(self):
        # purchase 104 belongs to customer 9, who does not exist: dropped
        r = run("select count(*) from customers join purchases "
                "on customers.id = purchases.customer_id")
        self.assertEqual(r.rows[0][0], 4)

    def test_left_join_keeps_unmatched(self):
        r = run("select customers.name, purchases.item "
                "from customers left join purchases on customers.id = purchases.customer_id "
                "order by customers.id, purchases.id")
        self.assertEqual(r.rows[-1], ("Edsger", None))

    def test_left_join_null_side(self):
        r = run("select count(*) from customers left join purchases "
                "on customers.id = purchases.customer_id where purchases.item is null")
        self.assertEqual(r.rows[0][0], 1)

    def test_qualified_columns(self):
        r = run("select customers.id, purchases.id from customers "
                "join purchases on customers.id = purchases.customer_id "
                "order by purchases.id limit 1")
        self.assertEqual(r.rows[0], (1, 101))

    def test_ambiguous_column(self):
        with self.assertRaises(SquealError) as ctx:
            run("select id from customers join purchases "
                "on customers.id = purchases.customer_id")
        self.assertIn("ambiguous", str(ctx.exception))

    def test_table_alias(self):
        r = run("select c.name, p.item from customers c join purchases p "
                "on c.id = p.customer_id where p.amount > 100 order by p.id")
        self.assertEqual(r.rows, [("Alan", "monitor")])

    def test_join_with_aggregation(self):
        r = run("select customers.city, sum(purchases.amount) as total "
                "from customers join purchases on customers.id = purchases.customer_id "
                "group by customers.city order by total desc")
        self.assertEqual(r.rows[0], ("London", 453))

    def test_cross_join(self):
        r = run("select count(*) from customers cross join purchases")
        self.assertEqual(r.rows[0][0], 20)

    def test_star_over_join(self):
        r = run("select * from customers join purchases "
                "on customers.id = purchases.customer_id order by purchases.id limit 1")
        self.assertEqual(r.columns, ["id", "name", "city", "id", "customer_id", "item", "amount"])
        self.assertEqual(r.rows[0], (1, "Ada", "London", 101, 1, "keyboard", 79))

    def test_self_join(self):
        r = run("select a.name, b.name from customers a join customers b "
                "on a.city = b.city and a.id < b.id")
        self.assertEqual(r.rows, [("Ada", "Alan")])

    def test_join_on_expression(self):
        r = run("select count(*) from customers join purchases "
                "on purchases.customer_id = customers.id and purchases.amount > 20")
        self.assertEqual(r.rows[0][0], 3)


if __name__ == "__main__":
    unittest.main()


class JoinStrategyTests(unittest.TestCase):
    def test_equijoin_uses_hash(self):
        from squeal.executor import scan_schema, join_strategy
        q = parse("select 1 from customers c join purchases p on c.id = p.customer_id")
        strategy = join_strategy(q.source, scan_schema(q.source.left, resolver),
                                 scan_schema(q.source.right, resolver))
        self.assertEqual(strategy, "hash")

    def test_non_equality_falls_back(self):
        from squeal.executor import scan_schema, join_strategy
        q = parse("select 1 from customers c join purchases p on c.id < p.customer_id")
        strategy = join_strategy(q.source, scan_schema(q.source.left, resolver),
                                 scan_schema(q.source.right, resolver))
        self.assertEqual(strategy, "nested loop")

    def test_anded_condition_falls_back(self):
        from squeal.executor import scan_schema, join_strategy
        q = parse("select 1 from customers c join purchases p "
                  "on c.id = p.customer_id and p.amount > 10")
        strategy = join_strategy(q.source, scan_schema(q.source.left, resolver),
                                 scan_schema(q.source.right, resolver))
        self.assertEqual(strategy, "nested loop")

    def test_hash_join_null_keys_never_match(self):
        import tempfile
        path = os.path.join(tempfile.mkdtemp(), "ghost.csv")
        with open(path, "w") as fh:
            fh.write("customer_id,item\n,keyboard\n")
        r = execute(parse("select count(*) from customers join '%s' g "
                          "on customers.id = g.customer_id" % path), resolver)
        self.assertEqual(r.rows[0][0], 0)

    def test_hash_join_matches_nested_loop(self):
        sql = "select c.name, p.item from customers c join purchases p " \
              "on c.id = p.customer_id order by p.id"
        r = run(sql)
        self.assertEqual([row[1] for row in r.rows],
                         ["keyboard", "mouse", "monitor", "cable"])

