"""Errors with a position in the SQL, printed with a caret underneath.

The lexer, parser and executor all raise SquealError. Nothing in squeal
prints a traceback at the user: a broken query gets one clear line and a
pointer at the exact character that confused us.
"""


class SquealError(Exception):
    def __init__(self, message, pos=None):
        super().__init__(message)
        self.message = message
        self.pos = pos  # (line, column) 1-based, or None

    def render(self, sql=""):
        if self.pos is None or not sql:
            return "error: " + self.message
        line_no, col = self.pos
        lines = sql.splitlines()
        if line_no - 1 >= len(lines):
            return "error: " + self.message
        text = lines[line_no - 1]
        return "error: %s\n  %s\n  %s^" % (self.message, text, " " * (col - 1))
