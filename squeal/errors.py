"""Errors with a position in the SQL, printed with a caret underneath.

The lexer, parser and executor all raise SquealError. Nothing in squeal
prints a traceback at the user: a broken query gets one clear line and a
pointer at the exact character that confused us.
"""


class SquealError(Exception):
    def __init__(self, message, pos=None, source=""):
        super().__init__(message)
        self.message = message
        self.pos = pos  # (line, column) 1-based, or None
        self.source = source

    def __str__(self):
        return self.render(self.source)

    def render(self, sql=""):
        if self.pos is None or not sql:
            return "error: " + self.message
        line_no, col = self.pos
        # The lexer advances lines on "\n" only; split the same way so the
        # caret lands on the line the lexer counted. splitlines() also
        # breaks on \r, \x0b, \u2028 and friends, shifting later lines.
        lines = sql.split("\n")
        if line_no - 1 >= len(lines):
            return "error: " + self.message
        text = lines[line_no - 1].rstrip("\r")
        return "error: %s\n  %s\n  %s^" % (self.message, text, " " * (col - 1))
