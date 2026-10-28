# pr-analyzer — PR / git Change Analyzer

Change-analysis module for CS5351-TestPilot (Member 1). It takes a PR (or a local git diff) and
returns structured JSON that the downstream **test dependency mapping** module (built on
`pytest-testmon`) consumes directly to answer "which tests must run for this change".

> The PR entity analyzer is deterministic tree-sitter AST analysis. The optional Member 2
> behavioral explanation adapter is separate, disabled by default, and never required for PR analysis.

## Layout

| File | Purpose |
| --- | --- |
| `main.py` | CLI: argument parsing, exit codes, file output, offline demos, `--analyze` PR pipeline |
| `analyzer.py` | Core logic: diff parsing, noise filtering, GitHub fetching, **tree-sitter AST entity extraction, old/new comparison and the `analyze_pr` pipeline** (**the module docstring at the top documents the full interface contract — read it first**) |
| `behavioral_change_detector.py` | Optional Python-only behavioral analysis with `ast`: branches, comparison boundaries, exceptions, returns, test suggestions, and optional LLM explanations |
| `requirements.txt` | Dependencies |
| `requirements-dev.txt` | Test dependencies (`pytest`) |
| `.env.example` | Environment variable example (`GITHUB_TOKEN` and optional behavior LLM settings) |
| `tests/test_ast_entities.py` | Offline tests for entity extraction and fingerprint behaviour (no network) |
| `tests/test_compare_entities.py` | Offline tests for old/new comparison and `change_category` |
| `tests/test_diff_lines.py` | Offline tests for per-line numbers inside a hunk |
| `tests/test_pr_sources.py` | Offline tests for the PR source mirror (fake session, no network) |
| `tests/test_analyze_pr.py` | Offline tests for the end-to-end pipeline and rename detection (fake session) |
| `tests/test_behavioral_change_detector.py` | Tests for behavior changes, output schema, test suggestions, syntax errors, and LLM fallback |

## Install

```bash
cd pr-analyzer
pip install -r requirements.txt
copy .env.example .env      # Windows; then fill in GITHUB_TOKEN (optional)
```

`tree-sitter` is **pinned to 0.21.3**: `tree-sitter-languages==1.10.2` only supports `tree-sitter<0.22`.
On Python 3.11+ you can switch to the still-maintained `tree-sitter-language-pack`; `analyzer._ts_backend()`
detects it automatically, so no caller code changes.

## Usage

```bash
# Offline: built-in sample source, prints extracted classes/functions/methods (with lines and AST hashes)
python main.py --demo-ast
python main.py --demo-ast --demo-lang java

# Offline: built-in base/head versions, demos add / delete / modify with change_category
python main.py --demo-compare

# Offline: compare text diff and AST-guided behavior evidence across five examples
python main.py --demo-behavior

# Offline: built-in sample diff, demos "filter noise -> split -> line ranges"
python main.py --demo
python main.py --demo --context-lines 1

# Parse any .diff/.patch (e.g. a PR diff downloaded from GitHub)
python main.py --diff-file pr.diff

# Offline: diff between two local commits
python main.py --repo ../pytest-testmon --base HEAD~1 --head HEAD

# Online: GitHub PR
python main.py --pr 12 --owner zxr030114 --name CS5351-TestPilot -o out/change.json

# Online: mirror every changed file at base_sha and head_sha (for offline AST comparison)
python main.py --pr 12 --owner zxr030114 --name CS5351-TestPilot --save-sources out/pr_12

# Online: full AST analysis of a PR (mirror -> entities -> compare -> renames) -> output.json
python main.py --repo zxr030114/CS5351-TestPilot --pr 12
python main.py --repo pallets/flask --pr 6168 -o out/flask_6168.json

# Summary only
python main.py --repo . --base main --head HEAD --summary-only

# Offline: analyze two Python versions and emit the behavior report
python main.py --behavior-old out/base.py --behavior-new out/head.py -o out/behavior.json
```

### Behavioral Change Detection (Member 2)

`BehavioralChangeDetector` is a separate Python source-pair API. It uses the standard-library
`ast` module and does not execute analyzed code. This keeps the existing `analyze_pr()` schema
and the tree-sitter entity pipeline unchanged. Use the existing PR JSON for dependency mapping,
and use the source-pair API when the four behavior categories and focused test suggestions are
needed.

