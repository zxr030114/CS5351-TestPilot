"""Tests for the explanation layer: wording, evidence tracing, rendering."""

from __future__ import annotations

import sys
import unittest
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from explain import (  # noqa: E402
    FORBIDDEN_PHRASES,
    Explanation,
    assert_forbidden_absent,
    describe_behavior,
    describe_problem,
    explain_unit,
    render_markdown,
    short_test_name,
    suggest_input,
)
from join import BehaviorLink, ScoringUnit, join_inputs  # noqa: E402
from loader import (  # noqa: E402
    AdequacyEntry,
    BehaviorChange,
    Change,
    MappingEntry,
    load_pipeline,
)
from scoring import score_unit, score_units  # noqa: E402

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def change(name: str = "withdraw") -> Change:
    return Change(
        file_path="mapping-demo/payment.py",
        entity_name=name,
        change_type="modify",
        change_category="Logic change",
        line_start=1,
        line_end=10,
    )


def behavior(kind: str = "RETURN_BEHAVIOR_CHANGE", **details) -> BehaviorChange:
    return BehaviorChange(type=kind, function="withdraw", new_line=6, details=details)


def entry(status: str, *, evidence=(), change_obj: BehaviorChange | None = None) -> AdequacyEntry:
    return AdequacyEntry(
        change=change_obj or behavior(), status=status, evidence=tuple(evidence)
    )


def scored(*, entry_obj: AdequacyEntry | None = None, behavior_obj: BehaviorChange | None = None,
           mapping_status: str | None = "matched"):
    behavior_obj = behavior_obj or behavior()
    if entry_obj is not None:
        # D echoes B's record verbatim; the level review reads entry.change, so a
        # hand-built entry must carry the same behaviour object as the link.
        entry_obj = replace(entry_obj, change=behavior_obj)
    mapping = MappingEntry(change=change(), status=mapping_status, tests=("t",)) if mapping_status else None
    unit = ScoringUnit(
        change=change(),
        mapping=mapping,
        links=(BehaviorLink(behavior=behavior_obj, adequacy=entry_obj),),
    )
    return score_unit(unit)


class BehaviorText(unittest.TestCase):
    def test_return_change(self) -> None:
        text = describe_behavior(
            behavior(old_expression="round(x, 2)", new_expression="round(x, 1)")
        )
        self.assertIn("round(x, 2)", text)
        self.assertIn("round(x, 1)", text)

    def test_boundary_change(self) -> None:
        text = describe_behavior(
            behavior(
                kind="BOUNDARY_CHANGE",
                variable="age",
                operator=">=",
                old_boundary=18,
                new_boundary=21,
            )
        )
        self.assertIn("age >= 18", text)
        self.assertIn("age >= 21", text)

    def test_exception_change(self) -> None:
        text = describe_behavior(
            behavior(
                kind="EXCEPTION_BEHAVIOR_CHANGE",
                operation="added",
                new_exception_type="ValueError",
            )
        )
        self.assertIn("ValueError", text)
        self.assertIn("added", text)

    def test_new_branch(self) -> None:
        text = describe_behavior(
            behavior(kind="NEW_BRANCH", condition="amount < 0", effect="raise ValueError")
        )
        self.assertIn("amount < 0", text)

    def test_unknown_type_does_not_crash(self) -> None:
        self.assertTrue(describe_behavior(behavior(kind="SOMETHING_NEW")))


class ProblemText(unittest.TestCase):
    def test_precision_blind_quotes_the_assertion(self) -> None:
        result = scored(
            entry_obj=entry(
                "assertion_evidence_found",
                evidence=(
                    {
                        "nodeid": "tests/test_payment.py::test_x",
                        "strength": "potential_match",
                        "assertion": {"expression": "withdraw(100, 30) == 70"},
                        "calls": [{"expression": "withdraw(100, 30)", "literal_arguments": [100, 30]}],
                    },
                ),
            ),
            behavior_obj=behavior(
                old_expression="round(balance - amount, 2)",
                new_expression="round(balance - amount, 1)",
            ),
        )
        text = describe_problem(result.links[0])
        self.assertIn("withdraw(100, 30) == 70", text)
        self.assertIn("integer", text)

    def test_all_evidence_weak(self) -> None:
        result = scored(
            entry_obj=entry(
                "assertion_evidence_found",
                evidence=({"nodeid": "t::a", "strength": "weak_or_mismatched"},),
            )
        )
        self.assertIn("do not actually check", describe_problem(result.links[0]))

    def test_no_test_reaches(self) -> None:
        result = scored(entry_obj=entry("no_candidate_test"), mapping_status=None)
        self.assertIn("no test reaches", describe_problem(result.links[0]))

    def test_related_test_does_not_reach(self) -> None:
        result = scored(entry_obj=entry("no_candidate_test"), mapping_status="matched")
        self.assertIn("none of them reaches", describe_problem(result.links[0]))

    def test_executed_without_assertion(self) -> None:
        result = scored(entry_obj=entry("no_matching_assertion"))
        self.assertIn("never asserts", describe_problem(result.links[0]))

    def test_weak_assertions(self) -> None:
        result = scored(entry_obj=entry("weak_assertions"))
        self.assertIn("too weak", describe_problem(result.links[0]))

    def test_covered_says_so(self) -> None:
        result = scored(entry_obj=entry("assertion_evidence_found"))
        self.assertIn("asserted", describe_problem(result.links[0]))


