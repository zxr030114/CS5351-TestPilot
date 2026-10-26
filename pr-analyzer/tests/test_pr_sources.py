"""Offline tests for the PR source mirror (``get_pr_source_files``), no network.

A fake session stands in for ``requests.Session``: the PR endpoint, the paginated file list and
``raw.githubusercontent.com`` are all answered from in-memory fixtures.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import shutil
import sys
import unittest
import uuid
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from analyzer import AnalysisError, get_pr_source_files  # noqa: E402

# Temporary mirrors must live inside the workspace: the system temp directory is not writable
# here, and deleting directories created there can be blocked by ACLs even for the same user.
TMP_ROOT = Path(__file__).resolve().parents[3] / "_tmp_pr_sources"
TMP_ROOT.mkdir(parents=True, exist_ok=True)


@contextlib.contextmanager
def workspace_tempdir() -> Iterator[str]:
    """``TemporaryDirectory`` rooted in the workspace, cleaning up on a best-effort basis.

    ``tempfile.mkdtemp`` is deliberately avoided: directories it creates cannot be written to in
    this environment (access is denied), while a plainly created directory works fine.
    """
    path = TMP_ROOT / f"case-{uuid.uuid4().hex[:8]}"
    path.mkdir(parents=True, exist_ok=True)
    try:
        yield str(path)
    finally:
        shutil.rmtree(path, ignore_errors=True)

BASE_SHA = "b" * 40
HEAD_SHA = "h" * 40

PR_PAYLOAD = {
    "number": 7,
    "base": {"sha": BASE_SHA, "ref": "main"},
    "head": {"sha": HEAD_SHA, "ref": "feature"},
}

FILES_PAYLOAD = [
    {"filename": "pkg/core.py", "status": "modified", "additions": 2, "deletions": 1},
    {"filename": "pkg/new_mod.py", "status": "added", "additions": 10, "deletions": 0},
    {"filename": "pkg/old_mod.py", "status": "removed", "additions": 0, "deletions": 4},
    {"filename": "pkg/moved.py", "status": "renamed", "previous_filename": "pkg/before.py"},
    {"filename": "../escape.py", "status": "modified"},
]

RAW_CONTENT: Dict[str, bytes] = {
    f"{BASE_SHA}/pkg/core.py": b"def add(a, b):\n    return a + b\n",
    f"{HEAD_SHA}/pkg/core.py": b"def add(a, b, c=0):\n    return a + b + c\n",
    f"{HEAD_SHA}/pkg/new_mod.py": b"VALUE = 1\n",
    f"{BASE_SHA}/pkg/old_mod.py": b"GONE = True\n",
    f"{BASE_SHA}/pkg/before.py": b"KEPT = 1\n",
    f"{HEAD_SHA}/pkg/moved.py": b"KEPT = 2\n",
}


class FakeResponse:
    def __init__(
        self,
        *,
        status: int = 200,
        payload: Any = None,
        content: bytes = b"",
        text: str = "",
        headers: Optional[Dict[str, str]] = None,
    ) -> None:
        self.status_code = status
        self._payload = payload
        self.content = content
        self.text = text
        self.headers = headers or {}

    def json(self) -> Any:
        return self._payload


class FakeSession:
    """Answers the three URL shapes used by ``get_pr_source_files``."""

    def __init__(self, raw: Optional[Dict[str, bytes]] = None, *, files: Optional[List[Dict[str, Any]]] = None) -> None:
        self.raw = dict(RAW_CONTENT if raw is None else raw)
        self.files = list(files if files is not None else FILES_PAYLOAD)
        self.calls: List[str] = []
        self.closed = False

    def get(self, url: str, headers: Optional[Dict[str, str]] = None, timeout: float = 0) -> FakeResponse:
        self.calls.append(url)
        if url.endswith("/pulls/7"):
            return FakeResponse(payload=PR_PAYLOAD)
        if "/pulls/7/files" in url:
            page = 1
            if "page=" in url:
                page = int(url.rsplit("page=", 1)[1])
            return FakeResponse(payload=self.files if page == 1 else [])
        marker = "raw.githubusercontent.com/"
        if marker in url:
            tail = url.split(marker, 1)[1]
            # <owner>/<name>/<sha>/<path>
            parts = tail.split("/", 3)
            key = f"{parts[2]}/{parts[3]}"
            if key not in self.raw:
                return FakeResponse(status=404, text="404: Not Found")
            return FakeResponse(content=self.raw[key])
        return FakeResponse(status=404, text=f"unexpected url {url}")

    def close(self) -> None:
        self.closed = True


class SourceMirrorTests(unittest.TestCase):
    def test_downloads_both_sides_and_writes_mirror(self) -> None:
        session = FakeSession()
        with workspace_tempdir() as tmp:
            manifest = get_pr_source_files("owner/repo", 7, out_dir=tmp, session=session)

            root = Path(tmp)
            self.assertTrue((root / "base" / "pkg" / "core.py").is_file())
            self.assertTrue((root / "head" / "pkg" / "core.py").is_file())
            self.assertEqual(
                (root / "head" / "pkg" / "core.py").read_bytes(), RAW_CONTENT[f"{HEAD_SHA}/pkg/core.py"]
            )

            # added file: head only; removed file: base only
            self.assertFalse((root / "base" / "pkg" / "new_mod.py").exists())
            self.assertTrue((root / "head" / "pkg" / "new_mod.py").is_file())
            self.assertTrue((root / "base" / "pkg" / "old_mod.py").is_file())
            self.assertFalse((root / "head" / "pkg" / "old_mod.py").exists())
            # renamed file: base copy lives at the previous path, head copy at the new one
            self.assertTrue((root / "base" / "pkg" / "before.py").is_file())
            self.assertFalse((root / "base" / "pkg" / "moved.py").exists())

            records = {item["path"]: item for item in manifest["files"]}
            self.assertIsNone(records["pkg/new_mod.py"]["base"])
            self.assertIsNone(records["pkg/old_mod.py"]["head"])
            self.assertEqual(records["pkg/core.py"]["old_path"], None)
            self.assertEqual(records["pkg/moved.py"]["old_path"], "pkg/before.py")
            self.assertEqual(records["pkg/moved.py"]["base"]["saved"], "base/pkg/before.py")
            self.assertTrue(records["pkg/core.py"]["changed"])
            self.assertTrue(records["pkg/moved.py"]["changed"])

            # sha256 must match what we actually wrote
            saved = (root / "head" / "pkg" / "core.py").read_bytes()
            self.assertEqual(records["pkg/core.py"]["head"]["sha256"], hashlib.sha256(saved).hexdigest())
            self.assertEqual(records["pkg/core.py"]["head"]["lines"], 2)

            self.assertEqual(manifest["repo"]["base_sha"], BASE_SHA)
            self.assertEqual(manifest["repo"]["head_sha"], HEAD_SHA)
            self.assertEqual(manifest["counts"]["files"], 4)  # ../escape.py is filtered out
            self.assertEqual(manifest["counts"]["failed"], 1)  # the unsafe path
            # an injected session belongs to the caller: the function must not close it
            self.assertFalse(session.closed)

    def test_manifest_is_written_to_disk(self) -> None:
        with workspace_tempdir() as tmp:
            get_pr_source_files("owner/repo", 7, out_dir=tmp, session=FakeSession())
            manifest_path = Path(tmp) / "manifest.json"
            self.assertTrue(manifest_path.is_file())
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(data["schema_version"], "1.0")
            self.assertEqual(data["mode"], "sources")
            self.assertEqual(len(data["files"]), 4)

    def test_unsafe_paths_never_escape_the_mirror(self) -> None:
        with workspace_tempdir() as tmp:
            get_pr_source_files("owner/repo", 7, out_dir=tmp, session=FakeSession())
            root = Path(tmp).resolve()
            self.assertFalse((root.parent / "escape.py").exists())
            self.assertEqual(list(root.rglob("escape.py")), [])

    def test_existing_file_is_kept_without_force(self) -> None:
        with workspace_tempdir() as tmp:
            root = Path(tmp)
            target = root / "head" / "pkg" / "core.py"
            target.parent.mkdir(parents=True)
            target.write_bytes(b"local edit\n")

            manifest = get_pr_source_files("owner/repo", 7, out_dir=tmp, session=FakeSession())
            self.assertEqual(target.read_bytes(), b"local edit\n")
            self.assertEqual(manifest["counts"]["kept"], 1)

            manifest = get_pr_source_files(
                "owner/repo", 7, out_dir=tmp, session=FakeSession(), force=True
            )
            self.assertEqual(target.read_bytes(), RAW_CONTENT[f"{HEAD_SHA}/pkg/core.py"])
            self.assertEqual(manifest["counts"]["kept"], 0)

    def test_all_downloads_failing_is_reported(self) -> None:
        failing = FakeSession(raw={})  # every raw URL answers 404
        with workspace_tempdir() as tmp:
            with self.assertRaises(AnalysisError):
                get_pr_source_files("owner/repo", 7, out_dir=tmp, session=failing)

    def test_unsafe_repo_name_is_rejected(self) -> None:
        with self.assertRaises(AnalysisError):
            get_pr_source_files("not-a-repo", 7, session=FakeSession())


if __name__ == "__main__":
    unittest.main(verbosity=2)




