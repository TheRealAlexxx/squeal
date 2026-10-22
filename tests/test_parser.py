import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from squeal import astnodes as ast
from squeal.errors import SquealError
from squeal.parser import parse

failures = 0

def check(name, fn):
    global failures
    try:
        fn()
        print("ok  " + name)
    except AssertionError as e:
        failures += 1
        print("FAIL %s: %s" % (name, e))

def t(name, cond, detail=""):
    assert cond, detail or "condition was false"

def test_select_star():
    q = parse("select * from people")
    t("star", isinstance(q.items[0][0], ast.Star))
    t("source", q.source.name == "people")

def test_columns_alias():
    q = parse("select name as n, age a from 'users.csv'")
    t("alias as", q.items[0][1] == "n")
    t("bare alias", q.items[1][1] == "a")
    t("csv source", q.source.name == "users.csv")

def test_distinct():
    q = parse("select distinct city from t")
    t("distinct flag", q.distinct is True)

def test_precedence():
    q = parse("select 1 + 2 * 3")
    expr = q.items[0][0]
    t("root is +", expr.op == "+")
    t("right is *", expr.right.op == "*")

def test_and_or():
    q = parse("select 1 where a or b and c")
    w = q.where
    t("root is or", w.op == "or")
    t("right is and", w.right.op == "and")

def test_not():
    q = parse("select 1 where not a")
    t("not", isinstance(q.where, ast.Unary) and q.where.op == "not")

def test_comparison_ops():
    q = parse("select 1 where a = 1 and b != 2 and c <= 3 and d >= 4")
    w = q.where
    t("nested and", w.op == "and")
    t("ge", w.right.op == ">=")

def test_is_null():
    q = parse("select 1 where a is null or b is not null")
    w = q.where
    t("is null", isinstance(w.left, ast.IsNull) and w.left.negated is False)
    t("is not null", w.right.negated is True)

def test_like():
    q = parse("select 1 where name like 'a%' and x not like 'b%'")
    t("like", isinstance(q.where.left, ast.Like))
    t("not like", q.where.right.negated is True)

def test_in():
    q = parse("select 1 where x in (1, 2, 3)")
    t("in list", isinstance(q.where, ast.InList) and len(q.where.values) == 3)

def test_between():
    q = parse("select 1 where x between 1 and 10")
    t("between", isinstance(q.where, ast.Between))

def test_group_having():
    q = parse("select city, count(*) from t group by city having count(*) > 2")
    t("group by", len(q.group_by) == 1)
    t("having", isinstance(q.having, ast.FuncCall) is False)  # comparison
    t("having op", q.having.op == ">")

def test_order_limit_offset():
    q = parse("select a from t order by a desc, b asc limit 5 offset 10")
    t("order desc", q.order_by[0][1] is True)
    t("order asc", q.order_by[1][1] is False)
    t("limit", q.limit == 5)
    t("offset", q.offset == 10)

def test_case():
    q = parse("select case when a > 1 then 'big' else 'small' end from t")
    t("case", isinstance(q.items[0][0], ast.Case))
    t("else", q.items[0][0].else_val.value == "small")

def test_func_args():
    q = parse("select upper(name), count(*) from t")
    t("upper", q.items[0][0].name == "upper" and len(q.items[0][0].args) == 1)
    t("count star", q.items[1][0].star is True)

def test_concat():
    q = parse("select a || b from t")
    t("concat", q.items[0][0].op == "||")

def test_error_message():
    try:
        parse("select from")
        t("should raise", False)
    except SquealError as e:
        t("has caret", "^" in str(e))

def test_trailing_junk():
    try:
        parse("select 1 2")
        t("should raise", False)
    except SquealError:
        pass

test_select_star(); test_columns_alias(); test_distinct(); test_precedence()
test_and_or(); test_not(); test_comparison_ops(); test_is_null(); test_like()
test_in(); test_between(); test_group_having(); test_order_limit_offset()
test_case(); test_func_args(); test_concat(); test_error_message(); test_trailing_junk()
print()
if failures:
    print("%d FAILURE(S)" % failures)
    sys.exit(1)
print("all parser tests passed")


def test_table_alias():
    q = parse("select o.id from orders as o")
    t("alias", q.source.alias == "o")
    q = parse("select o.id from orders o")
    t("bare alias", q.source.alias == "o")
    q = parse("select id from orders")
    t("no alias", q.source.alias is None and q.source.name == "orders")

def test_join_syntax():
    q = parse("select 1 from a join b on a.x = b.x")
    t("join", isinstance(q.source, ast.Join) and q.source.kind == "inner")
    q = parse("select 1 from a inner join b on a.x = b.x")
    t("inner", q.source.kind == "inner")
    q = parse("select 1 from a left join b on a.x = b.x")
    t("left", q.source.kind == "left")
    q = parse("select 1 from a left outer join b on a.x = b.x")
    t("left outer", q.source.kind == "left")

def test_join_chains_left_assoc():
    q = parse("select 1 from a join b on a.x = b.x join c on b.y = c.y")
    t("chain", q.source.right.name == "c" and isinstance(q.source.left, ast.Join))

def test_cross_join():
    q = parse("select 1 from a cross join b")
    t("cross", q.source.kind == "cross" and q.source.on is None)

def test_join_requires_on():
    try:
        parse("select 1 from a join b")
        t("should raise", False)
    except SquealError:
        pass

test_table_alias(); test_join_syntax(); test_join_chains_left_assoc()
test_cross_join(); test_join_requires_on()
print("join parser tests passed")


def test_window_parse():
    q = parse("select row_number() over (partition by city order by age desc) from people")
    w = q.items[0][0]
    t("window", isinstance(w, ast.WindowCall))
    t("window fn", w.call.name == "row_number")
    t("partition", len(w.partition_by) == 1)
    t("order", w.order_by[0][1] is True)

def test_window_empty_over():
    q = parse("select sum(score) over () from people")
    w = q.items[0][0]
    t("empty over", w.partition_by == [] and w.order_by == [])

def test_window_order_only():
    q = parse("select rank() over (order by score) from people")
    t("order only", len(q.items[0][0].order_by) == 1)

test_window_parse(); test_window_empty_over(); test_window_order_only()
print("window parser tests passed")
