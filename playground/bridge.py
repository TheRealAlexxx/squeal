"""Browser bridge: the small API the playground's JavaScript calls.

Pyodide loads this file into its filesystem next to the squeal package.
Every function returns a JSON string so no Python proxy objects leak
into JavaScript. The Node smoke test (playground/test_bridge.mjs) runs
this exact file the same way the browser does.
"""

import base64
import json
import os
import sys
import time

sys.path.insert(0, "/")

from squeal import VERSION
from squeal.cli import Session
from squeal.csvload import load
from squeal.errors import SquealError

SESSION = Session()
os.makedirs("/data", exist_ok=True)


def mount_csv(name, text):
    """Load trusted CSV text (the bundled samples) under a table name."""
    path = "/data/" + name + ".csv"
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return _mount(name, path)


def mount_csv_bytes(name, data_b64):
    """Load an uploaded CSV, keeping its raw bytes.

    Uploads cross as base64 so the original encoding reaches the
    loader: a UTF-16 file fails with the loader's clean error instead
    of being silently re-decoded by JavaScript.
    """
    path = "/data/" + name + ".csv"
    with open(path, "wb") as fh:
        fh.write(base64.b64decode(data_b64))
    return _mount(name, path)


def _mount(name, path):
    try:
        table = load(path)
    except SquealError as exc:
        return json.dumps({"ok": False, "error": exc.message})
    SESSION.tables[name] = table
    SESSION.views.pop(name, None)
    out = json.loads(describe())
    out["ok"] = True
    return json.dumps(out)


def drop_table(name):
    SESSION.tables.pop(name, None)
    return describe()


def describe():
    """JSON: engine version, mounted tables with shape, view names."""
    tables = [{"name": name, "columns": list(table.columns), "rows": len(table.rows)}
              for name, table in sorted(SESSION.tables.items())]
    return json.dumps({"version": VERSION, "tables": tables,
                       "views": sorted(SESSION.views)})


def run_sql(sql):
    """Run one statement. JSON: rows+columns, a message, or an error
    with the caret-rendered form the CLI prints."""
    start = time.perf_counter()
    try:
        result = SESSION.run(sql)
    except SquealError as exc:
        return json.dumps({"ok": False, "error": exc.message,
                           "rendered": exc.render(sql),
                           "pos": list(exc.pos) if exc.pos else None})
    except Exception as exc:  # a bug in squeal itself; show it plainly
        return json.dumps({"ok": False, "error": str(exc)})
    ms = round((time.perf_counter() - start) * 1000, 1)
    if isinstance(result, str):  # EXPLAIN output, view created/dropped
        return json.dumps({"ok": True, "message": result, "ms": ms})
    return json.dumps({"ok": True, "columns": result.columns,
                       "rows": [list(row) for row in result.rows], "ms": ms})
