"""Tests for the ``gap_scoring`` command line entry point."""

from __future__ import annotations

import contextlib
import io
import json
import shutil
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import gap_scoring  # noqa: E402

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def run(argv: list[str]) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = gap_scoring.main(argv)
    return code, out.getvalue(), err.getvalue()


class CliTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = Path(__file__).resolve().parent / "_cli_tmp"
        if cls.tmp.exists():
            shutil.rmtree(cls.tmp)
        cls.tmp.mkdir(parents=True)

        cls.changes = cls.tmp / "changes.json"
        cls.behavior = cls.tmp / "behavior.json"
        cls.adequacy = cls.tmp / "adequacy.json"
        cls.changes.write_text(
            json.dumps(
                {
                    "schema_version": "2.0",
                    "mode": "pr_ast",
                    "repo": {"owner": "demo", "name": "auth", "pr_number": 7},
                    "changes": [
                        {
                            "file_path": "src/auth.py",
                            "entity_name": "login",
                            "entity_type": "function",
                            "change_type": "modify",
                            "change_category": "Logic change",
                            "line_start": 10,
                            "line_end": 20,
                            "summary": "s",
                        }
                    ],
                    "skipped": [],
                    "warnings": [],
                }
            ),
            encoding="utf-8",
        )
        cls.behavior.write_text(
            json.dumps(
                {
                    "schema_version": "1.0",
                    "behavior_changes": [
                        {
                            "type": "EXCEPTION_BEHAVIOR_CHANGE",
                            "function": "login",
                            "operation": "added",
                            "new_exception_type": "PermissionError",
                            "new_line": 15,
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        cls.adequacy.write_text(
            json.dumps(
                {
                    "schema_version": "1.0",
                    "mode": "test_adequacy_analysis",
                    "analysis": {
                        "method": "python-ast",
                        "runtime_adequacy_proven": False,
                        "input_complete": True,
                        "selection_complete": None,
                    },
                    "results": [
                        {
                            "change": {
                                "type": "EXCEPTION_BEHAVIOR_CHANGE",
                                "function": "login",
                                "new_line": 15,
                            },
                            "status": "no_candidate_test",
                            "candidate_tests": [],
                            "evidence": [],
                            "recommendations": [],
                        }
                    ],
                    "errors": [],
                    "summary": {},
                    "limitations": [],
                }
            ),
            encoding="utf-8",
        )

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def full_argv(self) -> list[str]:
        return [
            "--changes", str(FIXTURES / "changes.json"),
            "--behavior", str(FIXTURES / "behavior.json"),
            "--mapping", str(FIXTURES / "mapping_result.json"),
            "--adequacy", str(FIXTURES / "adequacy.json"),
        ]

    def high_argv(self) -> list[str]:
        return ["--changes", str(self.changes), "--behavior", str(self.behavior), "--adequacy", str(self.adequacy)]

    # -- happy path -------------------------------------------------------

    def test_json_report_on_stdout(self) -> None:
        code, out, _ = run(self.full_argv())
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertEqual(payload["module"], "test_gap_scoring")
        self.assertEqual(len(payload["gaps"]), 1)
        self.assertEqual(payload["gaps"][0]["entity_name"], "withdraw")

    def test_writes_to_output_file(self) -> None:
        target = self.tmp / "report.json"
        code, out, _ = run(self.full_argv() + ["-o", str(target)])
        self.assertEqual(code, 0)
        self.assertEqual(out, "")
        payload = json.loads(target.read_text(encoding="utf-8"))
        self.assertEqual(payload["summary"]["changes_total"], 1)

    def test_markdown_format(self) -> None:
        code, out, _ = run(self.full_argv() + ["--format", "markdown"])
        self.assertEqual(code, 0)
        self.assertIn("## Test Gap Report", out)
        self.assertIn("Changed behavior", out)

    def test_top_option(self) -> None:
        code, out, _ = run(self.full_argv() + ["--top", "0"])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["gaps"], [])

    def test_sources_recorded(self) -> None:
        code, out, _ = run(self.full_argv())
        self.assertEqual(code, 0)
        self.assertIn("changes", json.loads(out)["sources"])

    def test_only_changes_still_reports(self) -> None:
        code, out, _ = run(["--changes", str(FIXTURES / "changes.json")])
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertEqual(payload["gaps"], [])
        self.assertEqual(payload["unscored"][0]["reason"], "no_behavior_change")

    def test_verbose_writes_diagnostics_to_stderr(self) -> None:
        code, _, err = run(self.full_argv() + ["-v"])
        self.assertEqual(code, 0)
        self.assertIn("scored", err)

    # -- failure modes ----------------------------------------------------

    def test_missing_changes_file_is_input_error(self) -> None:
        code, _, err = run(["--changes", str(self.tmp / "nope.json")])
        self.assertEqual(code, 2)
        self.assertIn("error:", err)

    def test_bad_schema_is_input_error(self) -> None:
        broken = self.tmp / "broken.json"
        broken.write_text('{"schema_version": "1.0", "mode": "diff", "changes": []}', encoding="utf-8")
        code, _, err = run(["--changes", str(broken)])
        self.assertEqual(code, 2)
        self.assertIn("diff report", err)

    def test_unwritable_output_is_input_error(self) -> None:
        # A directory cannot be opened as a file.
        code, _, err = run(self.full_argv() + ["-o", str(self.tmp)])
        self.assertEqual(code, 2)
        self.assertIn("cannot write", err)

    def test_usage_error_raises_systemexit(self) -> None:
        with self.assertRaises(SystemExit):
            with contextlib.redirect_stderr(io.StringIO()):
                gap_scoring.main([])

    # -- gating -----------------------------------------------------------

    def test_gate_passes_when_nothing_is_high(self) -> None:
        code, _, _ = run(self.full_argv() + ["--fail-on-high"])
        self.assertEqual(code, 0)

    def test_gate_fails_on_high_gap(self) -> None:
        code, out, err = run(self.high_argv() + ["--fail-on-high"])
        self.assertEqual(code, 3)
        self.assertIn("gate failed", err)
        payload = json.loads(out)
        self.assertEqual(payload["gaps"][0]["risk"], "HIGH")

    def test_high_gap_without_gate_returns_zero(self) -> None:
        code, out, _ = run(self.high_argv())
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["summary"]["gaps_high"], 1)


if __name__ == "__main__":
    unittest.main()