```python
from behavioral_change_detector import BehavioralChangeDetector

report = BehavioralChangeDetector().detect(old_code, new_code)
```

The JSON report always includes `change_type`, `summary`, `affected_functions`,
`behavioral_impact`, `behavior_changes`, `suggested_tests`, `errors`, and `analysis`. Supported
records are `NEW_BRANCH`, `DELETED_BRANCH`, `MODIFIED_BRANCH`, `BOUNDARY_CHANGE`,
`EXCEPTION_BEHAVIOR_CHANGE`, and `RETURN_BEHAVIOR_CHANGE`. The analyzer uses
`change_type: "unknown"` unless an optional model supplies a recognized interpretation.
AST findings are potential changes, not proof of runtime behavior.

LLM explanations are disabled by default. To enable the OpenAI-compatible adapter, set
`BEHAVIOR_LLM_API_KEY`; optionally set `BEHAVIOR_LLM_MODEL` and `BEHAVIOR_LLM_BASE_URL`.
The model may add summary and impact wording, but cannot add AST evidence or affected functions.
Request/configuration failures retain the rule-based summary. No key is needed for AST-only use.

Windows PowerShell:

```powershell
cd pr-analyzer
py -3 -m pip install -r requirements-dev.txt
py -3 main.py --demo-behavior
py -3 main.py --behavior-old .\out\base.py --behavior-new .\out\head.py -o .\out\behavior.json
py -3 -m pytest .\tests
```

Exit codes: `0` success, `1` unexpected error, `2` expected failure (not a git repo, API error,
failed self-check, ...).

## Key API

| Function | Purpose |
| --- | --- |
| `get_parser(language)` | Cached tree-sitter parser per language (`python` / `java`); raises `ASTError` with install hints when no backend exists. |
| `parse_to_ast(code_content, language="python", *, source_path=None)` | Parse source text/bytes into `ASTDocument` (`tree`, `source`, `backend`, `has_error`). |
| `parse_file(path, language=None)` | Read a file from disk and parse it; language inferred from the extension. |
| `extract_entities(ast_tree, *, include_methods=True, include_nested=True)` | **Core**: walk the AST and return class / function / method records in source order (`qualname`, `line_start/line_end`, `start_byte/end_byte`, `node_text`, `node_hash`, ...). |
| `entities_by_qualname(entities)` | `qualname -> entity` index, used to pair the base and head trees by name. |
| `summarize_entities(entities)` | Entity count summary (by type, language, error count) for logs and demos. |
| `compare_entities(old_entities, new_entities, *, include_unchanged=False, with_details=False)` | **Core**: pair two entity lists by `qualname` and emit `add` / `delete` / `modify` records (strictly seven keys, see below). |
| `classify_entity_change(old_entity, new_entity)` | Classify a single pair into `(change_category, reasons)`; pure function, easy to reuse and test. |
| `compare_sources(old_source, new_source, *, file_path="<source>", language=None)` | One-stop helper: parse both versions -> extract -> compare, returning `{"file_path": ..., "changes": [...]}`. |
| `find_renames(old_entities_by_path, new_entities_by_path, *, min_similarity=0.9)` | Pair entities deleted from one file with entities added to another: exact `node_hash` first, otherwise `difflib` similarity of the normalised source; greedy one-to-one. Used by `analyze_pr`. |
| `entity_similarity(old_entity, new_entity)` | Similarity in `[0, 1]` (1.0 when `node_hash` is equal); pure helper, exposed for reuse and tests. |
| `analyze_pr(repo_name, pr_number, *, token=None, out_dir=None, force=False, min_similarity=0.9)` | **End-to-end pipeline**: mirror -> parse both revisions -> extract entities -> cross-file rename pass -> `compare_entities` -> merged result. Returns the dict written to `output.json` (see below). |
| `summarize_changes(changes)` | Count records by `change_type` / `change_category`. |
| `get_pr_diff(repo_name, pr_number, *, token=None, timeout=30, max_retries=2)` | Fetch a PR's raw diff from the GitHub REST API. Handles timeouts, exponential backoff on 5xx/network errors, and 403/429 rate limits (`Retry-After` / `X-RateLimit-Reset`); 401/404 raise readable errors. |
| `get_pr_source_files(repo_name, pr_number, *, out_dir=None, force=False)` | **Source mirror**: download every changed file at `base_sha` and `head_sha` into `<out_dir>/base|head/<path>`, write `<out_dir>/manifest.json` (per-file `sha256` / `bytes` / `lines` / `binary` / `error`), and return that manifest. Renamed files are fetched from their previous path on the base side; absolute or `..` paths are rejected; existing files are kept unless `force=True`. |
| `parse_diff(diff_text)` | Split into `FileDiff` -> `HunkData` -> `DiffLine`. Uses `unidiff` when installed, otherwise the built-in state machine (both produce the same structure). |
| `is_noise_file(path)` / `noise_reason(path)` | Noise detection: lock files, `*.md`, images/fonts/media, `node_modules/`, `dist/`, `.min.js`, `__snapshots__`, ... `include_docs=True` keeps documentation. |
| `split_diff(diff_text, *, context_lines=3)` | Split into independent `DiffBlock`s (file x hunk); each block keeps changed lines plus 3 context lines and carries `new_range` / `old_range`. |
| `extract_hunk_context(hunk, *, context_lines=3)` | Trim one hunk, returning a `HunkContext` (with `truncated_before/after`). |
| `summarize_blocks(blocks)` | Block statistics: file count, block count, added/removed lines, blocks per file. |
| `flatten_affected_scopes(report)` | Union of all `path::qualname` in a report; skips `is_noise` files by default. |

