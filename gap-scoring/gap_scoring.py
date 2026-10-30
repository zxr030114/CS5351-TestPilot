"""Command line entry point for the test gap scorer (member E).

One command runs the whole pipeline stage E owns:

    load -> join -> score -> explain -> report

Upstream stages (A/B/C/D) are produced elsewhere; this tool consumes their JSON
files and never talks to GitHub itself.

Examples
--------
Score a PR from four saved reports:

    python gap_scoring.py \\
        --changes out/output.json \\
        --behavior out/behavior.json \\
        --mapping out/mapping_result.json \\
        --adequacy out/adequacy.json \\
        -o out/gap_report.json

Render a PR comment instead of JSON:

    python gap_scoring.py --changes ... --behavior ... --format markdown

Only stage A available (everything else degrades to "not scored"):

    python gap_scoring.py --changes out/output.json --format markdown

Exit codes: ``0`` success, ``1`` unexpected failure, ``2`` bad input or usage,
``3`` the gate failed (``--fail-on-high`` found a HIGH gap).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from join import join_inputs
from loader import LoaderError, load_pipeline
from report import ReportOptions, build_report, render_report_markdown
from scoring import context_from_pipeline, score_units

__all__ = ["build_parser", "main"]

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_INPUT = 2
EXIT_GATE = 3


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gap_scoring",
        description="Score potential test gaps for a change set (member E).",
    )
    parser.add_argument("--changes", required=True, help="stage A output.json (schema 2.0)")
    parser.add_argument("--behavior", help="stage B behavior.json")
    parser.add_argument("--mapping", help="stage C mapping_result.json")
    parser.add_argument("--adequacy", help="stage D adequacy.json")
    parser.add_argument("-o", "--output", help="write the report here instead of stdout")
    parser.add_argument(
        "--format",
        choices=("json", "markdown"),
        default="json",
        help="report format (default: json)",
    )
    parser.add_argument("--indent", type=int, default=2, help="JSON indent (default: 2)")
    parser.add_argument(
        "--top", type=int, default=None, help="keep only the N highest-scoring gaps"
    )
    parser.add_argument(
        "--include-none",
        action="store_true",
        help="also list changes whose gap is fully covered",
    )
    parser.add_argument(
        "--max-gaps-in-comment",
        type=int,
        default=5,
        help="how many gaps the markdown comment lists (default: 5)",
    )
    parser.add_argument(
        "--fail-on-high",
        action="store_true",
        help="exit with code 3 when at least one HIGH gap is found (for CI gating)",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="print diagnostics to stderr")
    return parser


def _report_options(args: argparse.Namespace) -> ReportOptions:
    sources = {"changes": str(args.changes)}
    for name in ("behavior", "mapping", "adequacy"):
        value = getattr(args, name)
        if value:
            sources[name] = str(value)
    return ReportOptions(top=args.top, include_none=args.include_none, sources=sources)


def _write(text: str, destination: str | None) -> None:
    if destination is None:
        sys.stdout.write(text)
        if not text.endswith("\n"):
            sys.stdout.write("\n")
        return
    path = Path(destination)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    try:
        inputs = load_pipeline(
            args.changes,
            behavior=args.behavior,
            mapping=args.mapping,
            adequacy=args.adequacy,
        )
    except LoaderError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_INPUT

    try:
        join_result = join_inputs(inputs)
        context = context_from_pipeline(inputs)
        scores = score_units(join_result, context=context)
        report = build_report(inputs, join_result, scores, _report_options(args))
    except Exception as exc:  # noqa: BLE001 - surfaced as exit code 1, never swallowed
        print(f"unexpected error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return EXIT_ERROR

    if args.verbose:
        for problem in (*inputs.problems, *join_result.problems):
            print(f"{problem.level}: {problem.code}: {problem.message}", file=sys.stderr)
        summary = report["summary"]
        print(
            f"scored {summary['changes_scored']}/{summary['changes_total']} changes, "
            f"{summary['gaps_high']} high / {summary['gaps_medium']} medium / {summary['gaps_low']} low",
            file=sys.stderr,
        )

    if args.format == "markdown":
        text = render_report_markdown(report, max_gaps=args.max_gaps_in_comment)
    else:
        text = json.dumps(report, ensure_ascii=False, indent=args.indent) + "\n"

    try:
        _write(text, args.output)
    except OSError as exc:
        print(f"error: cannot write {args.output}: {exc}", file=sys.stderr)
        return EXIT_INPUT

    if args.fail_on_high and report["summary"]["gaps_high"] > 0:
        print(
            f"gate failed: {report['summary']['gaps_high']} HIGH gap(s) reported",
            file=sys.stderr,
        )
        return EXIT_GATE
    return EXIT_OK


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
