"""Tests for the confidence model and its wiring into scoring."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from confidence import LEVELS, SIGNAL_PENALTIES, assess  # noqa: E402
from join import BehaviorLink, ScoringUnit, join_inputs  # noqa: E402
from loader import (  # noqa: E402
    AdequacyEntry,
    BehaviorChange,
    Change,
    MappingEntry,
    load_pipeline,
)
from scoring import ScoreContext, context_from_pipeline, score_unit, score_units  # noqa: E402

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def change(start: int = 1, end: int = 10) -> Change:
    return Change(
        file_path="pkg/mod.py",
        entity_name="f",
        change_type="modify",
        change_category="Logic change",
        line_start=start,
        line_end=end,
    )


def behavior(line: int | None = 6, file: str | None = None) -> BehaviorChange:
    return BehaviorChange(
        type="RETURN_BEHAVIOR_CHANGE", function="f", file_path=file, new_line=line
    )


def adequacy(status: str = "no_matching_assertion") -> AdequacyEntry:
    return AdequacyEntry(change=behavior(), status=status)


def unit(
    *, mapping: MappingEntry | None = None, entry: AdequacyEntry | None = None, target: BehaviorChange | None = None
) -> ScoringUnit:
    target = target or behavior()
    return ScoringUnit(
        change=change(),
        mapping=mapping if mapping is not None else MappingEntry(change=change(), status="matched"),
        links=(BehaviorLink(behavior=target, adequacy=entry if entry is not None else adequacy()),),
    )


class Model(unittest.TestCase):
    def test_no_signals_is_high(self) -> None:
        result = assess([])
        self.assertEqual(result.level, "HIGH")
        self.assertEqual(result.penalty, 0)
        self.assertTrue(result.high)

    def test_one_signal_is_medium(self) -> None:
        self.assertEqual(assess(["file_fallback"]).level, "MEDIUM")

    def test_two_signals_are_low(self) -> None:
        self.assertEqual(assess(["file_fallback", "weak_join"]).level, "LOW")

    def test_duplicates_count_once(self) -> None:
        once = assess(["file_fallback"])
        twice = assess(["file_fallback", "file_fallback"])
        self.assertEqual(once.penalty, twice.penalty)

    def test_signals_are_preserved_in_order(self) -> None:
        result = assess(["weak_join", "file_fallback", "weak_join"])
        self.assertEqual(result.signals, ("weak_join", "file_fallback"))

    def test_missing_adequacy_alone_is_low(self) -> None:
        self.assertEqual(assess(["no_adequacy"]).level, "LOW")

    def test_unknown_signal_costs_one(self) -> None:
        self.assertEqual(assess(["brand_new_caveat"]).penalty, 1)

    def test_every_known_signal_costs_at_least_one(self) -> None:
        for name, points in SIGNAL_PENALTIES.items():
            with self.subTest(signal=name):
                self.assertGreaterEqual(points, 1)

    def test_levels_are_ordered(self) -> None:
        self.assertEqual(LEVELS, ("HIGH", "MEDIUM", "LOW"))


class Wiring(unittest.TestCase):
    def test_clean_fixture_is_high(self) -> None:
        inputs = load_pipeline(
            FIXTURES / "changes.json",
            FIXTURES / "behavior.json",
            FIXTURES / "mapping_result.json",
            FIXTURES / "adequacy.json",
        )
        scores = score_units(join_inputs(inputs), context=context_from_pipeline(inputs))
        self.assertEqual(scores[0].confidence.level, "HIGH")
        self.assertEqual(scores[0].confidence.signals, ())

    def test_missing_adequacy_is_low(self) -> None:
        result = score_unit(unit(entry=None, mapping=MappingEntry(change=change(), status="matched")))
        # unit() substitutes a default entry, so build the bare case explicitly.
        bare = ScoringUnit(
            change=change(),
            mapping=MappingEntry(change=change(), status="matched"),
            links=(BehaviorLink(behavior=behavior(), adequacy=None),),
        )
        self.assertEqual(score_unit(bare).confidence.level, "LOW")
        self.assertIn("no_adequacy", score_unit(bare).confidence.signals)
        self.assertIn("assumed_level", score_unit(bare).confidence.signals)
        self.assertIsNotNone(result)

    def test_file_fallback_is_medium(self) -> None:
        fallback = unit(mapping=MappingEntry(change=change(), status="file_fallback"))
        result = score_unit(fallback)
        self.assertEqual(result.confidence.level, "MEDIUM")
        self.assertEqual(result.confidence.signals, ("file_fallback",))

    def test_function_only_join_is_medium(self) -> None:
        """A change and a behavior that share nothing but the function name."""
        from loader import PipelineInputs, parse_adequacy, parse_behavior, parse_changes, parse_mapping

        change_doc = {
            "schema_version": "2.0",
            "mode": "pr_ast",
            "repo": {"owner": "o", "name": "n"},
            "changes": [
                {
                    "file_path": "pkg/mod.py",
                    "entity_name": "f",
                    "entity_type": "function",
                    "change_type": "modify",
                    "change_category": "Logic change",
                    "line_start": 1,
                    "line_end": 10,
                    "summary": "s",
                }
            ],
            "skipped": [],
            "warnings": [],
        }
        changes, _ = parse_changes(change_doc, "changes")
        behaviors, _ = parse_behavior(
            {"schema_version": "1.0", "behavior_changes": [{"type": "RETURN_BEHAVIOR_CHANGE", "function": "f"}]},
            "behavior",
        )
        mapping, _ = parse_mapping(
            {
                "schema_version": "1.0",
                "mode": "test_dependency_mapping",
                "selected_tests": ["tests/test_mod.py::test_f"],
                "selection_complete": True,
                "changes": [
                    {
                        "change": change_doc["changes"][0],
                        "status": "matched",
                        "tests": ["tests/test_mod.py::test_f"],
                    }
                ],
                "limitations": [],
            },
            "mapping",
        )
        adequacy, _ = parse_adequacy(
            {
                "schema_version": "1.0",
                "mode": "test_adequacy_analysis",
                "analysis": {
                    "method": "python-ast",
                    "runtime_adequacy_proven": False,
                    "input_complete": True,
                    "selection_complete": True,
                },
                "results": [
                    {
                        "change": {"type": "RETURN_BEHAVIOR_CHANGE", "function": "f"},
                        "status": "no_matching_assertion",
                        "candidate_tests": [],
                        "evidence": [],
                        "recommendations": [],
                    }
                ],
                "errors": [],
                "summary": {},
                "limitations": [],
            },
            "adequacy",
        )
        scores = score_units(
            join_inputs(
                PipelineInputs(
                    changes=changes, behavior=behaviors, mapping=mapping, adequacy=adequacy
                )
            )
        )
        self.assertEqual(scores[0].links[0].method, "function_only")
        self.assertEqual(scores[0].confidence.signals, ("weak_join",))
        self.assertEqual(scores[0].confidence.level, "MEDIUM")

    def test_incomplete_selection_is_medium(self) -> None:
        result = score_unit(unit(), context=ScoreContext(selection_complete=False))
        self.assertEqual(result.confidence.level, "MEDIUM")
        self.assertEqual(result.confidence.signals, ("selection_incomplete",))

    def test_two_context_caveats_stack_to_low(self) -> None:
        context = ScoreContext(selection_complete=False, adequacy_input_complete=False)
        self.assertEqual(score_unit(unit(), context=context).confidence.level, "LOW")

    def test_confidence_appears_in_components(self) -> None:
        components = score_unit(unit()).links[0].components
        self.assertEqual(components["confidence"], "HIGH")
        self.assertEqual(components["confidence_signals"], [])

    def test_context_from_pipeline_reads_the_reports(self) -> None:
        inputs = load_pipeline(FIXTURES / "changes.json", FIXTURES / "adequacy.json")
        context = context_from_pipeline(inputs)
        self.assertTrue(context.selection_complete)
        self.assertTrue(context.adequacy_input_complete)

    def test_unscored_units_still_report_confidence(self) -> None:
        bare = ScoringUnit(change=change(), mapping=None, links=())
        result = score_unit(bare)
        self.assertEqual(result.unscored_reason, "no_behavior_change")
        self.assertEqual(result.confidence.level, "HIGH")


if __name__ == "__main__":
    unittest.main()
