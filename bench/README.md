# bench

Measurement scripts for keygraph. Not part of the package (the sdist include list in `pyproject.toml` leaves `bench/` out), stdlib only.

## scripts/fk_recovery.py

Measures how many declared foreign keys keygraph recovers without them. For each SQLite database it reads the declared FKs (`PRAGMA foreign_key_list`; one pair per column for multi-column FKs), writes a schema-only copy (same columns, types, primary keys, UNIQUE constraints and indexes; no FKs, no rows), runs `keygraph scan` on the copy and compares single-column inferred edges with the stripped FKs. A hit is an inferred edge joining the same two table.column pairs, in either direction, case-insensitive.

```
python3 -I bench/scripts/fk_recovery.py --db-root ../.bench-data/bird-minidev \
  --glob '*/*.sqlite' --work /tmp/fkwork --keygraph 'uv run keygraph' \
  --out bench/results/fk_recovery-0.3.3.json --md bench/results/fk_recovery-0.3.3.md --label 'main 0.3.3'
```

`--keygraph` is a command string (`uv run keygraph`, `.venv/bin/keygraph`). Output records recall and precision at confidence 0.8 and 0.6, the misses, the extra edges with confidence and first evidence line, the keygraph version, the git sha, the SHA-256 of every database file and the date.

Precision is a lower bound: benchmarks do not declare every real relation, so a correct inferred edge that was never declared counts as an extra.

## scripts/fetch_bird_minidev.sh

`sh bench/scripts/fetch_bird_minidev.sh [DIR]` downloads the BIRD mini-dev zip into DIR (default `../.bench-data/bird-minidev`), records its SHA-256 and unzips. It skips the download when the zip is already there.

## Data and licenses

Datasets are never committed. BIRD is CC BY-SA 4.0; Spider2 is MIT.
