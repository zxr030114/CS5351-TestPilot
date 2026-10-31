"""Validate E's public report contract and render bounded, escaped PR feedback."""
from __future__ import annotations

import html
import math
import re

MARKER = "<!-- testpilot:pr-report:v1 -->"
LIMIT = 60000  # Leave room below GitHub's issue-comment body limit.


def validate_report(report):
    if not isinstance(report, dict) or report.get("schema_version") != "1.0" or report.get("module") != "test_gap_scoring":
        raise ValueError("Expected E's test_gap_scoring schema 1.0 report")
    for key, kind in (("summary", dict), ("inputs", dict), ("pr", dict), ("gaps", list), ("unscored", list), ("problems", list)):
        if not isinstance(report.get(key), kind):
            raise ValueError(f"Invalid report field: {key}")
    summary = report["summary"]
    for key in ("changes_total", "changes_scored", "gaps_high", "gaps_medium", "gaps_low"):
        if type(summary.get(key)) is not int or summary[key] < 0:
            raise ValueError(f"Invalid summary count: {key}")
    if summary["changes_scored"] > summary["changes_total"]:
        raise ValueError("Scored count exceeds total changes")
    if sum(summary[key] for key in ("gaps_high", "gaps_medium", "gaps_low")) > summary["changes_scored"]:
        raise ValueError("Gap counts exceed scored changes")
    for name in ("behavior", "mapping", "adequacy"):
        if report["inputs"].get(name) not in {"loaded", "missing"}:
            raise ValueError(f"Invalid input state: {name}")
    for gap in report["gaps"]:
        if not isinstance(gap, dict) or gap.get("risk") not in {"HIGH", "MEDIUM", "LOW", "NONE"} or gap.get("confidence") not in {"HIGH", "MEDIUM", "LOW"}:
            raise ValueError("Invalid gap risk/confidence")
        score = gap.get("gap_score")
        if type(score) not in (int, float) or not math.isfinite(score) or not 0 <= score <= 100:
            raise ValueError("Invalid gap score")
        if not isinstance(gap.get("explanation"), dict):
            raise ValueError("Missing gap explanation")
    for band in ("HIGH", "MEDIUM", "LOW"):
        if sum(item["risk"] == band for item in report["gaps"]) > summary["gaps_" + band.lower()]:
            raise ValueError("Visible gaps disagree with summary counts")
    return report


def safe_text(value, limit=700):
    """Treat code, model text and filenames as data; suppress mentions/HTML."""
    text = str(value or "")
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", text)
    text = html.escape(text, quote=True).replace("@", "&#64;")
    text = re.sub(r"([\\`*_{}\[\]()#+.!|>~-])", r"\\\1", text)
    text = " ".join(text.split())
    return text[:limit] + ("…" if len(text) > limit else "")


def render_comment(report, top=5):
    validate_report(report)
    if not 1 <= top <= 20:
        raise ValueError("top must be between 1 and 20")
    s, pr = report["summary"], report["pr"]
    input_states = {key: report["inputs"][key] for key in ("behavior", "mapping", "adequacy")}
    missing = [key for key, state in input_states.items() if state == "missing"]
    lines = [MARKER, "## TestPilot — Potential Test Gaps", "",
             f"Repository: {safe_text(pr.get('repo') or 'unspecified')} | PR: {safe_text(pr.get('number') or 'local')}",
             f"Revision: {safe_text(pr.get('head_sha') or 'not recorded', 64)}", "",
             f"- Changes scored: **{s['changes_scored']}/{s['changes_total']}**",
             f"- Potential gaps: **{s['gaps_high']} high**, {s['gaps_medium']} medium, {s['gaps_low']} low",
             "- Inputs: " + ", ".join(f"{name}={state}" for name, state in input_states.items())]
    if missing:
        lines += ["", "**Degraded analysis:** missing " + ", ".join(missing) + ". Missing data is not evidence of adequate testing."]
    if s["changes_scored"] == 0:
        lines += ["", "**No changes could be scored. No test-adequacy conclusion is available.**"]
    elif not report["gaps"]:
        lines += ["", "No potential gaps are listed at the reporting threshold. This does not prove test adequacy."]
    gaps = sorted(report["gaps"], key=lambda item: -item["gap_score"])
    for gap in gaps[:top]:
        explanation = gap["explanation"]
        lines += ["", f"### {safe_text(gap.get('file_path'))}::{safe_text(gap.get('entity_name'))}",
                  f"Risk: **{gap['risk']}** | Score: **{gap['gap_score']:g}/100** | Confidence: **{gap['confidence']}**"]
        for label, key in (("Changed behavior", "changed_behavior"), ("Potential missing verification", "problem"), ("Suggested test input", "suggested_test_input")):
            if explanation.get(key):
                lines.append(f"- {label}: {safe_text(explanation[key])}")
        tests = gap.get("affected_tests", [])
        if isinstance(tests, list) and tests:
            lines.append("- Related tests: " + "; ".join(safe_text(test, 160) for test in tests[:3]))
    remaining = max(0, s["gaps_high"] + s["gaps_medium"] + s["gaps_low"] - min(top, len(gaps)))
    if remaining:
        lines += ["", f"{remaining} additional gap(s) are omitted here; inspect the JSON artifact."]
    if report["unscored"]:
        lines += ["", f"Unscored changes: {len(report['unscored'])}. See the JSON artifact for reasons."]
    problems = report["problems"]
    if problems:
        lines += ["", f"Analysis warnings/errors: {len(problems)}."]
        for problem in problems[:4]:
            if isinstance(problem, dict):
                lines.append("- " + safe_text(problem.get("message") or problem.get("code")))
    lines += ["", "Static assertion evidence and historical dependencies do not prove branch execution or correct expected values."]
    return "\n".join(lines)[:LIMIT] + "\n"


def render_failure(message):
    return MARKER + "\n## TestPilot — Analysis failed\n\n" + safe_text(message, 1600) + "\n\nNo test-adequacy conclusion is available. Check the workflow logs and artifacts.\n"


def render_html(report, comment):
    """Offline, script-free artifact; raw report content cannot inject markup."""
    validate_report(report)
    return ('<!doctype html><html lang="en"><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>TestPilot report</title><style>body{max-width:960px;margin:40px auto;padding:0 24px;'
            'font:16px/1.6 system-ui;color:#172033;background:#f5f7fb}pre{white-space:pre-wrap;'
            'overflow-wrap:anywhere;background:white;padding:24px;border-radius:12px}</style>'
            '<h1>TestPilot report</h1><pre>' + html.escape(comment) + '</pre></html>')

