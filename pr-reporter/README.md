# Member F — PR Reporter and Evaluation

[中文使用说明](README.zh.md) · [中文演示报告](sample-reports/gap_report.zh.html) · [中文验收评估](sample-reports/evaluation.zh.html)

Member F is an additive module beside A-E. Its single CLI entry point is
`pr_reporter.py`. It integrates the existing algorithms, generates reviewable
artifacts, optionally updates a GitHub PR comment, and measures frozen acceptance
cases. Existing A-E and bundled testmon files are unchanged.

## Requirements and installation

Python **3.10+**, Git, and the repository's sibling A-E directories. Python 3.12
is used in CI. Render/publish use the standard library; real source analysis and
baseline execution need the dependencies below.

Run commands from the repository root:

```powershell
python -m venv pr-reporter/.venv
pr-reporter/.venv/Scripts/python.exe -m pip install -r pr-reporter/requirements.txt
pr-reporter/.venv/Scripts/python.exe -m unittest discover -s pr-reporter/tests -v
```

On Linux/macOS substitute `pr-reporter/.venv/bin/python`.

## One-command local demonstration

```powershell
pr-reporter/.venv/Scripts/python.exe pr-reporter/pr_reporter.py demo --out pr-reporter/out/demo
```

This creates a temporary Git repository, commits baseline and changed payment
implementations, executes the **baseline** pytest test with real testmon, then
calls A, B, C, D and E. The unchanged test file is read from the complete head
checkout. Expected result: one precision gap, **43.55 / LOW**, confidence HIGH.
No GitHub request or model call occurs. Neither the original checkout nor its
testmon database is changed. Only generated output artifacts persist.

Generated files:

| File | Meaning |
| --- | --- |
| `changes.json` | A's entity comparisons wrapped in schema 2.0 |
| `behavior.json` | B evidence tagged with repository and module paths |
| `mapping.json` | C's real historical dependency results, when available |
| `adequacy.json` | D assertion evidence from full head test sources |
| `gap_report.json` | E report plus run identity and measured timings |
| `gap_report.md` | Bounded, escaped PR comment |
| `gap_report.html` | Script-free offline view |

## Consume A-E artifacts

Render an existing E report:

```powershell
python pr-reporter/pr_reporter.py report --report gap_report.json --out pr-reporter/out/report
```

Run E on saved A-D reports before rendering:

```powershell
python pr-reporter/pr_reporter.py score --changes gap-scoring/fixtures/changes.json --behavior gap-scoring/fixtures/behavior_with_path.json --mapping gap-scoring/fixtures/mapping_result.json --adequacy gap-scoring/fixtures/adequacy.json --out pr-reporter/out/fixture
```

Missing optional B/C/D inputs remain visible. Zero scored changes never become a
claim that testing is adequate. `--top 5` controls the number of displayed gaps.

## Analyse real local commits

```powershell
python pr-reporter/pr_reporter.py run --repo-dir . --base HEAD~1 --head HEAD --collect-baseline --testmon-root mapping-demo --pytest-target test_payment.py --out pr-reporter/out/local
```

`--collect-baseline` explicitly authorizes executing **base revision tests** in a
fresh temporary clone. Head sources are parsed, not imported or tested. The
baseline test command has a timeout and receives no inherited token, secret,
password or API-key environment variables. Base code can still access files and
network available to the process; use only a project you intend to execute.
The runner disables third-party pytest plugin auto-loading and loads testmon
explicitly. Projects requiring extra pytest plugins need a configured test runner;
this bounded prototype does not infer arbitrary project environments.

`--testmon-root` is the pytest root relative to the analysed repository.
`--pytest-target` is relative to that root and may be repeated. F separately uses
the full head repository root for D's node IDs; it does not pass C's root to D.

To reuse a previously collected database:

```powershell
python pr-reporter/pr_reporter.py run --repo-dir . --base BASE_SHA --head HEAD_SHA --db mapping-demo/.testmondata --db-base-sha BASE_SHA --testmon-root mapping-demo --out pr-reporter/out/local
```

`--db-base-sha` is the caller's provenance assertion, not an independently proven
property of testmon's database. F records that distinction. Omit both database
options to produce a report explicitly missing mapping data.

F invokes A's public `compare_sources` API for local commits. Git renames are
conservatively represented as delete/add in this adapter, without guessing alias
migration. A's online PR command remains available separately. Only Python is
integrated here. Module-level changes outside A's entity support are retained as
unscored changes and warnings.

