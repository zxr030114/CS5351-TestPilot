"""Offline tests for the end-to-end PR pipeline (``analyze_pr`` / ``find_renames``), no network.

The fake session mirrors ``tests/test_pr_sources.py``: the PR endpoint, the file list and
``raw.githubusercontent.com`` are answered from in-memory fixtures, so the whole pipeline
(mirror -> parse -> extract -> compare -> rename pass -> merge) runs on real files on disk.
"""

from __future__ import annotations

import contextlib
import hashlib
import shutil
import sys
import unittest
import uuid
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from analyzer import (  # noqa: E402
    CATEGORY_RENAME,
    analyze_pr,
    entity_similarity,
    find_renames,
)

TMP_ROOT = Path(__file__).resolve().parents[3] / "_tmp_analyze_pr"
TMP_ROOT.mkdir(parents=True, exist_ok=True)


@contextlib.contextmanager
def workspace_tempdir() -> Iterator[str]:
    path = TMP_ROOT / f"case-{uuid.uuid4().hex[:8]}"
    path.mkdir(parents=True, exist_ok=True)
    try:
        yield str(path)
    finally:
        shutil.rmtree(path, ignore_errors=True)


BASE_SHA = "b" * 40
HEAD_SHA = "h" * 40

PR_PAYLOAD = {
    "number": 9,
    "base": {"sha": BASE_SHA, "ref": "main"},
    "head": {"sha": HEAD_SHA, "ref": "feature"},
}

CORE_BASE = '''def add(a, b):
    return a + b


def helper(x):
    return x * 2
'''

CORE_HEAD = '''def add(a, b, c=0):
    return a + b + c
'''

UTIL_BASE = "VALUE = 1\n"

UTIL_HEAD = '''VALUE = 1


def helper(x):
    return x * 2
'''

OLD_PY = '''def legacy():
    return 1
'''

NEW_PY = '''def brand_new():
    return 42
'''

FILES_PAYLOAD = [
    {"filename": "pkg/core.py", "status": "modified", "additions": 3, "deletions": 3},
    {"filename": "pkg/util.py", "status": "modified", "additions": 4, "deletions": 0},
    {"filename": "pkg/old.py", "status": "removed", "additions": 0, "deletions": 2},
    {"filename": "pkg/new.py", "status": "added", "additions": 2, "deletions": 0},
    {"filename": "docs/guide.md", "status": "modified", "additions": 1, "deletions": 1},
]

RAW_CONTENT: Dict[str, bytes] = {
    f"{BASE_SHA}/pkg/core.py": CORE_BASE.encode(),
    f"{HEAD_SHA}/pkg/core.py": CORE_HEAD.encode(),
    f"{BASE_SHA}/pkg/util.py": UTIL_BASE.encode(),
    f"{HEAD_SHA}/pkg/util.py": UTIL_HEAD.encode(),
    f"{BASE_SHA}/pkg/old.py": OLD_PY.encode(),
    f"{HEAD_SHA}/pkg/new.py": NEW_PY.encode(),
    f"{BASE_SHA}/docs/guide.md": b"# old\n",
    f"{HEAD_SHA}/docs/guide.md": b"# new\n",
}


class FakeResponse:
    def __init__(self, *, status: int = 200, payload: Any = None, content: bytes = b"", text: str = "") -> None:
        self.status_code = status
        self._payload = payload
        self.content = content
        self.text = text
        self.headers: Dict[str, str] = {}

    def json(self) -> Any:
        return self._payload


class FakeSession:
    def __init__(self, raw: Optional[Dict[str, bytes]] = None) -> None:
        self.raw = dict(RAW_CONTENT if raw is None else raw)
        self.closed = False

    def get(self, url: str, headers: Optional[Dict[str, str]] = None, timeout: float = 0) -> FakeResponse:
        if url.endswith("/pulls/9"):
            return FakeResponse(payload=PR_PAYLOAD)
        if "/pulls/9/files" in url:
            page = 1
            if "page=" in url:
                page = int(url.rsplit("page=", 1)[1])
            return FakeResponse(payload=FILES_PAYLOAD if page == 1 else [])
        marker = "raw.githubusercontent.com/"
        if marker in url:
            parts = url.split(marker, 1)[1].split("/", 3)
            key = f"{parts[2]}/{parts[3]}"
            if key not in self.raw:
                return FakeResponse(status=404, text="404: Not Found")
            return FakeResponse(content=self.raw[key])
        return FakeResponse(status=404, text=f"unexpected url {url}")

    def close(self) -> None:
        self.closed = True


class AnalyzePrTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = workspace_tempdir()
        self.addCleanup(self._tmp.__exit__, None, None, None)
        self.out_dir = self._tmp.__enter__()

    def _analyze(self) -> Dict[str, Any]:
        return analyze_pr("owner/repo", 9, out_dir=self.out_dir, session=FakeSession())

    def test_schema_and_summary(self) -> None:
        result = self._analyze()
        self.assertEqual(result["schema_version"], "2.0")
        self.assertEqual(result["mode"], "pr_ast")
        self.assertEqual(result["repo"]["base_sha"], BASE_SHA)
        self.assertEqual(result["summary"]["files_changed"], 5)
        self.assertEqual(result["summary"]["files_skipped"], 1)  # docs/guide.md is noise
        self.assertEqual(result["summary"]["renames"], 1)
        self.assertEqual(result["summary"]["changes"], 4)

    def test_cross_file_move_becomes_rename(self) -> None:
        result = self._analyze()
        renames = [c for c in result["changes"] if c["change_type"] == "rename"]
        self.assertEqual(len(renames), 1)
        record = renames[0]
        self.assertEqual(record["entity_name"], "helper")
        self.assertEqual(record["change_category"], CATEGORY_RENAME)
        self.assertEqual(record["old_path"], "pkg/core.py")
        self.assertEqual(record["new_path"], "pkg/util.py")
        self.assertEqual(record["old_qualname"], "helper")
        self.assertEqual(record["file_path"], "pkg/util.py")
        self.assertAlmostEqual(record["similarity"], 1.0)

    def test_moved_entity_is_not_reported_as_delete_plus_add(self) -> None:
        result = self._analyze()
        core = [c for c in result["files"] if c["file_path"] == "pkg/core.py"][0]
        self.assertNotIn("delete", [c["change_type"] for c in core["changes"]])
        util = [c for c in result["files"] if c["file_path"] == "pkg/util.py"][0]
        self.assertEqual([c["change_type"] for c in util["changes"]], ["rename"])

    def test_signature_change_survives_the_rename_pass(self) -> None:
        result = self._analyze()
        add = [c for c in result["changes"] if c["entity_name"] == "add"][0]
        self.assertEqual(add["change_type"], "modify")
        self.assertEqual(add["change_category"], "Signature change")

    def test_real_add_and_delete_are_kept(self) -> None:
        result = self._analyze()
        new_py = [c for c in result["files"] if c["file_path"] == "pkg/new.py"][0]
        self.assertEqual(new_py["changes"][0]["change_type"], "add")
        old_py = [c for c in result["files"] if c["file_path"] == "pkg/old.py"][0]
        self.assertEqual(old_py["changes"][0]["change_type"], "delete")
        # delete records use old-file lines; pkg/new.py is a test-free helper file
        self.assertFalse(new_py["is_test_file"])

    def test_every_record_keeps_the_strict_seven_keys(self) -> None:
        result = self._analyze()
        for change in result["changes"]:
            for key in (
                "entity_type",
                "entity_name",
                "change_type",
                "change_category",
                "line_start",
                "line_end",
                "summary",
                "file_path",
            ):
                self.assertIn(key, change)

    def test_skipped_files_are_reported_with_a_reason(self) -> None:
        result = self._analyze()
        self.assertEqual([item["path"] for item in result["skipped"]], ["docs/guide.md"])
        self.assertTrue(result["skipped"][0]["reason"].startswith("noise:"))

    def test_mirror_layout_is_kept_for_downstream_debugging(self) -> None:
        result = self._analyze()
        self.assertTrue((Path(result["mirror"]["out_dir"]) / "manifest.json").is_file())
        self.assertTrue((Path(result["mirror"]["out_dir"]) / "base" / "pkg" / "core.py").is_file())
        self.assertTrue((Path(result["mirror"]["out_dir"]) / "head" / "pkg" / "util.py").is_file())


class FindRenamesTests(unittest.TestCase):
    @staticmethod
    def entity(
        qualname: str,
        text: str,
        *,
        line_start: int = 1,
        entity_type: str = "function",
        name: Optional[str] = None,
    ) -> Dict[str, Any]:
        return {
            "qualname": qualname,
            "entity_name": name or qualname.rsplit(".", 1)[-1],
            "entity_type": entity_type,
            "node_text": text,
            "node_hash": hashlib.sha1(text.encode("utf-8")).hexdigest()[:16],
            "line_start": line_start,
            "line_end": line_start + text.count("\n"),
        }

    def test_identical_text_matches_with_similarity_one(self) -> None:
        old = {"a.py": [self.entity("helper", "identical body")]}
        new = {"b.py": [self.entity("helper", "identical body")]}
        matches = find_renames(old, new)
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0]["similarity"], 1.0)
        self.assertTrue(matches[0]["node_hash_equal"])
        self.assertEqual((matches[0]["old_path"], matches[0]["new_path"]), ("a.py", "b.py"))

    def test_unrelated_bodies_are_not_paired(self) -> None:
        old = {"a.py": [self.entity("helper", "return x * 2")]}
        new = {"b.py": [self.entity("other", "print('totally different output here')")]}
        self.assertEqual(find_renames(old, new), [])

    def test_threshold_is_respected(self) -> None:
        old = {"a.py": [self.entity("helper", "abcdef")]}
        new = {"b.py": [self.entity("helper", "abcXYZ")]}
        self.assertEqual(find_renames(old, new, min_similarity=0.99), [])
        self.assertEqual(len(find_renames(old, new, min_similarity=0.5)), 1)

    def test_entity_type_must_match(self) -> None:
        old = {"a.py": [self.entity("Thing", "identical body", entity_type="class")]}
        new = {"b.py": [self.entity("thing", "identical body", entity_type="function")]}
        self.assertEqual(find_renames(old, new), [])

    def test_greedy_matching_uses_each_candidate_once(self) -> None:
        old = {"a.py": [self.entity("one", "identical payload")]}
        new = {
            "b.py": [self.entity("two", "identical payload")],
            "c.py": [self.entity("three", "identical payload")],
        }
        matches = find_renames(old, new)
        self.assertEqual(len(matches), 1)
        used = {(match["new_path"], match["new_qualname"]) for match in matches}
        self.assertEqual(len(used), 1)

    def test_similarity_is_symmetric_and_bounded(self) -> None:
        first = self.entity("a", "x = 1\ny = 2\n")
        second = self.entity("b", "x = 1\ny = 3\n")
        self.assertGreaterEqual(entity_similarity(first, second), 0.0)
        self.assertLessEqual(entity_similarity(first, second), 1.0)
        self.assertAlmostEqual(
            entity_similarity(first, second), entity_similarity(second, first)
        )


if __name__ == "__main__":
    unittest.main()
