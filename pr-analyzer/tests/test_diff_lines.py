"""Regression tests for diff line-number parsing.

Focus: ``HunkData.lines`` must fill ``old_line`` / ``new_line`` for every line
(reading a non-existent unidiff attribute once left them all ``None``, so every
hunk was misclassified as module_level). Both parser paths are checked here.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from analyzer import _parse_diff_manual, parse_diff  # noqa: E402

try:  # unidiff is an optional dependency
    import unidiff  # noqa: F401

    HAS_UNIDIFF = True
except ImportError:  # pragma: no cover
    HAS_UNIDIFF = False

SAMPLE = """diff --git a/pkg/mod.py b/pkg/mod.py
index 1111111..2222222 100644
--- a/pkg/mod.py
+++ b/pkg/mod.py
@@ -10,3 +10,4 @@ def foo():
     a = 1
-    b = 2
+    b = 3
+    c = 4
     return a
@@ -30,1 +31,1 @@
-x = 1
+x = 2
diff --git a/pkg/new.py b/pkg/new.py
new file mode 100644
--- /dev/null
+++ b/pkg/new.py
@@ -0,0 +1,2 @@
+def h():
+    return 1
diff --git a/pkg/old.py b/pkg/old.py
deleted file mode 100644
--- a/pkg/old.py
+++ /dev/null
@@ -1,1 +0,0 @@
-def gone():
"""


def _line_tuples(files):
    out = []
    for file_diff in files:
        for hunk in file_diff.hunks:
            out.append(
                [
                    (line.kind, line.old_line, line.new_line, line.text)
                    for line in hunk.lines
                ]
            )
    return out


class DiffLineNumberTests(unittest.TestCase):
    def test_line_numbers_are_filled(self) -> None:
        files = parse_diff(SAMPLE)
        tuples = _line_tuples(files)
        for hunk_lines in tuples:
            for kind, old_line, new_line, _ in hunk_lines:
                if kind == "add":
                    self.assertIsNone(old_line)
                    self.assertIsNotNone(new_line, "added lines must have new_line")
                elif kind == "del":
                    self.assertIsNotNone(old_line, "deleted lines must have old_line")
                    self.assertIsNone(new_line)
                else:
                    self.assertIsNotNone(old_line)
                    self.assertIsNotNone(new_line)

    def test_hunk_one_numbering(self) -> None:
        files = {f.path: f for f in parse_diff(SAMPLE)}
        hunk = files["pkg/mod.py"].hunks[0]
        self.assertEqual(
            [(line.kind, line.old_line, line.new_line) for line in hunk.lines],
            [
                ("context", 10, 10),
                ("del", 11, None),
                ("add", None, 11),
                ("add", None, 12),
                ("context", 12, 13),
            ],
        )
        self.assertEqual(hunk.added, [11, 12])
        self.assertEqual(hunk.removed, [11])

    def test_added_and_removed_files(self) -> None:
        files = {f.path: f for f in parse_diff(SAMPLE)}
        new_file = files["pkg/new.py"]
        self.assertEqual(new_file.status, "added")
        self.assertEqual(
            [(line.kind, line.old_line, line.new_line) for line in new_file.hunks[0].lines],
            [("add", None, 1), ("add", None, 2)],
        )
        old_file = files["pkg/old.py"]
        self.assertEqual(old_file.status, "removed")
        self.assertEqual(
            [(line.kind, line.old_line, line.new_line) for line in old_file.hunks[0].lines],
            [("del", 1, None)],
        )

    @unittest.skipUnless(HAS_UNIDIFF, "unidiff not installed; built-in parser only")
    def test_unidiff_and_manual_agree(self) -> None:
        self.assertEqual(_line_tuples(parse_diff(SAMPLE)), _line_tuples(_parse_diff_manual(SAMPLE)))

    def test_no_newline_marker_is_not_a_line(self) -> None:
        text = SAMPLE.replace(
            "-def gone():", "-def gone():\n\\ No newline at end of file"
        )
        files = {f.path: f for f in _parse_diff_manual(text)}
        self.assertEqual(len(files["pkg/old.py"].hunks[0].lines), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
