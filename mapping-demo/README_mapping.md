# Test dependency mapping prototype

This module implements Member 3's initial change-to-test mapping. It reuses
Member 1's `compare_sources` and pytest-testmon's block fingerprints. Keep these
files together in `mapping-demo/`:

- `explain_mapping.py`: query function-body dependencies.
- `map_changes.py`: consume Schema 2.0 changes and map baseline code to tests.
- `test_map_changes.py`: offline integration checks using temporary Git repositories
  and a SQLite fixture with the queried testmon v14 tables.

## Setup and baseline

From the repository root, using the project's Python 3.12 virtual environment:

```bat
python -m pip install -r pr-analyzer/requirements.txt
python -m pip install -e pytest-testmon
cd mapping-demo
python -m pytest --testmon -v test_payment.py
cd ..
```

Collect the database on the **base** revision before editing production code.
Keep that Git revision available locally. Database provenance is not recorded
as a Git SHA, so this is a workflow precondition, not automatically verified.
The mapper reads the database and never updates its dependency records.

## Local working-tree change

After collecting the baseline and committing it, change `withdraw` from
`round(balance - amount, 2)` to `round(balance - amount, 1)`. Before rerunning
testmon on the changed code, run from the repository root:

```bat
python mapping-demo/map_changes.py --local-file mapping-demo/payment.py --base HEAD --db mapping-demo/.testmondata --test-root mapping-demo --output mapping-demo/mapping_result.json
```

The script calls upstream `compare_sources`, reads baseline source using
`git show`, snapshots the database with SQLite backup, and reconstructs baseline
block fingerprints. The expected `selected_tests` contains three withdrawal
tests, expressed as node IDs relative to the repository root. No file fallback
is expected for this example. If the edited source has already been committed,
use the original baseline commit with `--base` instead of `HEAD`.

## Upstream PR report

```bat
python mapping-demo/map_changes.py --report pr-analyzer/output.json --db mapping-demo/.testmondata --test-root mapping-demo --output mapping-demo/mapping_result.json
```

This accepts Schema 2.0 `changes` and `repo.base_sha`; `--base` can override the
revision. The base commit and files must already be present in the local Git
repository. `--test-root` must be the pytest root that created this database,
which can differ from the Git root. A real PR must have matching baseline
testmon data for the project under analysis. A database from this payment demo
cannot supply coverage for unrelated projects such as Flask.

## Result states

- `matched`: baseline function body fingerprints have recorded test dependencies.
- `file_fallback`: signature/class change, ambiguous function, unavailable source,
  or fingerprint gaps; select all recorded tests depending on the old file.
- `needs_new_tests`: new entity with no historical baseline.
- `skipped_documentation`: documentation-only change.
- `unresolved`: missing file history, unsupported paths/languages, or other missing data.

`selection_complete: false` means the emitted test list is incomplete. An empty
list never proves that tests are unnecessary when unresolved/new-code states exist.
Even a complete mapping describes historical dependencies, not all possible
runtime impacts or a guarantee of regression safety.

Rename records query the old path/name and propose a `mapping_alias`. Persistent
rename migration is not implemented; no database rows are rewritten.

## Validation

```bat
python -m unittest discover -s mapping-demo -p test_map_changes.py -v
```

The integration checks cover changed head source with baseline mapping, database
preservation, signature fallback, documentation, new code, missing history,
rename aliases, deletion, ambiguous names, schema validation, path safety, and
unchanged code.

Ignore `.testmondata*`, `.pytest_cache/`, `*.egg-info/`, and the generated
`mapping-demo/mapping_result.json` in Git.

## Current limits

Python-only, DB v14 (bundled testmon 2.2), one test root/environment per invocation.
This prototype does not integrate Member 2's separate behavior report, reconstruct
runtime call chains, prove branch coverage, perform static cold-start mapping,
persist rename aliases, or execute the selected tests. Cold-start mapping and
persistent rename migration remain follow-up work.
