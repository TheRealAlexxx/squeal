# sqllogictest subset

squeal's published correctness corpus, in the sqllogictest format
(subset): `query <types> <sortmode>` records and `statement error`
records. Values render the sqllogictest way: `NULL`, `%.3f` floats,
one row per line, values joined by single spaces. `rowsort` sorts both
sides before comparing; `nosort` compares in order.

- Feature files (`select_basic.test`, `windows.test`, ...): expected
  results were verified against SQLite at generation time by
  `tests/sqllogictest_generate.py`, which keeps only byte-exact
  agreements between the two engines over the shared fixtures in
  `tests/fixtures`. Curated queries live in
  `tests/sqllogictest_queries.py`.
- `pinned.test`: deliberate dialect differences and documented limits,
  hand-verified against `docs/sql-reference.md`, each with its reason.
  Expected values here come from squeal itself.

Run it: `python3 tests/run_sqllogictest.py` (exit 1 on any mismatch),
or as part of the unittest suite (`tests/test_sqllogictest.py`), which
also asserts the README's published record count matches reality.

Regenerate after changing the engine: `python3
tests/sqllogictest_generate.py`, then review the diff line by line -
regeneration makes squeal's CURRENT output the oracle-verified claim
only where SQLite agrees, so any change in the diff is either a real
behavior change or a new divergence to pin.