## Explicit GitHub publication

```powershell
python pr-reporter/pr_reporter.py report --report gap_report.json --out pr-reporter/out/published --publish --repo-name OWNER/REPOSITORY --pr 12 --expected-head FULL_HEAD_SHA
```

Publication requires `GITHUB_TOKEN` in the process environment. GitHub Actions
supplies its own installation token; no credential file or interactive password
is used. Report repository, PR number and head SHA must match the target. The
client checks the live PR head before writing and updates only the authenticated
author's marked comment. Repeated identical feedback causes no write. Comments
from other users are never updated. An installation-token `/user` 403 can fall
back to `github-actions[bot]` only inside GitHub Actions.

`--fail-on-high` returns exit 3 **after** artifacts and any requested comment are
written. The default is advisory. Input/API errors remain errors, not HIGH gaps.

| Exit | Meaning |
| --- | --- |
| 0 | Report/evaluation produced successfully |
| 1 | Upstream failure notification, unexpected failure, or publication failure |
| 2 | Invalid input, unavailable dependency, timeout, or baseline execution error |
| 3 | Explicit HIGH gate failed, or frozen evaluation criteria failed |

For an upstream failure use `report --failure-file failure.txt --out ...`; this
emits an analysis-unavailable comment and exits 1. A subsequent successful run
clears its own stale failure marker.

## GitHub Actions

The active workflow is `../.github/workflows/testpilot-f.yml`:

1. Run automated tests, frozen evaluation and the real local demo.
2. For a PR, analyse complete snapshots of base/head using trusted tool code.
3. Upload JSON, Markdown, HTML and measurements, even when analysis fails.
4. A separate job with PR write permission updates feedback on same-repository
   PRs. Fork PRs get read-only analysis and downloadable artifacts, not comments.
5. Set repository variable `TESTPILOT_FAIL_ON_HIGH=true` to enable the HIGH gate.

Bootstrap this module and workflow onto the base branch before using the PR
analysis/publish jobs: they deliberately execute the base branch's tool code.
Workflow syntax and local commands are checked locally; a real hosted workflow
and real PR publication still require a push and a GitHub run.

The checked-in workflow collects only `mapping-demo/test_payment.py` as this
repository's bounded baseline. Changes elsewhere can legitimately have unresolved
mapping; the reports disclose this. For another project, adjust the root, test
targets, dependencies and runner limits explicitly.

## Frozen acceptance evaluation

```powershell
python pr-reporter/pr_reporter.py evaluate --out pr-reporter/out/evaluation
```

`benchmarks/contract.json` fixes task, dataset, labels, baseline, threshold,
repetitions and acceptance criteria before comparison. Its SHA256 is recorded
in `evaluation.json`. The six cases reuse E's synthetic fixtures: three positive
gap cases, two negative controls, and one unknown-label case excluded from accuracy
metrics. It measures precision, recall, specificity, and scoring/rendering p95.

The comparator is explicitly a **coverage-presence heuristic**, not a native
testmon gap detector. Native testmon selects tests; it does not predict assertion
gaps. Small acceptance-fixture scores are not real-world accuracy estimates.
Pipeline measurements separately include real baseline test execution and total
local orchestration time. They exclude GitHub publication/network latency.

## Implementation map

| File | Responsibility |
| --- | --- |
| `pr_reporter.py` | Single CLI and real local demo |
| `integration.py` | A-E API orchestration and version/path adapters |
| `rendering.py` | E schema checks, escaped bounded Markdown, offline HTML |
| `github_client.py` | REST adapter, PR identity checks and comment upsert |
| `evaluation.py` | Frozen cases, confusion matrices, timing and criteria |
| `tests/test_reporter.py` | Rendering, failures, API fakes, gates and real integration |
| `benchmarks/contract.json` | Frozen acceptance definitions |
| `docs/course-alignment.zh.md` | Slide-to-code traceability, two NFRs and risks |
| `sample-reports/` | Observed local demonstration/evaluation output snapshots |

## Limits

Static assertions do not prove path execution or correct expected values. F keeps
file identity while joining D evidence, but cannot eliminate A/B/D analysis limits.
It does not execute head tests, generate tests, perform mutation testing, persist
rename aliases, or provide static cold-start mapping. Meeting minutes, sprint
burndowns, user feedback and individual productive hours must be maintained by
the team; this module does not manufacture historical process evidence.