## AST entity format

`extract_entities()` returns a list of dicts; one record looks like this:

```json
{
  "entity_type": "method",
  "entity_name": "deposit",
  "qualname": "Account.deposit",
  "parent_qualname": "Account",
  "node_type": "function_definition",
  "language": "python",
  "line_start": 18,
  "line_end": 23,
  "start_byte": 512,
  "end_byte": 713,
  "node_text": "def deposit(self, amount):\n    ...",
  "node_hash": "6c5814703b9d96fa",
  "signature_parts": {
    "parameters": "(self, amount: float) -> None",
    "decorators": "", "modifiers": "", "return_type": "", "extends": ""
  },
  "signature_hash": "0b6f...",
  "body_hash": "9a41...",
  "docstring_hash": null,
  "has_error": false
}
```

**Three hashes, three jobs** (`compare_entities` uses them to classify changes):

| Field | Covers | Meaning |
| --- | --- | --- |
| `signature_hash` | parameters, return type, decorators / Java modifiers, `extends` | changed -> `Signature change` (callers most likely need to rerun tests) |
| `body_hash` | function-body statements (docstring excluded; a class body **excludes** nested methods and nested classes) | changed -> `Logic change` |
| `docstring_hash` | the first docstring statement | only this changed -> `Documentation change` |

`node_hash` fingerprints the whole subtree (signature and body), for a cheap "did anything change" check.

**Line convention**: `line_start/line_end` are 1-based inclusive; `line_start` is the `def` / `class`
line (the modifier line for Java) and `line_end` is the last line of the definition (`}` / end of body).
When decorators are present the span **includes the decorator line** (the start of `decorated_definition`),
because "just added an `@property`" is also a calling-convention change and must be visible.

**Fingerprint convention** (`node_hash`): build a structural signature of the subtree (node types +
children + leaf tokens, skipping comments) and take `sha256(...)[:16]`. Therefore indentation, blank
lines, trailing commas and comment-only edits **do not** change it, while changing a constant, an
operator or a docstring does -- the same semantics as `pytest-testmon`'s `crc32`, but finer (per
entity instead of per block).

## Output (diff report, schema_version 1.0)

The full schema lives in the `analyzer.py` module docstring, section "3. Output JSON". Key points:

- `files[].path` is **repository-root relative with forward slashes**, directly comparable to testmon's `filename`.
- Every hunk carries both `old_*` and `new_*` line numbers (1-based, inclusive) so line shifts can be handled.
- `change_kind ∈ {body, signature, module_level, comment_only, file_level}`.
- `affected_scopes` is a flattened list of `path::qualname`, ready to query dependencies with.
- `comment_only` hunks have an empty `affects` -- AST hashes ignore comments, so downstream can skip them.

