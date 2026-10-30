"""Turn joined scoring units into gap scores.

The model is the one agreed in the project discussion and written up in
``docs/gap-scoring-contract.zh.md`` §3:

    Base  = 100 * (0.45 * BehaviorRisk * ChangeImportance + 0.55 * AdequacyDeficit)
    Bonus = sum of triggered rules, capped at 15
    Gap   = min(100, Base + Bonus)

Every constant lives in :mod:`weights`; this module only applies them, so
calibration never touches the formulas.

Two rules refine the raw table lookups:

* a change whose behaviour a test actually verifies carries no *gap*
  (``SUPPRESS_WHEN_COVERED``), because the residual structural risk is already
  described by ``ChangeImportance`` and is not something a test can close;
* ``no_candidate_test`` on a *matched* mapping means related tests exist but do
  not reach the change, which the contract calls Level 1 rather than Level 0.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

import weights
from confidence import Confidence, assess
from join import JoinResult, ScoringUnit
from loader import AdequacyEntry, BehaviorChange, Change, MappingEntry, PipelineInputs

__all__ = [
    "LevelDecision",
    "LinkScore",
    "RuleHit",
    "ScoreContext",
    "UnitScore",
    "band_for",
    "context_from_pipeline",
    "precision_suspect",
    "resolve_level",
    "importance_for",
    "risk_for",
    "score_unit",
    "score_units",
    "summarize",
]


@dataclass(frozen=True)
class RuleHit:
    """One triggered bonus rule."""

    code: str
    points: float
    reason: str

    def __str__(self) -> str:
        return f"{self.code} (+{self.points:g}): {self.reason}"


@dataclass(frozen=True)
class ScoreContext:
    """Report-wide caveats that apply to every unit.

    Kept separate from :class:`~join.ScoringUnit` because they describe the
    inputs, not the change: an incomplete mapping report lowers confidence for
    every gap it produced, even the ones that looked clean.
    """

    selection_complete: bool = True
    adequacy_input_complete: bool = True

    def signals(self) -> tuple[str, ...]:
        found: list[str] = []
        if not self.selection_complete:
            found.append("selection_incomplete")
        if not self.adequacy_input_complete:
            found.append("input_incomplete")
        return tuple(found)


DEFAULT_CONTEXT = ScoreContext()


def context_from_pipeline(inputs: PipelineInputs) -> ScoreContext:
    """Derive the report-wide caveats from a loaded pipeline."""
    return ScoreContext(
        selection_complete=(
            inputs.mapping.selection_complete if inputs.mapping is not None else True
        ),
        adequacy_input_complete=(
            inputs.adequacy.input_complete if inputs.adequacy is not None else True
        ),
    )


@dataclass(frozen=True)
class LinkScore:
    """Score of one (change, behavior) pair."""

    behavior: BehaviorChange
    adequacy: AdequacyEntry | None
    level: float | None
    deficit: float
    risk: float
    importance: float
    rule_hits: tuple[RuleHit, ...] = ()
    base: float = 0.0
    bonus: float = 0.0
    score: float = 0.0
    band: str = "NONE"
    assumed_level: bool = False
    downgraded: str | None = None
    uncertain: str | None = None
    method: str = "unmatched"
    confidence: Confidence = field(default_factory=lambda: assess(()))

    @property
    def components(self) -> Mapping[str, object]:
        return {
            "behavior_type": self.behavior.type,
            "behavior_risk": self.risk,
            "change_importance": self.importance,
            "adequacy_level": self.level,
            "adequacy_deficit": self.deficit,
            "assumed_level": self.assumed_level,
            "downgraded": self.downgraded,
            "uncertain": self.uncertain,
            "join_method": self.method,
            "confidence": self.confidence.level,
            "confidence_signals": list(self.confidence.signals),
            "base": self.base,
            "bonus": self.bonus,
            "rule_hits": [hit.code for hit in self.rule_hits],
        }


@dataclass(frozen=True)
class UnitScore:
    """Score of one structural change; the highest of its behavior links wins."""

    unit: ScoringUnit
    links: tuple[LinkScore, ...] = ()
    score: float = 0.0
    band: str = "NONE"
    unscored_reason: str | None = None
    confidence: Confidence = field(default_factory=lambda: assess(()))

    @property
    def change(self) -> Change:
        return self.unit.change

    @property
    def scored(self) -> bool:
        return self.unscored_reason is None

    @property
    def rule_hits(self) -> tuple[RuleHit, ...]:
        """Rules from the winning link, de-duplicated by code."""
        if not self.links:
            return ()
        best = max(self.links, key=lambda link: link.score)
        return best.rule_hits


def band_for(score: float) -> str:
    """Map a numeric score onto the reporting band."""
    for threshold, name in weights.BANDS:
        if score >= threshold:
            return name
    return "NONE"  # pragma: no cover - BANDS always ends at 0


def importance_for(change: Change) -> float:
    """Structural importance of *change*; 0.0 for documentation-only changes."""
    category = change.change_category
    if category is not None:
        if category in weights.CHANGE_IMPORTANCE:
            return weights.CHANGE_IMPORTANCE[category]
        return weights.CHANGE_TYPE_IMPORTANCE.get(change.change_type, 0.9)
    return weights.CHANGE_TYPE_IMPORTANCE.get(change.change_type, 0.9)


def risk_for(behavior: BehaviorChange) -> float:
    """Danger of the behavior change kind."""
    return weights.BEHAVIOR_RISK.get(behavior.type, weights.DEFAULT_BEHAVIOR_RISK)


def _level_of(entry: AdequacyEntry | None, mapping: MappingEntry | None) -> tuple[float | None, bool]:
    """Return ``(level, assumed)`` for one adequacy result.

    Kept for callers that only need the pair; :func:`resolve_level` exposes the
    full decision including why a level was lowered.
    """
    decision = resolve_level(entry, mapping)
    return decision.level, decision.assumed


@dataclass(frozen=True)
class LevelDecision:
    """Result of verifying D's self-reported status against its evidence."""

    level: float | None
    assumed: bool = False
    downgraded: str | None = None
    uncertain: str | None = None


