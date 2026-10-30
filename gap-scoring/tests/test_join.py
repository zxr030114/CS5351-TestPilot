"""Tests for ``join``: the four joins, ambiguity handling and coverage checks."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from join import (  # noqa: E402
    METHOD_EXACT,
    METHOD_FUNCTION,
    METHOD_LINE,
    METHOD_UNMATCHED,
    ScoringUnit,
    join_inputs,
    same_file,
    same_test,
)
from loader import (  # noqa: E402
    PipelineInputs,
    load_pipeline,
    parse_adequacy,
    parse_behavior,
    parse_changes,
    parse_mapping,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def fixture_pipeline(behavior_file: str = "behavior.json") -> PipelineInputs:
    return load_pipeline(
        FIXTURES / "changes.json",
        FIXTURES / behavior_file,
        FIXTURES / "mapping_result.json",
        FIXTURES / "adequacy.json",
    )


def change(file: str, name: str, start: int = 1, end: int = 10) -> dict:
    return {
        "file_path": file,
        "entity_name": name,
        "entity_type": "function",
        "change_type": "modify",
        "change_category": "Logic change",
        "line_start": start,
        "line_end": end,
        "summary": "s",
    }


def behavior(name: str, *, file: str | None = None, line: int | None = None, kind: str = "RETURN_BEHAVIOR_CHANGE") -> dict:
    payload = {"type": kind, "function": name, "old_line": None, "new_line": line, "confidence": "potential"}
    if file is not None:
        payload["file_path"] = file
    return payload


def build(
    changes: list[dict] | None = None,
    behaviors: list[dict] | None = None,
    mapping: dict | None = None,
    adequacy: dict | None = None,
) -> PipelineInputs:
    change_doc = {
        "schema_version": "2.0",
        "mode": "pr_ast",
        "repo": {"owner": "o", "name": "n"},
        "changes": changes if changes is not None else [change("pkg/mod.py", "f")],
        "skipped": [],
        "warnings": [],
    }
    change_report, _ = parse_changes(change_doc, "changes")
    inputs = PipelineInputs(changes=change_report)
    if behaviors is not None:
        report, _ = parse_behavior({"schema_version": "1.0", "behavior_changes": behaviors}, "behavior")
        inputs = PipelineInputs(changes=change_report, behavior=report)
    if mapping is not None:
        report, _ = parse_mapping(mapping, "mapping")
        inputs = PipelineInputs(
            changes=change_report, behavior=inputs.behavior, mapping=report, adequacy=inputs.adequacy
        )
    if adequacy is not None:
        report, _ = parse_adequacy(adequacy, "adequacy")
        inputs = PipelineInputs(
            changes=change_report, behavior=inputs.behavior, mapping=inputs.mapping, adequacy=report
        )
    return inputs


class PathHelpers(unittest.TestCase):
    def test_same_file_accepts_repo_and_module_forms(self) -> None:
        self.assertTrue(same_file("mapping-demo/payment.py", "payment.py"))
        self.assertTrue(same_file("payment.py", "mapping-demo/payment.py"))
        self.assertTrue(same_file("a\\b\\c.py", "a/b/c.py"))

    def test_same_file_rejects_lookalikes(self) -> None:
        self.assertFalse(same_file("payment.py", "test_payment.py"))
        self.assertFalse(same_file(None, "payment.py"))
        self.assertFalse(same_file("", "payment.py"))

    def test_same_test_ignores_root_prefix(self) -> None:
        self.assertTrue(same_test("test_payment.py::test_x", "mapping-demo/test_payment.py::test_x"))
        self.assertFalse(same_test("test_payment.py::test_x", "mapping-demo/test_payment.py::test_y"))

    def test_same_test_with_bare_name(self) -> None:
        self.assertTrue(same_test("test_x", "mapping-demo/test_payment.py::test_x") is False)


class FixtureJoin(unittest.TestCase):
    def test_single_unit_is_fully_joined(self) -> None:
        result = join_inputs(fixture_pipeline())
        self.assertEqual(len(result.units), 1)
        unit = result.units[0]
        self.assertIsInstance(unit, ScoringUnit)
        self.assertTrue(unit.mapped)
        self.assertTrue(unit.has_behavior)
        self.assertEqual(len(unit.links), 1)
        self.assertEqual(unit.change.entity_name, "withdraw")
        self.assertEqual(unit.mapping.status, "matched")
        self.assertEqual(len(unit.tests), 3)
        self.assertEqual(result.coverage, 1.0)
        self.assertEqual(result.orphan_behaviors, ())

    def test_j2_attaches_adequacy_despite_missing_file_path(self) -> None:
        """B has no file_path; D's echoed record has one. Signature must ignore it."""
        result = join_inputs(fixture_pipeline())
        unit = result.units[0]
        self.assertIsNotNone(unit.links[0].adequacy)
        assert unit.links[0].adequacy is not None
        self.assertEqual(unit.links[0].adequacy.status, "assertion_evidence_found")
        self.assertEqual(unit.adequacy_entries[0].change.file_path, "payment.py")

    def test_method_is_line_window_without_file_path(self) -> None:
        result = join_inputs(fixture_pipeline("behavior.json"))
        self.assertEqual(result.units[0].method, METHOD_LINE)

    def test_method_is_exact_when_file_path_present(self) -> None:
        result = join_inputs(fixture_pipeline("behavior_with_path.json"))
        self.assertEqual(result.units[0].method, METHOD_EXACT)

    def test_stats_count_selected_tests_with_evidence(self) -> None:
        result = join_inputs(fixture_pipeline())
        self.assertEqual(result.stats["selected_with_evidence"], 3)
        self.assertEqual(result.stats["changes"], 1)
        self.assertEqual(result.stats["with_adequacy"], 1)