Downstream usage advice (including the degraded path and four boundary rules) is in section "4" of the
`analyzer.py` module docstring.

## How it connects to pytest-testmon

testmon splits each `.py` into `Block(start, end, name)` (`start` is the first statement of the body,
`end` is the line before the next definition, and the module block is named `<module>`); the block
fingerprint is the `crc32` of an AST string. Each test records the set of block fingerprints it covered,
and a rerun decides a test is affected when that set is no longer contained. That is why this module
emits **scopes plus line numbers for both versions**, not "line N changed" (which cannot be aligned
after shifting).

With `extract_entities()` downstream has two options:

1. **Precise path**: parse the `base_sha` and `head_sha` sources separately to get two entity trees;
   pair by `qualname`; entities whose `node_hash` changed are the changed entities, which then map onto
   testmon's block dependencies.
2. **Degraded path**: query dependencies directly with a diff report's `affected_scopes` /
   `changed_new_lines`; no base source needed.

## Change categories (change_category)

Output of `compare_entities()` / `compare_sources()` (strictly seven keys, including for `add` /
`delete`, so downstream can build one table shape):

```json
{
  "file_path": "src/billing.py",
  "changes": [
    {
      "entity_type": "function",
      "entity_name": "calculate_total",
      "change_type": "modify",
      "change_category": "Logic change",
      "line_start": 12,
      "line_end": 30,
      "summary": "Logic change: body changed (120 -> 132 chars); no signature change"
    }
  ]
}
```

| Condition (checked in this order) | `change_type` | `change_category` |
| --- | --- | --- |
| Only in the new version | `add` | `null` |
| Only in the old version | `delete` | `null` |
| Signature subtree (params / return type / decorators / modifiers / extends) changed | `modify` | `Signature change` |
| Signature same, body fingerprint (docstring excluded) changed | `modify` | `Logic change` |
| Only the docstring changed | `modify` | `Documentation change` |
| None of the three changed (only indentation / blank lines / comments) | no record emitted | -- |
| A `delete` in one file and a similar `add` in another (paired by `analyze_pr`) | `rename` | `Rename / Move` |

Semantics that are easy to get wrong:

- **The matching key is `qualname`** (`Account.__init__`, `Account.rich._threshold`), so same-named
  functions at different levels never get paired, and renaming an outer scope turns its inner functions
  into `delete` + `add`.
- **A pure rename within one file is `delete` + `add`** in `compare_entities` (pairing is by
  `qualname`). The end-to-end `analyze_pr()` adds a rename pass on top: entities that vanish from one
  file and reappear in another (or under a new name) are paired by `node_hash` / text similarity and
  reported as `change_type="rename"`, `change_category="Rename / Move"`, with `old_path` / `new_path` /
  `old_qualname` / `new_qualname` / `similarity` attached. This only happens when the matched pair is
  genuinely gone from one file and new in another, so unchanged code is never reclassified.
- **Line numbers**: `modify` / `add` report **new-version** lines; `delete` reports **old-version** lines
  (those lines only exist in the old file).
- `add` / `delete` always have `change_category = null` (constant `ADD_DELETE_CATEGORY`; to emit
  `"New code"` / `"Removed code"` instead, change that one table).
- `with_details=True` appends `details` after the seven keys (reason list, parameter diff, line-count
  change, similarity); the default is `False` so downstream can rely on a fixed set of columns.
- **Classes are not reported twice**: a class body's `body_hash` excludes nested methods and nested
  classes, so changing one method yields only that method's record; the class gets an extra record only
  when its own signature / class attributes / other statements changed.
- **Javadoc is invisible**: Java's `/** ... */` is a `comment` node outside the method subtree, so editing
  it produces no record -- consistent with `pytest-testmon`'s fingerprint semantics (comments do not
  affect checksums).

## Source mirror (offline AST comparison)

`get_pr_source_files()` turns a PR into a local two-revision checkout, so the AST layer never needs
a clone or git:

```bash
python main.py --pr 12 --owner zxr030114 --name CS5351-TestPilot --save-sources out/pr_12
```