_NUMBER_RE = re.compile(r"\d+(?:\.\d+)?")


def _numbers(text: str) -> tuple[float, ...]:
    return tuple(float(match) for match in _NUMBER_RE.findall(text))


def precision_suspect(behavior: BehaviorChange) -> bool:
    """True when the change turns on a numeric literal, e.g. a rounding precision.

    ``round(x, 2)`` -> ``round(x, 1)`` changes behaviour only for inputs whose
    result lands on the dropped digit, so an integer example cannot reveal it.
    """
    old = behavior.detail("old_expression")
    new = behavior.detail("new_expression")
    if not isinstance(old, str) or not isinstance(new, str) or old == new:
        return False
    if "round(" not in old or "round(" not in new:
        return False
    return _numbers(old) != _numbers(new)


def _argument_literals(entry: AdequacyEntry) -> tuple[object, ...]:
    values: list[object] = []
    for item in entry.evidence:
        calls = item.get("calls")
        if not isinstance(calls, list):
            continue
        for call in calls:
            if not isinstance(call, Mapping):
                continue
            arguments = call.get("literal_arguments")
            if isinstance(arguments, list):
                values.extend(arguments)
    return tuple(values)


def _integer_only(entry: AdequacyEntry) -> bool:
    values = _argument_literals(entry)
    if not values:
        return False
    return all(isinstance(value, int) and not isinstance(value, bool) for value in values)


