"""Tests for ``loader``: schema checks, tolerance and the fixture round-trip.

Temporary files live in ``tests/_loader_tmp`` (created and removed per class)
instead of ``tempfile``: on the Windows host this project is developed on,
``mkdtemp`` produces a directory the process cannot afterwards write into.
"""

from __future__ import annotations

import json
import shutil
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import loader  # noqa: E402
from loader import (  # noqa: E402
    AdequacyReport,
    BehaviorReport,
    ChangeReport,
    LoaderError,
    MappingReport,
    load_pipeline,
    parse_adequacy,
    parse_behavior,
    parse_changes,
    parse_mapping,
    read_json,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def _fixture(name: str) -> Path:
    return FIXTURES / name


class FixtureRoundTrip(unittest.TestCase):
    """The four fixtures must parse into the shapes the scorer expects."""

    def test_stage_a(self) -> None:
        report, problems = parse_changes(read_json(_fixture("changes.json")), "changes.json")
        self.assertIsInstance(report, ChangeReport)
        self.assertEqual(len(report.changes), 1)
        change = report.changes[0]
        self.assertEqual(change.file_path, "mapping-demo/payment.py")
        self.assertEqual(change.entity_name, "withdraw")
        self.assertEqual(change.change_category, "Logic change")
        self.assertEqual((change.line_start, change.line_end), (1, 6))
        self.assertEqual(change.key, ("mapping-demo/payment.py", "withdraw", 1))
        self.assertEqual(report.repo_name, "demo/payment")
        self.assertEqual(report.pr_number, 1)
        self.assertTrue(report.skipped)
        self.assertEqual([p.level for p in problems], [])

    def test_stage_b(self) -> None:
        report, problems = parse_behavior(read_json(_fixture("behavior.json")), "behavior.json")
        self.assertIsInstance(report, BehaviorReport)
        self.assertEqual(len(report.changes), 1)
        behavior = report.changes[0]
        self.assertEqual(behavior.type, "RETURN_BEHAVIOR_CHANGE")
        self.assertEqual(behavior.function, "withdraw")
        self.assertIsNone(behavior.file_path)
        self.assertEqual(behavior.detail("new_expression"), "round(balance - amount, 1)")
        self.assertTrue(behavior.detail("nonexistent") is None)
        # B does not emit file_path; the loader flags it once.
        self.assertIn("behavior.missing_file_path", [p.code for p in problems])

    def test_stage_c(self) -> None:
        report, problems = parse_mapping(read_json(_fixture("mapping_result.json")), "mapping_result.json")
        self.assertIsInstance(report, MappingReport)
        self.assertEqual(len(report.entries), 1)
        entry = report.entries[0]
        self.assertEqual(entry.status, "matched")
        self.assertEqual(len(entry.tests), 3)
        self.assertTrue(report.selection_complete)
        self.assertEqual(len(report.selected_tests), 3)
        self.assertEqual(list(problems), [])

    def test_stage_c_entry_matches_stage_a_change(self) -> None:
        changes, _ = parse_changes(read_json(_fixture("changes.json")), "changes.json")
        mapping, _ = parse_mapping(read_json(_fixture("mapping_result.json")), "mapping.json")
        echoed = mapping.status_of(changes.changes[0])
        self.assertIsNotNone(echoed)
        assert echoed is not None
        self.assertEqual(echoed.change.key, changes.changes[0].key)

    def test_stage_d(self) -> None:
        report, problems = parse_adequacy(read_json(_fixture("adequacy.json")), "adequacy.json")
        self.assertIsInstance(report, AdequacyReport)
        self.assertEqual(len(report.entries), 1)
        entry = report.entries[0]
        self.assertEqual(entry.status, "assertion_evidence_found")
        self.assertEqual(len(entry.candidate_tests), 3)
        self.assertEqual(len(entry.evidence), 3)
        self.assertEqual(len(entry.recommendations), 2)
        self.assertEqual(set(entry.strengths), {"potential_match", "weak_or_mismatched"})
        self.assertTrue(report.input_complete)
        self.assertTrue(report.selection_complete)
        self.assertEqual(list(problems), [])

    def test_adequacy_results_for_filters_by_module_path(self) -> None:
        report, _ = parse_adequacy(read_json(_fixture("adequacy.json")), "adequacy.json")
        self.assertEqual(len(report.results_for("withdraw")), 1)
        self.assertEqual(len(report.results_for("withdraw", "payment.py")), 1)
        self.assertEqual(len(report.results_for("withdraw", "elsewhere.py")), 0)
        self.assertEqual(len(report.results_for("deposit")), 0)


class PipelineAssembly(unittest.TestCase):
    def test_all_four_inputs(self) -> None:
        inputs = load_pipeline(
            _fixture("changes.json"),
            _fixture("behavior.json"),
            _fixture("mapping_result.json"),
            _fixture("adequacy.json"),
        )
        self.assertEqual(len(inputs.changes.changes), 1)
        assert inputs.behavior is not None and inputs.mapping is not None and inputs.adequacy is not None
        self.assertEqual(len(inputs.behavior.changes), 1)
        self.assertEqual(len(inputs.mapping.entries), 1)
        self.assertEqual(len(inputs.adequacy.entries), 1)
        self.assertEqual(inputs.errors, ())

    def test_optional_stages_may_be_absent(self) -> None:
        inputs = load_pipeline(_fixture("changes.json"))
        self.assertIsNone(inputs.behavior)
        self.assertIsNone(inputs.mapping)
        self.assertIsNone(inputs.adequacy)
        self.assertEqual(len(inputs.warnings), 3)
        self.assertEqual({p.code for p in inputs.warnings}, {"input.missing"})

    def test_nonexistent_optional_path_degrades(self) -> None:
        inputs = load_pipeline(_fixture("changes.json"), mapping=_fixture("does_not_exist.json"))
        self.assertIsNone(inputs.mapping)
        self.assertEqual(len(inputs.warnings), 3)


class Tolerances(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = Path(__file__).resolve().parent / "_loader_tmp"
        if cls.tmp.exists():
            shutil.rmtree(cls.tmp)
        cls.tmp.mkdir(parents=True)

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def _write(self, name: str, payload, *, bom: bool = False) -> Path:
        path = self.tmp / name
        text = json.dumps(payload, ensure_ascii=False)
        data = (b"\xef\xbb\xbf" if bom else b"") + text.encode("utf-8")
        path.write_bytes(data)
        return path

    def test_utf8_bom_is_accepted(self) -> None:
        payload = json.loads(_fixture("changes.json").read_text(encoding="utf-8"))
        path = self._write("bom.json", payload, bom=True)
        report, _ = parse_changes(read_json(path), str(path))
        self.assertEqual(len(report.changes), 1)

    def test_diff_report_schema_is_rejected_with_hint(self) -> None:
        path = self._write("diff.json", {"schema_version": "1.0", "mode": "diff", "changes": []})
        with self.assertRaises(LoaderError) as caught:
            parse_changes(read_json(path), str(path))
        self.assertIn("diff report", str(caught.exception))

    def test_wrong_mode_is_rejected(self) -> None:
        payload = json.loads(_fixture("adequacy.json").read_text(encoding="utf-8"))
        payload["mode"] = "test_dependency_mapping"
        with self.assertRaises(LoaderError):
            parse_adequacy(payload, "adequacy")

    def test_invalid_json_reports_position(self) -> None:
        path = self.tmp / "broken.json"
        path.write_text('{"changes": [}', encoding="utf-8")
        with self.assertRaises(LoaderError) as caught:
            read_json(path)
        self.assertIn("invalid JSON", str(caught.exception))

    def test_missing_required_file(self) -> None:
        with self.assertRaises(LoaderError):
            load_pipeline(self.tmp / "nope.json")

    def test_old_behavior_schema_warns_but_loads(self) -> None:
        payload = json.loads(_fixture("behavior.json").read_text(encoding="utf-8"))
        payload["schema_version"] = "0.9"
        report, problems = parse_behavior(payload, "behavior")
        self.assertEqual(len(report.changes), 1)
        self.assertIn("schema.version", [p.code for p in problems])

    def test_malformed_change_is_dropped_with_error(self) -> None:
        payload = json.loads(_fixture("changes.json").read_text(encoding="utf-8"))
        payload["changes"].append({"file_path": "x.py", "entity_name": "f"})  # missing required fields
        report, problems = parse_changes(payload, "changes")
        self.assertEqual(len(report.changes), 1)
        self.assertEqual([p.code for p in problems], ["change.malformed"])
        self.assertEqual(problems[0].level, loader.ERROR)

    def test_malformed_mapping_entry_is_dropped(self) -> None:
        payload = json.loads(_fixture("mapping_result.json").read_text(encoding="utf-8"))
        payload["changes"].append({"status": "matched"})  # no 'change' object
        report, problems = parse_mapping(payload, "mapping")
        self.assertEqual(len(report.entries), 1)
        self.assertEqual([p.code for p in problems], ["mapping.malformed"])

    def test_empty_change_list_warns(self) -> None:
        payload = json.loads(_fixture("changes.json").read_text(encoding="utf-8"))
        payload["changes"] = []
        report, problems = parse_changes(payload, "changes")
        self.assertEqual(report.changes, ())
        self.assertIn("changes.empty", [p.code for p in problems])

    def test_non_string_list_items_are_dropped(self) -> None:
        payload = json.loads(_fixture("mapping_result.json").read_text(encoding="utf-8"))
        payload["selected_tests"] = ["a::b", 42, None]
        report, problems = parse_mapping(payload, "mapping")
        self.assertEqual(report.selected_tests, ("a::b",))
        self.assertIn("field.type", [p.code for p in problems])

    def test_selection_complete_non_boolean_degrades_to_false(self) -> None:
        payload = json.loads(_fixture("mapping_result.json").read_text(encoding="utf-8"))
        payload["selection_complete"] = "yes"
        report, problems = parse_mapping(payload, "mapping")
        self.assertFalse(report.selection_complete)
        self.assertIn("field.type", [p.code for p in problems])

    def test_problem_renders_context(self) -> None:
        problem = loader.Problem(loader.WARNING, "code", "message", "ctx")
        self.assertIn("[ctx]", str(problem))


if __name__ == "__main__":
    unittest.main()
