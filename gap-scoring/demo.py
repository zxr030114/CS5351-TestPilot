"""Demo: score the six bundled scenarios and render one PR comment.

Runs entirely offline: the scenarios are complete sets of A/B/C/D reports, so no
network, no testmon database and no subprocesses are involved.  Use it to see how
the same tool reacts to different risk classes.

    python demo.py                      # score table + comment for scenario 01
    python demo.py --scenario 00-precision-blind
    python demo.py --format json        # the full report for one scenario
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from explain import explain_unit
from join import join_inputs
from loader import load_pipeline
from report import build_report, render_report_markdown, status_line
from scoring import context_from_pipeline, score_units

FIXTURES = Path(__file__).resolve().parent / "fixtures"
MANIFEST = FIXTURES / "scenarios" / "expected.json"


def load_manifest() -> list[dict]:
    """Read the scenario list; each entry names a directory of four reports."""
    payload = json.loads(MANIFEST.read_text(encoding="utf-8"))
    return payload["scenarios"]


def scenario_dir(entry: dict) -> Path:
    """Scenario 00 reuses the payment fixtures one level up."""
    return FIXTURES if entry.get("path") == "." else FIXTURES / "scenarios" / entry["name"]


def score_scenario(entry: dict):
    """Return ``(inputs, join_result, scores)`` for one scenario."""
    base = scenario_dir(entry)
    inputs = load_pipeline(
        base / "changes.json",
        base / "behavior.json",
        base / "mapping_result.json",
        base / "adequacy.json",
    )
    join_result = join_inputs(inputs)
    scores = score_units(join_result, context=context_from_pipeline(inputs))
    return inputs, join_result, scores


def print_table(manifest: list[dict]) -> dict[str, dict]:
    """Print one line per scenario and return the built reports by name."""
    reports: dict[str, dict] = {}
    header = f"{'scenario':<26}{'score':>7}  {'band':<8}{'confidence':<11}problem"
    print(header)
    print("-" * len(header))
    for entry in manifest:
        inputs, join_result, scores = score_scenario(entry)
        score = scores[0]
        reports[entry["name"]] = build_report(inputs, join_result, scores)
        problem = explain_unit(score).problem
        print(
            f"{entry['name']:<26}{score.score:>7.2f}  {score.band:<8}"
            f"{score.confidence.level:<11}{problem[:46]}"
        )
    print()
    return reports


def show_comment(report: dict, title: str) -> None:
    print(f"=== PR comment for {title} ===")
    print(render_report_markdown(report))
    print("=== one-line status ===")
    print(status_line(report))
    print()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Score the bundled scenarios offline.")
    parser.add_argument(
        "--scenario",
        default="01-exception-uncovered",
        help="scenario whose comment is rendered (default: 01-exception-uncovered)",
    )
    parser.add_argument(
        "--format",
        choices=("comment", "json"),
        default="comment",
        help="what to print for the chosen scenario (default: comment)",
    )
    args = parser.parse_args(argv)

    manifest = load_manifest()
    reports = print_table(manifest)

    if args.scenario not in reports:
        print(f"error: unknown scenario {args.scenario!r}", file=sys.stderr)
        print("available: " + ", ".join(reports), file=sys.stderr)
        return 2

    report = reports[args.scenario]
    if args.format == "json":
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        show_comment(report, args.scenario)
        print("summary:", json.dumps(report.get("summary", {}), ensure_ascii=False))
    return 0



if __name__ == "__main__":
    raise SystemExit(main())