def resolve_level(entry: AdequacyEntry | None, mapping: MappingEntry | None) -> LevelDecision:
    """Decide the adequacy level, trusting evidence over D's headline status.

    D answers ``assertion_evidence_found`` whenever *some* assertion syntactically
    matches the changed call.  Two shapes of evidence do not deserve Level 3:

    * every ``evidence[].strength`` is ``weak_or_mismatched`` -- nothing confirms
      the call, so the change falls back to Level 2 (executed, not checked);
    * the only matching assertion uses integer literals while the change alters a
      numeric literal (rounding precision) -- the example cannot distinguish the
      two implementations, so the change lands at Level 2.5.

    An empty evidence list is not downgraded (D may simply not report it) but is
    flagged ``uncertain`` for the confidence model.
    """
    if entry is None:
        return LevelDecision(weights.DEFAULT_LEVEL, assumed=True)
    if entry.status not in weights.ADEQUACY_STATUS_LEVEL:
        return LevelDecision(None)
    level = weights.ADEQUACY_STATUS_LEVEL[entry.status]
    if level is None:
        return LevelDecision(None)
    if level == 0.0 and mapping is not None and mapping.status == "matched":
        # Related tests exist (C matched them) but none reaches the change.
        level = 1.0
    if level < 3.0:
        return LevelDecision(level)

    strengths = set(entry.strengths)
    if not entry.evidence:
        return LevelDecision(level, uncertain="no_evidence_records")
    if strengths and strengths <= weights.EVIDENCE_WEAK:
        return LevelDecision(weights.LEVEL_ALL_EVIDENCE_WEAK, downgraded="all_evidence_weak")
    if (
        strengths & weights.EVIDENCE_MATCH
        and precision_suspect(entry.change)
        and _integer_only(entry)
    ):
        return LevelDecision(weights.LEVEL_PRECISION_BLIND, downgraded="precision_blind_evidence")
    return LevelDecision(level)


def deficit_for(level: float | None) -> float:
    """Missing-coverage share for an adequacy level."""
    if level is None:
        return weights.DEFAULT_DEFICIT
    return weights.ADEQUACY_DEFICIT.get(level, weights.DEFAULT_DEFICIT)


def _rules(
    unit: ScoringUnit, link, level: float | None
) -> tuple[RuleHit, ...]:
    """Evaluate the five discussion rules plus two mapping-quality hints."""
    hits: list[RuleHit] = []
    mapping = unit.mapping
    behavior = link.behavior
    status = link.adequacy.status if link.adequacy is not None else None
    covered = level is not None and level >= 3.0

    if not covered and behavior.type == "EXCEPTION_BEHAVIOR_CHANGE":
        hits.append(
            RuleHit(
                "exception_without_raises_test",
                weights.RULE_EXCEPTION_WITHOUT_RAISES,
                "exception behaviour changed but no test asserts it with pytest.raises",
            )
        )
    if not covered and behavior.type == "BOUNDARY_CHANGE":
        hits.append(
            RuleHit(
                "boundary_without_boundary_test",
                weights.RULE_BOUNDARY_WITHOUT_TEST,
                "boundary value changed but no boundary test was found",
            )
        )
    if status in {"no_matching_assertion", "weak_assertions"}:
        hits.append(
            RuleHit(
                "executed_without_assertion",
                weights.RULE_EXECUTED_WITHOUT_ASSERTION,
                "a test executes the changed behaviour but does not assert its effect",
            )
        )
    if mapping is not None and mapping.status == "matched" and (level is not None and level <= 1.0):
        hits.append(
            RuleHit(
                "related_tests_do_not_reach",
                weights.RULE_RELATED_TESTS_DO_NOT_REACH,
                "dependency mapping lists related tests but none reaches the change",
            )
        )
    if mapping is not None and mapping.status == "needs_new_tests":
        hits.append(
            RuleHit(
                "new_code_without_baseline",
                weights.RULE_NEW_CODE_WITHOUT_BASELINE,
                "new code has no historical test dependency to rely on",
            )
        )
    if mapping is not None and mapping.status == "file_fallback":
        hits.append(
            RuleHit(
                "file_level_fallback",
                weights.RULE_FILE_LEVEL_FALLBACK,
                "dependency mapping fell back to file granularity",
            )
        )
    return tuple(hits)


