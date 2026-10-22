"""The tokenizer: SQL text in, positioned tokens out.

Every token knows its line and column so errors later can point at the
exact character. Keywords are matched case insensitively but keep their
original spelling for error messages.
"""

from .errors import SquealError

KEYWORDS = {
    "select", "from", "where", "group", "by", "having", "order", "limit",
    "offset", "as", "and", "or", "not", "null", "true", "false", "asc",
    "desc", "distinct", "case", "when", "then", "else", "end", "in", "is",
    "like", "between",
    "explain", "join", "inner", "left", "outer", "on", "cross", "exists",
    "over", "partition", "analyze",
}

TWO_CHAR_OPS = ("!=", "<>", "<=", ">=", "||")
ONE_CHAR = set("(),.*+-/%=<>")

IDENT = 0
NUMBER = 1
STRING = 2
KEYWORD = 3
OP = 4
END = 5
SEMI = 6

NAMES = {IDENT: "identifier", NUMBER: "number", STRING: "string",
         KEYWORD: "keyword", OP: "operator", END: "end of input",
         SEMI: "semicolon"}


class Token:
    __slots__ = ("kind", "text", "value", "line", "col")

    def __init__(self, kind, text, value, line, col):
        self.kind = kind
        self.text = text
        self.value = value
        self.line = line
        self.col = col

    @property
    def pos(self):
        return (self.line, self.col)

    def is_kw(self, word):
        return self.kind == KEYWORD and self.text.lower() == word

    def __repr__(self):
        return "Token(%s, %r)" % (NAMES[self.kind], self.text)


def tokenize(sql):
    tokens = []
    i, line, line_start = 0, 1, 0
    n = len(sql)

    def col():
        return i - line_start + 1

    while i < n:
        ch = sql[i]
        if ch == "\n":
            line += 1
            line_start = i + 1
            i += 1
            continue
        if ch in " \t\r":
            i += 1
            continue
        if sql.startswith("--", i):
            j = sql.find("\n", i)
            i = n if j < 0 else j
            continue
        start_line, start_col = line, col()
        if ch.isalpha() or ch == "_":
            j = i + 1
            while j < n and (sql[j].isalnum() or sql[j] == "_"):
                j += 1
            word = sql[i:j]
            kind = KEYWORD if word.lower() in KEYWORDS else IDENT
            tokens.append(Token(kind, word, word, start_line, start_col))
            i = j
            continue
        if ch.isdigit() or (ch == "." and i + 1 < n and sql[i + 1].isdigit()):
            j = i
            seen_dot = False
            while j < n and (sql[j].isdigit() or sql[j] == "_" or (sql[j] == "." and not seen_dot)):
                seen_dot = seen_dot or sql[j] == "."
                j += 1
            if j < n and sql[j] in "eE":
                k = j + 1
                if k < n and sql[k] in "+-":
                    k += 1
                if k < n and sql[k].isdigit():
                    j = k
                    while j < n and sql[j].isdigit():
                        j += 1
            raw = sql[i:j].replace("_", "")
            try:
                value = float(raw) if ("." in raw or "e" in raw.lower()) else int(raw)
            except ValueError:
                raise SquealError("bad number: " + sql[i:j], (start_line, start_col))
            tokens.append(Token(NUMBER, sql[i:j], value, start_line, start_col))
            i = j
            continue
        if ch in "'\"":
            quote = ch
            j = i + 1
            parts = []
            while True:
                if j >= n:
                    raise SquealError("unterminated string", (start_line, start_col))
                if sql[j] == quote:
                    if j + 1 < n and sql[j + 1] == quote:
                        parts.append(quote)
                        j += 2
                        continue
                    break
                if sql[j] == "\n":
                    line += 1
                    line_start = j + 1
                parts.append(sql[j])
                j += 1
            tokens.append(Token(STRING, sql[i:j + 1], "".join(parts), start_line, start_col))
            i = j + 1
            continue
        two = sql[i:i + 2]
        if two in TWO_CHAR_OPS:
            tokens.append(Token(OP, two, two, start_line, start_col))
            i += 2
            continue
        if ch == ";":
            tokens.append(Token(SEMI, ch, None, start_line, start_col))
            i += 1
            continue
        if ch in ONE_CHAR:
            tokens.append(Token(OP, ch, ch, start_line, start_col))
            i += 1
            continue
        raise SquealError("unexpected character: " + ch, (start_line, start_col))
    tokens.append(Token(END, "", None, line, col()))
    return tokens
