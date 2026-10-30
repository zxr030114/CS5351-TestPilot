"""Run the whole team pipeline: A -> B -> C -> D -> E.

    python run_pipeline.py --repo-dir ../CS5351-TestPilot --workdir ./gap-work \\
        --owner pallets --name flask --pr 6168

    # reuse an existing stage-A report (skips the network)
    python run_pipeline.py --repo-dir ../CS5351-TestPilot --workdir ./gap-work \\
        --changes ../CS5351-TestPilot/pr-analyzer/output.json

Stages B/C/D are skipped when their inputs are missing (no source mirror, no
testmon database), and stage E still produces a report saying so.  Add
``--dry-run`` to print the commands without running them.

Exit codes: ``0`` report produced, ``1`` unexpected failure, ``2`` stage A
unavailable or the configuration is invalid.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from pipeline import PipelineConfig, run_pipeline

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_INPUT = 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="run_pipeline",
        description="Run stages A-D of the team pipeline and score the result (stage E).",
    )
    parser.add_argument("--repo-dir", required=True, help="shared repository checkout")
    parser.add_argument("--workdir", default="gap-work", help="where artefacts are written (default: gap-work)")
    parser.add_argument("--changes", help="reuse an existing stage A output.json instead of calling A")
    parser.add_argument("--behavior-file", help="reuse an existing stage B behavior.json")
    parser.add_argument("--mapping-file", help="reuse an existing stage C mapping_result.json")
    parser.add_argument("--adequacy-file", help="reuse an existing stage D adequacy.json")
    parser.add_argument("--owner", help="repository owner for stage A (online mode)")
    parser.add_argument("--name", help="repository name for stage A (online mode)")
    parser.add_argument("--pr", type=int, help="pull request number for stage A")
    parser.add_argument(
        "--test-root",
        default=None,
        help="pytest root for stages C/D; defaults to the analysed project's head mirror",
    )
    parser.add_argument("--db", help="path to the testmon database for stage C")
    parser.add_argument(
        "--format", choices=("json", "markdown"), default="json", help="stage E report format"
    )
    parser.add_argument(
        "--skip", action="append", default=[], metavar="STAGE", help="skip a stage (repeatable: B, C, D)"
    )
    parser.add_argument("--dry-run", action="store_true", help="print commands without executing")
    parser.add_argument("--python", default=sys.executable, help="interpreter for stage commands")
    parser.add_argument(
        "--env", action="append", default=[], metavar="KEY=VALUE", help="extra environment for stages"
    )
    return parser


def _extra_env(repo: Path, pairs: list[str]) -> dict[str, str]:
    """Compose the environment every stage needs.

    The in-repo modules (``pr-analyzer``, the bundled ``pytest-testmon``) are put
    on ``PYTHONPATH`` automatically; anything else -- for example a virtualenv's
    ``site-packages`` -- can be added with ``--env PYTHONPATH=...``.
    """
    entries = [str(repo / "pr-analyzer"), str(repo / "pytest-testmon")]
    existing = os.environ.get("PYTHONPATH")
    if existing:
        entries.append(existing)
    env = {"PYTHONPATH": os.pathsep.join(entries)}

    for pair in pairs:
        if "=" not in pair:
            print(f"error: --env expects KEY=VALUE, got {pair!r}", file=sys.stderr)
            raise SystemExit(EXIT_INPUT)
        key, _, value = pair.partition("=")
        env[key] = value
    return env


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    repo = Path(args.repo_dir).resolve()
    if not repo.is_dir():
        print(f"error: --repo-dir does not exist: {repo}", file=sys.stderr)
        return EXIT_INPUT
    if args.changes is None and not (args.owner and args.name and args.pr):
        print("error: give either --changes or --owner/--name/--pr", file=sys.stderr)
        return EXIT_INPUT

    config = PipelineConfig(
        repo=repo,
        workdir=Path(args.workdir).resolve(),
        python=args.python,
        env=_extra_env(repo, args.env),
        changes=Path(args.changes).resolve() if args.changes else None,
        behavior_file=Path(args.behavior_file).resolve() if args.behavior_file else None,
        mapping_file=Path(args.mapping_file).resolve() if args.mapping_file else None,
        adequacy_file=Path(args.adequacy_file).resolve() if args.adequacy_file else None,
        pr=args.pr,
        owner=args.owner,
        name=args.name,
        test_root=args.test_root,
        db=Path(args.db).resolve() if args.db else None,
        skip=tuple(args.skip),
        dry_run=args.dry_run,
    )

    try:
        results = run_pipeline(config, output_format=args.format)
    except Exception as exc:  # noqa: BLE001 - surfaced as exit code 1
        print(f"unexpected error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return EXIT_ERROR

    print("pipeline stages:")
    for result in results:
        print(result.row())

    failed = [result for result in results if not result.ok]
    if failed:
        print(
            f"\nstage A unavailable: {failed[0].message}" if failed[0].name == "A" else "\nstage failed",
            file=sys.stderr,
        )
        return EXIT_INPUT if failed[0].name == "A" else EXIT_ERROR

    scored = next((result for result in results if result.name == "E"), None)
    if scored is None or not scored.ok:
        print("stage E did not produce a report", file=sys.stderr)
        return EXIT_ERROR
    print(f"\nreport: {scored.output}")
    return EXIT_OK


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
