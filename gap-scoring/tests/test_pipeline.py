"""Tests for the end-to-end runner (job building, merging, command assembly)."""

from __future__ import annotations

import json
import shutil
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from loader import ChangeReport, Change  # noqa: E402
from pipeline import (  # noqa: E402
    BehaviorJob,
    PipelineConfig,
    build_behavior_jobs,
    build_commands,
    merge_behavior_reports,
    run_pipeline,
    stage_adequacy,
    stage_behavior,
    stage_changes,
    stage_mapping,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def change(file_path: str, name: str = "f") -> Change:
    return Change(
        file_path=file_path,
        entity_name=name,
        change_type="modify",
        change_category="Logic change",
        line_start=1,
        line_end=5,
    )


def report(*changes: Change) -> ChangeReport:
    return ChangeReport(changes=tuple(changes))


class MirrorJobs(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = Path(__file__).resolve().parent / "_pipe_tmp"
        if cls.tmp.exists():
            shutil.rmtree(cls.tmp)
        cls.mirror = cls.tmp / "sources"
        # package with an __init__.py -> module path keeps the package
        for side in ("base", "head"):
            (cls.mirror / side / "pkg").mkdir(parents=True)
            (cls.mirror / side / "pkg" / "__init__.py").write_text("", encoding="utf-8")
            (cls.mirror / side / "pkg" / "mod.py").write_text("x = 1\n", encoding="utf-8")
            # loose file -> module path is the bare name
            (cls.mirror / side / "loose.py").write_text("y = 2\n", encoding="utf-8")
        # head-only file (pure addition) must be skipped
        (cls.mirror / "head" / "pkg" / "added.py").write_text("z = 3\n", encoding="utf-8")
        # non-Python change must be skipped
        for side in ("base", "head"):
            (cls.mirror / side / "README.md").write_text("doc\n", encoding="utf-8")

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_pairs_only_files_present_on_both_sides(self) -> None:
        jobs = build_behavior_jobs(
            report(change("pkg/mod.py"), change("pkg/added.py"), change("README.md")),
            self.mirror,
        )
        self.assertEqual([job.file_path for job in jobs], ["pkg/mod.py"])

    def test_module_path_uses_package_layout(self) -> None:
        jobs = build_behavior_jobs(report(change("pkg/mod.py")), self.mirror)
        self.assertEqual(jobs[0].module_path, "pkg/mod.py")

    def test_module_path_for_loose_file(self) -> None:
        jobs = build_behavior_jobs(report(change("loose.py")), self.mirror)
        self.assertEqual(jobs[0].module_path, "loose.py")

    def test_each_file_appears_once(self) -> None:
        jobs = build_behavior_jobs(
            report(change("pkg/mod.py", "a"), change("pkg/mod.py", "b")), self.mirror
        )
        self.assertEqual(len(jobs), 1)


class Merging(unittest.TestCase):
    def test_file_path_is_replaced_with_module_path(self) -> None:
        job = BehaviorJob("mapping-demo/payment.py", Path("a"), Path("b"), "payment.py")
        report_one = {
            "behavior_changes": [
                {"type": "RETURN_BEHAVIOR_CHANGE", "function": "withdraw", "file_path": "mapping-demo/payment.py"}
            ],
            "summary": "changed return",
            "suggested_tests": ["test it"],
            "errors": [],
        }
        merged = merge_behavior_reports([(job, report_one)])
        self.assertEqual(merged["behavior_changes"][0]["file_path"], "payment.py")
        self.assertEqual(merged["behavior_changes"][0]["module_path"], "payment.py")

    def test_summaries_and_errors_are_attributed(self) -> None:
        job_a = BehaviorJob("a.py", Path("a"), Path("b"), "a.py")
        job_b = BehaviorJob("dir/b.py", Path("a"), Path("b"), "b.py")
        merged = merge_behavior_reports(
            [
                (job_a, {"behavior_changes": [{"type": "X", "function": "f"}], "summary": "s1", "errors": ["e1"]}),
                (job_b, {"behavior_changes": [{"type": "Y", "function": "g"}], "summary": "s2"}),
            ]
        )
        self.assertEqual(len(merged["behavior_changes"]), 2)
        self.assertIn("[a.py] s1", merged["summary"])
        self.assertIn("[b.py] s2", merged["summary"])
        self.assertEqual(merged["errors"], ["[a.py] e1"])
        self.assertEqual(merged["affected_functions"], ["f", "g"])

    def test_input_reports_are_not_mutated(self) -> None:
        job = BehaviorJob("a.py", Path("a"), Path("b"), "a.py")
        original = {"behavior_changes": [{"type": "X", "function": "f"}]}
        merge_behavior_reports([(job, original)])
        self.assertNotIn("file_path", original["behavior_changes"][0])

    def test_empty_input_produces_valid_skeleton(self) -> None:
        merged = merge_behavior_reports([])
        self.assertEqual(merged["behavior_changes"], [])
        self.assertEqual(merged["schema_version"], "1.0")


class Commands(unittest.TestCase):
    def make_config(self, **kwargs) -> PipelineConfig:
        base = dict(
            repo=Path("C:/repo"),
            workdir=Path("C:/work"),
            python="python",
            owner="o",
            name="n",
            pr=7,
        )
        base.update(kwargs)
        return PipelineConfig(**base)

    def test_stage_a_command_uses_online_mode(self) -> None:
        config = self.make_config()
        commands = build_commands(config, report())
        self.assertEqual(commands[0][:2], ["python", "main.py"])
        self.assertIn("--pr", commands[0])
        self.assertIn("o/n", commands[0])

    def test_stage_a_omitted_when_report_is_reused(self) -> None:
        config = self.make_config(changes=Path("C:/work/output.json"))
        commands = build_commands(config, report())
        self.assertTrue(all("--pr" not in command for command in commands))

    def test_adequacy_command_includes_mapping_only_when_available(self) -> None:
        with_db = self.make_config(db=Path("C:/db"))
        without_db = self.make_config()
        with_mapping = build_commands(with_db, report())[-1]
        without_mapping = build_commands(without_db, report())[-1]
        self.assertIn("--mapping", with_mapping)
        self.assertNotIn("--mapping", without_mapping)


class StageFallbacks(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(__file__).resolve().parent / "_pipe_tmp2"
        if self.tmp.exists():
            shutil.rmtree(self.tmp)
        self.tmp.mkdir(parents=True)
        self.config = PipelineConfig(repo=self.tmp, workdir=self.tmp / "work", python=sys.executable)

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_stage_a_requires_inputs(self) -> None:
        result = stage_changes(self.config)
        self.assertFalse(result.ok)
        self.assertIn("--changes", result.message)

    def test_existing_report_is_reused(self) -> None:
        path = self.tmp / "output.json"
        path.write_text("{}", encoding="utf-8")
        result = stage_changes(PipelineConfig(repo=self.tmp, workdir=self.tmp, changes=path))
        self.assertTrue(result.ok)
        self.assertEqual(result.message, "reused")

    def test_mapping_skips_without_database(self) -> None:
        result = stage_mapping(self.config, self.tmp / "output.json")
        self.assertTrue(result.ok)
        self.assertTrue(result.skipped)
        self.assertIn("testmon", result.message)

    def test_adequacy_skips_without_behaviour_report(self) -> None:
        result = stage_adequacy(self.config, self.tmp / "missing.json", None)
        self.assertTrue(result.ok)
        self.assertTrue(result.skipped)

    def test_pipeline_stops_cleanly_when_a_is_unavailable(self) -> None:
        results = run_pipeline(self.config)
        self.assertEqual(len(results), 5)
        self.assertFalse(results[0].ok)
        self.assertTrue(all(item.skipped for item in results[1:]))
        self.assertIn("stage A unavailable", results[1].message)

    def test_skip_flag_marks_stage_skipped(self) -> None:
        path = self.tmp / "output.json"
        path.write_text(
            json.dumps({"schema_version": "2.0", "mode": "pr_ast", "changes": [], "skipped": [], "warnings": []}),
            encoding="utf-8",
        )
        config = PipelineConfig(repo=self.tmp, workdir=self.tmp / "work2", changes=path, skip=("B",))
        results = {item.name: item for item in run_pipeline(config)}
        self.assertTrue(results["B"].skipped)
        self.assertEqual(results["B"].message, "skipped by request")


class ReuseArtefacts(unittest.TestCase):
    """Stages C/D often cannot run here (no testmon DB, different project root),
    so the pipeline must accept artefacts produced elsewhere."""

    def setUp(self) -> None:
        self.tmp = Path(__file__).resolve().parent / "_pipe_tmp3"
        if self.tmp.exists():
            shutil.rmtree(self.tmp)
        self.tmp.mkdir(parents=True)

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def config(self, **kwargs) -> PipelineConfig:
        base = dict(
            repo=self.tmp,
            workdir=self.tmp / "work",
            python=sys.executable,
            changes=FIXTURES / "changes.json",
        )
        base.update(kwargs)
        return PipelineConfig(**base)

    def test_behavior_reuse_skips_subprocesses(self) -> None:
        result = stage_behavior(self.config(behavior_file=FIXTURES / "behavior.json"), report())
        self.assertTrue(result.ok)
        self.assertEqual(result.message, "reused")
        self.assertEqual(result.output, FIXTURES / "behavior.json")

    def test_mapping_reuse_skips_the_database_requirement(self) -> None:
        result = stage_mapping(self.config(mapping_file=FIXTURES / "mapping_result.json"), FIXTURES / "changes.json")
        self.assertTrue(result.ok)
        self.assertEqual(result.message, "reused")

    def test_adequacy_reuse_ignores_missing_behavior(self) -> None:
        result = stage_adequacy(self.config(adequacy_file=FIXTURES / "adequacy.json"), self.tmp / "missing.json", None)
        self.assertTrue(result.ok)
        self.assertEqual(result.message, "reused")

    def test_missing_reused_file_fails_the_stage(self) -> None:
        result = stage_mapping(self.config(mapping_file=self.tmp / "nope.json"), FIXTURES / "changes.json")
        self.assertFalse(result.ok)
        self.assertIn("not found", result.message)

    def test_full_chain_from_artefacts_reproduces_the_fixture_score(self) -> None:
        config = self.config(
            behavior_file=FIXTURES / "behavior.json",
            mapping_file=FIXTURES / "mapping_result.json",
            adequacy_file=FIXTURES / "adequacy.json",
        )
        results = {item.name: item for item in run_pipeline(config)}
        self.assertTrue(all(item.ok for item in results.values()))
        self.assertEqual(results["C"].message, "reused")
        payload = json.loads(results["E"].output.read_text(encoding="utf-8"))
        self.assertAlmostEqual(payload["gaps"][0]["gap_score"], 43.55, delta=0.01)
        self.assertEqual(payload["inputs"], {"behavior": "loaded", "mapping": "loaded", "adequacy": "loaded"})


if __name__ == "__main__":
    unittest.main()
