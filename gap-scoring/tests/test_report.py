"""Tests for the report assembly layer (JSON shape and markdown rendering)."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from join import join_inputs  # noqa: E402
from loader import load_pipeline  # noqa: E402
from report import (  # noqa: E402
    MODULE_NAME,
    REPORT_SCHEMA_VERSION,
    ReportOptions,
    build_report,
    render_report_markdown,
    status_line,
)
from scoring import context_from_pipeline, score_units  # noqa: E402

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def full_pipeline():
    inputs = load_pipeline(
        FIXTURES / "changes.json",
        FIXTURES / "behavior.json",
        FIXTURES / "mapping_result.json",
        FIXTURES / "adequacy.json",
    )
    join_result = join_inputs(inputs)
    scores = score_units(join_result, context=context_from_pipeline(inputs))
    return inputs, join_result, scores


class Shape(unittest.TestCase):
    def setUp(self) -> None:
        self.inputs, self.join_result, self.scores = full_pipeline()
        self.report = build_report(self.inputs, self.join_result, self.scores)

    def test_header_fields(self) -> None:
        self.assertEqual(self.report["schema_version"], REPORT_SCHEMA_VERSION)
        self.assertEqual(self.report["module"], MODULE_NAME)
        self.assertEqual(self.report["pr"]["number"], 1)
        self.assertEqual(self.report["pr"]["repo"], "demo/payment")

    def test_inputs_section_reports_what_was_loaded(self) -> None:
        self.assertEqual(self.report["inputs"], {"behavior": "loaded", "mapping": "loaded", "adequacy": "loaded"})

    def test_summary_counts(self) -> None:
        summary = self.report["summary"]
        self.assertEqual(summary["changes_total"], 1)
        self.assertEqual(summary["changes_scored"], 1)
        self.assertEqual(summary["gaps_low"], 1)
        self.assertEqual(summary["reported_gaps"], 1)
        self.assertEqual(summary["mapping_coverage"], 1.0)

    def test_gap_payload_shape(self) -> None:
        gap = self.report["gaps"][0]
        for field in (
            "entity_key",
            "file_path",
            "entity_name",
            "entity_type",
            "line_start",
            "line_end",
            "gap_score",
            "risk",
            "confidence",
            "components",
            "rule_hits",
            "affected_tests",
            "explanation",
        ):
            self.assertIn(field, gap)
        self.assertEqual(gap["entity_key"], "mapping-demo/payment.py::withdraw@1")
        self.assertAlmostEqual(gap["gap_score"], 43.55)
        self.assertEqual(gap["risk"], "LOW")
        self.assertEqual(gap["confidence"], "HIGH")
        self.assertEqual(gap["components"]["adequacy_level"], 2.5)

    def test_explanation_payload_is_traceable(self) -> None:
        explanation = self.report["gaps"][0]["explanation"]
        self.assertIn("integer", explanation["problem"])
        self.assertEqual(explanation["suggested_test_input"], "withdraw(100, 30.05)")
        self.assertTrue(explanation["evidence"])
        self.assertTrue(any(item["test"] for item in explanation["evidence"]))
        self.assertTrue(any(item["line"] for item in explanation["evidence"]))

    def test_report_is_json_serialisable(self) -> None:
        text = json.dumps(self.report, ensure_ascii=False)
        self.assertIn("withdraw", text)

    def test_sources_are_recorded_when_given(self) -> None:
        report = build_report(
            self.inputs,
            self.join_result,
            self.scores,
            ReportOptions(sources={"changes": "a.json", "behavior": "b.json"}),
        )
        self.assertEqual(report["sources"], {"changes": "a.json", "behavior": "b.json"})

    def test_unscored_is_empty_for_the_fixture(self) -> None:
        self.assertEqual(self.report["unscored"], [])


class Selection(unittest.TestCase):
    def setUp(self) -> None:
        self.inputs, self.join_result, self.scores = full_pipeline()

    def test_fully_covered_changes_are_hidden_by_default(self) -> None:
        from dataclasses import replace

        covered = replace(self.scores[0], score=0.0, band="NONE")
        report = build_report(self.inputs, self.join_result, [covered])
        self.assertEqual(report["gaps"], [])
        self.assertEqual(report["summary"]["reported_gaps"], 0)

    def test_include_none_lists_them(self) -> None:
        from dataclasses import replace

        covered = replace(self.scores[0], score=0.0, band="NONE")
        report = build_report(
            self.inputs, self.join_result, [covered], ReportOptions(include_none=True)
        )
        self.assertEqual(len(report["gaps"]), 1)

    def test_top_limits_and_sorts(self) -> None:
        from dataclasses import replace

        low = self.scores[0]
        high = replace(low, score=95.0, band="HIGH")
        mid = replace(low, score=60.0, band="MEDIUM")
        report = build_report(
            self.inputs, self.join_result, [low, high, mid], ReportOptions(top=2)
        )
        scores = [gap["gap_score"] for gap in report["gaps"]]
        self.assertEqual(scores, [95.0, 60.0])

    def test_unscored_changes_are_listed_separately(self) -> None:
        only_changes = load_pipeline(FIXTURES / "changes.json")
        join_result = join_inputs(only_changes)
        scores = score_units(join_result)
        report = build_report(only_changes, join_result, scores)
        self.assertEqual(report["gaps"], [])
        self.assertEqual(len(report["unscored"]), 1)
        self.assertEqual(report["unscored"][0]["reason"], "no_behavior_change")
        self.assertEqual(report["inputs"]["behavior"], "missing")


class Markdown(unittest.TestCase):
    def setUp(self) -> None:
        inputs, join_result, scores = full_pipeline()
        self.report = build_report(inputs, join_result, scores)

    def test_header_and_summary(self) -> None:
        text = render_report_markdown(self.report)
        self.assertIn("## Test Gap Report", text)
        self.assertIn("demo/payment", text)
        self.assertIn("average score", text)
        self.assertIn("mapping coverage", text)

    def test_lists_the_gap_with_its_explanation(self) -> None:
        text = render_report_markdown(self.report)
        self.assertIn("Changed behavior", text)
        self.assertIn("withdraw", text)
        self.assertIn("Suggested input", text)

    def test_no_forbidden_wording(self) -> None:
        text = render_report_markdown(self.report).lower()
        self.assertNotIn("missing test", text)

    def test_empty_report_says_so(self) -> None:
        empty = dict(self.report, gaps=[])
        text = render_report_markdown(empty)
        self.assertIn("No potential test gaps", text)

    def test_max_gaps_is_respected_and_counted(self) -> None:
        gaps = []
        for index in range(4):
            gap = dict(self.report["gaps"][0])
            gap["entity_name"] = f"fn{index}"
            gap["gap_score"] = 90 - index
            gaps.append(gap)
        report = dict(self.report, gaps=gaps)
        text = render_report_markdown(report, max_gaps=2)
        self.assertIn("and 2 more gap(s)", text)


class StatusLine(unittest.TestCase):
    """The one-liner CI puts in a check name; it must never imply "clean" wrongly."""

    def setUp(self) -> None:
        inputs, join_result, scores = full_pipeline()
        self.report = build_report(inputs, join_result, scores)

    def test_fixture_line(self) -> None:
        line = status_line(self.report)
        self.assertIn("0 high, 0 medium, 1 low", line)
        self.assertIn("avg 43.55/100", line)
        self.assertIn("1/1 changes scored", line)

    def test_no_markdown(self) -> None:
        self.assertNotIn("**", status_line(self.report))

    def test_high_counts_are_reported(self) -> None:
        report = dict(self.report, summary=dict(self.report["summary"], gaps_high=2, gaps_medium=1, gaps_low=3))
        line = status_line(report)
        self.assertTrue(line.startswith("Test gaps: 2 high, 1 medium, 3 low"))

    def test_nothing_scored_says_so(self) -> None:
        report = dict(
            self.report,
            summary=dict(self.report["summary"], changes_scored=0, changes_total=4),
        )
        line = status_line(report)
        self.assertIn("No analysable changes", line)
        self.assertIn("4 change(s)", line)

    def test_scored_but_clean_says_no_gaps(self) -> None:
        report = dict(
            self.report,
            summary=dict(self.report["summary"], gaps_high=0, gaps_medium=0, gaps_low=0),
        )
        line = status_line(report)
        self.assertIn("No potential test gaps", line)
        self.assertIn("scored change(s)", line)


if __name__ == "__main__":
    unittest.main()