class Suggestions(unittest.TestCase):
    def test_precision_suggestion_uses_a_fraction(self) -> None:
        result = scored(
            entry_obj=entry(
                "assertion_evidence_found",
                evidence=(
                    {
                        "nodeid": "t::a",
                        "strength": "potential_match",
                        "calls": [{"expression": "withdraw(100, 30)", "literal_arguments": [100, 30]}],
                    },
                ),
            ),
            behavior_obj=behavior(
                old_expression="round(balance - amount, 2)",
                new_expression="round(balance - amount, 1)",
            ),
        )
        suggestion = suggest_input(result.links[0])
        self.assertEqual(suggestion, "withdraw(100, 30.05)")

    def test_exception_suggestion_names_the_exception(self) -> None:
        result = scored(
            entry_obj=entry("no_matching_assertion"),
            behavior_obj=behavior(
                kind="EXCEPTION_BEHAVIOR_CHANGE", operation="added", new_exception_type="KeyError"
            ),
        )
        self.assertIn("KeyError", suggest_input(result.links[0]) or "")

    def test_boundary_suggestion_names_the_boundary(self) -> None:
        result = scored(
            entry_obj=entry("no_matching_assertion"),
            behavior_obj=behavior(kind="BOUNDARY_CHANGE", new_boundary=21),
        )
        self.assertIn("21", suggest_input(result.links[0]) or "")

    def test_no_suggestion_when_nothing_to_anchor(self) -> None:
        result = scored(entry_obj=entry("no_candidate_test"))
        self.assertIsNone(suggest_input(result.links[0]))


class Rendering(unittest.TestCase):
    def test_short_test_name(self) -> None:
        self.assertEqual(
            short_test_name("mapping-demo/test_payment.py::test_normal_withdraw"), "test_normal_withdraw"
        )
        self.assertEqual(short_test_name("tests/test_x.py::test_y[1-2]"), "test_y")

    def test_render_has_header_and_bounded_body(self) -> None:
        explanation = Explanation(
            entity="withdraw",
            file_path="src/payment.py",
            score=43.55,
            band="LOW",
            confidence="HIGH",
            changed_behavior="return changed",
            affected_tests=("a", "b"),
            problem="p",
            suggested_input="withdraw(100, 30.05)",
        )
        text = render_markdown(explanation)
        lines = text.splitlines()
        self.assertIn("src/payment.py::withdraw", lines[0])
        self.assertIn("LOW", lines[0])
        self.assertLessEqual(len(lines), 6)
        self.assertIn("Suggested input", text)

    def test_one_liner_mentions_potential(self) -> None:
        explanation = Explanation(
            entity="f", file_path="x.py", score=10.0, band="LOW", confidence="HIGH",
            changed_behavior="c",
        )
        self.assertIn("Potential test gap", explanation.one_liner)


class WordingGuard(unittest.TestCase):
    def test_forbidden_phrase_raises(self) -> None:
        bad = Explanation(
            entity="f", file_path="x.py", score=1.0, band="LOW", confidence="HIGH",
            changed_behavior="c", problem="missing test for this branch",
        )
        with self.assertRaises(AssertionError):
            assert_forbidden_absent(bad)

    def test_clean_explanation_passes(self) -> None:
        good = Explanation(
            entity="f", file_path="x.py", score=1.0, band="LOW", confidence="HIGH",
            changed_behavior="c", problem="a potential test gap",
        )
        assert_forbidden_absent(good)

    def test_guard_covers_every_forbidden_phrase(self) -> None:
        for phrase in FORBIDDEN_PHRASES:
            bad = Explanation(
                entity="f", file_path="x.py", score=1.0, band="LOW", confidence="HIGH",
                changed_behavior="c", problem=f"there is a {phrase} here",
            )
            with self.assertRaises(AssertionError):
                assert_forbidden_absent(bad)


class FixtureEndToEnd(unittest.TestCase):
    def setUp(self) -> None:
        inputs = load_pipeline(
            FIXTURES / "changes.json",
            FIXTURES / "behavior.json",
            FIXTURES / "mapping_result.json",
            FIXTURES / "adequacy.json",
        )
        self.scores = score_units(join_inputs(inputs))

    def test_explanation_matches_the_real_case(self) -> None:
        explanation = explain_unit(self.scores[0])
        self.assertEqual(explanation.entity, "withdraw")
        self.assertIn("round(balance - amount, 1)", explanation.changed_behavior)
        self.assertIn("integer", explanation.problem)
        self.assertEqual(explanation.suggested_input, "withdraw(100, 30.05)")
        self.assertEqual(len(explanation.affected_tests), 3)
        self.assertEqual(explanation.confidence, "HIGH")

    def test_evidence_is_traceable(self) -> None:
        explanation = explain_unit(self.scores[0])
        kinds = {ref.kind for ref in explanation.evidence}
        self.assertIn("changed_entity", kinds)
        self.assertTrue(any(ref.test for ref in explanation.evidence))
        self.assertTrue(any(ref.line for ref in explanation.evidence))

    def test_rendered_block_is_comment_sized(self) -> None:
        text = render_markdown(explain_unit(self.scores[0]))
        self.assertLessEqual(len(text.splitlines()), 6)
        self.assertNotIn("missing test", text.lower())

    def test_no_forbidden_phrase_in_generated_text(self) -> None:
        for score in self.scores:
            explanation = explain_unit(score)
            assert_forbidden_absent(explanation)


class UnscoredEntity(unittest.TestCase):
    def test_unscored_unit_produces_explanation(self) -> None:
        unit = ScoringUnit(change=change(), mapping=None, links=())
        explanation = explain_unit(score_unit(unit))
        self.assertEqual(explanation.problem, "no_behavior_change")
        self.assertIn("no behaviour change", explanation.changed_behavior)


if __name__ == "__main__":
    unittest.main()
