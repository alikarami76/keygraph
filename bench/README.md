# bench

Measurement scripts for keygraph. Not part of the package (the sdist include list in `pyproject.toml` leaves `bench/` out), stdlib only.

## scripts/fk_recovery.py

Measures how many declared foreign keys keygraph recovers without them. For each SQLite database it reads the declared FKs (`PRAGMA foreign_key_list`; one pair per column for multi-column FKs), writes a schema-only copy (same columns, types, primary keys, UNIQUE constraints and indexes; no FKs, no rows), runs `keygraph scan` on the copy and compares single-column inferred edges with the stripped FKs. A hit is an inferred edge joining the same two table.column pairs, in either direction, case-insensitive.

```
python3 -I bench/scripts/fk_recovery.py --db-root ../.bench-data/bird-minidev/minidev/MINIDEV/dev_databases \
  --glob '*/*.sqlite' --work /tmp/fkwork --keygraph 'uv run keygraph' \
  --out bench/results/fk-recovery/bird-LABEL.json --md bench/results/fk-recovery/bird-LABEL.md --label 'bird mini-dev (LABEL)'
```

`--keygraph` is a command string (`uv run keygraph`, `.venv/bin/keygraph`). Output records recall and precision at confidence 0.8 and 0.6, the misses, the extra edges with confidence and first evidence line, the keygraph version, the git sha, the SHA-256 of every database file and the date.

Precision is a lower bound: benchmarks do not declare every real relation, so a correct inferred edge that was never declared counts as an extra.

## FK recovery

Question: with foreign keys removed, how many declared keys does keygraph infer again?

**Datasets** (never committed; fetch them yourself).

| Dataset | Archive | SHA-256 |
|---|---|---|
| BIRD mini-dev (11 SQLite databases, 104 declared FKs) | `minidev.zip` | `cc48ba16838204e4e214512030cb572eeb5f7bcdd999bae4b9b6ff12ec13b92f` |
| Spider2-Lite (30 SQLite databases, 178 declared FKs) | `local_sqlite.zip` | `d56acf7c9d89be4bdf1f4f1281f4a03d91735f1858f6d6d0cfe0f9d562e3a94f` |

**Method.** For each database the script reads the declared FKs, writes a schema-only copy without foreign keys or rows (columns, types, primary keys, UNIQUE constraints and indexes stay), runs `keygraph scan` on the copy and compares the single-column inferred edges with the stripped FKs. An edge matches when it joins the same two table.column pairs in either direction. Recall is matched declared FKs over declared FKs; precision is matched inferred edges over inferred edges, at confidence >= 0.8 and >= 0.6.

**Precision is a lower bound.** These databases do not declare every real relation, so a correct inferred edge that was never declared counts as wrong.

**Reproduce.** Run from the repository root; `$DATA` is any folder for the datasets (here `../.bench-data`).

```
DATA=../.bench-data
sh bench/scripts/fetch_bird_minidev.sh $DATA/bird-minidev   # downloads, checks the SHA-256, unzips
python3 -I bench/scripts/fk_recovery.py --db-root $DATA/bird-minidev/minidev/MINIDEV/dev_databases \
  --glob '*/*.sqlite' --work /tmp/fkwork-bird --keygraph 'uv run keygraph' \
  --out bench/results/fk-recovery/bird-LABEL.json --md bench/results/fk-recovery/bird-LABEL.md \
  --label 'bird mini-dev (LABEL)'
# Spider2-Lite: unzip local_sqlite.zip (SHA-256 above) so that the databases are $DATA/spider2-lite/dbs/*.sqlite
python3 -I bench/scripts/fk_recovery.py --db-root $DATA/spider2-lite/dbs --glob '*.sqlite' \
  --work /tmp/fkwork-spider2 --keygraph 'uv run keygraph' \
  --out bench/results/fk-recovery/spider2-LABEL.json --md bench/results/fk-recovery/spider2-LABEL.md \
  --label 'spider2-lite (LABEL)'
```

Replace `LABEL` with the measured git sha. Flags: `--db-root` (folder with the databases), `--glob` (default `*/*.sqlite`), `--work` (scratch folder for the copies), `--keygraph` (command string), `--out` (JSON), `--md` (optional Markdown), `--label`, `--sha` (git sha to record; default is HEAD of the current folder). Raw results are in `bench/results/fk-recovery/`.

**Results.**

| Dataset | Version | Recall / precision at >= 0.8 | Recall / precision at >= 0.6 |
|---|---|---|---|
| BIRD, 11 DBs, 104 FKs | 0.3.3 (5530560) | 48.1% / 92.6% | 52.9% / 91.7% |
| BIRD, 11 DBs, 104 FKs | this branch (97ab6cd) | 54.8% / 93.4% | 65.4% / 93.2% |
| Spider2-Lite, 30 DBs, 178 FKs | before (5530560) | 68.0% / 50.2% | 69.7% / 50.4% |
| Spider2-Lite, 30 DBs, 178 FKs | after (97ab6cd) | 68.0% / 50.2% | 73.0% / 49.1% |

Spider2-Lite is not a clean held-out set: its extra edges were inspected while the rules were tuned. Of the 13 new extra edges after the change, 12 look like real undeclared relations on a hand check, so the precision drop there understates their quality, but that is a judgement, not a measurement.

**Remaining BIRD misses** (at >= 0.6). `california_schools`: `CDSCode` is joined to `cds` and `cdscode` columns of another table that are not key-shaped by name. `card_games`: `cards.uuid` in three tables is a merely unique column, which the rules exclude on purpose, and `set_translations.setCode` does not reach `sets.code`. `european_football_2`: the 22 numbered `home_player_N` and `away_player_N` columns stay ambiguous at 0.55, below the 0.6 floor, and the team API ids and `team_fifa_api_id` are missed. A few single misses remain in `student_club` (`zip`), `superhero` (three `hero_id` and `power_id` links) and `thrombosis_prediction` (`laboratory.id` to `patient.id`). No new rules are planned for these.

## scripts/fetch_bird_minidev.sh

`sh bench/scripts/fetch_bird_minidev.sh [DIR]` downloads the BIRD mini-dev zip into DIR (default `../.bench-data/bird-minidev`), records its SHA-256 and unzips. It skips the download when the zip is already there.

## Data and licenses

Datasets are never committed. BIRD is CC BY-SA 4.0; Spider2 is MIT.
