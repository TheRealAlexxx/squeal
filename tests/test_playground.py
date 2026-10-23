"""Guards for the browser playground's contract with the engine.

The playground loads squeal's modules straight from the repo and drives
them through playground/bridge.py. These tests keep the JavaScript's
hardcoded module list honest and the bridge importable without a
browser. playground/test_bridge.mjs runs the same bridge under real
Pyodide; run it before shipping playground changes.
"""

import os
import re
import sys
import unittest

ROOT = os.path.join(os.path.dirname(__file__), "..")
PLAYGROUND = os.path.join(ROOT, "playground")


class PlaygroundContractTests(unittest.TestCase):
    def test_engine_file_list_matches_the_package(self):
        js = open(os.path.join(PLAYGROUND, "playground.js")).read()
        match = re.search(r"ENGINE_FILES\s*=\s*\[(.*?)\]", js, re.S)
        self.assertIsNotNone(match, "ENGINE_FILES array not found in playground.js")
        listed = sorted(re.findall(r'"([^"]+\.py)"', match.group(1)))
        on_disk = sorted(f for f in os.listdir(os.path.join(ROOT, "squeal"))
                         if f.endswith(".py"))
        self.assertEqual(listed, on_disk,
                         "playground.js ENGINE_FILES drifted from squeal/*.py")

    def test_bridge_compiles(self):
        import py_compile
        py_compile.compile(os.path.join(PLAYGROUND, "bridge.py"), doraise=True)

    def test_referenced_files_exist(self):
        js = open(os.path.join(PLAYGROUND, "playground.js")).read()
        for path in re.findall(r'"(data/[^"]+\.csv)"', js):
            self.assertTrue(os.path.exists(os.path.join(PLAYGROUND, path)),
                            path + " referenced by playground.js is missing")
        html = open(os.path.join(PLAYGROUND, "index.html")).read()
        for asset in ("playground.js", "playground.css", "bridge.py"):
            self.assertTrue(os.path.exists(os.path.join(PLAYGROUND, asset)),
                            asset + " missing")
        self.assertIn("playground.js", html)
        self.assertIn("playground.css", html)


if __name__ == "__main__":
    unittest.main()
