"""Tests for ``scoring``: the reference 87/100 case, suppression, bands, cap."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import weights  # noqa: E402
from join import BehaviorLink, ScoringUnit  # noqa: E402
from loader import AdequacyEntry, BehaviorChange, Change, MappingEntry  # noqa: E402
from scoring import (  # noqa: E402
    band_for,
    deficit_for,
    importance_for,
    precision_suspect,
    resolve_level,
    risk_for,
    score_unit,
    score_units,
    summarize,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def change(category: str | None = "Logic change", *, name: str = "withdraw", kind: str = "modify") -> Change:
    return Change(
        file_path="mapping-demo/payment.py",
        entity_name=name,
        change_type=kind,
        change_category=category,
        line_start=1,
        line_end=10,
    )


def behavior(kind: str, name: str = "withdraw") -> BehaviorChange:
    return BehaviorChange(type=kind, function=name, new_line=6)


def adequacy(status: str, kind: str = "EXCEPTION_BEHAVIOR_CHANGE", name: str = "withdraw") -> AdequacyEntry:
    return AdequacyEntry(change=behavior(kind, name), status=status)


def mapping(status: str = "matched") -> MappingEntry:
    return MappingEntry(change=change(), status=status, tests=("t",))


def unit(
    *,
    kind: str = "EXCEPTION_BEHAVIOR_CHANGE",
    category: str | None = "Logic change",
    d_status: str | None = "no_matching_assertion",
    c_status: str = "matched",
    with_mapping: bool = True,
) -> ScoringUnit:
    entry = adequacy(d_status, kind) if d_status is not None else None
    return ScoringUnit(
        change=change(category),
        mapping=mapping(c_status) if with_mapping else None,
        links=(BehaviorLink(behavior=behavior(kind), adequacy=entry),),
    )


class ReferenceCase(unittest.TestCase):
    def test_contract_reference_is_87(self) -> None:
        """exception + Logic change + executed-but-unasserted -> 79 + 5 + 3 = 87."""
        result = score_unit(unit())
        link = result.links[0]
        self.assertAlmostEqual(link.base, 79.0)
        self.assertEqual([hit.code for hit in link.rule_hits], [
            "exception_without_raises_test",
            "executed_without_assertion",
        ])
        self.assertAlmostEqual(link.bonus, 8.0)
        self.assertAlmostEqual(link.score, 87.0)
        self.assertEqual(link.band, "HIGH")
        self.assertEqual(result.band, "HIGH")

    def test_components_are_exposed(self) -> None:
        components = score_unit(unit()).links[0].components
        self.assertEqual(components["behavior_risk"], 1.0)
        self.assertEqual(components["change_importance"], 0.9)
        self.assertEqual(components["adequacy_level"], 2.0)
        self.assertEqual(components["adequacy_deficit"], 0.7)


class Suppression(unittest.TestCase):
    def test_covered_change_scores_zero(self) -> None:
        result = score_unit(unit(kind="RETURN_BEHAVIOR_CHANGE", d_status="assertion_evidence_found"))
        self.assertEqual(result.score, 0.0)
        self.assertEqual(result.band, "NONE")
        self.assertTrue(result.scored)

    def test_covered_change_still_gets_rule_bonus(self) -> None:
        """A covered exception change keeps a bonus only if a rule fires."""
        covered = unit(d_status="assertion_evidence_found")
        result = score_unit(covered)
        self.assertEqual(result.links[0].rule_hits, ())
        self.assertEqual(result.score, 0.0)

    def test_uncovered_change_is_not_suppressed(self) -> None:
        result = score_unit(unit(d_status="no_candidate_test"))
        self.assertGreater(result.score, 0.0)


class LevelResolution(unittest.TestCase):
    def test_no_candidate_on_matched_mapping_is_level_one(self) -> None:
        result = score_unit(unit(d_status="no_candidate_test", c_status="matched"))
        self.assertEqual(result.links[0].level, 1.0)

    def test_no_candidate_without_mapping_stays_level_zero(self) -> None:
        result = score_unit(unit(d_status="no_candidate_test", with_mapping=False))
        self.assertEqual(result.links[0].level, 0.0)

    def test_missing_adequacy_assumes_level_two(self) -> None:
        result = score_unit(unit(d_status=None))
        self.assertEqual(result.links[0].level, 2.0)
        self.assertTrue(result.links[0].assumed_level)

    def test_unsupported_behavior_is_unscored(self) -> None:
        result = score_unit(unit(d_status="unsupported_behavior"))
        self.assertFalse(result.scored)
        self.assertEqual(result.unscored_reason, "unsupported_behavior")

    def test_unknown_status_is_unscored(self) -> None:
        result = score_unit(unit(d_status="something_new"))
        self.assertFalse(result.scored)


class UnscoredCases(unittest.TestCase):
    def test_documentation_only(self) -> None:
        result = score_unit(unit(category="Documentation change"))
        self.assertEqual(result.unscored_reason, "documentation_only")
        self.assertEqual(result.score, 0.0)

    def test_no_behavior_links(self) -> None:
        bare = ScoringUnit(change=change(), mapping=mapping(), links=())
        self.assertEqual(score_unit(bare).unscored_reason, "no_behavior_change")


class Bands(unittest.TestCase):
    def test_thresholds(self) -> None:
        self.assertEqual(band_for(100), "HIGH")
        self.assertEqual(band_for(80), "HIGH")
        self.assertEqual(band_for(79.99), "MEDIUM")
        self.assertEqual(band_for(50), "MEDIUM")
        self.assertEqual(band_for(49.99), "LOW")
        self.assertEqual(band_for(20), "LOW")
        self.assertEqual(band_for(19.99), "NONE")


class BaseScore(unittest.TestCase):
    def test_level_zero_exception_is_high(self) -> None:
        result = score_unit(unit(d_status="no_candidate_test", with_mapping=False))
        # base = 100 * (0.45*1.0*0.9 + 0.55*1.0) = 95.5, plus exception rule 5
        self.assertAlmostEqual(result.links[0].base, 95.5)
        self.assertAlmostEqual(result.score, 100.0)  # capped

    def test_boundary_change_bonus(self) -> None:
        result = score_unit(unit(kind="BOUNDARY_CHANGE", d_status="no_matching_assertion"))
        codes = [hit.code for hit in result.links[0].rule_hits]
        self.assertIn("boundary_without_boundary_test", codes)

    def test_new_code_rule(self) -> None:
        result = score_unit(unit(d_status="no_candidate_test", c_status="needs_new_tests", with_mapping=True))
        codes = [hit.code for hit in result.links[0].rule_hits]
        self.assertIn("new_code_without_baseline", codes)

    def test_file_fallback_rule(self) -> None:
        result = score_unit(unit(c_status="file_fallback"))
        codes = [hit.code for hit in result.links[0].rule_hits]
        self.assertIn("file_level_fallback", codes)

    def test_bonus_is_capped(self) -> None:
        with mock.patch.object(weights, "BONUS_CAP", 5.0):
            result = score_unit(unit())
        self.assertEqual(result.links[0].bonus, 5.0)

    def test_score_never_exceeds_100(self) -> None:
        result = score_unit(unit(d_status="no_candidate_test", with_mapping=False))
        self.assertLessEqual(result.score, 100.0)


class TableLookups(unittest.TestCase):
    def test_importance_prefers_category(self) -> None:
        self.assertEqual(importance_for(change("Signature change")), 1.0)
        self.assertEqual(importance_for(change("Logic change")), 0.9)

    def test_importance_falls_back_to_change_type(self) -> None:
        self.assertEqual(importance_for(change(None, kind="add")), 0.7)

    def test_risk_known_and_unknown(self) -> None:
        self.assertEqual(risk_for(behavior("EXCEPTION_BEHAVIOR_CHANGE")), 1.0)
        self.assertEqual(risk_for(behavior("SOMETHING_NEW")), weights.DEFAULT_BEHAVIOR_RISK)

    def test_deficit_unknown_level_uses_default(self) -> None:
        self.assertEqual(deficit_for(9.0), weights.DEFAULT_DEFICIT)


class Ordering(unittest.TestCase):
    """Better evidence must never score higher than worse evidence.

    A sensitivity matrix showed ``weak_assertions`` outscoring
    ``no_matching_assertion`` because it was mapped to level 1.5 (below level 2).
    An assertion that merely fails to check the changed effect is still better
    than no assertion at all, so ``weak_assertions`` sits at 2.5.  These tests
    keep that ordering from silently inverting again.
    """

    STATUSES = [
        "no_candidate_test",
        "no_matching_assertion",
        "weak_assertions",
        "assertion_evidence_found",
    ]

    def test_deficit_decreases_as_level_rises(self) -> None:
        levels = sorted(weights.ADEQUACY_DEFICIT)
        deficits = [weights.ADEQUACY_DEFICIT[level] for level in levels]
        self.assertEqual(deficits, sorted(deficits, reverse=True))
        self.assertEqual(len(set(deficits)), len(deficits))

    def test_weak_assertions_rank_between_executed_and_asserted(self) -> None:
        self.assertGreater(weights.ADEQUACY_STATUS_LEVEL["weak_assertions"], weights.ADEQUACY_STATUS_LEVEL["no_matching_assertion"])
        self.assertLess(weights.ADEQUACY_STATUS_LEVEL["weak_assertions"], weights.ADEQUACY_STATUS_LEVEL["assertion_evidence_found"])

    def test_scores_fall_as_evidence_improves(self) -> None:
        for kind in ("EXCEPTION_BEHAVIOR_CHANGE", "BOUNDARY_CHANGE", "NEW_BRANCH", "RETURN_BEHAVIOR_CHANGE"):
            scores = [score_unit(unit(kind=kind, d_status=status)).score for status in self.STATUSES]
            self.assertEqual(scores, sorted(scores, reverse=True), f"{kind} is not monotonic: {scores}")

    def test_exception_always_outranks_return_for_same_evidence(self) -> None:
        for status in self.STATUSES:
            exception = score_unit(unit(kind="EXCEPTION_BEHAVIOR_CHANGE", d_status=status)).score
            returned = score_unit(unit(kind="RETURN_BEHAVIOR_CHANGE", d_status=status)).score
            self.assertGreaterEqual(exception, returned)


class Aggregation(unittest.TestCase):
    def test_unit_takes_the_riskiest_link(self) -> None:
        covered = BehaviorLink(behavior=behavior("RETURN_BEHAVIOR_CHANGE"), adequacy=adequacy("assertion_evidence_found", "RETURN_BEHAVIOR_CHANGE"))
        uncovered = BehaviorLink(behavior=behavior("EXCEPTION_BEHAVIOR_CHANGE"), adequacy=adequacy("no_matching_assertion"))
        both = ScoringUnit(change=change(), mapping=mapping(), links=(covered, uncovered))
        result = score_unit(both)
        self.assertAlmostEqual(result.score, 87.0)
        self.assertEqual(len(result.links), 2)

    def test_summarize_counts(self) -> None:
        scores = [score_unit(unit()), score_unit(unit(category="Documentation change"))]
        summary = summarize(scores)
        self.assertEqual(summary["changes_total"], 2)
        self.assertEqual(summary["changes_scored"], 1)
        self.assertEqual(summary["gaps_high"], 1)
        self.assertAlmostEqual(summary["average_score"], 87.0)

    def test_summarize_handles_empty(self) -> None:
        summary = summarize([])
        self.assertEqual(summary["average_score"], 0.0)
        self.assertEqual(summary["max_score"], 0.0)


class EvidenceReview(unittest.TestCase):
    """Stage D reports `assertion_evidence_found` optimistically; verify it."""

    @staticmethod
    def entry(
        status: str = "assertion_evidence_found",
        *,
        strengths: tuple[str, ...] = ("potential_match",),
        literals: tuple[object, ...] = (100, 30),
        old: str | None = "round(balance - amount, 2)",
        new: str | None = "round(balance - amount, 1)",
    ) -> AdequacyEntry:
        details = {}
        if old is not None:
            details["old_expression"] = old
        if new is not None:
            details["new_expression"] = new
        behavior_change = BehaviorChange(
            type="RETURN_BEHAVIOR_CHANGE", function="withdraw", new_line=6, details=details
        )
        evidence = tuple(
            {
                "nodeid": f"tests/test_payment.py::test_{index}",
                "strength": strength,
                "calls": [{"target": "payment.withdraw", "literal_arguments": list(literals)}],
            }
            for index, strength in enumerate(strengths)
        )
        return AdequacyEntry(change=behavior_change, status=status, evidence=evidence)

    def test_all_weak_evidence_downgrades_to_level_two(self) -> None:
        decision = resolve_level(self.entry(strengths=("weak_or_mismatched", "weak_or_mismatched")), None)
        self.assertEqual(decision.level, 2.0)
        self.assertEqual(decision.downgraded, "all_evidence_weak")

    def test_one_potential_match_keeps_level_three(self) -> None:
        decision = resolve_level(
            self.entry(strengths=("potential_match", "weak_or_mismatched"), old=None, new=None), None
        )
        self.assertEqual(decision.level, 3.0)

    def test_precision_blind_integer_evidence_downgrades(self) -> None:
        decision = resolve_level(self.entry(), None)
        self.assertEqual(decision.level, 2.5)
        self.assertEqual(decision.downgraded, "precision_blind_evidence")

    def test_fractional_arguments_keep_level_three(self) -> None:
        decision = resolve_level(self.entry(literals=(100.5, 30.25)), None)
        self.assertEqual(decision.level, 3.0)

    def test_non_precision_change_keeps_level_three(self) -> None:
        decision = resolve_level(self.entry(old="balance - amount", new="balance + amount"), None)
        self.assertEqual(decision.level, 3.0)

    def test_empty_evidence_is_uncertain_but_not_downgraded(self) -> None:
        entry = AdequacyEntry(change=behavior("RETURN_BEHAVIOR_CHANGE"), status="assertion_evidence_found")
        decision = resolve_level(entry, None)
        self.assertEqual(decision.level, 3.0)
        self.assertEqual(decision.uncertain, "no_evidence_records")

    def test_precision_suspect_helper(self) -> None:
        precision = BehaviorChange(
            type="RETURN_BEHAVIOR_CHANGE",
            function="f",
            details={"old_expression": "round(x, 2)", "new_expression": "round(x, 1)"},
        )
        other = BehaviorChange(
            type="RETURN_BEHAVIOR_CHANGE",
            function="f",
            details={"old_expression": "x - y", "new_expression": "x + y"},
        )
        self.assertTrue(precision_suspect(precision))
        self.assertFalse(precision_suspect(other))

    def test_downgraded_level_lowers_the_score(self) -> None:
        confident = score_unit(unit(kind="RETURN_BEHAVIOR_CHANGE", d_status="assertion_evidence_found"))
        blind = ScoringUnit(
            change=change(),
            mapping=mapping(),
            links=(BehaviorLink(behavior=behavior("RETURN_BEHAVIOR_CHANGE"), adequacy=self.entry()),),
        )
        self.assertEqual(confident.score, 0.0)
        self.assertGreater(score_unit(blind).score, 0.0)

    def test_components_report_the_downgrade(self) -> None:
        blind = ScoringUnit(
            change=change(),
            mapping=mapping(),
            links=(BehaviorLink(behavior=behavior("RETURN_BEHAVIOR_CHANGE"), adequacy=self.entry()),),
        )
        components = score_unit(blind).links[0].components
        self.assertEqual(components["downgraded"], "precision_blind_evidence")
        self.assertEqual(components["adequacy_level"], 2.5)


class FixturePipeline(unittest.TestCase):
    def test_fixture_downgrades_precision_blind_evidence(self) -> None:
        """D says covered, but `withdraw(100, 30) == 70` cannot see a rounding change.

        Before the evidence review this fixture scored 0.0 (silently trusting D's
        headline status).  It must now be flagged as a low-severity gap.
        """
        from join import join_inputs
        from loader import load_pipeline

        inputs = load_pipeline(
            FIXTURES / "changes.json",
            FIXTURES / "behavior.json",
            FIXTURES / "mapping_result.json",
            FIXTURES / "adequacy.json",
        )
        scores = score_units(join_inputs(inputs))
        self.assertEqual(len(scores), 1)
        link = scores[0].links[0]
        self.assertEqual(link.level, 2.5)
        self.assertEqual(link.downgraded, "precision_blind_evidence")
        self.assertAlmostEqual(scores[0].score, 43.55)
        self.assertEqual(scores[0].band, "LOW")


if __name__ == "__main__":
    unittest.main()
