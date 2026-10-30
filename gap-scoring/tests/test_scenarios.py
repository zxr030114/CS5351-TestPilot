"""Golden tests over the scenario fixtures.

Each scenario pins what stage E is supposed to conclude for one risk class.  The
expected values are written by hand in ``expected.json`` (not computed by the code
under test), so a change in calibration shows up as a diff instead of silently
passing.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from explain import assert_forbidden_absent, explain_unit, render_markdown  # noqa: E402
from join import join_inputs  # noqa: E402
from loader import load_pipeline  # noqa: E402
from report import build_report, render_report_markdown  # noqa: E402
from scoring import context_from_pipeline, score_units  # noqa: E402

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
SCENARIOS = FIXTURES / "scenarios"


def scenario_paths(entry: dict) -> tuple[Path, Path, Path, Path]:
    base = SCENARIOS / entry["name"] if "path" not in entry else FIXTURES
    return (
        base / "changes.json",
        base / "behavior.json",
        base / "mapping_result.json",
        base / "adequacy.json",
    )


def run_scenario(entry: dict):
    changes, behavior, mapping, adequacy = scenario_paths(entry)
    inputs = load_pipeline(changes, behavior, mapping, adequacy)
    join_result = join_inputs(inputs)
    scores = score_units(join_result, context=context_from_pipeline(inputs))
    return inputs, join_result, scores


class ScenarioExpectations(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.manifest = json.loads((SCENARIOS / "expected.json").read_text(encoding="utf-8"))["scenarios"]

    def test_manifest_covers_six_scenarios(self) -> None:
        self.assertEqual(len(self.manifest), 6)
        names = {entry["name"] for entry in self.manifest}
        self.assertIn("00-precision-blind", names)
        self.assertIn("05-mapping-unresolved", names)

    def test_each_scenario_matches_its_expected_outcome(self) -> None:
        for entry in self.manifest:
            with self.subTest(scenario=entry["name"]):
                _, _, scores = run_scenario(entry)
                self.assertEqual(len(scores), 1, "each scenario describes exactly one change")
                score = scores[0]
                expected = entry["expected"]
                self.assertEqual(score.scored, expected["scored"])
                self.assertEqual(score.band, expected["band"])
                self.assertAlmostEqual(score.score, expected["score"], delta=0.02)
                self.assertEqual(score.confidence.level, expected["confidence"])

    def test_every_scenario_is_explainable(self) -> None:
        for entry in self.manifest:
            with self.subTest(scenario=entry["name"]):
                _, _, scores = run_scenario(entry)
                explanation = explain_unit(scores[0])
                assert_forbidden_absent(explanation)
                text = render_markdown(explanation)
                self.assertLessEqual(len(text.splitlines()), 6)
                self.assertNotIn("missing test", text.lower())

    def test_every_scenario_builds_a_report(self) -> None:
        for entry in self.manifest:
            with self.subTest(scenario=entry["name"]):
                inputs, join_result, scores = run_scenario(entry)
                report = build_report(inputs, join_result, scores)
                self.assertEqual(report["schema_version"], "1.0")
                self.assertEqual(report["summary"]["changes_total"], 1)
                markdown = render_report_markdown(report)
                self.assertIn("Test Gap Report", markdown)


class ScenarioDetails(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.manifest = {
            entry["name"]: entry
            for entry in json.loads((SCENARIOS / "expected.json").read_text(encoding="utf-8"))["scenarios"]
        }

    def score(self, name: str):
        return run_scenario(self.manifest[name])[2][0]

    def test_exception_without_test_is_high_and_names_the_exception(self) -> None:
        score = self.score("01-exception-uncovered")
        self.assertEqual(score.band, "HIGH")
        codes = [hit.code for hit in score.rule_hits]
        self.assertIn("exception_without_raises_test", codes)
        self.assertIn("related_tests_do_not_reach", codes)
        explanation = explain_unit(score)
        self.assertIn("InsufficientFunds", explanation.suggested_input or "")

    def test_boundary_change_reports_both_rules(self) -> None:
        score = self.score("02-boundary-no-assertion")
        codes = [hit.code for hit in score.rule_hits]
        self.assertIn("boundary_without_boundary_test", codes)
        self.assertIn("executed_without_assertion", codes)
        self.assertEqual(score.band, "MEDIUM")

    def test_covered_branch_is_suppressed_to_zero(self) -> None:
        score = self.score("03-new-branch-covered")
        self.assertTrue(score.scored)
        self.assertEqual(score.score, 0.0)
        self.assertEqual(score.band, "NONE")
        self.assertEqual(score.links[0].level, 3.0)

    def test_documentation_change_is_not_scored(self) -> None:
        score = self.score("04-documentation-only")
        self.assertFalse(score.scored)
        self.assertEqual(score.unscored_reason, "documentation_only")

    def test_unresolved_mapping_lowers_confidence(self) -> None:
        score = self.score("05-mapping-unresolved")
        # Two caveats stack: the mapping cannot be used, and C itself marks the
        # selection incomplete when it cannot resolve a change.
        self.assertIn("mapping_unresolved", score.confidence.signals)
        self.assertIn("selection_incomplete", score.confidence.signals)
        self.assertEqual(score.confidence.level, "LOW")
        self.assertEqual(score.links[0].level, 0.0)

    def test_precision_scenario_keeps_its_evidence_review(self) -> None:
        score = self.score("00-precision-blind")
        self.assertEqual(score.links[0].downgraded, "precision_blind_evidence")
        self.assertEqual(score.links[0].level, 2.5)

    def test_bands_are_ordered_across_scenarios(self) -> None:
        """The whole point of the fixtures: different risk classes land in different bands."""
        bands = {name: self.score(name).band for name in self.manifest}
        self.assertEqual(bands["01-exception-uncovered"], "HIGH")
        self.assertEqual(bands["02-boundary-no-assertion"], "MEDIUM")
        self.assertEqual(bands["00-precision-blind"], "LOW")
        self.assertEqual(bands["03-new-branch-covered"], "NONE")


if __name__ == "__main__":
    unittest.main()
