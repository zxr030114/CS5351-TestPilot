# Test Gap Scoring & Explanation

Stage **E** of the team pipeline. It answers one question about a pull request:

> Which of these changes are *not really verified* by the tests that appear to
> cover them, and how much should a reviewer care?

```
A  PR / Git Change Analyzer ──output.json──▶  B  Behavioral Change Detector
   │                                              │
   └──────────────────────────────────────▶  C  Test Dependency Mapper
                                                  │
                                                  ▼
                                             D  Test Adequacy Analyzer
                                                  │  status + evidence[]
                                                  ▼
                                        ★ E  Test Gap Scoring  ──▶  F  PR Reporter
```

E consumes four JSON reports, produces a score per changed entity with a written
explanation, and never talks to GitHub. It does not re-parse diffs, does not run
tests, and does not generate test code.

## Requirements

- Python 3.10+
- No third-party dependencies (standard library only)

## Quick start

Score a PR from four saved reports:

```bash
python gap_scoring.py \
  --changes   out/output.json \
  --behavior  out/behavior.json \
  --mapping   out/mapping_result.json \
  --adequacy  out/adequacy.json \
  -o out/gap_report.json
```

Render a ready-to-post PR comment instead:

```bash
python gap_scoring.py --changes ... --behavior ... --format markdown -o out/gap_report.md
```

Run stages A-D yourself and score the result:

```bash
python run_pipeline.py --repo-dir ../CS5351-TestPilot --workdir ./gap-work \
  --owner pallets --name flask --pr 6168 --format markdown
```

## Inputs

| File | Producer | Key fields E reads |
| --- | --- | --- |
| `output.json` | A (schema 2.0) | `changes[]`: `file_path`, `entity_name`, `change_category`, `line_start/end` |
| `behavior.json` | B (schema 1.0) | `behavior_changes[]`: `type`, `function`, `new_line`, type-specific fields |
| `mapping_result.json` | C (schema 1.0) | `changes[].status`, `tests[]`, `selection_complete` |
| `adequacy.json` | D (schema 1.0) | `results[].status`, `evidence[]`, `recommendations[]` |

Only A is mandatory. Missing B/C/D degrade the result instead of failing, and the
report records which inputs were missing.

## Scoring

```
Base  = 100 * (0.45 * BehaviorRisk * ChangeImportance + 0.55 * AdequacyDeficit)
Bonus = sum of triggered rules, capped at 15
Gap   = min(100, Base + Bonus)
```

| Factor | Source | Values |
| --- | --- | --- |
| `BehaviorRisk` | B change type | exception 1.00, boundary 0.85, branch 0.70, return 0.60 |
| `ChangeImportance` | A change category | signature 1.00, logic 0.90, add 0.70, delete 0.60, rename 0.30 |
| `AdequacyDeficit` | D status + evidence review | level 0→1.00, 1→0.85, 2→0.70, 2.5→0.35, 3→0.00 |

Rules add up to 15 points: an unverified exception change (+5), an untested
boundary (+4), related tests that never reach the change (+4), an executed but
unasserted path (+3), new code without a baseline (+3), file-level mapping (+2).

Bands: `HIGH` ≥ 80, `MEDIUM` ≥ 50, `LOW` ≥ 20, otherwise `NONE`. A change whose
behaviour is actually asserted is suppressed to zero: its remaining structural
risk is not something a test can close.

### Evidence review

Stage D answers `assertion_evidence_found` whenever *some* assertion syntactically
matches the changed call. Two shapes of evidence do not deserve that level:

- every `evidence[].strength` is `weak_or_mismatched` → downgraded to level 2;
- the only match uses integer literals while the change alters a rounding
  precision → downgraded to level 2.5.

This is the part that makes E stricter than D: `withdraw(100, 30) == 70` cannot
tell `round(x, 2)` from `round(x, 1)`, so a change that looks covered is reported
as a 43.55 LOW gap instead of disappearing.

### Confidence

Score and confidence are orthogonal: the score says *how big the gap is*, the
confidence says *how much the data supports it*. Nine caveat signals (missing
stage, assumed level, file-level mapping, weak join, incomplete selection…) add
up per unit; 0 points is `HIGH`, 1 is `MEDIUM`, 2+ is `LOW`.

A 79-point gap with `LOW` confidence means "we do not know", not "worse".

## Output

`gap_report.json` follows `docs/gap-scoring-contract.zh.md` §7: `summary`,
`gaps[]` (sorted by score, each with `components`, `rule_hits`, `confidence` and a
three-part `explanation`), `unscored[]` with a reason for every change that could
not be scored, `problems[]`, and `sources`.

`--format markdown` renders the same data as a PR comment: a summary block, then
the top N gaps, each at most five lines.

## CLI

```
python gap_scoring.py --changes FILE
                      [--behavior FILE] [--mapping FILE] [--adequacy FILE]
                      [-o FILE] [--format json|markdown] [--top N]
                      [--include-none] [--fail-on-high] [-v]
```

| Exit code | Meaning |
| --- | --- |
| 0 | report produced |
| 1 | unexpected failure |
| 2 | bad input (missing file, wrong schema, unwritable output) |
| 3 | gate failed: at least one `HIGH` gap (`--fail-on-high`) |

Codes 2 and 3 are distinct on purpose: an input problem needs a fixed file, a
failed gate needs written tests.

## Testing

```bash
python -m unittest discover -s tests      # 210 tests, no network
```

`fixtures/` holds real artefacts from a payment demo; `fixtures/scenarios/` holds
six self-contained scenarios with hand-written expected scores
(`HIGH`/`MEDIUM`/`LOW`/`NONE`/unscored) so calibration changes show up as a test
failure instead of passing silently.

## Layout

```
gap_scoring.py     CLI: load -> join -> score -> explain -> report
pipeline.py        run stages A-D and hand the artefacts to E
run_pipeline.py    CLI for pipeline.py
loader.py          read + validate the four reports
join.py            link A/B/C/D records into scoring units
module_path.py     repository path -> import module path (required by D)
weights.py         every tunable number
scoring.py         the formula, rules, bands
confidence.py      caveat signals -> HIGH/MEDIUM/LOW
explain.py         three-part explanation, forbidden-wording guard
report.py          JSON report + markdown comment + one-line status
tools/             fixture generators
docs/              contract, field map, reporter integration, related work
```

## Limitations

- Python only: D's assertion extraction is AST-based.
- Never executes tests; every statement is static evidence.
- Stage C needs a testmon database collected on the analysed project, which is
  usually unavailable — pass `--mapping-file` with a report produced elsewhere.
- The A↔B join is heuristic: it matches short function names plus a line window,
  and leaves genuinely ambiguous records unattached rather than guessing.
- Confidence is a penalty count, not a probability; do not multiply it with the
  score.

## Documentation

- `docs/gap-scoring-contract.zh.md` — module contract (scoring, evidence review,
  confidence, JSON/CLI contract, acceptance)
- `docs/field-map.zh.md` — every field of the four inputs and the join rules
- `docs/integration-with-reporter.zh.md` — how stage F consumes the report
- `docs/related-work.zh.md` — literature behind each design decision