class Ambiguity(unittest.TestCase):
    def test_same_named_changes_receive_no_duplicate_behavior(self) -> None:
        inputs = build(
            changes=[change("a/mod.py", "f"), change("b/mod.py", "f")],
            behaviors=[behavior("f")],
        )
        result = join_inputs(inputs)
        self.assertEqual(len(result.units), 2)
        attached = [unit for unit in result.units if unit.has_behavior]
        self.assertEqual(attached, [])
        self.assertEqual(len(result.orphan_behaviors), 1)
        self.assertIn("join.ambiguous_behavior", [p.code for p in result.problems])
        self.assertTrue(all(unit.method == METHOD_UNMATCHED for unit in result.units))

    def test_file_path_disambiguates(self) -> None:
        inputs = build(
            changes=[change("a/mod.py", "f"), change("b/mod.py", "f")],
            behaviors=[behavior("f", file="b/mod.py")],
        )
        result = join_inputs(inputs)
        by_file = {unit.change.file_path: unit for unit in result.units}
        self.assertFalse(by_file["a/mod.py"].has_behavior)
        self.assertTrue(by_file["b/mod.py"].has_behavior)

    def test_line_window_disambiguates_same_file(self) -> None:
        inputs = build(
            changes=[change("pkg/mod.py", "f", 1, 10), change("pkg/mod.py", "f", 20, 30)],
            behaviors=[behavior("f", line=25)],
        )
        result = join_inputs(inputs)
        owners = [unit.change.line_start for unit in result.units if unit.has_behavior]
        self.assertEqual(owners, [20])

    def test_orphan_behavior_is_reported(self) -> None:
        inputs = build(
            changes=[change("pkg/mod.py", "f")],
            behaviors=[behavior("other")],
        )
        result = join_inputs(inputs)
        self.assertEqual(result.units[0].method, METHOD_UNMATCHED)
        self.assertEqual(len(result.orphan_behaviors), 1)
        self.assertIn("join.orphan_behavior", [p.code for p in result.problems])

    def test_function_only_match_when_both_sides_lack_paths(self) -> None:
        inputs = build(
            changes=[change("pkg/mod.py", "f", 1, 10)],
            behaviors=[behavior("f")],
        )
        result = join_inputs(inputs)
        self.assertEqual(result.units[0].method, METHOD_FUNCTION)

    def test_qualified_function_name_matches_short_entity_name(self) -> None:
        """Stage B emits ``Class.method``; stage A emits the short name."""
        inputs = build(
            changes=[change("pkg/mod.py", "raise_any", 1, 10)],
            behaviors=[behavior("_CollectErrors.raise_any", line=6)],
        )
        result = join_inputs(inputs)
        self.assertTrue(result.units[0].has_behavior)
        self.assertEqual(result.units[0].links[0].behavior.function, "_CollectErrors.raise_any")

    def test_qualified_names_on_both_sides_still_match(self) -> None:
        inputs = build(
            changes=[change("pkg/mod.py", "Account.deposit", 1, 10)],
            behaviors=[behavior("Account.deposit", line=6)],
        )
        self.assertTrue(join_inputs(inputs).units[0].has_behavior)


class Coverage(unittest.TestCase):
    def test_low_coverage_warns(self) -> None:
        mapping = json.loads((FIXTURES / "mapping_result.json").read_text(encoding="utf-8"))
        inputs = load_pipeline(
            FIXTURES / "changes.json",
            FIXTURES / "behavior.json",
            FIXTURES / "mapping_result.json",
        )
        # Simulate A and C describing different runs: A gains unrelated changes.
        extra = {
            "schema_version": "2.0",
            "mode": "pr_ast",
            "repo": {"owner": "o", "name": "n"},
            "changes": [
                change("pkg/mod.py", "f"),
                change("pkg/other.py", "g"),
                change("pkg/third.py", "h"),
            ],
            "skipped": [],
            "warnings": [],
        }
        from loader import parse_changes as _parse

        report, _ = _parse(extra, "changes")
        mixed = PipelineInputs(changes=report, mapping=inputs.mapping)
        result = join_inputs(mixed)
        self.assertIsNotNone(result.coverage)
        assert result.coverage is not None
        self.assertLess(result.coverage, 0.5)
        self.assertIn("join.low_coverage", [p.code for p in result.problems])

    def test_no_mapping_means_no_coverage(self) -> None:
        result = join_inputs(load_pipeline(FIXTURES / "changes.json"))
        self.assertIsNone(result.coverage)
        self.assertEqual(result.stats["with_mapping"], 0)


if __name__ == "__main__":
    unittest.main()
