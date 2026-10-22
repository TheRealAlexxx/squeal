import unittest

from squeal.errors import SquealError
from squeal.lexer import END, IDENT, KEYWORD, NUMBER, OP, STRING, tokenize


def kinds(tokens):
    return [t.kind for t in tokens]


class TestLexer(unittest.TestCase):
    def test_simple_select(self):
        toks = tokenize("SELECT name, age FROM people")
        self.assertEqual(kinds(toks), [KEYWORD, IDENT, OP, IDENT, KEYWORD, IDENT, END])
        self.assertTrue(toks[0].is_kw("select"))

    def test_keywords_case_insensitive(self):
        for w in ("select", "SELECT", "Select", "sElEcT"):
            self.assertTrue(tokenize(w)[0].is_kw("select"), w)

    def test_ident_not_keyword_prefix(self):
        toks = tokenize("selection fromage")
        self.assertEqual(toks[0].kind, IDENT)
        self.assertEqual(toks[1].kind, IDENT)

    def test_numbers(self):
        toks = tokenize("42 3.14 1e5 1_000_000 .5")
        self.assertEqual([t.value for t in toks[:-1]], [42, 3.14, 100000.0, 1000000, 0.5])
        self.assertEqual(toks[0].kind, NUMBER)

    def test_strings_with_escapes(self):
        toks = tokenize("'it''s' \"double\"")
        self.assertEqual(toks[0].value, "it's")
        self.assertEqual(toks[1].value, "double")

    def test_unterminated_string_points_at_start(self):
        with self.assertRaises(SquealError) as cm:
            tokenize("select 'oops")
        self.assertEqual(cm.exception.pos, (1, 8))

    def test_two_char_ops(self):
        toks = tokenize("a != b <> c <= d >= e || f")
        ops = [t.text for t in toks if t.kind == OP]
        self.assertEqual(ops, ["!=", "<>", "<=", ">=", "||"])

    def test_positions_track_lines(self):
        toks = tokenize("select\n  name")
        self.assertEqual(toks[1].pos, (2, 3))

    def test_comments_skipped(self):
        toks = tokenize("select -- a comment\nname")
        self.assertEqual(toks[1].text, "name")

    def test_bad_character(self):
        with self.assertRaises(SquealError):
            tokenize("select `weird`")

    def test_error_render_has_caret(self):
        err = SquealError("unexpected character: `", (2, 4))
        out = err.render("select\nabc`def")
        self.assertIn("abc`def", out)
        self.assertIn("   ^", out)


if __name__ == "__main__":
    unittest.main()