```
out/pr_12/
  base/src/app.py        # file content at base_sha
  head/src/app.py        # file content at head_sha
  manifest.json          # per-file sha256 / bytes / lines / binary / error, plus base_sha / head_sha
```

Using it from Python:

```python
from analyzer import compare_sources, get_pr_source_files, parse_file

manifest = get_pr_source_files("pallets/flask", 6168, out_dir="out/pr_6168")
old = parse_file("out/pr_6168/base/src/flask/helpers.py")
new = parse_file("out/pr_6168/head/src/flask/helpers.py")
changes = compare_sources(
    old.source.decode("utf-8"), new.source.decode("utf-8"), file_path="src/flask/helpers.py"
)["changes"]
```

Notes:

- Content is fetched from `raw.githubusercontent.com` (no Contents-API 1 MB limit and no REST quota);
  only the PR metadata and the file list use the API, so a 12-file PR costs 2 API calls.
- `status="added"` files have no `base` entry, `status="removed"` files no `head` entry; a renamed file
  is read at `previous_filename` on the base side.
- Per-file failures (404, oversized, truncated) are recorded as `error` in the manifest instead of
  aborting the run; only a total failure raises `AnalysisError`.
- Re-running keeps already-downloaded files; pass `--force` to overwrite them.

## Full PR analysis (`analyze_pr` / `output.json`)

```bash
python main.py --repo pallets/flask --pr 6168                 # writes ./output.json
python main.py --repo pallets/flask --pr 6168 -o out/pr.json  # custom path
python main.py --analyze --pr 6168 --repo pallets/flask       # explicit flag
```

`--repo` is shared with offline mode, so it is interpreted as `owner/repo` only when it does not name an
existing directory; passing `--owner/--name` keeps the older diff-report behaviour. `--mirror-dir`
chooses where the downloaded sources live (default `out/pr_<N>`), `--force` re-downloads them, and
`--min-similarity` (default `0.9`) tunes the rename/move threshold.

`analyze_pr()` writes one JSON document (`schema_version` `"2.0"`):

| Field | Type | Meaning |
| --- | --- | --- |
| `schema_version` | string | `"2.0"` for the pipeline output. |
| `mode` | string | Always `"pr_ast"`. |
| `repo` | object | `owner`, `name`, `pr_number`, `base_sha`, `head_sha`. |
| `mirror` | object | `out_dir` of the downloaded sources and the mirror `counts`. |
| `summary` | object | `files_changed`, `files_analyzed`, `files_skipped`, `changes`, `renames`, `by_change_type`, `by_change_category`, `warnings`. |
| `files` | array | Per file: `file_path`, `language`, `is_test_file`, `changes` (records of that file). |
| `changes` | array | **Flat list** of all records, in file order; every record also carries `file_path`. |
| `skipped` | array | `{"path", "reason"}` for noise files and unsupported languages (not analysed, not an error). |
| `warnings` | array | Non-fatal problems (download/parse errors); the run still succeeds. |

Each record keeps the strict seven keys plus `file_path`:

| Field | Type | Meaning |
| --- | --- | --- |
| `entity_type` | string | `class` / `function` / `method`. |
| `entity_name` | string | Bare name (`deposit`). |
| `change_type` | string | `add` / `delete` / `modify` / `rename`. |
| `change_category` | string or null | `Signature change` / `Logic change` / `Documentation change` / `Rename / Move`; `null` for `add` / `delete`. |
| `line_start`, `line_end` | int | 1-based inclusive; `modify`/`add`/`rename` use **new** lines, `delete` uses **old** lines, `rename` also reports new lines. |
| `summary` | string | Human-readable one-liner (what changed and why). |
| `file_path` | string | Repo-relative POSIX path of the record's file (the new path for `rename`). |
| `old_path`, `new_path`, `old_qualname`, `new_qualname`, `similarity` | extra | Only on `rename` records. |

Notes: `add` records describe new entities for which no test coverage exists yet (the downstream module
should flag them as "needs new tests" rather than "rerun"), `is_test_file` lets Member 3 exclude test
files from their own mapping, and `skipped` + `warnings` explain everything that was not analysed.

## Known limitations / TODO

- [x] **Rename detection**: a pure rename (content unchanged, no hunk) emits one synthetic hunk with
      `change_kind=file_level`.
