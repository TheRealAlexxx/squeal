import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from squeal.executor import Result
from squeal.render import as_csv, as_json, as_table

RESULT = Result(["name", "note"], [("Ada", "clear the \x1b[2J screen"),
                                    ("Bob", "bell\aand\ttab")])


class RenderTests(unittest.TestCase):
    def test_table_escapes_control_characters(self):
        out = as_table(RESULT)
        self.assertNotIn("\x1b", out)
        self.assertNotIn("\a", out)
        self.assertNotIn("\t", out)
        self.assertIn("\\x1b", out)
        self.assertIn("clear the \\x1b[2J screen", out)

    def test_table_escapes_headers(self):
        evil = Result(["na\x1b]8;;http://x\x07me"], [("v",)])
        self.assertNotIn("\x1b", as_table(evil))

    def test_csv_and_json_stay_raw(self):
        # machine-readable formats feed other programs; escaping there
        # would corrupt the data
        self.assertIn("\x1b", as_csv(RESULT))
        import json
        parsed = json.loads(as_json(RESULT))
        self.assertEqual(parsed[0]["note"], "clear the \x1b[2J screen")


if __name__ == "__main__":
    unittest.main()
