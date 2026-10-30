"""Smoke tests for ``demo.py`` (the offline scenario walkthrough)."""

from __future__ import annotations

import contextlib
import io
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import demo  # noqa: E402


def run(argv: list[str]) -> tuple[int, str]:
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        code = demo.main(argv)
    return code, buffer.getvalue()


class DemoTests(unittest.TestCase):
    def test_default_run_lists_every_scenario(self) -> None:
        code, output = run([])
        self.assertEqual(code, 0)
        for name in (
            "00-precision-blind",
            "01-exception-uncovered",
            "02-boundary-no-assertion",
            "03-new-branch-covered",
            "04-documentation-only",
            "05-mapping-unresolved",
        ):
            self.assertIn(name, output)

    def test_default_run_renders_a_comment(self) -> None:
        _, output = run([])
        self.assertIn("PR comment for 01-exception-uncovered", output)
        self.assertIn("Changed behavior", output)
        self.assertIn("Suggested input", output)
        self.assertIn("one-line status", output)

    def test_choose_a_scenario(self) -> None:
        code, output = run(["--scenario", "00-precision-blind"])
        self.assertEqual(code, 0)
        self.assertIn("withdraw", output)
        self.assertIn("integer inputs", output)

    def test_json_format_emits_the_report(self) -> None:
        code, output = run(["--scenario", "03-new-branch-covered", "--format", "json"])
        self.assertEqual(code, 0)
        self.assertIn('"schema_version": "1.0"', output)
        self.assertIn('"module": "test_gap_scoring"', output)

    def test_unknown_scenario_is_an_input_error(self) -> None:
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer), contextlib.redirect_stderr(io.StringIO()):
            code = demo.main(["--scenario", "nope"])
        self.assertEqual(code, 2)


if __name__ == "__main__":
    unittest.main()
