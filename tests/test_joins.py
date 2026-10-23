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

    def test_anded_equijoin_hashes_with_residual(self):
        from squeal.executor import scan_schema, join_strategy
        q = parse("select 1 from customers c join purchases p "
                  "on c.id = p.customer_id and p.amount > 10")
        strategy = join_strategy(q.source, scan_schema(q.source.left, resolver),
                                 scan_schema(q.source.right, resolver))
        self.assertEqual(strategy, "hash")

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



class OuterJoinTests(unittest.TestCase):
    def test_right_join(self):
        r = run("select c.name, p.item from customers c "
                "right join purchases p on c.id = p.customer_id order by p.id")
        self.assertEqual(len(r.rows), 5)
        self.assertEqual(r.rows[3], (None, "laptop"))  # customer 9 is missing

    def test_full_join(self):
        r = run("select c.name, p.item from customers c "
                "full join purchases p on c.id = p.customer_id")
        self.assertEqual(len(r.rows), 6)  # 4 matched + Edsger + the orphan laptop

    def test_full_join_null_sides(self):
        r = run("select count(*) from customers c full join purchases p "
                "on c.id = p.customer_id where c.name is null or p.item is null")
        self.assertEqual(r.rows[0][0], 2)

    def test_right_join_aggregated(self):
        r = run("select count(c.name) from customers c "
                "right join purchases p on c.id = p.customer_id")
        self.assertEqual(r.rows[0][0], 4)  # NULL name from the orphan row not counted

    def test_outer_keyword_optional(self):
        r = run("select count(*) from customers c full outer join purchases p "
                "on c.id = p.customer_id")
        self.assertEqual(r.rows[0][0], 6)
        r = run("select count(*) from customers c right outer join purchases p "
                "on c.id = p.customer_id")
        self.assertEqual(r.rows[0][0], 5)



class MultiKeyJoinTests(unittest.TestCase):
    def test_residual_filter_applies(self):
        # same rows as the nested-loop version: the amount condition must hold
        r = run("select c.name, p.item from customers c join purchases p "
                "on c.id = p.customer_id and p.amount > 20 order by p.id")
        self.assertEqual(r.rows, [("Ada", "keyboard"), ("Ada", "mouse"),
                                  ("Alan", "monitor")])

    def test_residual_no_match_left_pads(self):
        r = run("select count(*) from customers c left join purchases p "
                "on c.id = p.customer_id and p.amount > 1000")
        self.assertEqual(r.rows[0][0], 4)  # every customer kept, mostly padded

    def test_multi_column_key(self):
        import tempfile
        path = os.path.join(tempfile.mkdtemp(), "prices.csv")
        with open(path, "w") as fh:
            fh.write("item,city,price\nkeyboard,London,79\nkeyboard,New York,85\n")
        r = execute(parse("select p.item, x.price from purchases p join '%s' x "
                          "on p.item = x.item and x.city = 'London'" % path),
                    resolver)
        # one keyboard purchase, matched to the London price only (not the 85 one)
        self.assertEqual(r.rows, [("keyboard", 79)])

    def test_multi_key_null_component_never_matches(self):
        import tempfile
        path = os.path.join(tempfile.mkdtemp(), "g2.csv")
        with open(path, "w") as fh:
            fh.write("customer_id,amount,item\n1,,ghost\n")
        r = execute(parse("select count(*) from purchases p join '%s' g "
                          "on p.customer_id = g.customer_id and p.amount = g.amount"
                          % path), resolver)
        self.assertEqual(r.rows[0][0], 0)

