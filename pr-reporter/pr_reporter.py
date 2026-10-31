#!/usr/bin/env python3
"""Member F: local A-E integration, PR feedback, and frozen acceptance evaluation."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

from integration import command, read_json, run_analysis, score_reports, write_json
from rendering import render_comment, render_failure, render_html, validate_report


def save_outputs(out, report, top):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    validate_report(report)
    comment = render_comment(report, top)
    write_json(out / "gap_report.json", report)
    (out / "gap_report.md").write_text(comment, encoding="utf-8")
    (out / "gap_report.html").write_text(render_html(report, comment), encoding="utf-8")
    (out / "failure.txt").unlink(missing_ok=True)
    return comment


def publish(args, report, body):
    from github_client import GitHubClient
    if report:
        pr = report["pr"]
        if (pr.get("repo") or "").lower() != (args.repo_name or "").lower() or pr.get("number") != args.pr:
            raise ValueError("Publication target must match report repository and PR")
        if not args.expected_head or pr.get("head_sha") != args.expected_head:
            raise ValueError("Publication head must match the report head SHA")
    result = GitHubClient(os.environ.get("GITHUB_TOKEN")).publish(args.repo_name, args.pr, body, expected_head=args.expected_head)
    print(json.dumps(result))


def demo(out):
    """Create a tiny real Git history and collect real baseline testmon data."""
    with tempfile.TemporaryDirectory(prefix="testpilot-demo-") as temp:
        root = Path(temp).resolve()
        command(["git", "init", "-q"], root)
        (root / "payment.py").write_text('def withdraw(balance, amount):\n    return round(balance - amount, 2)\n', encoding="utf-8")
        (root / "test_payment.py").write_text('from payment import withdraw\n\ndef test_integer():\n    assert withdraw(100, 30) == 70\n', encoding="utf-8")
        command(["git", "add", "payment.py", "test_payment.py"], root)
        command(["git", "-c", "user.name=TestPilot Demo", "-c", "user.email=demo@example.invalid", "commit", "-qm", "Baseline"], root)
        (root / "payment.py").write_text('def withdraw(balance, amount):\n    return round(balance - amount, 1)\n', encoding="utf-8")
        command(["git", "add", "payment.py"], root)
        command(["git", "-c", "user.name=TestPilot Demo", "-c", "user.email=demo@example.invalid", "commit", "-qm", "Precision change"], root)
        return run_analysis(root, "HEAD~1", "HEAD", out, collect_baseline=True, pytest_targets=("test_payment.py",))


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    sub = result.add_subparsers(dest="operation", required=True)
    run = sub.add_parser("run", help="analyse two local commits using the real A-E APIs")
    run.add_argument("--repo-dir", required=True)
    run.add_argument("--base", required=True)
    run.add_argument("--head", required=True)
    run.add_argument("--collect-baseline", action="store_true", help="explicitly execute BASE tests in an isolated clone")
    run.add_argument("--db")
    run.add_argument("--db-base-sha")
    run.add_argument("--testmon-root", default=".")
    run.add_argument("--pytest-target", action="append", default=[])
    run.add_argument("--timeout", type=int, default=120)
    report = sub.add_parser("report", help="render an existing E report or an explicit failure")
    source = report.add_mutually_exclusive_group(required=True)
    source.add_argument("--report")
    source.add_argument("--failure-file")
    score = sub.add_parser("score", help="consume saved A-D reports and call E")
    score.add_argument("--changes", required=True)
    score.add_argument("--behavior")
    score.add_argument("--mapping")
    score.add_argument("--adequacy")
    sub.add_parser("demo", help="real local Git + testmon + A-E demo; no network or publishing")
    evaluation = sub.add_parser("evaluate", help="measure frozen synthetic acceptance cases")
    evaluation.add_argument("--manifest", default=str(Path(__file__).parent / "benchmarks" / "contract.json"))
    for name, item in (("run", run), ("report", report), ("score", score), ("demo", sub.choices["demo"]), ("evaluate", evaluation)):
        item.add_argument("--out", required=True)
        if name != "evaluate":
            item.add_argument("--top", type=int, default=5)
            item.add_argument("--fail-on-high", action="store_true")
        if name in {"run", "report", "score"}:
            item.add_argument("--repo-name", help="owner/repository; required for publication")
            item.add_argument("--pr", type=int)
            item.add_argument("--publish", action="store_true", help="explicitly upsert the PR comment")
            item.add_argument("--expected-head", help="full analysed head SHA; required for publication")
    return result


def main(argv=None):
    args = parser().parse_args(argv)
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    try:
        if hasattr(args, "top") and not 1 <= args.top <= 20:
            raise ValueError("top must be between 1 and 20")
        if args.operation == "evaluate":
            from evaluation import evaluate
            result = evaluate(args.manifest, out)
            print(json.dumps({"passed": result["passed"], "timing": result["timing"]}))
            return 0 if result["passed"] else 3
        if args.operation == "report" and args.failure_file:
            # A failed upstream run has no trustworthy report to validate.
            body = render_failure(Path(args.failure_file).read_text(encoding="utf-8-sig"))
            (out / "gap_report.md").write_text(body, encoding="utf-8")
            if args.publish:
                publish(args, None, body)
            return 1
        if args.operation == "run":
            if args.timeout <= 0:
                raise ValueError("timeout must be positive")
            report = run_analysis(args.repo_dir, args.base, args.head, out, repo_name=args.repo_name or "", pr=args.pr,
                collect_baseline=args.collect_baseline, db=args.db, db_base_sha=args.db_base_sha,
                testmon_root=args.testmon_root, pytest_targets=args.pytest_target, timeout=args.timeout)
        elif args.operation == "score":
            report = score_reports(args.changes, args.behavior, args.mapping, args.adequacy)
        elif args.operation == "demo":
            report = demo(out)
        else:
            report = read_json(args.report)
        body = save_outputs(out, report, args.top)
        if getattr(args, "publish", False):
            publish(args, report, body)
        summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
        if summary_path:
            with Path(summary_path).open("a", encoding="utf-8") as stream:
                stream.write(body + "\n")
        print(json.dumps({"report": str(out / "gap_report.json"), "summary": report["summary"]}))
        return 3 if args.fail_on_high and report["summary"]["gaps_high"] else 0
    except (ValueError, OSError, KeyError, TypeError, ImportError, subprocess.TimeoutExpired) as exc:
        (out / "failure.txt").write_text(f"Analysis unavailable ({type(exc).__name__}). Inspect the configuration, dependencies and input schemas.\n", encoding="utf-8")
        (out / "gap_report.md").write_text(render_failure(type(exc).__name__ + ": " + str(exc)), encoding="utf-8")
        print(f"TestPilot input/execution error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        (out / "failure.txt").write_text(f"Analysis failed ({type(exc).__name__}). No conclusion is available.\n", encoding="utf-8")
        (out / "gap_report.md").write_text(render_failure(type(exc).__name__), encoding="utf-8")
        print(f"TestPilot failed ({type(exc).__name__}); see artifacts.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