- [x] **PR source mirror**: `get_pr_source_files()` downloads every changed file at `base_sha` and
      `head_sha` into `<dir>/base|head/<path>` plus `manifest.json` (sha256 / bytes / lines / errors),
      so the AST layer can compare two versions without a local clone.
- [ ] **Mirror gaps**: submodule content and Git LFS pointers are downloaded as the plain pointer file;
      files above GitHub's raw size ceiling are recorded as `error` instead of failing the whole run.
- [ ] **Rename plus edit**: with `status=renamed` and hunks present, hunks are still located by the new
      path; the "same symbol" across paths is not paired explicitly yet.
- [ ] **Language coverage**: only `python` and `java` are configured; `language_for_path()` returns
      `unknown` for others (add a row to `LANGUAGE_SPECS` when needed).
- [ ] **Cross-file call graph**: this module only resolves in-file scopes; the test-level call graph is
      downstream's job.
- [ ] **`get_pr_diff` has no local cache**: re-analysing the same PR spends GitHub quota again
      (60 requests/hour anonymous); `get_pr_source_files` keeps already-downloaded files unless
      `--force` is passed.
- [x] **Rename detection in the AST layer**: `analyze_pr()` pairs entities that disappear from one file
      with similar entities that appear in another (`node_hash` equality first, then `difflib` similarity
      >= `--min-similarity`) and emits `change_type="rename"` instead of `delete` + `add`.
- [ ] **Ambiguous renames**: two unrelated one-line functions with identical text in different files are
      indistinguishable from a real move; the greedy matcher may pair either one. Lower
      `--min-similarity` makes it more eager, `1.0` restricts it to byte-identical subtrees.
- [ ] **Partial test coverage**: `tests/test_ast_entities.py` + `tests/test_compare_entities.py` +
      `tests/test_diff_lines.py` + `tests/test_pr_sources.py` cover extraction/comparison, hunk line
      numbers and the mirror; edge cases of `ScopeIndex.locate` / `noise_reason` are still missing.

## Tests

```bash
python -m unittest discover -s tests -v     # or: python -m pytest tests -v
```

- `tests/test_ast_entities.py`: entity extraction (class/function/method/nested function), line spans
  matching `node_text`, fingerprint semantics (comments/blank lines/indentation change nothing; a
  semantic edit only affects its own entity), Java support, `include_*` switches.
- `tests/test_compare_entities.py`: the `add` / `delete` / `modify` kinds; classification and precedence
  of `Signature change` / `Logic change` / `Documentation change`; the strict seven keys; `file_path`
  passthrough; indentation/blank lines/comment-only edits not counting as changes; classes not being
  reported twice for a method edit; decorators counting as signature changes; `modify`/`add` using new
  lines while `delete` uses old lines; `summarize_changes`.
- `tests/test_diff_lines.py`: per-hunk `old_line` / `new_line` numbering, and consistency between the
  `unidiff` path and the built-in parser (this was a real bug once: reading a non-existent unidiff
  attribute left every line number empty and degraded every hunk to `module_level`).
- `tests/test_pr_sources.py`: `get_pr_source_files()` against a fake session -- base/head mirror layout,
  added/removed/renamed files, per-file sha256 in the manifest, unsafe path rejection, resume without
  `--force`, and total-failure reporting.
- `tests/test_analyze_pr.py`: the end-to-end pipeline on a fake session -- schema/summary, a cross-file
  move becoming `rename` (not `delete` + `add`), signature changes surviving the rename pass, real
  add/delete records, the strict key set, skipped noise files, mirror layout, plus `find_renames` /
  `entity_similarity` unit cases (threshold, type matching, greedy one-to-one pairing, symmetry).

## Dependencies

- **Required**: `tree-sitter==0.21.3`, `tree-sitter-languages==1.10.2` (AST parsing).
- **Optional**: `unidiff` (use `unidiff.PatchSet` when installed, else fall back to the built-in diff
  state machine), `requests` (needed by `get_pr_diff` / `get_pr_source_files`), `python-dotenv`
  (read `.env`), `PyGithub` (kept for callers that prefer the SDK; the built-in client does not need it).
- Pure parsing (`--demo` / `--diff-file` / `--demo-ast` / `--demo-compare`) needs no network.
