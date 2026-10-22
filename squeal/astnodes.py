"""The expression and statement trees the parser builds.

Every node is a tiny value object. The executor walks these; the planner
(EXPLAIN) prints them. Keeping them dumb is deliberate: all the behavior
lives in the executor, all the structure lives here.
"""


class Node:
    __slots__ = ()

    def walk(self):
        """Yield this node and every descendant, depth first."""
        yield self
        for value in vars(self).values():
            if isinstance(value, Node):
                yield from value.walk()
            elif isinstance(value, (list, tuple)):
                for item in value:
                    if isinstance(item, Node):
                        yield from item.walk()
                    elif isinstance(item, (list, tuple)):
                        for sub in item:
                            if isinstance(sub, Node):
                                yield from sub.walk()


class Literal(Node):
    __slots__ = ("value",)

    def __init__(self, value):
        self.value = value

    def __repr__(self):
        return "Literal(%r)" % self.value


class Column(Node):
    __slots__ = ("name",)

    def __init__(self, name):
        self.name = name

    def __repr__(self):
        return "Column(%s)" % self.name


class Star(Node):
    __slots__ = ()

    def __repr__(self):
        return "Star()"


class Unary(Node):
    __slots__ = ("op", "expr")

    def __init__(self, op, expr):
        self.op = op
        self.expr = expr

    def __repr__(self):
        return "Unary(%s, %r)" % (self.op, self.expr)


class Binary(Node):
    __slots__ = ("op", "left", "right")

    def __init__(self, op, left, right):
        self.op = op
        self.left = left
        self.right = right

    def __repr__(self):
        return "Binary(%s, %r, %r)" % (self.op, self.left, self.right)


class IsNull(Node):
    __slots__ = ("expr", "negated")

    def __init__(self, expr, negated):
        self.expr = expr
        self.negated = negated

    def __repr__(self):
        return "IsNull(%r, %s)" % (self.expr, self.negated)


class Like(Node):
    __slots__ = ("expr", "pattern", "negated")

    def __init__(self, expr, pattern, negated):
        self.expr = expr
        self.pattern = pattern
        self.negated = negated

    def __repr__(self):
        return "Like(%r, %r, %s)" % (self.expr, self.pattern, self.negated)


class InList(Node):
    __slots__ = ("expr", "values", "negated")

    def __init__(self, expr, values, negated):
        self.expr = expr
        self.values = values
        self.negated = negated

    def __repr__(self):
        return "InList(%r, %d values, %s)" % (self.expr, len(self.values), self.negated)


class Between(Node):
    __slots__ = ("expr", "low", "high", "negated")

    def __init__(self, expr, low, high, negated):
        self.expr = expr
        self.low = low
        self.high = high
        self.negated = negated

    def __repr__(self):
        return "Between(%r, %r, %r, %s)" % (self.expr, self.low, self.high, self.negated)


class FuncCall(Node):
    __slots__ = ("name", "args", "star")

    def __init__(self, name, args, star=False):
        self.name = name.lower()
        self.args = args
        self.star = star

    def __repr__(self):
        return "FuncCall(%s, %r%s)" % (self.name, self.args, ", *" if self.star else "")


class Case(Node):
    __slots__ = ("whens", "else_val")

    def __init__(self, whens, else_val):
        self.whens = whens  # list of (condition, value)
        self.else_val = else_val

    def __repr__(self):
        return "Case(%d whens)" % len(self.whens)


class Select(Node):
    __slots__ = ("distinct", "items", "source", "where", "group_by",
                 "having", "order_by", "limit", "offset")

    def __init__(self, distinct, items, source, where, group_by, having,
                 order_by, limit, offset):
        self.distinct = distinct
        self.items = items          # [(expr, alias or None)]
        self.source = source        # table name or 'file.csv' (string keeps its literal quotes off)
        self.where = where
        self.group_by = group_by    # [expr]
        self.having = having
        self.order_by = order_by    # [(expr, descending)]
        self.limit = limit
        self.offset = offset

    def __repr__(self):
        return "Select(%d items from %s)" % (len(self.items), self.source)