def score_link(unit: ScoringUnit, link) -> LinkScore:
    """Score one behavior link of *unit*."""
    decision = resolve_level(link.adequacy, unit.mapping)
    level = decision.level
    deficit = deficit_for(level)
    risk = risk_for(link.behavior)
    importance = importance_for(unit.change)

    hits = _rules(unit, link, level)
    bonus = min(weights.BONUS_CAP, sum(hit.points for hit in hits))

    base = 100.0 * (
        weights.WEIGHT_STRUCTURAL * risk * importance + weights.WEIGHT_COVERAGE * deficit
    )
    total = base + bonus
    if weights.SUPPRESS_WHEN_COVERED and deficit == 0.0 and bonus == 0.0:
        total = 0.0
    if level is None:
        total = 0.0
    total = round(min(100.0, total), 2)

    return LinkScore(
        behavior=link.behavior,
        adequacy=link.adequacy,
        level=level,
        deficit=deficit,
        risk=risk,
        importance=importance,
        rule_hits=hits,
        base=round(base, 2),
        bonus=bonus,
        score=total,
        band=band_for(total),
        assumed_level=decision.assumed,
        downgraded=decision.downgraded,
        uncertain=decision.uncertain,
        method=link.method,
        confidence=assess(_confidence_signals(unit, link, decision)),
    )


def _confidence_signals(unit: ScoringUnit, link, decision: LevelDecision) -> tuple[str, ...]:
    """Caveats that apply to one link's score."""
    signals: list[str] = []
    mapping = unit.mapping
    if mapping is None:
        signals.append("no_mapping")
    elif mapping.status == "file_fallback":
        signals.append("file_fallback")
    elif mapping.status == "unresolved":
        signals.append("mapping_unresolved")
    if link.adequacy is None:
        signals.append("no_adequacy")
    if decision.assumed:
        signals.append("assumed_level")
    if decision.uncertain:
        signals.append("uncertain_evidence")
    if link.method == "function_only":
        signals.append("weak_join")
    return tuple(signals)


def score_unit(unit: ScoringUnit, *, context: ScoreContext = DEFAULT_CONTEXT) -> UnitScore:
    """Score a whole change; the riskiest behavior link represents it."""
    if importance_for(unit.change) == 0.0:
        return UnitScore(
            unit=unit,
            score=0.0,
            band="NONE",
            unscored_reason="documentation_only",
            confidence=assess(context.signals()),
        )
    if not unit.links:
        return UnitScore(
            unit=unit,
            score=0.0,
            band="NONE",
            unscored_reason="no_behavior_change",
            confidence=assess(context.signals()),
        )

    links = tuple(score_link(unit, link) for link in unit.links)
    scored = [link for link in links if link.level is not None]
    if not scored:
        return UnitScore(
            unit=unit,
            links=links,
            score=0.0,
            band="NONE",
            unscored_reason="unsupported_behavior",
            confidence=assess(context.signals()),
        )

    best = max(scored, key=lambda link: link.score)
    combined = assess(tuple(best.confidence.signals) + context.signals())
    return UnitScore(
        unit=unit, links=links, score=best.score, band=best.band, confidence=combined
    )


def score_units(
    join_result: JoinResult, *, context: ScoreContext = DEFAULT_CONTEXT
) -> tuple[UnitScore, ...]:
    """Score every unit of a join result, in A's order."""
    return tuple(score_unit(unit, context=context) for unit in join_result.units)


def summarize(unit_scores: Iterable[UnitScore]) -> dict[str, object]:
    """Aggregate scores for a PR-level summary."""
    scores = list(unit_scores)
    scored = [item for item in scores if item.scored]
    bands = {"HIGH": 0, "MEDIUM": 0, "LOW": 0, "NONE": 0}
    for item in scores:
        bands[item.band] = bands.get(item.band, 0) + 1
    total = sum(item.score for item in scored)
    return {
        "changes_total": len(scores),
        "changes_scored": len(scored),
        "changes_unscored": len(scores) - len(scored),
        "gaps_high": bands["HIGH"],
        "gaps_medium": bands["MEDIUM"],
        "gaps_low": bands["LOW"],
        "gaps_none": bands["NONE"],
        "average_score": round(total / len(scored), 2) if scored else 0.0,
        "max_score": max((item.score for item in scored), default=0.0),
    }
