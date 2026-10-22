"""The parser: tokens in, a Select tree out, by recursive descent.

Precedence, weakest to strongest:
  OR < AND < NOT < comparisons (=, !=, <, <=, IS NULL, LIKE, IN, BETWEEN)
  < + - || < * / % < unary - + < primary
"""

from . import astnodes as ast
from .errors import SquealError
from .lexer import END, IDENT, KEYWORD, NUMBER, OP, SEMI, STRING, tokenize


class Parser:
    def __init__(self, sql):
        self.sql = sql
        self.tokens = tokenize(sql)
        self.i = 0

    @property
    def cur(self):
        return self.tokens[self.i]

    def advance(self):
        tok = self.tokens[self.i]
        if tok.kind != END:
            self.i += 1
        return tok

    def fail(self, message, tok=None):
        raise SquealError(message, (tok or self.cur).pos)

    def expect_op(self, op):
        if self.cur.kind == OP and self.cur.text == op:
            return self.advance()
        self.fail("expected '%s' but found %r" % (op, self.cur.text or "end of input"))

    def expect_kw(self, word):
        if self.cur.is_kw(word):
            return self.advance()
        self.fail("expected %s but found %r" % (word.upper(), self.cur.text or "end of input"))

    def at_kw(self, word):
        return self.cur.is_kw(word)

    def at_op(self, op):
        return self.cur.kind == OP and self.cur.text == op

    # entry point ---------------------------------------------------------

    def parse(self):
        while self.cur.kind == SEMI:
            self.advance()
        if self.at_kw("create"):
            stmt = self.parse_create_view()
            while self.cur.kind == SEMI:
                self.advance()
            if self.cur.kind != END:
                self.fail("unexpected %r after the end of the statement" % self.cur.text)
            return stmt
        if self.at_kw("drop"):
            stmt = self.parse_drop_view()
            while self.cur.kind == SEMI:
                self.advance()
            if self.cur.kind != END:
                self.fail("unexpected %r after the end of the statement" % self.cur.text)
            return stmt
        explain = False
        analyze = False
        if self.at_kw("explain"):
            self.advance()
            explain = True
            if self.at_kw("analyze"):
                self.advance()
                analyze = True
        stmt = self.parse_select()
        while self.cur.kind == SEMI:
            self.advance()
        if self.cur.kind != END:
            self.fail("unexpected %r after the end of the query" % self.cur.text)
        stmt.explain = explain
        stmt.analyze = analyze
        return stmt

    def parse_many(self):
        """A script: successive statements separated by semicolons."""
        statements = []
        while True:
            while self.cur.kind == SEMI:
                self.advance()
            if self.cur.kind == END:
                return statements
            if self.at_kw("create"):
                statements.append(self.parse_create_view())
            elif self.at_kw("drop"):
                statements.append(self.parse_drop_view())
            else:
                explain = False
                if self.at_kw("explain"):
                    self.advance()
                    explain = True
                stmt = self.parse_select()
                stmt.explain = explain
                statements.append(stmt)
            if self.cur.kind not in (SEMI, END):
                self.fail("expected ';' between statements, found %r" % self.cur.text)

    def parse_create_view(self):
        self.expect_kw("create")
        self.expect_kw("view")
        if self.cur.kind != IDENT:
            self.fail("expected a view name")
        name = self.advance().text
        self.expect_kw("as")
        return ast.CreateView(name, self.parse_select())

    def parse_drop_view(self):
        self.expect_kw("drop")
        self.expect_kw("view")
        if_exists = False
        if self.at_kw("if"):
            self.advance()
            self.expect_kw("exists")
            if_exists = True
        if self.cur.kind != IDENT:
            self.fail("expected a view name")
        return ast.DropView(self.advance().text, if_exists)

    def parse_select(self):
        self.expect_kw("select")
        distinct = False
        if self.at_kw("distinct"):
            self.advance()
            distinct = True
        items = [self.parse_select_item()]
        while self.at_op(","):
            self.advance()
            items.append(self.parse_select_item())
        source = None
        if self.at_kw("from"):
            self.advance()
            source = self.parse_source()
        where = None
        if self.at_kw("where"):
            self.advance()
            where = self.parse_expr()
        group_by = []
        if self.at_kw("group"):
            self.advance()
            self.expect_kw("by")
            group_by.append(self.parse_expr())
            while self.at_op(","):
                self.advance()
                group_by.append(self.parse_expr())
        having = None
        if self.at_kw("having"):
            self.advance()
            having = self.parse_expr()
        order_by = []
        if self.at_kw("order"):
            self.advance()
            self.expect_kw("by")
            order_by.append(self.parse_order_item())
            while self.at_op(","):
                self.advance()
                order_by.append(self.parse_order_item())
        limit = None
        if self.at_kw("limit"):
            self.advance()
            tok = self.cur
            if tok.kind != NUMBER or not isinstance(tok.value, int):
                self.fail("LIMIT needs a whole number", tok)
            self.advance()
            limit = tok.value
        offset = None
        if self.at_kw("offset"):
            self.advance()
            tok = self.cur
            if tok.kind != NUMBER or not isinstance(tok.value, int):
                self.fail("OFFSET needs a whole number", tok)
            self.advance()
            offset = tok.value
        return ast.Select(distinct, items, source, where, group_by, having,
                          order_by, limit, offset)

    def parse_select_item(self):
        if self.at_op("*"):
            self.advance()
            return (ast.Star(), None)
        expr = self.parse_expr()
        alias = None
        if self.at_kw("as"):
            self.advance()
            if self.cur.kind not in (IDENT, STRING):
                self.fail("expected an alias after AS")
            tok2 = self.advance()
            alias = tok2.value if tok2.kind == STRING else tok2.text
        elif self.cur.kind == IDENT:
            alias = self.advance().text  # bare alias: select total t
        return (expr, alias)

    def parse_source(self):
        left = self.parse_table_factor()
        while True:
            kind = None
            if self.at_kw("inner"):
                self.advance()
                self.expect_kw("join")
                kind = "inner"
            elif self.at_kw("join"):
                self.advance()
                kind = "inner"
            elif self.at_kw("left"):
                self.advance()
                if self.at_kw("outer"):
                    self.advance()
                self.expect_kw("join")
                kind = "left"
            elif self.at_kw("cross"):
                self.advance()
                self.expect_kw("join")
                kind = "cross"
            if kind is None:
                return left
            right = self.parse_table_factor()
            on = None
            if kind != "cross":
                self.expect_kw("on")
                on = self.parse_expr()
            left = ast.Join(left, right, kind, on)

    def parse_table_factor(self):
        tok = self.cur
        if tok.kind == STRING:  # from 'data.csv': a file path
            self.advance()
            name = tok.value
        elif tok.kind == IDENT:
            parts = [self.advance().text]
            while self.at_op("."):
                self.advance()
                if self.cur.kind != IDENT:
                    self.fail("expected a name after the dot")
                parts.append(self.advance().text)
            name = ".".join(parts)
        else:
            self.fail("expected a table name or a 'file.csv' after FROM")
        alias = None
        if self.at_kw("as"):
            self.advance()
            if self.cur.kind != IDENT:
                self.fail("expected an alias after AS")
            alias = self.advance().text
        elif self.cur.kind == IDENT:
            alias = self.advance().text
        return ast.TableRef(name, alias)

    def parse_order_item(self):
        expr = self.parse_expr()
        desc = False
        if self.at_kw("desc"):
            self.advance()
            desc = True
        elif self.at_kw("asc"):
            self.advance()
        return (expr, desc)

    # expressions ---------------------------------------------------------

    def parse_expr(self):
        return self.parse_or()

    def parse_or(self):
        left = self.parse_and()
        while self.at_kw("or"):
            self.advance()
            left = ast.Binary("or", left, self.parse_and())
        return left

    def parse_and(self):
        left = self.parse_not()
        while self.at_kw("and"):
            self.advance()
            left = ast.Binary("and", left, self.parse_not())
        return left

    def parse_not(self):
        if self.at_kw("not"):
            self.advance()
            return ast.Unary("not", self.parse_not())
        return self.parse_comparison()

    def parse_comparison(self):
        left = self.parse_additive()
        negated = False
        if self.at_kw("not") and self.tokens[self.i + 1].is_kw("like") or \
           self.at_kw("not") and self.tokens[self.i + 1].is_kw("in") or \
           self.at_kw("not") and self.tokens[self.i + 1].is_kw("between"):
            self.advance()
            negated = True
        if self.at_kw("is"):
            self.advance()
            is_neg = False
            if self.at_kw("not"):
                self.advance()
                is_neg = True
            self.expect_kw("null")
            return ast.IsNull(left, is_neg)
        if self.at_kw("like"):
            self.advance()
            return ast.Like(left, self.parse_additive(), negated)
        if self.at_kw("in"):
            self.advance()
            self.expect_op("(")
            if self.at_kw("select"):
                values = [ast.Subquery(self.parse_select())]
                self.expect_op(")")
                return ast.InList(left, values, negated)
            values = [self.parse_expr()]
            while self.at_op(","):
                self.advance()
                values.append(self.parse_expr())
            self.expect_op(")")
            return ast.InList(left, values, negated)
        if self.at_kw("between"):
            self.advance()
            low = self.parse_additive()
            self.expect_kw("and")
            high = self.parse_additive()
            return ast.Between(left, low, high, negated)
        if negated:
            self.fail("expected LIKE, IN or BETWEEN after NOT")
        if self.cur.kind == OP and self.cur.text in ("=", "!=", "<>", "<", "<=", ">", ">="):
            op = self.advance().text
            if op == "<>":
                op = "!="
            return ast.Binary(op, left, self.parse_additive())
        return left

    def parse_additive(self):
        left = self.parse_multiplicative()
        while self.cur.kind == OP and self.cur.text in ("+", "-", "||"):
            op = self.advance().text
            left = ast.Binary(op, left, self.parse_multiplicative())
        return left

    def parse_multiplicative(self):
        left = self.parse_unary()
        while self.cur.kind == OP and self.cur.text in ("*", "/", "%"):
            op = self.advance().text
            left = ast.Binary(op, left, self.parse_unary())
        return left

    def parse_unary(self):
        if self.cur.kind == OP and self.cur.text in ("-", "+"):
            op = self.advance().text
            return ast.Unary(op, self.parse_unary())
        return self.parse_primary()

    def parse_primary(self):
        tok = self.cur
        if tok.kind == NUMBER:
            self.advance()
            return ast.Literal(tok.value)
        if tok.kind == STRING:
            self.advance()
            return ast.Literal(tok.value)
        if tok.is_kw("null"):
            self.advance()
            return ast.Literal(None)
        if tok.is_kw("true"):
            self.advance()
            return ast.Literal(True)
        if tok.is_kw("false"):
            self.advance()
            return ast.Literal(False)
        if tok.is_kw("case"):
            return self.parse_case()
        if tok.is_kw("exists"):
            self.advance()
            self.expect_op("(")
            inner = self.parse_select()
            self.expect_op(")")
            return ast.Exists(inner)
        if self.at_op("("):
            self.advance()
            if self.at_kw("select"):
                inner = self.parse_select()
                self.expect_op(")")
                return ast.Subquery(inner)
            expr = self.parse_expr()
            self.expect_op(")")
            return expr
        if tok.kind == IDENT:
            parts = [self.advance().text]
            while self.at_op("."):
                self.advance()
                if self.cur.kind == OP and self.cur.text == "*":
                    self.fail("table.* is not supported yet, use plain *")
                if self.cur.kind != IDENT:
                    self.fail("expected a name after the dot")
                parts.append(self.advance().text)
            if self.at_op("("):
                self.advance()
                if self.at_op("*"):
                    self.advance()
                    self.expect_op(")")
                    call = ast.FuncCall(parts[0], [], star=True)
                else:
                    distinct = False
                    if self.at_kw("distinct"):
                        self.advance()
                        distinct = True
                    args = []
                    if not self.at_op(")"):
                        args.append(self.parse_expr())
                        while self.at_op(","):
                            self.advance()
                            args.append(self.parse_expr())
                    self.expect_op(")")
                    call = ast.FuncCall(parts[0], args, distinct=distinct)
                if self.at_kw("over"):
                    return self.parse_window(call)
                return call
            return ast.Column(".".join(parts))
        self.fail("expected a value but found %r" % (tok.text or "end of input"), tok)

    def parse_window(self, call):
        self.expect_kw("over")
        self.expect_op("(")
        partition_by = []
        if self.at_kw("partition"):
            self.advance()
            self.expect_kw("by")
            partition_by.append(self.parse_expr())
            while self.at_op(","):
                self.advance()
                partition_by.append(self.parse_expr())
        order_by = []
        if self.at_kw("order"):
            self.advance()
            self.expect_kw("by")
            order_by.append(self.parse_order_item())
            while self.at_op(","):
                self.advance()
                order_by.append(self.parse_order_item())
        self.expect_op(")")
        return ast.WindowCall(call, partition_by, order_by)

    def parse_case(self):
        self.expect_kw("case")
        whens = []
        while self.at_kw("when"):
            self.advance()
            cond = self.parse_expr()
            self.expect_kw("then")
            whens.append((cond, self.parse_expr()))
        if not whens:
            self.fail("CASE needs at least one WHEN")
        else_val = None
        if self.at_kw("else"):
            self.advance()
            else_val = self.parse_expr()
        self.expect_kw("end")
        return ast.Case(whens, else_val)


def parse(sql):
    try:
        return Parser(sql).parse()
    except SquealError as e:
        if not e.source:
            e.source = sql
        raise


def parse_many(sql):
    try:
        return Parser(sql).parse_many()
    except SquealError as e:
        if not e.source:
            e.source = sql
        raise
