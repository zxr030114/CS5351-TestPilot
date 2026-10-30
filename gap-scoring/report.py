"""Assemble the scored changes into the report E hands downstream.

``scoring`` answers "how big is the gap"; this module decides what actually goes
into the artefact: which gaps are worth showing, how they are ordered, what the
summary counts look like, and how the whole thing renders as a PR comment.

The JSON shape follows ``docs/gap-scoring-contract.zh.md`` §4.  Field names are a
public contract with the reporting stage (member F), so they are spelled out here
once rather than assembled ad hoc at each call site.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from explain import Explanation, explain_unit, render_markdown
from join import JoinResult
from loader import PipelineInputs
from scoring import UnitScore, summarize

__all__ = [
    "REPORT_SCHEMA_VERSION",
    "build_report",
    "render_report_markdown",
    "status_line",
]

REPORT_SCHEMA_VERSION = "1.0"
MODULE_NAME = "test_gap_scoring"

#: Bands worth listing individually, ordered by severity.
REPORTABLE_BANDS = ("HIGH", "MEDIUM", "LOW")


@dataclass(frozen=True)
class ReportOptions:
    """What to include in the report."""

    top: int | None = None
    include_none: bool = False
    sources: Mapping[str, str] | None = None


def entity_key(unit_score: UnitScore) -> str:
    """Stable identifier used by the report and by downstream filtering."""
    change = unit_score.change
    return f"{change.file_path}::{change.entity_name}@{change.line_start}"


def _gap_payload(unit_score: UnitScore, explanation: Explanation) -> dict[str, Any]:
    link = max(unit_score.links, key=lambda item: item.score)
    change = unit_score.change
    return {
        "entity_key": entity_key(unit_score),
        "file_path": change.file_path,
        "entity_name": change.entity_name,
        "entity_type": change.entity_type,
        "line_start": change.line_start,
        "line_end": change.line_end,
        "gap_score": unit_score.score,
        "risk": unit_score.band,
        "confidence": unit_score.confidence.level,
        "confidence_signals": list(unit_score.confidence.signals),
        "components": dict(link.components),
        "rule_hits": [hit.code for hit in unit_score.rule_hits],
        "affected_tests": list(explanation.affected_tests),
        "explanation": {
            "one_liner": explanation.one_liner,
            "changed_behavior": explanation.changed_behavior,
            "problem": explanation.problem,
            "suggested_test_input": explanation.suggested_input,
            "evidence": [
                {
                    "kind": ref.kind,
                    "detail": ref.detail,
                    "file_path": ref.file_path,
                    "line": ref.line,
                    "test": ref.test,
                }
                for ref in explanation.evidence
            ],
        },
    }


def _unscored_payload(unit_score: UnitScore) -> dict[str, Any]:
    return {
        "entity_key": entity_key(unit_score),
        "file_path": unit_score.change.file_path,
        "entity_name": unit_score.change.entity_name,
        "reason": unit_score.unscored_reason,
    }


def _reportable(scores: Iterable[UnitScore], options: ReportOptions) -> list[UnitScore]:
    selected = [
        item
        for item in scores
        if item.scored and (options.include_none or item.band in REPORTABLE_BANDS)
    ]
    selected.sort(key=lambda item: (-item.score, entity_key(item)))
    if options.top is not None:
        selected = selected[: options.top]
    return selected


def build_report(
    inputs: PipelineInputs,
    join_result: JoinResult,
    scores: Sequence[UnitScore],
    options: ReportOptions | None = None,
) -> dict[str, Any]:
    """Build the JSON-serialisable report.

    Args:
        inputs: Loaded pipeline reports, used for the PR header and the summary.
        join_result: Join output, used for coverage and orphan statistics.
        scores: Per-change scores, in A's order.
        options: Reporting knobs (top-N, include fully covered changes, sources).

    Returns:
        A dict ready for ``json.dump``.
    """
    options = options or ReportOptions()
    reportable = _reportable(scores, options)
    gaps = [_gap_payload(item, explain_unit(item)) for item in reportable]
    unscored = [_unscored_payload(item) for item in scores if not item.scored]

    summary = summarize(scores)
    summary.update(
        {
            "reported_gaps": len(gaps),
            "mapping_coverage": None if join_result.coverage is None else round(join_result.coverage, 3),
            "orphan_behaviors": len(join_result.orphan_behaviors),
        }
    )

    repo = inputs.changes.repo
    payload: dict[str, Any] = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "module": MODULE_NAME,
        "pr": {
            "repo": inputs.changes.repo_name,
            "number": inputs.changes.pr_number,
            "base_sha": repo.get("base_sha") if isinstance(repo.get("base_sha"), str) else None,
            "head_sha": repo.get("head_sha") if isinstance(repo.get("head_sha"), str) else None,
        },
        "inputs": {
            "behavior": "missing" if inputs.behavior is None else "loaded",
            "mapping": "missing" if inputs.mapping is None else "loaded",
            "adequacy": "missing" if inputs.adequacy is None else "loaded",
        },
        "summary": summary,
        "gaps": gaps,
        "unscored": unscored,
        "problems": [_problem_payload(problem) for problem in (*inputs.problems, *join_result.problems)],
    }
    if options.sources:
        payload["sources"] = dict(options.sources)
    return payload


def _problem_payload(problem) -> dict[str, Any]:
    return {
        "level": problem.level,
        "code": problem.code,
        "message": problem.message,
        "context": problem.context,
    }


def status_line(report: Mapping[str, Any]) -> str:
    """One-line summary for a check name, commit status or comment title.

    Deliberately plain text with no markdown, because CI systems render check
    names literally.  Three shapes:

    * nothing analysable -> says so instead of implying the code is clean;
    * analysed but no gap -> "no potential test gaps";
    * otherwise -> counts, average and how much of the PR was scored.
    """
    summary = report.get("summary", {})
    total = int(summary.get("changes_total", 0) or 0)
    scored = int(summary.get("changes_scored", 0) or 0)

    if scored == 0:
        return f"No analysable changes ({total} change(s) in this PR)"

    high = int(summary.get("gaps_high", 0) or 0)
    medium = int(summary.get("gaps_medium", 0) or 0)
    low = int(summary.get("gaps_low", 0) or 0)
    average = summary.get("average_score", 0)
    average_text = f"{float(average):g}"

    if high + medium + low == 0:
        return f"No potential test gaps in {scored} scored change(s) (avg {average_text}/100)"

    return (
        f"Test gaps: {high} high, {medium} medium, {low} low "
        f"(avg {average_text}/100, {scored}/{total} changes scored)"
    )


def render_report_markdown(report: Mapping[str, Any], *, max_gaps: int = 5) -> str:
    """Render the report as a PR comment.

    The comment leads with the summary so a reviewer can decide whether to read
    on, then lists the worst gaps.  Everything beyond ``max_gaps`` is summarised
    rather than dropped silently.
    """
    pr = report.get("pr", {})
    summary = report.get("summary", {})
    header = f"## Test Gap Report — {pr.get('repo') or 'unknown repo'}"
    if pr.get("number") is not None:
        header += f" #{pr['number']}"

    lines = [
        header,
        "",
        f"- changes analysed: **{summary.get('changes_scored', 0)}** / {summary.get('changes_total', 0)}",
        f"- gaps: **{summary.get('gaps_high', 0)} high**, {summary.get('gaps_medium', 0)} medium, "
        f"{summary.get('gaps_low', 0)} low",
        f"- average score: **{summary.get('average_score', 0)}** / 100",
    ]
    if summary.get("mapping_coverage") is not None:
        lines.append(f"- mapping coverage: {summary['mapping_coverage']:.0%}")
    if report.get("unscored"):
        lines.append(f"- not scored: {len(report['unscored'])} change(s), see report JSON")

    gaps = report.get("gaps", [])
    if not gaps:
        lines.extend(["", "_No potential test gaps above the reporting threshold._"])
        return "\n".join(lines)

    lines.append("")
    for gap in gaps[:max_gaps]:
        lines.append(
            render_markdown(_explanation_from_payload(gap), max_lines=4)
        )
        lines.append("")
    if len(gaps) > max_gaps:
        lines.append(f"_... and {len(gaps) - max_gaps} more gap(s) in the report JSON._")
    return "\n".join(lines).rstrip() + "\n"


def _explanation_from_payload(gap: Mapping[str, Any]) -> Explanation:
    """Rebuild the renderable explanation from the JSON payload."""
    explanation = gap.get("explanation", {})
    return Explanation(
        entity=gap.get("entity_name", ""),
        file_path=gap.get("file_path", ""),
        score=gap.get("gap_score", 0),
        band=gap.get("risk", "NONE"),
        confidence=gap.get("confidence", "HIGH"),
        changed_behavior=explanation.get("changed_behavior", ""),
        affected_tests=tuple(gap.get("affected_tests", ())),
        problem=explanation.get("problem", ""),
        suggested_input=explanation.get("suggested_test_input"),
    )
