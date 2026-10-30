"""Turn a scored change into a short, human-readable explanation.

The output is what a reviewer sees in a PR comment, so it is deliberately
terse: a header line plus at most four body lines.  Everything in it must be
traceable to an upstream record -- the behaviour sentence comes from stage B,
the affected tests from stage C, the problem statement from stage D's evidence
plus this package's own review, and every claim carries a line number where one
exists.

Two rules are enforced in code rather than by review:

* the phrase "missing test" never appears; this tool reports *potential* gaps
  because static evidence can be wrong (see ``docs/related-work.zh.md`` and the
  false-positive discussion in the contract);
* a suggestion only ever describes an input, never a test function body, so the
  project cannot drift into automated test generation.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from join import ScoringUnit
from loader import AdequacyEntry, BehaviorChange
from scoring import LinkScore, UnitScore

__all__ = [
    "Explanation",
    "EvidenceRef",
    "FORBIDDEN_PHRASES",
    "describe_behavior",
    "describe_problem",
    "explain_unit",
    "render_markdown",
    "short_test_name",
    "suggest_input",
]

#: Wording the report must never use.  A gap is a hypothesis, not a verdict.
FORBIDDEN_PHRASES: tuple[str, ...] = ("missing test", "missing tests", "no test exists")

#: How many test names a comment may list before eliding.
MAX_TESTS = 3

_NUMBER_RE = re.compile(r"\d+(?:\.\d+)?")


@dataclass(frozen=True)
class EvidenceRef:
    """A traceable pointer behind one explanation sentence."""

    kind: str
    detail: str
    file_path: str | None = None
    line: int | None = None
    test: str | None = None


@dataclass(frozen=True)
class Explanation:
    """Structured explanation; ``render_markdown`` formats it for a comment."""

    entity: str
    file_path: str
    score: float
    band: str
    confidence: str
    changed_behavior: str
    affected_tests: tuple[str, ...] = ()
    problem: str = ""
    suggested_input: str | None = None
    evidence: tuple[EvidenceRef, ...] = ()
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def one_liner(self) -> str:
        return f"Potential test gap in `{self.entity}` ({self.band}, {self.score:g}/100)"

    @property
    def subject(self) -> str:
        return f"{self.file_path}::{self.entity}"


def short_test_name(node_id: str) -> str:
    """Reduce a pytest node ID to the test name a reader recognises."""
    name = node_id.split("::")[-1]
    return name.split("[", 1)[0]


def _expression_of(entry: AdequacyEntry | None) -> str | None:
    """First asserted expression D reported, used to quote the weak evidence."""
    if entry is None:
        return None
    for item in entry.evidence:
        assertion = item.get("assertion")
        if isinstance(assertion, Mapping):
            expression = assertion.get("expression")
            if isinstance(expression, str) and expression.strip():
                return expression.strip()
    return None


def _first_call(entry: AdequacyEntry | None) -> str | None:
    """First call expression D reported, used to anchor a suggested input."""
    if entry is None:
        return None
    for item in entry.evidence:
        calls = item.get("calls")
        if not isinstance(calls, list):
            continue
        for call in calls:
            if isinstance(call, Mapping):
                expression = call.get("expression")
                if isinstance(expression, str) and expression.strip():
                    return expression.strip()
    return None


def describe_behavior(behavior: BehaviorChange) -> str:
    """One sentence describing what changed, phrased for a reviewer."""
    kind = behavior.type
    if kind == "RETURN_BEHAVIOR_CHANGE":
        old = behavior.detail("old_expression")
        new = behavior.detail("new_expression")
        if isinstance(old, str) and isinstance(new, str):
            return f"return expression changed: `{old}` -> `{new}`"
        return "returned value changed"
    if kind == "BOUNDARY_CHANGE":
        variable = behavior.detail("variable")
        operator = behavior.detail("operator")
        old = behavior.detail("old_boundary")
        new = behavior.detail("new_boundary")
        if variable is not None and operator is not None:
            return f"comparison boundary changed: `{variable} {operator} {old}` -> `{variable} {operator} {new}`"
        return "comparison boundary changed"
    if kind == "EXCEPTION_BEHAVIOR_CHANGE":
        operation = behavior.detail("operation") or "changed"
        new_type = behavior.detail("new_exception_type") or behavior.detail("exception_type")
        old_type = behavior.detail("old_exception_type")
        if isinstance(new_type, str):
            if isinstance(old_type, str) and old_type != new_type:
                return f"exception {operation}: `{old_type}` -> `{new_type}`"
            return f"exception behaviour {operation}: `{new_type}`"
        return f"exception behaviour {operation}"
    if kind in {"NEW_BRANCH", "MODIFIED_BRANCH"}:
        condition = behavior.detail("condition") or behavior.detail("new_condition")
        effect = behavior.detail("effect") or behavior.detail("new_effect")
        prefix = "new branch" if kind == "NEW_BRANCH" else "branch changed"
        if isinstance(condition, str) and isinstance(effect, str):
            return f"{prefix}: `{condition}` -> `{effect}`"
        if isinstance(condition, str):
            return f"{prefix}: `{condition}`"
        return prefix
    if kind == "DELETED_BRANCH":
        return "a branch was removed"
    return f"{kind.lower().replace('_', ' ')}"


def describe_problem(link: LinkScore) -> str:
    """Explain why the existing tests are judged insufficient."""
    level = link.level
    entry = link.adequacy
    expression = _expression_of(entry)

    if link.downgraded == "precision_blind_evidence":
        quote = f"`{expression}`" if expression else "the matching assertion"
        return (
            f"{quote} uses integer inputs, so it cannot tell the two implementations apart"
        )
    if link.downgraded == "all_evidence_weak":
        return "the assertions D matched do not actually check this call"
    if level is not None and level <= 0.0:
        return "no test reaches this change"
    if level is not None and level <= 1.0:
        return "related tests exist, but none of them reaches the changed behaviour"
    if link.adequacy is not None and link.adequacy.status == "no_matching_assertion":
        return "a test executes the changed code but never asserts its effect"
    if link.adequacy is not None and link.adequacy.status == "weak_assertions":
        return "the matching assertions are too weak to observe the changed effect"
    if link.rule_hits:
        return link.rule_hits[0].reason
    if link.adequacy is None:
        return "no adequacy analysis was available, so coverage is assumed"
    return "the changed behaviour is asserted, no gap detected"


def suggest_input(link: LinkScore) -> str | None:
    """Suggest an *input*, never a test body.

    A precision change needs a fractional example; an exception change needs an
    input that triggers it.  The suggestion is anchored on the call D already
    found so it stays concrete, and it is labelled as an example downstream.
    """
    call = _first_call(link.adequacy)
    if link.downgraded == "precision_blind_evidence" and call:
        numbers = _NUMBER_RE.findall(call)
        if numbers:
            last = numbers[-1]
            try:
                nudged = f"{float(last) + 0.05:.2f}"
            except ValueError:  # pragma: no cover - regex guarantees a float
                return None
            index = call.rfind(last)
            return call[:index] + nudged + call[index + len(last):]
    if link.behavior.type == "EXCEPTION_BEHAVIOR_CHANGE":
        new_type = link.behavior.detail("new_exception_type") or link.behavior.detail("exception_type")
        if isinstance(new_type, str):
            return f"an input that makes the new `{new_type}` branch run"
    if link.behavior.type == "BOUNDARY_CHANGE":
        boundary = link.behavior.detail("new_boundary")
        if boundary is not None:
            return f"inputs on both sides of the new boundary `{boundary}`"
    return None


def _evidence_refs(unit: ScoringUnit, link: LinkScore) -> tuple[EvidenceRef, ...]:
    refs: list[EvidenceRef] = [
        EvidenceRef(
            kind="changed_entity",
            detail=f"{unit.change.entity_name} ({unit.change.change_category or unit.change.change_type})",
            file_path=unit.change.file_path,
            line=unit.change.line_start,
        )
    ]
    entry = link.adequacy
    if entry is not None:
        for item in entry.evidence:
            node = item.get("nodeid")
            line = item.get("line")
            refs.append(
                EvidenceRef(
                    kind=str(item.get("kind") or "assertion"),
                    detail=str(item.get("strength") or ""),
                    test=node if isinstance(node, str) else None,
                    line=line if isinstance(line, int) else None,
                )
            )
    return tuple(refs)


def explain_unit(unit_score: UnitScore) -> Explanation:
    """Build the explanation for one scored change."""
    unit = unit_score.unit
    if not unit_score.links:
        return Explanation(
            entity=unit.change.entity_name,
            file_path=unit.change.file_path,
            score=unit_score.score,
            band=unit_score.band,
            confidence=unit_score.confidence.level,
            changed_behavior="no behaviour change was detected for this entity",
            problem=unit_score.unscored_reason or "not analysed",
            notes=(),
        )

    link = max(unit_score.links, key=lambda item: item.score)
    tests = tuple(dict.fromkeys(short_test_name(test) for test in unit.tests))[:MAX_TESTS]
    problem = describe_problem(link)

    notes: list[str] = []
    if link.assumed_level:
        notes.append("adequacy level assumed; stage D output missing")
    if link.adequacy is not None:
        for recommendation in link.adequacy.recommendations[:1]:
            notes.append(recommendation)

    explanation = Explanation(
        entity=unit.change.entity_name,
        file_path=unit.change.file_path,
        score=unit_score.score,
        band=unit_score.band,
        confidence=unit_score.confidence.level,
        changed_behavior=describe_behavior(link.behavior),
        affected_tests=tests,
        problem=problem,
        suggested_input=suggest_input(link),
        evidence=_evidence_refs(unit, link),
        notes=tuple(notes),
    )
    assert_forbidden_absent(explanation)
    return explanation


def assert_forbidden_absent(explanation: Explanation) -> None:
    """Fail loudly if a forbidden phrase slipped into the generated text."""
    text = " ".join(
        [
            explanation.problem,
            explanation.changed_behavior,
            explanation.suggested_input or "",
            " ".join(explanation.notes),
        ]
    ).lower()
    for phrase in FORBIDDEN_PHRASES:
        if phrase in text:
            raise AssertionError(f"forbidden phrasing {phrase!r} in explanation for {explanation.entity}")


def render_markdown(explanation: Explanation, *, max_lines: int = 5) -> str:
    """Render the explanation as a compact markdown block for a PR comment."""
    header = (
        f"**{explanation.subject}** — Risk: {explanation.band} "
        f"(GapScore {explanation.score:g}/100, confidence {explanation.confidence})"
    )
    body = [f"Changed behavior : {explanation.changed_behavior}"]
    if explanation.affected_tests:
        body.append("Affected tests   : " + ", ".join(explanation.affected_tests))
    body.append(f"Problem          : {explanation.problem}")
    if explanation.suggested_input:
        body.append(f"Suggested input  : {explanation.suggested_input}")
    return "\n".join([header, *body[:max_lines]])
