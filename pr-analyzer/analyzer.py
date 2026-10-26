"""PR / git Change Analyzer -- core logic (Member 1).

===============================================================================
1. Where this module sits in the system
===============================================================================
Upstream: a unified diff from a GitHub PR or a local git repository.
Downstream: the test-dependency mapping module (Member 2). It owns the
``.testmondata`` SQLite file produced by pytest-testmon, which records, for
every test, the files it executed and the fingerprints of the code blocks in
those files.

This module does exactly one thing: turn a diff into structured JSON that says
which files and which scopes (class / function / method / module level) changed,
and how. Downstream therefore never has to parse a diff or run an AST pass.

===============================================================================
2. Why the interface must look like this (read this before changing it)
===============================================================================
pytest-testmon matches by *code-block fingerprint*, not by line numbers:
  * ``testmon/process_code.py:126-188`` -- ``Module.blocks`` splits every .py
    file into ``Block(start, end, name)`` where
        - ``start`` = line of the FIRST STATEMENT of the body (not the ``def``);
        - ``end``   = line before the next sibling definition; the module block
          ends at len(lines);
        - ``name``  = function name; the module-level block is ``<module>``.
  * a block's fingerprint is ``zlib.crc32(<ast.dump-style repr>)``
    (``process_code.py:34-39``); function bodies appear as
    ``transformed_into_block`` inside that repr.
  * every test records the set of block fingerprints it covered
    (``create_fingerprint``, ``process_code.py:291-306``: a covered line inside
    [start, end] pulls that block in).
  * on a re-run a test is "affected" when its recorded set is no longer a subset
    of the current file's fingerprints (``match_fingerprint``,
    ``process_code.py:280-283``; ``check_fingerprint_db``, ``db.py:39-46``).

Three hard constraints follow:

(1) The join key must be ``(repo-relative POSIX path, code block)``.
    testmon stores ``filename`` produced by ``cached_relpath``
    (``testmon_core.py:436``):
    ``os.path.relpath(path, basepath).replace(os.sep, "/")``.
    So ``files[].path`` here is always repo-relative with forward slashes: no
    ``./``, no absolute paths, no case folding. Downstream can simply check
    ``file_record["path"] == testmon_filename``.

(2) Line numbers must be given twice: ``old_*`` (base) and ``new_*`` (head).
    A changed checksum is gone forever, so an "old block" can only be rebuilt
    from base line numbers inside the base source, and a "new block" only from
    head line numbers inside the head source. A single set would make line
    shifts unmanageable (insert three lines at the top and every later block
    moves).

(3) "Body edit" and "signature/decorator edit" must be distinguished.
    ``ast.iter_fields`` emits no ``lineno``, so the signature (``name``,
    ``args``, ``returns``, ``decorator_list``) belongs to the repr of the
    ENCLOSING block: a signature change hits the module (or class) block, not
    the function-body block. Nearly every test records the module-level block
    (imports execute), so a signature change is roughly file-level
    invalidation. Every hunk therefore carries ``change_kind``:
        - ``body``         affects only that scope's block
        - ``signature``    affects the parent block (coarse, usually file-wide)
        - ``module_level`` affects the ``<module>`` block
        - ``comment_only``  comments/blank lines only -- the AST repr ignores
          them, so NO checksum changes and downstream may skip the hunk
          (the single biggest time saver this module offers)
        - ``file_level``   non-Python / syntax error / binary: file-level match
    ``affects`` pre-expands the affected blocks into ``path::qualname`` strings,
    so downstream never has to walk the AST again.

===============================================================================
3. Output JSON (schema_version = "1.0")
===============================================================================
{
  "schema_version": "1.0",
  "mode": "pr" | "local",
  "repo": {"owner", "name", "base_sha", "head_sha", "pr_number", "root"},
  "files": [
    {
      "path": "pkg/mod.py",          # repo-relative, forward slashes
      "old_path": null,              # set on rename
      "status": "added|modified|removed|renamed",
      "language": "python",
      "is_python": true,
      "is_test_file": false,
      "is_noise": false,             # lock files / docs / images: skip
      "parse_ok": true,
      "binary": false,
      "file_level_change": false,    # true -> downstream matches by file
      "stats": {"added": 3, "removed": 1},
      "affected_scopes": ["pkg/mod.py::Class.method"],   # expanded, deduped
      "removed_scopes": [],
      "hunks": [
        {
          "id": "pkg/mod.py#h1",
          "old_start": 10, "old_lines": 4,
          "new_start": 10, "new_lines": 5,
          "changed_old_lines": [12],           # 1-based, inclusive, base version
          "changed_new_lines": [12, 13],       # 1-based, inclusive, head version
          "change_kind": "body",
          "scope": {                           # dominant scope of this hunk
            "qualname": "Class.method",        # "<module>" at module level
            "kind": "function|method|class|module",
            "def_line_new": 9,
            "body_start_new": 10,              # aligns with testmon Block.start
            "body_end_new": 14,                # aligns with testmon Block.end
            "signature_changed": false,
            "is_new_symbol": false
          },
          "affects": ["pkg/mod.py::Class.method"],
          "confidence": 0.9,
          "rationale": "AST located the change inside a function body ..."
        }
      ]
    }
  ],
  "summary": {"files_changed": 1, "python_files": 1, "hunks": 1,
              "lines_added": 3, "lines_removed": 1, "symbols_changed": 1,
              "test_files_touched": 0, "comment_only_hunks": 0,
              "noise_files": 0},
  "warnings": []
}

===============================================================================
4. How downstream (test-dependency mapping) should use this JSON
===============================================================================
Recommended path (most accurate, recommended for Member 2):
    With the head source in hand, reuse testmon's own
    ``testmon.process_code.Module`` to recompute head fingerprints, then use
    ``changed_new_lines`` + ``scope.body_start_new/body_end_new`` to map the
    "old block line numbers" onto "new block line numbers" and diff the
    fingerprint sets. This module reports *where* things changed; fingerprinting
    stays bit-identical with testmon.

Fallback path (no head source needed):
    Look up ``affected_scopes`` (``path::qualname``) against "which tests depend
    on this block of this file". A hit means affected. Files with
    ``file_level_change: true`` degrade to file-level matching (run every test
    that depends on the file).

Boundary rules:
  * ``status: "removed"``      -> every dependent test is affected (a deleted
                                  function can never produce a new fingerprint).
  * ``status: "added"`` or ``is_new_symbol: true``
                               -> nothing recorded in testmon yet; downstream
                                  should flag "needs new tests", not "re-run".
  * ``parse_ok: false`` / ``is_python: false``
                               -> file-level matching only; do not attempt
                                  symbol-level matching.
  * ``is_test_file: true``     -> do not use it to map against itself (a changed
                                  test file should simply be executed).
  * ``comment_only``           -> ``affects`` is empty; downstream may skip.
  * top-level ``schema_version`` -> must be validated; fields are additive and
                                  new fields always carry defaults.

===============================================================================
5. AST layer: entity comparison that does not need a diff
===============================================================================
``compare_entities(old_entities, new_entities)`` and
``compare_sources(old_source, new_source, file_path=...)`` emit records with
exactly seven keys: entity_type / entity_name / change_type(add|delete|modify) /
change_category(Signature change|Logic change|Documentation change|null) /
line_start / line_end / summary.
Priority: signature subtree > body > docstring; when none changed no record is
emitted (indentation, blank lines and comments never count as changes).
Line ownership: modify/add report head line numbers, delete reports base ones.
For downstream: entities with Signature change / Logic change must have their
covering tests re-run; Documentation change only needs that file's doctests;
add/delete follow the boundary rules above.
A pure rename shows up as delete + add (matching is by qualname); rename
semantics need a second pass over body_hash downstream, or wiring in
status=renamed from the diff report.
===============================================================================
"""

from __future__ import annotations

import ast
import difflib
import hashlib
import json
import logging
import os
import re
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

LOGGER = logging.getLogger(__name__)

SCHEMA_VERSION = "1.0"
GENERATOR_NAME = "pr-analyzer"
GENERATOR_VERSION = "0.1.0"
MODULE_SCOPE = "<module>"
GITHUB_API_URL = "https://api.github.com"

_LANGUAGE_BY_SUFFIX = {
    ".py": "python",
    ".pyi": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".java": "java",
    ".go": "go",
    ".rs": "rust",
    ".c": "c",
    ".h": "c",
    ".cpp": "cpp",
    ".hpp": "cpp",
    ".rb": "ruby",
    ".php": "php",
    ".cs": "csharp",
    ".kt": "kotlin",
    ".swift": "swift",
    ".md": "markdown",
    ".rst": "rst",
    ".txt": "text",
    ".toml": "toml",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".json": "json",
    ".ini": "ini",
    ".cfg": "ini",
}


class AnalysisError(RuntimeError):
    """An expected failure of this module (git missing, not a repo, API error, ...)."""


# ---------------------------------------------------------------------------
# Diff parsing
# ---------------------------------------------------------------------------


@dataclass
class DiffLine:
    """One line of a hunk (context lines included). Line numbers are 1-based;

    ``None`` means the line does not exist on that side.
    """

    kind: str
    text: str
    old_line: Optional[int] = None
    new_line: Optional[int] = None

    @property
    def marker(self) -> str:
        return {"add": "+", "del": "-"}.get(self.kind, " ")

    @property
    def is_change(self) -> bool:
        return self.kind in ("add", "del")

    def render(self) -> str:
        """Render back into a diff line (with the +/-/space marker)."""
        return self.marker + self.text


@dataclass
class HunkData:
    """A single unified-diff hunk. All line numbers are 1-based."""

    old_start: int = 0
    old_lines: int = 0
    new_start: int = 0
    new_lines: int = 0
    added: List[int] = field(default_factory=list)
    removed: List[int] = field(default_factory=list)
    added_texts: List[str] = field(default_factory=list)
    removed_texts: List[str] = field(default_factory=list)
    section: str = ""
    lines: List["DiffLine"] = field(default_factory=list)

    def line(self, kind: str) -> List[DiffLine]:
        return [ln for ln in self.lines if ln.kind == kind]


@dataclass
class FileDiff:
    """All changes to one file in a diff."""

    path: str = ""
    old_path: str = ""
    status: str = "modified"
    hunks: List[HunkData] = field(default_factory=list)
    binary: bool = False


_HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@ ?(.*)$")


def _strip_diff_prefix(name: Optional[str]) -> Optional[str]:
    """Strip the git ``a/`` / ``b/`` prefixes and quotes; ``/dev/null`` maps to None."""
    if not name:
        return None
    name = name.strip()
    if name == "/dev/null":
        return None
    if name.startswith('"') and name.endswith('"') and len(name) >= 2:
        name = name[1:-1]
    if name.startswith(("a/", "b/")):
        name = name[2:]
    return name or None


def _parse_diff_unidiff(patch_set: Any) -> List[FileDiff]:
    out: List[FileDiff] = []
    for patched in patch_set:
        status = "modified"
        if getattr(patched, "is_added_file", False):
            status = "added"
        elif getattr(patched, "is_removed_file", False):
            status = "removed"
        elif getattr(patched, "is_rename", False):
            status = "renamed"

        path = _strip_diff_prefix(patched.target_file) or _strip_diff_prefix(
            patched.source_file
        ) or ""
        old_path = _strip_diff_prefix(patched.source_file) or path

        hunks: List[HunkData] = []
        for hunk in patched:
            lines: List[DiffLine] = []
            # Do not rely on unidiff's Line.source_line_no (attribute names
            # differ between releases and it needs internal hunk state anyway);
            # count from the hunk start, which the header gives us.
            old_no = hunk.source_start or 0
            new_no = hunk.target_start or 0
            for line in hunk:
                text = line.value.rstrip("\r\n")
                if text.startswith("\\"):  # "\ No newline at end of file"
                    continue
                if getattr(line, "is_added", False):
                    kind = "add"
                elif getattr(line, "is_removed", False):
                    kind = "del"
                else:
                    kind = "context"

                if kind == "add":
                    old_line, new_line = None, new_no
                    new_no += 1
                elif kind == "del":
                    old_line, new_line = old_no, None
                    old_no += 1
                else:
                    old_line, new_line = old_no, new_no
                    old_no += 1
                    new_no += 1

                lines.append(
                    DiffLine(kind=kind, text=text, old_line=old_line, new_line=new_line)
                )

            hunks.append(
                HunkData(
                    old_start=hunk.source_start or 0,
                    old_lines=hunk.source_length or 0,
                    new_start=hunk.target_start or 0,
                    new_lines=hunk.target_length or 0,
                    added=[ln.new_line for ln in lines if ln.kind == "add" and ln.new_line is not None],
                    removed=[
                        ln.old_line
                        for ln in lines
                        if ln.kind == "del" and ln.old_line is not None
                    ],
                    added_texts=[ln.text for ln in lines if ln.kind == "add"],
                    removed_texts=[ln.text for ln in lines if ln.kind == "del"],
                    section=hunk.section_header or "",
                    lines=lines,
                )
            )
        out.append(
            FileDiff(
                path=path,
                old_path=old_path,
                status=status,
                hunks=hunks,
                binary=bool(getattr(patched, "is_binary", False)),
            )
        )
    return out


def _paths_from_git_header(line: str) -> Tuple[Optional[str], Optional[str]]:
    """Extract (old_path, new_path) from ``diff --git a/x.py b/y.py``.

    Binary files have no ``---``/``+++`` lines, only this header, so the paths
    must come from here. When a path itself contains `` b/`` we split on the
    last occurrence (rare enough to be acceptable).
    """
    rest = line[len("diff --git ") :].strip()
    marker = " b/"
    index = rest.rfind(marker)
    if index > 0:
        return _strip_diff_prefix(rest[:index]), _strip_diff_prefix(rest[index + 1 :])
    return _strip_diff_prefix(rest), None


def _parse_diff_manual(diff_text: str) -> List[FileDiff]:
    """Minimal parser with no unidiff dependency, so the tool runs without optional deps."""
    out: List[FileDiff] = []
    current: Optional[FileDiff] = None
    hunk: Optional[HunkData] = None
    old_no = 0
    new_no = 0

    for raw in diff_text.splitlines():
        line = raw.rstrip("\r")
        if line.startswith("diff --git "):
            current = FileDiff()
            out.append(current)
            hunk = None
            current.old_path, current.path = _paths_from_git_header(line)
            continue
        if current is None:
            continue
        if line.startswith("--- "):
            path = _strip_diff_prefix(line[4:])
            if path is None:
                current.status = "added"
            else:
                current.old_path = path
            continue
        if line.startswith("+++ "):
            path = _strip_diff_prefix(line[4:])
            if path is None:
                current.status = "removed"
            else:
                current.path = path
            continue
        if line.startswith("new file mode"):
            current.status = "added"
            continue
        if line.startswith("deleted file mode"):
            current.status = "removed"
            continue
        if line.startswith("rename from "):
            current.old_path = line[len("rename from ") :].strip()
            current.status = "renamed"
            continue
        if line.startswith("rename to "):
            current.path = line[len("rename to ") :].strip()
            current.status = "renamed"
            continue
        if line.startswith("Binary files ") and line.endswith(" differ"):
            current.binary = True
            continue

        match = _HUNK_RE.match(line)
        if match:
            hunk = HunkData(
                old_start=int(match.group(1)),
                old_lines=int(match.group(2)) if match.group(2) is not None else 1,
                new_start=int(match.group(3)),
                new_lines=int(match.group(4)) if match.group(4) is not None else 1,
                section=(match.group(5) or "").strip(),
            )
            current.hunks.append(hunk)
            old_no, new_no = hunk.old_start, hunk.new_start
            continue

        if hunk is None:
            continue
        if line.startswith("+"):
            hunk.added.append(new_no)
            hunk.added_texts.append(line[1:])
            hunk.lines.append(DiffLine("add", line[1:], None, new_no))
            new_no += 1
        elif line.startswith("-"):
            hunk.removed.append(old_no)
            hunk.removed_texts.append(line[1:])
            hunk.lines.append(DiffLine("del", line[1:], old_no, None))
            old_no += 1
        elif line.startswith(" ") or line == "":
            hunk.lines.append(DiffLine("context", line[1:], old_no, new_no))
            old_no += 1
            new_no += 1
        # Ignore everything else, e.g. the "\ No newline at end of file" marker.

    for diff in out:
        if not diff.path:
            diff.path = diff.old_path
        if not diff.old_path:
            diff.old_path = diff.path
    return out


def parse_diff(diff_text: str) -> List[FileDiff]:
    """Parse a unified diff; prefer unidiff and fall back to the built-in parser."""
    if not diff_text or not diff_text.strip():
        return []
    try:
        from unidiff import PatchSet  # type: ignore

        return _parse_diff_unidiff(PatchSet(diff_text))
    except ImportError:
        LOGGER.debug("unidiff is not installed; using the built-in diff parser")
    except Exception as exc:  # noqa: BLE001 - a parser failure must fall back
        LOGGER.warning("unidiff failed to parse the diff (%s); falling back to the built-in parser", exc)
    return _parse_diff_manual(diff_text)


# ---------------------------------------------------------------------------
# Noise-file filtering
# ---------------------------------------------------------------------------
# Changing these files cannot flip a test, so analysing them only burns downstream time.
# Every rule matches the repo-relative POSIX path, which lines up with testmon's filename.

_NOISE_EXACT_NAMES = {
    # VCS / editor metadata
    ".gitignore",
    ".gitattributes",
    ".gitmodules",
    ".editorconfig",
    ".prettierignore",
    ".eslintignore",
    ".dockerignore",
    ".npmignore",
    ".DS_Store",
    "Thumbs.db",
    # Licences / changelogs (editing them never turns a test red)
    "LICENSE",
    "LICENSE.md",
    "LICENSE.txt",
    "CHANGELOG",
    "CHANGELOG.md",
    "CHANGELOG.rst",
}

# Lock files: huge, tool-generated, never hand-edited
_NOISE_EXACT_NAMES |= {
    "package-lock.json",
    "npm-shrinkwrap.json",
    "yarn.lock",
    "pnpm-lock.yaml",
    "bun.lockb",
    "poetry.lock",
    "Pipfile.lock",
    "uv.lock",
    "pdm.lock",
    "requirements.lock",
    "composer.lock",
    "Gemfile.lock",
    "Cargo.lock",
    "go.sum",
    "mix.lock",
    "pubspec.lock",
    "packages.lock.json",
    "flake.lock",
}

_NOISE_SUFFIXES = {
    # Images / fonts / media: not parsed here and practically unable to affect unit tests
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".icns", ".webp",
    ".tif", ".tiff", ".psd", ".ai", ".eps",
    ".woff", ".woff2", ".ttf", ".otf", ".eot",
    ".mp3", ".mp4", ".wav", ".mov", ".avi", ".webm",
    # Build artefacts / archives
    ".pyc", ".pyo", ".class", ".o", ".obj", ".so", ".dylib", ".dll",
    ".exe", ".jar", ".war", ".zip", ".tar", ".gz", ".tgz", ".bz2",
    ".7z", ".rar", ".pdf", ".bin", ".dat",
    # Documentation: editing it does not change code behaviour
    ".md", ".markdown", ".rst", ".adoc", ".txt",
}

_NOISE_DIR_PREFIXES = (
    "docs/",
    "doc/",
    "documentation/",
    "assets/",
    "asset/",
    "static/",
    "public/",
    "images/",
    "img/",
    "res/",
    "resources/",
    "node_modules/",
    "vendor/",
    "third_party/",
    "dist/",
    "build/",
    "coverage/",
    "htmlcov/",
    ".github/ISSUE_TEMPLATE/",
)

_NOISE_PATTERNS = (
    (re.compile(r"\.min\.(js|css)$"), "minified"),
    (re.compile(r"\.map$"), "sourcemap"),
    (re.compile(r"\.(pb|generated|g)\.(go|dart|py|ts|js)$"), "generated"),
    (re.compile(r"(^|/)__snapshots__/"), "snapshot"),
)


def noise_reason(path: str, *, include_docs: bool = False) -> Optional[str]:
    """Return why this file is considered noise, or ``None`` when it is not.

    With ``include_docs=True`` files such as ``.md/.rst/.txt`` and the ``docs/``
    directory are kept, because some teams keep test cases in documentation.
    """
    if not path:
        return None
    name = path.rsplit("/", 1)[-1]
    lowered = name.lower()

    if name in _NOISE_EXACT_NAMES:
        return "vcs-meta" if name.startswith((".git", ".editor", ".prettier", ".eslint")) else "lock-or-license"

    suffix = Path(lowered).suffix
    if suffix in _NOISE_SUFFIXES:
        if include_docs and suffix in (".md", ".markdown", ".rst", ".adoc", ".txt"):
            return None
        return "docs" if suffix in (".md", ".markdown", ".rst", ".adoc", ".txt") else "binary-asset"

    for prefix in _NOISE_DIR_PREFIXES:
        if path.startswith(prefix):
            if include_docs and prefix in ("docs/", "doc/", "documentation/"):
                return None
            return "docs" if prefix.startswith(("doc",)) else "vendored-asset"

    for pattern, reason in _NOISE_PATTERNS:
        if pattern.search(path.lower()):
            return reason

    return None


def is_noise_file(path: str, *, include_docs: bool = False) -> bool:
    """``True`` when this file does not need change analysis."""
    return noise_reason(path, include_docs=include_docs) is not None


def filter_noise_files(
    files: Sequence[FileDiff], *, include_docs: bool = False
) -> Tuple[List[FileDiff], List[Tuple[str, str]]]:
    """Split parsed files into (kept, [(skipped_path, reason)])."""
    kept: List[FileDiff] = []
    skipped: List[Tuple[str, str]] = []
    for file_diff in files:
        reason = noise_reason(file_diff.path, include_docs=include_docs)
        if reason:
            skipped.append((file_diff.path, reason))
        else:
            kept.append(file_diff)
    return kept, skipped


# ---------------------------------------------------------------------------
# Context trimming and block splitting
# ---------------------------------------------------------------------------


@dataclass
class HunkContext:
    """One trimmed hunk: its text plus the line ranges on both sides.

    Line convention: ``new_start``/``new_end`` are the first/last line of the
    KEPT BLOCK in the new file (1-based, inclusive). A pure deletion has no
    lines on that side, represented by ``new_end == new_start - 1``.
    """

    text: str
    new_start: int = 0
    new_end: int = 0
    old_start: int = 0
    old_end: int = 0
    new_line_count: int = 0
    old_line_count: int = 0
    added: int = 0
    removed: int = 0
    section: str = ""
    truncated_before: bool = False
    truncated_after: bool = False

    @property
    def new_range(self) -> str:
        if self.new_line_count == 0:
            return f"L{self.new_start} (deletion only; no lines in the new file)"
        return f"L{self.new_start}-{self.new_end}"

    @property
    def old_range(self) -> str:
        if self.old_line_count == 0:
            return f"L{self.old_start} (new file; no old lines)"
        return f"L{self.old_start}-{self.old_end}"


def _number_range(numbers: Sequence[int], fallback: int) -> Tuple[int, int]:
    if not numbers:
        return fallback, fallback - 1
    return min(numbers), max(numbers)


def extract_hunk_context(hunk: HunkData, *, context_lines: int = 3) -> HunkContext:
    """Keep only the changed lines plus ``context_lines`` of context on each side.

    git already emits 3 context lines by default, so this mainly normalises wide
    diffs (``-U10`` and friends) and hunks that GitHub tooling has inflated.
    Trimmed context loses nothing: it is far from the change and useless for
    test mapping.
    """
    lines = hunk.lines
    if not lines:
        return HunkContext(
            text="",
            new_start=hunk.new_start,
            new_end=hunk.new_start - 1,
            old_start=hunk.old_start,
            old_end=hunk.old_start - 1,
            section=hunk.section,
        )

    changed = [i for i, line in enumerate(lines) if line.is_change]
    if changed:
        first, last = changed[0], changed[-1]
    else:  # context-only hunk (should not happen); keep it as is
        first, last = 0, len(lines) - 1

    start = max(0, first - context_lines)
    end = min(len(lines) - 1, last + context_lines)
    retained = lines[start : end + 1]

    new_numbers = [ln.new_line for ln in retained if ln.new_line]
    old_numbers = [ln.old_line for ln in retained if ln.old_line]
    new_start, new_end = _number_range(new_numbers, hunk.new_start)
    old_start, old_end = _number_range(old_numbers, hunk.old_start)

    return HunkContext(
        text="\n".join(line.render() for line in retained),
        new_start=new_start,
        new_end=new_end,
        old_start=old_start,
        old_end=old_end,
        new_line_count=len(new_numbers),
        old_line_count=len(old_numbers),
        added=sum(1 for ln in retained if ln.kind == "add"),
        removed=sum(1 for ln in retained if ln.kind == "del"),
        section=hunk.section,
        truncated_before=start > 0,
        truncated_after=end < len(lines) - 1,
    )


@dataclass
class DiffBlock:
    """Smallest analysable unit: one file plus one hunk, for per-block downstream work."""

    path: str
    old_path: str
    status: str
    hunk_index: int
    context: HunkContext
    binary: bool = False

    @property
    def header(self) -> str:
        return f"{self.path} #{self.hunk_index}"

    def render(self, *, indent: int = 4) -> str:
        """Pretty-print helper: one metadata line plus the indented diff text."""
        pad = " " * indent
        scope = f"  § {self.context.section}" if self.context.section else ""
        return (
            f"{self.path}  [hunk #{self.hunk_index}]  "
            f"new {self.context.new_range}  "
            f"old {self.context.old_range}  "
            f"+{self.context.added} -{self.context.removed}"
            f"{scope}\n"
            + "\n".join(pad + line for line in self.context.text.splitlines())
        )


def split_diff(
    diff_text: str,
    *,
    context_lines: int = 3,
    skip_noise: bool = True,
    include_docs: bool = False,
    skip_binary: bool = True,
) -> List[DiffBlock]:
    """Split a raw diff into independent blocks (file x hunk), trimming context.

    This is the most direct entry point for downstream: one call returns every
    unit to analyse, each carrying its path, hunk index and both line ranges.
    """
    blocks: List[DiffBlock] = []
    for file_diff in parse_diff(diff_text):
        if skip_binary and file_diff.binary:
            continue
        if skip_noise and is_noise_file(file_diff.path, include_docs=include_docs):
            continue
        for index, hunk in enumerate(file_diff.hunks, start=1):
            blocks.append(
                DiffBlock(
                    path=file_diff.path,
                    old_path=file_diff.old_path,
                    status=file_diff.status,
                    hunk_index=index,
                    context=extract_hunk_context(hunk, context_lines=context_lines),
                    binary=file_diff.binary,
                )
            )
    return blocks


def summarize_blocks(blocks: Sequence[DiffBlock]) -> Dict[str, Any]:
    """Aggregate stats for the split, for logs and the CLI summary."""
    files = sorted({block.path for block in blocks})
    return {
        "files": files,
        "blocks": len(blocks),
        "added": sum(block.context.added for block in blocks),
        "removed": sum(block.context.removed for block in blocks),
        "by_file": {
            path: sum(1 for block in blocks if block.path == path) for path in files
        },
    }


# ---------------------------------------------------------------------------
# AST scope index
# ---------------------------------------------------------------------------


@dataclass
class Scope:
    """A scope that testmon would treat as a block."""

    qualname: str
    kind: str  # module | class | function | method
    def_line: int
    body_start: int
    body_end: int
    parent: Optional[str] = None
    decorator_line: Optional[int] = None


class ScopeIndex:
    """Map head (or base) source code to "line number -> scope"."""

    def __init__(self, source: Optional[str]) -> None:
        self.parse_ok = source is not None
        self.source = source or ""
        self.lines = self.source.splitlines()
        total = max(len(self.lines), 1)
        self.module = Scope(MODULE_SCOPE, "module", 1, 1, total, None)
        self.scopes: List[Scope] = [self.module]
        self.by_qualname: Dict[str, Scope] = {MODULE_SCOPE: self.module}

        if source is None:
            return
        try:
            tree = ast.parse(source)
        except (SyntaxError, ValueError) as exc:
            LOGGER.debug("AST parse failed: %s", exc)
            self.parse_ok = False
            return

        if tree.body:
            self.module.body_start = getattr(tree.body[0], "lineno", 1) or 1
        # Match testmon: the module block ends at len(lines)
        self.module.body_end = total
        self._walk(tree.body, prefix="", parent=MODULE_SCOPE)

    # -- build ---------------------------------------------------------------
    def _walk(
        self, body: Sequence[ast.stmt], prefix: str, parent: str
    ) -> None:
        for node in body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                qualname = prefix + node.name
                if isinstance(node, ast.ClassDef):
                    kind = "class"
                elif prefix:
                    kind = "method" if not prefix.endswith(".<locals>.") else "function"
                else:
                    kind = "function"

                first = node.body[0] if node.body else node
                body_start = (
                    getattr(first, "lineno", None) or getattr(node, "lineno", 1) or 1
                )
                body_end = (
                    getattr(node, "end_lineno", None)
                    or getattr(node, "lineno", body_start)
                    or body_start
                )
                decorators = getattr(node, "decorator_list", None) or []
                decorator_line = decorators[0].lineno if decorators else None

                scope = Scope(
                    qualname=qualname,
                    kind=kind,
                    def_line=getattr(node, "lineno", 1) or 1,
                    body_start=body_start,
                    body_end=body_end,
                    parent=parent,
                    decorator_line=decorator_line,
                )
                self.scopes.append(scope)
                self.by_qualname[qualname] = scope
                self._walk(node.body, qualname + ".", qualname)
            else:
                for child in ast.iter_child_nodes(node):
                    if isinstance(child, ast.stmt):
                        self._walk([child], prefix, parent)

    # -- query ---------------------------------------------------------------
    def locate(self, line: int) -> Tuple[Scope, str]:
        """Return (scope, region) where region is decorator | header | body.

        ``header`` spans the ``def``/``class`` line up to (not including) the
        first statement of the body. Editing it is a signature change and hits
        the PARENT scope's block (see section 2, constraint 3).
        """
        for scope in self.scopes:
            if scope is self.module:
                continue
            if (
                scope.decorator_line is not None
                and scope.decorator_line <= line < scope.def_line
            ):
                return scope, "decorator"

        best: Optional[Scope] = None
        for scope in self.scopes:
            if scope is self.module:
                continue
            if scope.def_line <= line <= scope.body_end:
                if best is None or (scope.body_end - scope.def_line) < (
                    best.body_end - best.def_line
                ):
                    best = scope
        if best is None:
            return self.module, "body"
        if line < best.body_start:
            return best, "header"
        return best, "body"

    def qualnames(self) -> List[str]:
        return [s.qualname for s in self.scopes]


# ---------------------------------------------------------------------------
# Change classification
# ---------------------------------------------------------------------------


def _is_noise_text(text: str) -> bool:
    """Blank or comment-only line: absent from the AST repr, so no checksum changes."""
    stripped = text.strip()
    return not stripped or stripped.startswith("#")


def _format_affects(path: str, qualnames: Iterable[str]) -> List[str]:
    return sorted({f"{path}::{q}" for q in qualnames if q})


def _analyze_hunk(
    hunk: HunkData,
    head_index: ScopeIndex,
    base_index: ScopeIndex,
    path: str,
    status: str,
) -> Dict[str, Any]:
    """Map one hunk to a scope and pre-compute the ``affects`` list."""
    texts = hunk.added_texts + hunk.removed_texts
    if texts and all(_is_noise_text(t) for t in texts):
        return {
            "old_start": hunk.old_start,
            "old_lines": hunk.old_lines,
            "new_start": hunk.new_start,
            "new_lines": hunk.new_lines,
            "changed_old_lines": sorted(hunk.removed),
            "changed_new_lines": sorted(hunk.added),
            "change_kind": "comment_only",
            "scope": None,
            "affects": [],
            "removed_scopes": [],
            "is_new_symbol": False,
            "confidence": 0.9,
            "rationale": (
                "Only comments/blank lines changed: testmon fingerprints are crc32 "
                "of the ast.dump string, which ignores both, so no block checksum "
                "changes and downstream can skip this hunk."
            ),
        }

    head_touches = [head_index.locate(line) for line in hunk.added]
    base_touches = [base_index.locate(line) for line in hunk.removed]
    primary = head_touches or base_touches or [(head_index.module, "body")]
    regions = {region for _, region in primary}

    if "header" in regions or "decorator" in regions:
        change_kind = "signature"
    elif all(scope.kind == "module" for scope, _ in primary):
        change_kind = "module_level"
    else:
        change_kind = "body"

    affected: List[str] = []
    if change_kind == "module_level":
        affected.append(MODULE_SCOPE)
    for scope, region in primary:
        if region in ("header", "decorator"):
            affected.append(scope.parent or MODULE_SCOPE)
            affected.append(scope.qualname)
        else:
            affected.append(scope.qualname)

    # Removed/renamed scopes: present in base, absent in head
    removed_scopes: List[str] = []
    for scope, _ in base_touches:
        if scope.qualname == MODULE_SCOPE:
            continue
        if scope.qualname not in head_index.by_qualname:
            removed_scopes.append(scope.qualname)
            affected.append(scope.qualname)

    dominant = max(primary, key=lambda item: item[0].def_line)[0]
    is_new_symbol = (
        dominant.qualname != MODULE_SCOPE
        and dominant.qualname not in base_index.by_qualname
    )
    if is_new_symbol:
        affected.append(dominant.qualname)

    if change_kind == "signature" and is_new_symbol:
        confidence = 0.8
        rationale = (
            "New symbol: the definition does not exist in base and appears for the "
            "first time in head. Downstream should flag it as 'needs new tests', "
            "not 'needs a re-run'; the parent block fingerprint changes as well."
        )
    elif change_kind == "signature":
        confidence = 0.85
        rationale = (
            "Change lands on the def/class header or a decorator: the signature is "
            "part of the enclosing block's repr, so the parent block fingerprint "
            "is invalidated (usually equivalent to file-level invalidation)."
        )
    elif change_kind == "module_level":
        confidence = 0.9
        rationale = "Change is at module level: it alters the <module> block fingerprint, so every test depending on this file is hit."
    elif not head_index.parse_ok or not base_index.parse_ok:
        confidence = 0.4
        rationale = "AST parsing failed (syntax error or non-Python source); the hunk is classified by nearest line number."
    else:
        confidence = 0.95
        rationale = (
            "Change is inside the function body: only this function's block fingerprint changes; the parent repr is untouched."
        )

    if status == "added" and dominant.qualname == MODULE_SCOPE:
        affected.append(MODULE_SCOPE)

    scope_out = {
        "qualname": dominant.qualname,
        "kind": dominant.kind,
        "def_line_new": None if dominant is head_index.module else dominant.def_line,
        "body_start_new": dominant.body_start,
        "body_end_new": dominant.body_end,
        "signature_changed": change_kind == "signature",
        "is_new_symbol": is_new_symbol,
    }

    return {
        "old_start": hunk.old_start,
        "old_lines": hunk.old_lines,
        "new_start": hunk.new_start,
        "new_lines": hunk.new_lines,
        "changed_old_lines": sorted(hunk.removed),
        "changed_new_lines": sorted(hunk.added),
        "change_kind": change_kind,
        "scope": scope_out,
        "affects": _format_affects(path, affected),
        "removed_scopes": _format_affects(path, removed_scopes),
        "is_new_symbol": is_new_symbol,
        "confidence": confidence,
        "rationale": rationale,
    }


def _analyze_file(
    diff: FileDiff,
    provider: "SourceProvider",
    base_rev: str,
    head_rev: str,
) -> Dict[str, Any]:
    path = diff.path or diff.old_path
    language = _language_for(path)
    is_python = language == "python"

    head_source = None
    base_source = None
    if diff.status != "removed" and not diff.binary:
        head_source = provider.read(head_rev, path)
    if diff.status != "added" and not diff.binary:
        base_source = provider.read(base_rev, diff.old_path or path)

    head_index = ScopeIndex(head_source)
    base_index = ScopeIndex(base_source)
    parse_ok = is_python and head_index.parse_ok and not diff.binary

    file_level = diff.status in ("added", "removed") or not parse_ok

    hunks_out: List[Dict[str, Any]] = []
    affected: List[str] = []
    removed_scopes: List[str] = []

    for idx, hunk in enumerate(diff.hunks, start=1):
        if parse_ok:
            info = _analyze_hunk(hunk, head_index, base_index, path, diff.status)
        else:
            info = {
                "old_start": hunk.old_start,
                "old_lines": hunk.old_lines,
                "new_start": hunk.new_start,
                "new_lines": hunk.new_lines,
                "changed_old_lines": sorted(hunk.removed),
                "changed_new_lines": sorted(hunk.added),
                "change_kind": "file_level",
                "scope": None,
                "affects": _format_affects(path, [MODULE_SCOPE]),
                "removed_scopes": [],
                "is_new_symbol": False,
                "confidence": 0.3,
                "rationale": (
                    "Non-Python / binary / syntax error: symbol-level location is "
                    "impossible, so downstream should match at file level."
                ),
            }
        info["id"] = f"{path}#h{idx}"
        hunks_out.append(info)
        affected.extend(info["affects"])
        removed_scopes.extend(info.get("removed_scopes") or [])

    if diff.status == "removed":
        affected.extend(_format_affects(path, base_index.qualnames()))
        removed_scopes.extend(
            _format_affects(
                path,
                [q for q in base_index.qualnames() if q != MODULE_SCOPE],
            )
        )
    if diff.status == "added":
        affected.extend(_format_affects(path, head_index.qualnames()))

    # Pure rename (identical content, no hunk): the path is testmon's key, so say so explicitly
    if diff.status == "renamed" and not hunks_out and diff.old_path:
        old_scopes = _format_affects(diff.old_path, base_index.qualnames() or [MODULE_SCOPE])
        new_scopes = _format_affects(path, head_index.qualnames() or [MODULE_SCOPE])
        hunks_out.append(
            {
                "old_start": 1,
                "old_lines": 0,
                "new_start": 1,
                "new_lines": 0,
                "changed_old_lines": [],
                "changed_new_lines": [],
                "change_kind": "file_level",
                "scope": None,
                "affects": new_scopes,
                "removed_scopes": old_scopes,
                "is_new_symbol": False,
                "confidence": 0.6,
                "rationale": (
                    "Pure rename: identical content, new path. testmon keys by "
                    "repo-relative path, so dependencies recorded for the old path "
                    "must be remapped to the new one or those tests will be skipped."
                ),
                "id": f"{path}#h1",
            }
        )
        affected.extend(new_scopes)
        removed_scopes.extend(old_scopes)
        file_level = True

    stats = {
        "added": sum(len(h.added) for h in diff.hunks),
        "removed": sum(len(h.removed) for h in diff.hunks),
    }

    return {
        "path": path,
        "old_path": diff.old_path if diff.old_path != path else None,
        "status": diff.status,
        "language": language,
        "is_python": is_python,
        "is_test_file": _is_test_file(path),
        "parse_ok": parse_ok,
        "binary": diff.binary,
        "file_level_change": file_level,
        "stats": stats,
        "affected_scopes": sorted(set(affected)),
        "removed_scopes": sorted(set(removed_scopes)),
        "hunks": hunks_out,
    }


def _language_for(path: str) -> str:
    return _LANGUAGE_BY_SUFFIX.get(Path(path).suffix.lower(), "other")


def _is_test_file(path: str) -> bool:
    parts = path.split("/")
    name = parts[-1]
    if name == "conftest.py":
        return True
    if name.startswith("test_") and name.endswith(".py"):
        return True
    if name.endswith("_test.py"):
        return True
    return any(part in ("tests", "test") for part in parts[:-1])


# ---------------------------------------------------------------------------
# Source access (local git first, GitHub raw as fallback)
# ---------------------------------------------------------------------------


class SourceProvider:
    """Read source text for (revision, path); fall back to GitHub raw when local git cannot."""

    def __init__(
        self,
        repo_path: Optional[str] = None,
        owner: Optional[str] = None,
        name: Optional[str] = None,
        token: Optional[str] = None,
    ) -> None:
        self.repo_path = repo_path
        self.owner = owner
        self.name = name
        self.token = token or os.environ.get("GITHUB_TOKEN")
        self._cache: Dict[Tuple[str, str], Optional[str]] = {}

    def read(self, rev: str, path: str) -> Optional[str]:
        if not rev or not path:
            return None
        key = (rev, path)
        if key in self._cache:
            return self._cache[key]
        source = self._read_local(rev, path)
        if source is None and self.owner and self.name:
            source = self._read_github(rev, path)
        self._cache[key] = source
        return source

    def _read_local(self, rev: str, path: str) -> Optional[str]:
        if not self.repo_path:
            return None
        rc, out, _ = _git_raw(["show", f"{rev}:{path}"], self.repo_path)
        if rc != 0:
            return None
        return out

    def _read_github(self, rev: str, path: str) -> Optional[str]:
        url = f"https://raw.githubusercontent.com/{self.owner}/{self.name}/{rev}/{path}"
        try:
            import requests  # type: ignore

            response = requests.get(url, headers=self._headers(), timeout=30)
            if response.status_code == 200:
                return response.text
            LOGGER.debug("GitHub raw read failed for %s: HTTP %s", url, response.status_code)
        except Exception as exc:  # noqa: BLE001 - no network must not abort the analysis
            LOGGER.warning("GitHub raw read failed (%s): %s", url, exc)
        return None

    def _headers(self) -> Dict[str, str]:
        headers = {"Accept": "application/vnd.github+json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers


# ---------------------------------------------------------------------------
# git / GitHub helpers
# ---------------------------------------------------------------------------


def _git_raw(args: Sequence[str], cwd: Optional[str]) -> Tuple[int, str, str]:
    cmd = ["git"]
    if cwd:
        cmd.extend(["-C", str(cwd)])
    cmd.extend(args)
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except FileNotFoundError as exc:
        raise AnalysisError("git executable not found; install Git and add it to PATH.") from exc
    return proc.returncode, proc.stdout or "", proc.stderr or ""


def _git(args: Sequence[str], cwd: Optional[str], check: bool = True) -> str:
    rc, out, err = _git_raw(args, cwd)
    if check and rc != 0:
        raise AnalysisError(
            f"git {' '.join(args)} failed (exit {rc}): {err.strip() or out.strip()}"
        )
    return out


def _git_root(repo_path: str) -> str:
    rc, out, err = _git_raw(["rev-parse", "--show-toplevel"], repo_path)
    if rc != 0:
        raise AnalysisError(
            f"{repo_path} is not a git repository ({err.strip() or 'rev-parse failed'})."
        )
    return out.strip()


def _rev_parse(root: str, rev: str) -> str:
    rc, out, err = _git_raw(["rev-parse", rev], root)
    if rc != 0:
        raise AnalysisError(f"Cannot resolve revision {rev!r}: {err.strip()}")
    return out.strip()


_REMOTE_RE = re.compile(r"[/:]([^/:]+)/([^/]+?)(?:\.git)?$")


def _parse_remote(url: str) -> Tuple[Optional[str], Optional[str]]:
    if not url:
        return None, None
    match = _REMOTE_RE.search(url.strip())
    if not match:
        return None, None
    return match.group(1), match.group(2)


def _http_get(url: str, headers: Dict[str, str]) -> Any:
    try:
        import requests  # type: ignore
    except ImportError as exc:  # pragma: no cover - missing dependency path
        raise AnalysisError("requests is required: pip install -r requirements.txt") from exc
    try:
        response = requests.get(url, headers=headers, timeout=60)
    except Exception as exc:  # noqa: BLE001 - wrap network errors uniformly
        raise AnalysisError(f"Request to {url} failed: {exc}") from exc
    if response.status_code >= 400:
        raise AnalysisError(
            f"GitHub API {url} returned HTTP {response.status_code}: {response.text[:300]}"
        )
    return response


def _auth_headers(token: Optional[str]) -> Dict[str, str]:
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


# ---------------------------------------------------------------------------
# Fetch a PR's raw diff from GitHub
# ---------------------------------------------------------------------------

DIFF_ACCEPT = "application/vnd.github.v3.diff"


def parse_repo_name(repo_name: str) -> Tuple[str, str]:
    """Normalise ``owner/repo``, ``https://github.com/owner/repo`` and
    ``owner/repo.git`` into ``(owner, repo)``."""
    raw = (repo_name or "").strip()
    if not raw:
        raise AnalysisError("repo_name must not be empty; expected owner/repo")
    raw = re.sub(r"^[a-zA-Z][a-zA-Z0-9+.-]*://[^/]+/", "", raw)  # strip scheme and host
    raw = raw.split("?", 1)[0].split("#", 1)[0].strip("/")
    if raw.endswith(".git"):
        raw = raw[: -len(".git")]
    parts = [part for part in raw.split("/") if part]
    if len(parts) < 2:
        raise AnalysisError(f"Expected a repo name like owner/repo, got {repo_name!r}")
    return parts[0], parts[1]


def _rate_limit_wait(response: Any) -> Optional[float]:
    """Seconds to wait when rate limited; ``None`` when this is not a rate limit."""
    headers = getattr(response, "headers", {}) or {}
    remaining = str(headers.get("X-RateLimit-Remaining", "")).strip()
    retry_after = str(headers.get("Retry-After", "")).strip()
    body = (getattr(response, "text", "") or "")[:2000].lower()
    limited = (
        remaining == "0"
        or bool(retry_after)
        or "rate limit" in body
        or "abuse detection" in body
    )
    if not limited:
        return None
    if retry_after.isdigit():
        return float(retry_after)
    reset = str(headers.get("X-RateLimit-Reset", "")).strip()
    if reset.isdigit():
        return max(0.0, float(reset) - time.time())
    return 0.0


def _format_reset_time(wait_seconds: float) -> str:
    moment = datetime.fromtimestamp(time.time() + wait_seconds).astimezone()
    return moment.isoformat(timespec="seconds")


def get_pr_diff(
    repo_name: str,
    pr_number: int,
    *,
    token: Optional[str] = None,
    timeout: float = 30.0,
    max_retries: int = 2,
    backoff: float = 1.5,
    max_rate_wait: float = 60.0,
    api_url: Optional[str] = None,
    session: Any = None,
) -> str:
    """Fetch the raw unified-diff text of one PR through the GitHub REST API.

    Parameters
    ----
    repo_name:
        ``owner/repo``; a full URL is accepted too (see :func:`parse_repo_name`).
    pr_number:
        PR number.
    token:
        Defaults to the ``GITHUB_TOKEN`` environment variable. Anonymous requests work but are
        limited to 60 requests/hour and cannot see private repositories.
    timeout:
        Per-request timeout in seconds; GitHub sometimes needs 10+ s for large PRs.
    max_retries:
        Max retries on network errors / 5xx / rate limits.
    max_rate_wait:
        Upper bound (seconds) for rate-limit waits; beyond it we fail fast instead of hanging CI.

    Raises
    ----
    :class:`AnalysisError`: timeout, connection failure, rate limit, no permission,
    missing PR, etc. The message carries the HTTP status and GitHub's own hint.
    """
    try:
        import requests  # type: ignore
    except ImportError as exc:  # pragma: no cover - missing dependency path
        raise AnalysisError("requests is required: pip install -r requirements.txt") from exc

    owner, name = parse_repo_name(repo_name)
    try:
        number = int(pr_number)
    except (TypeError, ValueError) as exc:
        raise AnalysisError(f"pr_number must be an integer, got {pr_number!r}") from exc
    if number <= 0:
        raise AnalysisError(f"pr_number must be a positive integer, got {number}")

    token = token or os.environ.get("GITHUB_TOKEN")
    if not token:
        LOGGER.warning(
            "GITHUB_TOKEN is not set; using anonymous requests (60/h, private repos invisible)"
        )
    url = f"{api_url or GITHUB_API_URL}/repos/{owner}/{name}/pulls/{number}"
    headers = _auth_headers(token)
    headers["Accept"] = DIFF_ACCEPT

    owns_session = session is None
    if owns_session:
        session = requests.Session()
    attempts = max(0, int(max_retries)) + 1
    last_error = ""

    try:
        for attempt in range(attempts):
            remaining_tries = attempts - attempt - 1
            try:
                response = session.get(url, headers=headers, timeout=timeout)
            except requests.Timeout:
                last_error = f"request timed out (>{timeout:g}s)"
            except requests.ConnectionError as exc:
                last_error = f"network connection failed: {exc.__class__.__name__}"
            except requests.RequestException as exc:  # other requests failures
                raise AnalysisError(f"Request to {url} failed: {exc}") from exc
            else:
                status = response.status_code
                if status == 200:
                    text = response.text or ""
                    if not text.strip():
                        LOGGER.warning("PR #%s has an empty diff (no file changes?)", number)
                    return text
                if status == 401:
                    raise AnalysisError(
                        "GitHub returned 401: the token is invalid or expired. "
                        "Check GITHUB_TOKEN (https://github.com/settings/tokens)."
                    )
                if status == 404:
                    raise AnalysisError(
                        f"GitHub returned 404: {owner}/{name} or PR #{number} does not exist; "
                        "private repositories also need a token with repo scope."
                    )
                if status in (403, 429):
                    wait = _rate_limit_wait(response)
                    if wait is not None:
                        if wait <= 0:
                            wait = min(60.0, max(backoff, 1.0))
                        if wait <= max_rate_wait and remaining_tries > 0:
                            LOGGER.warning(
                                "GitHub rate limit hit; retrying in %.0fs (%d tries left)",
                                wait,
                                remaining_tries,
                            )
                            time.sleep(wait)
                            last_error = "API rate limit hit"
                            continue
                        raise AnalysisError(
                            "GitHub API rate limit exhausted; "
                            f"expected to reset at {_format_reset_time(wait)}"
                            f" (in about {wait / 60:.0f} minutes). "
                            "Set GITHUB_TOKEN to raise the limit to 5000/h."
                        )
                    raise AnalysisError(
                        f"GitHub returned {status}: not allowed to access this PR (private repo, "
                        f"org-level SSO restriction, or a token without repo scope). "
                        f"Raw hint: {(response.text or '')[:200]}"
                    )
                if 500 <= status < 600:
                    last_error = f"GitHub server error HTTP {status}"
                else:
                    raise AnalysisError(
                        f"GitHub returned HTTP {status}: {(response.text or '')[:300]}"
                    )

            # Anything left is a network failure / 5xx: retry with exponential backoff
            if remaining_tries > 0:
                delay = min(30.0, backoff * (2**attempt))
                LOGGER.warning("%s; retrying in %.1fs (%d tries left)", last_error, delay, remaining_tries)
                time.sleep(delay)

        raise AnalysisError(
            f"Failed to fetch the diff of {owner}/{name} PR #{number} after {attempts - 1} retries: {last_error}"
        )
    finally:
        if owns_session:
            session.close()


# ---------------------------------------------------------------------------
# Fetch a PR's complete base/head source files
# ---------------------------------------------------------------------------

RAW_CONTENT_URL = "https://raw.githubusercontent.com"


def _safe_repo_path(path: str) -> str:
    """Normalise a repository-relative path and reject anything that can escape the mirror."""
    cleaned = (path or "").replace("\\", "/").strip()
    while cleaned.startswith("./"):
        cleaned = cleaned[2:]
    if cleaned.startswith("/") or re.match(r"^[a-zA-Z]:", cleaned):
        raise AnalysisError(f"Unsafe repository path: {path!r}")
    parts = [part for part in cleaned.split("/") if part not in ("", ".")]
    if not parts or any(part == ".." for part in parts):
        raise AnalysisError(f"Unsafe repository path: {path!r}")
    return "/".join(parts)


def _source_meta(data: bytes) -> Dict[str, Any]:
    """Describe one downloaded file: content hash, size, line count and binary flag."""
    try:
        text: Optional[str] = data.decode("utf-8")
    except UnicodeDecodeError:
        text = None
    binary = text is None or "\x00" in text
    return {
        "sha256": hashlib.sha256(data).hexdigest(),
        "bytes": len(data),
        "lines": None if binary else len(text.splitlines()),
        "binary": binary,
    }


def _download_raw(
    session: Any,
    url: str,
    headers: Dict[str, str],
    *,
    timeout: float,
    max_retries: int,
    backoff: float,
) -> bytes:
    """Download one file's bytes from ``raw.githubusercontent.com`` (no API quota used)."""
    try:
        import requests  # type: ignore
    except ImportError as exc:  # pragma: no cover - missing dependency path
        raise AnalysisError("requests is required: pip install -r requirements.txt") from exc

    attempts = max(0, int(max_retries)) + 1
    last_error = ""
    for attempt in range(attempts):
        try:
            response = session.get(url, headers=headers, timeout=timeout)
        except requests.Timeout:
            last_error = f"request timed out (>{timeout:g}s)"
        except requests.ConnectionError as exc:
            last_error = f"network connection failed: {exc.__class__.__name__}"
        except requests.RequestException as exc:  # other requests failures
            raise AnalysisError(f"Request to {url} failed: {exc}") from exc
        else:
            status = response.status_code
            if status == 200:
                return response.content or b""
            if status == 404:
                raise AnalysisError("file does not exist at this revision (HTTP 404)")
            if status in (403, 429):
                wait = _rate_limit_wait(response)
                if wait is not None:
                    raise AnalysisError(
                        "GitHub rate limit hit; expected to reset in about "
                        f"{wait / 60:.0f} minutes. Set GITHUB_TOKEN to raise the limit to 5000/h."
                    )
                raise AnalysisError(f"HTTP {status}: {(response.text or '')[:200]}")
            if 500 <= status < 600:
                last_error = f"GitHub server error HTTP {status}"
            else:
                raise AnalysisError(f"HTTP {status}: {(response.text or '')[:200]}")
        if attempt + 1 < attempts:
            time.sleep(min(30.0, backoff * (2**attempt)))
    raise AnalysisError(f"Failed to download {url} after {attempts - 1} retries: {last_error}")


def _github_json(
    session: Any,
    url: str,
    headers: Dict[str, str],
    *,
    timeout: float,
    max_retries: int,
    backoff: float,
) -> Any:
    """GET one GitHub API endpoint and decode its JSON payload, with readable failures."""
    try:
        import requests  # type: ignore
    except ImportError as exc:  # pragma: no cover - missing dependency path
        raise AnalysisError("requests is required: pip install -r requirements.txt") from exc

    attempts = max(0, int(max_retries)) + 1
    last_error = ""
    for attempt in range(attempts):
        try:
            response = session.get(url, headers=headers, timeout=timeout)
        except requests.Timeout:
            last_error = f"request timed out (>{timeout:g}s)"
        except requests.ConnectionError as exc:
            last_error = f"network connection failed: {exc.__class__.__name__}"
        except requests.RequestException as exc:
            raise AnalysisError(f"Request to {url} failed: {exc}") from exc
        else:
            status = response.status_code
            if status == 200:
                try:
                    return response.json()
                except ValueError as exc:
                    raise AnalysisError(f"GitHub returned invalid JSON for {url}: {exc}") from exc
            if status == 401:
                raise AnalysisError(
                    "GitHub returned 401: the token is invalid or expired. "
                    "Check GITHUB_TOKEN (https://github.com/settings/tokens)."
                )
            if status == 404:
                raise AnalysisError(
                    f"GitHub returned 404: {url} does not exist; private repositories also need "
                    "a token with repo scope."
                )
            if status in (403, 429):
                wait = _rate_limit_wait(response)
                if wait is not None:
                    raise AnalysisError(
                        "GitHub API rate limit exhausted; expected to reset at "
                        f"{_format_reset_time(wait)} (in about {wait / 60:.0f} minutes). "
                        "Set GITHUB_TOKEN to raise the limit to 5000/h."
                    )
                raise AnalysisError(f"HTTP {status}: {(response.text or '')[:200]}")
            if 500 <= status < 600:
                last_error = f"GitHub server error HTTP {status}"
            else:
                raise AnalysisError(f"GitHub returned HTTP {status}: {(response.text or '')[:300]}")
        if attempt + 1 < attempts:
            time.sleep(min(30.0, backoff * (2**attempt)))
    raise AnalysisError(f"Failed to call {url} after {attempts - 1} retries: {last_error}")


def _list_pr_files(
    session: Any,
    owner: str,
    name: str,
    number: int,
    headers: Dict[str, str],
    *,
    api_url: str,
    timeout: float,
    max_retries: int,
    backoff: float,
    max_pages: int = 10,
) -> List[Dict[str, Any]]:
    """List every file a PR touches (paginated; 100 per page, up to 1000 files)."""
    files: List[Dict[str, Any]] = []
    per_page = 100
    for page in range(1, max_pages + 1):
        url = (
            f"{api_url}/repos/{owner}/{name}/pulls/{number}/files"
            f"?per_page={per_page}&page={page}"
        )
        batch = _github_json(
            session, url, headers, timeout=timeout, max_retries=max_retries, backoff=backoff
        )
        if not isinstance(batch, list):
            raise AnalysisError(f"Unexpected payload for PR files: {type(batch).__name__}")
        files.extend(item for item in batch if isinstance(item, dict))
        if len(batch) < per_page:
            break
    else:
        LOGGER.warning("PR #%s has more than %d changed files; listing was truncated", number, max_pages * per_page)
    return files


def _write_mirrored(
    root: Path, relative: str, data: bytes, *, force: bool
) -> Tuple[Path, bool]:
    """Write ``data`` to ``root/<relative>``; returns ``(path, wrote)``.

    With ``force=False`` an existing file is left untouched (``wrote`` is ``False``), which lets
    an interrupted mirror be resumed without downloading the same bytes again.
    """
    target = root
    for part in relative.split("/"):
        target = target / part
    target.parent.mkdir(parents=True, exist_ok=True)
    wrote = force or not target.exists()
    if wrote:
        target.write_bytes(data)
    return target, wrote


def get_pr_source_files(
    repo_name: str,
    pr_number: int,
    *,
    token: Optional[str] = None,
    out_dir: Optional[str] = None,
    force: bool = False,
    timeout: float = 30.0,
    max_retries: int = 2,
    backoff: float = 1.5,
    api_url: Optional[str] = None,
    session: Any = None,
) -> Dict[str, Any]:
    """Download the full source of every file a PR touches, at both revisions.

    The result is an on-disk mirror that lets the AST layer compare two versions without
    running git locally::

        out/pr_<number>/base/<path>     # file content at base_sha
        out/pr_<number>/head/<path>     # file content at head_sha
        out/pr_<number>/manifest.json   # per-file sha256 / bytes / lines / errors

    Parameters
    ----
    repo_name:
        ``owner/repo`` (a full URL is accepted; see :func:`parse_repo_name`).
    pr_number:
        PR number.
    token:
        Defaults to ``GITHUB_TOKEN``; anonymous access works for public repos at 60 req/h.
    out_dir:
        Mirror root; defaults to ``out/pr_<number>`` under the current directory.
    force:
        Re-download and overwrite files that already exist (default: keep them, useful for
        resuming an interrupted download without spending quota again).
    session:
        Inject a ``requests.Session``-like object for tests; by default one is created and closed.

    Returns
    ----
    The manifest dict (also written to ``<out_dir>/manifest.json``)::

        {
          "schema_version": "1.0",
          "mode": "sources",
          "repo": {"owner": ..., "name": ..., "base_sha": ..., "head_sha": ..., "pr_number": ...},
          "out_dir": ...,
          "counts": {"files": 3, "downloaded": 5, "kept": 0, "failed": 0},
          "files": [
            {"path": "src/app.py", "old_path": null, "status": "modified",
             "base": {"ref": "<sha>", "url": ..., "sha256": ..., "bytes": ..., "lines": ...,
                      "binary": false, "saved": "base/src/app.py", "error": null},
             "head": {...}},
            ...
          ]
        }

    Notes
    ----
    - Raw content comes from ``raw.githubusercontent.com`` (no Contents-API 1 MB limit and no
      REST quota), so large files are handled; only the PR metadata and file list use the API.
    - ``status="added"`` files have no ``base`` entry, ``status="removed"`` files no ``head``
      entry; per-file failures are recorded as ``error`` instead of aborting the whole mirror.
    - Paths are validated: absolute paths and ``..`` segments raise :class:`AnalysisError`.
    """
    try:
        import requests  # type: ignore
    except ImportError as exc:  # pragma: no cover - missing dependency path
        raise AnalysisError("requests is required: pip install -r requirements.txt") from exc

    owner, name = parse_repo_name(repo_name)
    try:
        number = int(pr_number)
    except (TypeError, ValueError) as exc:
        raise AnalysisError(f"pr_number must be an integer, got {pr_number!r}") from exc
    if number <= 0:
        raise AnalysisError(f"pr_number must be a positive integer, got {number}")

    token = token or os.environ.get("GITHUB_TOKEN")
    if not token:
        LOGGER.warning(
            "GITHUB_TOKEN is not set; using anonymous requests (60/h, private repos invisible)"
        )

    base_api = (api_url or GITHUB_API_URL).rstrip("/")
    headers = _auth_headers(token)

    owns_session = session is None
    if owns_session:
        session = requests.Session()

    try:
        pull = _github_json(
            session,
            f"{base_api}/repos/{owner}/{name}/pulls/{number}",
            headers,
            timeout=timeout,
            max_retries=max_retries,
            backoff=backoff,
        )
        if not isinstance(pull, dict):
            raise AnalysisError("Unexpected PR payload from GitHub: expected a JSON object")
        base = pull.get("base") or {}
        head = pull.get("head") or {}
        base_sha = str(base.get("sha") or "")
        head_sha = str(head.get("sha") or "")
        if not base_sha or not head_sha:
            raise AnalysisError(f"PR #{number} payload has no base/head sha: cannot locate sources")

        patch_files = _list_pr_files(
            session,
            owner,
            name,
            number,
            headers,
            api_url=base_api,
            timeout=timeout,
            max_retries=max_retries,
            backoff=backoff,
        )
        if not patch_files:
            LOGGER.warning("PR #%s lists no changed files", number)

        root = Path(out_dir).expanduser() if out_dir else Path("out") / f"pr_{number}"
        root = root.resolve()
        base_root = root / "base"
        head_root = root / "head"

        records: List[Dict[str, Any]] = []
        downloaded = kept = failed = 0
        for item in patch_files:
            raw_path = str(item.get("filename") or "").strip()
            status = str(item.get("status") or "modified").strip().lower()
            if not raw_path:
                LOGGER.warning("Skipping a PR file entry without a filename: %r", item)
                failed += 1
                continue
            try:
                relative = _safe_repo_path(raw_path)
            except AnalysisError as exc:
                LOGGER.warning("%s", exc)
                failed += 1
                continue

            old_relative: Optional[str] = None
            if item.get("previous_filename"):
                try:
                    old_relative = _safe_repo_path(str(item["previous_filename"]).strip())
                except AnalysisError as exc:
                    LOGGER.warning("Ignoring unsafe previous filename in PR entry: %s", exc)
                    old_relative = None

            record: Dict[str, Any] = {
                "path": relative,
                "old_path": old_relative,
                "status": status,
                "additions": item.get("additions"),
                "deletions": item.get("deletions"),
            }
            for side, ref, side_root in (
                ("base", base_sha, base_root),
                ("head", head_sha, head_root),
            ):
                if status == "added" and side == "base":
                    record[side] = None
                    continue
                if status == "removed" and side == "head":
                    record[side] = None
                    continue
                # A renamed file lives at its previous path in the base revision.
                side_relative = (old_relative or relative) if side == "base" else relative
                url = f"{RAW_CONTENT_URL}/{owner}/{name}/{ref}/{side_relative}"
                entry: Dict[str, Any] = {
                    "ref": ref,
                    "url": url,
                    "sha256": None,
                    "bytes": None,
                    "lines": None,
                    "binary": None,
                    "saved": None,
                    "error": None,
                }
                try:
                    data = _download_raw(
                        session,
                        url,
                        headers,
                        timeout=timeout,
                        max_retries=max_retries,
                        backoff=backoff,
                    )
                    entry.update(_source_meta(data))
                    target, wrote = _write_mirrored(side_root, side_relative, data, force=force)
                    if wrote:
                        downloaded += 1
                    else:
                        kept += 1
                    entry["saved"] = target.relative_to(root).as_posix()
                except AnalysisError as exc:
                    entry["error"] = str(exc)
                    failed += 1
                    LOGGER.warning("Failed to fetch %s@%s: %s", relative, side, exc)
                record[side] = entry
            if record.get("base") and record.get("head"):
                old_sha = record["base"].get("sha256")
                new_sha = record["head"].get("sha256")
                record["changed"] = bool(old_sha and new_sha and old_sha != new_sha)
            records.append(record)

        manifest: Dict[str, Any] = {
            "schema_version": "1.0",
            "mode": "sources",
            "repo": {
                "owner": owner,
                "name": name,
                "base_sha": base_sha,
                "head_sha": head_sha,
                "pr_number": number,
            },
            "out_dir": str(root),
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "counts": {
                "files": len(records),
                "downloaded": downloaded,
                "kept": kept,
                "failed": failed,
            },
            "files": records,
        }
        manifest_path = root / "manifest.json"
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        LOGGER.info(
            "Saved sources of %s/%s PR #%s: %d files (%d downloaded, %d kept, %d failed) -> %s",
            owner,
            name,
            number,
            len(records),
            downloaded,
            kept,
            failed,
            root,
        )
        if records and downloaded + kept == 0 and failed:
            raise AnalysisError(
                f"Every download of {owner}/{name} PR #{number} failed; "
                f"manifest kept at {manifest_path}"
            )
        return manifest
    finally:
        if owns_session:
            session.close()


# ---------------------------------------------------------------------------
# Report assembly
# ---------------------------------------------------------------------------


def analyze_diff(
    diff_text: str,
    provider: SourceProvider,
    base_rev: str,
    head_rev: str,
    *,
    mode: str = "local",
    repo: Optional[Dict[str, Any]] = None,
    warnings: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Turn diff text into the final report (it does not fetch the diff)."""
    file_diffs = parse_diff(diff_text)
    files = [_analyze_file(diff, provider, base_rev, head_rev) for diff in file_diffs]

    # Noise marking: lock files / docs / binaries. Records are KEPT and only
    # flagged, so downstream can skip them via flatten_affected_scopes(report).
    for file_record in files:
        reason = noise_reason(
            file_record.get("path") or "", include_docs=False
        )
        file_record["is_noise"] = bool(reason)
        file_record["noise_reason"] = reason

    summary = {
        "files_changed": len(files),
        "python_files": sum(1 for f in files if f["is_python"]),
        "noise_files": sum(1 for f in files if f["is_noise"]),
        "hunks": sum(len(f["hunks"]) for f in files),
        "lines_added": sum(f["stats"]["added"] for f in files),
        "lines_removed": sum(f["stats"]["removed"] for f in files),
        "symbols_changed": len(
            {
                scope
                for f in files
                for scope in f["affected_scopes"]
                if not scope.endswith("::" + MODULE_SCOPE)
            }
        ),
        "test_files_touched": sum(1 for f in files if f["is_test_file"]),
        "comment_only_hunks": sum(
            1
            for f in files
            for h in f["hunks"]
            if h["change_kind"] == "comment_only"
        ),
    }

    return {
        "schema_version": SCHEMA_VERSION,
        "generator": {"name": GENERATOR_NAME, "version": GENERATOR_VERSION},
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "mode": mode,
        "repo": repo or {},
        "files": files,
        "summary": summary,
        "warnings": list(warnings or []),
    }


def analyze_local(
    base: str = "HEAD~1",
    head: str = "HEAD",
    repo_path: str = ".",
    *,
    three_dot: bool = True,
) -> Dict[str, Any]:
    """Offline mode: diff two revisions of a local git repository."""
    root = _git_root(repo_path)
    diff_range = f"{base}...{head}" if three_dot else f"{base}..{head}"
    diff_text = _git(
        ["diff", "--unified=0", "--find-renames", "--no-color", diff_range], root
    )
    base_sha = _rev_parse(root, base)
    head_sha = _rev_parse(root, head)
    remote_url = _git(["config", "--get", "remote.origin.url"], root, check=False)
    owner, name = _parse_remote(remote_url.strip())

    repo_meta = {
        "owner": owner,
        "name": name,
        "base_sha": base_sha,
        "head_sha": head_sha,
        "pr_number": None,
        "root": root,
    }
    provider = SourceProvider(repo_path=root)
    report = analyze_diff(
        diff_text,
        provider,
        base_sha,
        head_sha,
        mode="local",
        repo=repo_meta,
    )
    if not report["files"]:
        report["warnings"].append(
            f"No file differences between {base}...{head} (commit local changes first, or use --pr)."
        )
    return report


def analyze_pull_request(
    owner: str,
    name: str,
    pr_number: int,
    *,
    token: Optional[str] = None,
    repo_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Online mode: fetch PR metadata and diff from the GitHub API."""
    token = token or os.environ.get("GITHUB_TOKEN")
    if not token:
        LOGGER.warning("GITHUB_TOKEN is not set; using anonymous requests (limited to 60/h)")
    auth = _auth_headers(token)
    pr_url = f"{GITHUB_API_URL}/repos/{owner}/{name}/pulls/{pr_number}"

    meta_response = _http_get(pr_url, auth)
    meta = meta_response.json()

    diff_text = get_pr_diff(f"{owner}/{name}", pr_number, token=token)

    base_sha = (meta.get("base") or {}).get("sha") or ""
    head_sha = (meta.get("head") or {}).get("sha") or ""
    if not base_sha or not head_sha:
        raise AnalysisError("PR metadata has no base/head sha; cannot continue.")

    warnings: List[str] = []
    local_root = None
    if repo_path:
        try:
            local_root = _git_root(repo_path)
        except AnalysisError as exc:
            warnings.append(f"Local repository unavailable; reading source via GitHub raw: {exc}")

    provider = SourceProvider(
        repo_path=local_root, owner=owner, name=name, token=token
    )
    repo_meta = {
        "owner": owner,
        "name": name,
        "base_sha": base_sha,
        "head_sha": head_sha,
        "pr_number": pr_number,
        "title": meta.get("title"),
        "root": local_root,
    }
    return analyze_diff(
        diff_text,
        provider,
        base_sha,
        head_sha,
        mode="github-pr",
        repo=repo_meta,
        warnings=warnings,
    )


def flatten_affected_scopes(
    report: Dict[str, Any], *, include_noise: bool = False
) -> List[str]:
    """Convenience entry point for downstream: the union of all `path::qualname`.

    Noise files (lock files, docs, images, ...) are skipped by default because
    they cannot change test results. Pass ``include_noise=True`` for the full list.
    """
    scopes = set()
    for file_record in report.get("files", []):
        if not include_noise and file_record.get("is_noise"):
            continue
        scopes.update(file_record.get("affected_scopes", []))
    return sorted(scopes)


# ---------------------------------------------------------------------------
# AST core (tree-sitter)
# ---------------------------------------------------------------------------
# Design notes (complements the diff layer, does not replace it):
#   * the diff layer answers "which lines changed"; the AST layer answers "which
#     entity changed and how";
#   * the AST layer parses both FULL versions once and takes line numbers straight
#     from node coordinates, so hunk shifts, context trimming and line reordering
#     cannot affect it;
#   * fingerprints depend on syntax only: indentation, newlines, trailing commas
#     and comments do not change them, while docstrings do (a docstring is a
#     string node, exactly as in testmon's ast.dump-based checksum).


class ASTError(AnalysisError):
    """An expected AST-layer failure: unsupported language, missing grammar, unparsable source."""


@dataclass(frozen=True)
class LanguageSpec:
    """Describes how one language defines entities.

    ``unit_nodes`` maps tree-sitter node types to the unified entity types
    ``class`` / ``function`` / ``method``. Adding a language only needs a spec
    here plus a grammar name; the traversal itself never changes.
    """

    name: str  # grammar name used by tree_sitter_languages
    extensions: Tuple[str, ...]
    unit_nodes: Dict[str, str]
    container_nodes: Tuple[str, ...] = ()


LANGUAGE_SPECS: Dict[str, LanguageSpec] = {
    "python": LanguageSpec(
        name="python",
        extensions=(".py", ".pyi"),
        unit_nodes={
            "class_definition": "class",
            "function_definition": "function",
        },
        container_nodes=("class_definition",),
    ),
    "java": LanguageSpec(
        name="java",
        extensions=(".java",),
        unit_nodes={
            "class_declaration": "class",
            "interface_declaration": "class",
            "enum_declaration": "class",
            "record_declaration": "class",
            "method_declaration": "method",
            "constructor_declaration": "method",
        },
        container_nodes=(
            "class_declaration",
            "interface_declaration",
            "enum_declaration",
            "record_declaration",
        ),
    ),
}

_LANGUAGE_ALIASES = {
    "py": "python",
    "python3": "python",
    "pyi": "python",
    "java": "java",
}

# Grammar package: prefer tree-sitter-languages (pinned 1.10.2); if missing, try
# the maintained tree-sitter-language-pack (same get_parser/get_language API).
_TS_BACKEND: Optional[Tuple[str, Any]] = None
_TS_BACKEND_PROBED = False
_PARSER_CACHE: Dict[str, Any] = {}


def _ts_backend() -> Tuple[str, Any]:
    """Lazily import the grammar package; raise :class:`ASTError` when neither exists."""
    global _TS_BACKEND, _TS_BACKEND_PROBED
    if not _TS_BACKEND_PROBED:
        _TS_BACKEND_PROBED = True
        try:  # preferred: the package pinned by the project
            import tree_sitter_languages as backend  # type: ignore

            _TS_BACKEND = ("tree-sitter-languages", backend)
        except ImportError:
            try:  # fallback: maintained alternative with the same API
                import tree_sitter_language_pack as backend  # type: ignore

                _TS_BACKEND = ("tree-sitter-language-pack", backend)
            except ImportError:
                _TS_BACKEND = None
    if _TS_BACKEND is None:
        raise ASTError(
            "No tree-sitter grammar package installed; AST analysis is unavailable. "
            "Run: pip install tree-sitter==0.21.3 tree-sitter-languages==1.10.2"
        )
    return _TS_BACKEND


def language_spec(language: str) -> LanguageSpec:
    """Normalise ``python`` / ``py`` / ``.py`` into a :class:`LanguageSpec`."""
    if not language:
        raise ASTError("language must not be empty")
    key = str(language).strip().lower()
    if key.startswith("."):
        key = key[1:]
    key = _LANGUAGE_ALIASES.get(key, key)
    spec = LANGUAGE_SPECS.get(key)
    if spec is None:
        supported = ", ".join(sorted(LANGUAGE_SPECS))
        raise ASTError(f"Unsupported language {language!r} (supported: {supported})")
    return spec


def language_for_path(path: str) -> str:
    """Infer the language from the extension; unknown returns ``"unknown"``."""
    suffix = Path(path).suffix.lower()
    for spec in LANGUAGE_SPECS.values():
        if suffix in spec.extensions:
            return spec.name
    return "unknown"


def get_parser(language: str) -> Any:
    """Get (and cache) the tree-sitter parser for one language."""
    spec = language_spec(language)
    backend_name, backend = _ts_backend()
    parser = _PARSER_CACHE.get(spec.name)
    if parser is None:
        try:
            parser = backend.get_parser(spec.name)
        except Exception as exc:  # noqa: BLE001 - the package raises inconsistent types
            raise ASTError(
                f"{backend_name} cannot load the {spec.name} grammar: {exc}"
            ) from exc
        _PARSER_CACHE[spec.name] = parser
    return parser


@dataclass
class ASTDocument:
    """One parse result: syntax tree + source + language spec.

    ``extract_entities`` accepts either this object or a bare ``tree``; passing
    this object gives you ``node_text`` and line numbers (recommended).
    """

    language: str
    source: bytes
    tree: Any
    backend: str
    spec: LanguageSpec
    has_error: bool = False

    @property
    def root(self) -> Any:
        return self.tree.root_node

    @property
    def source_text(self) -> str:
        return self.source.decode("utf-8", errors="replace")

    def text_of(self, node: Any) -> str:
        return self.source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")

    def lines_of(self, node: Any) -> str:
        """The WHOLE-LINE source covered by a node (first-line indent included).

        Unlike ``text_of``: tree-sitter's ``start_byte`` points at the first
        token, so ``text_of`` drops the leading indent; here the indent is put
        back so the snippet can be displayed directly.
        """
        line_start = self.source.rfind(b"\n", 0, node.start_byte) + 1
        line_end = self.source.find(b"\n", node.end_byte)
        if line_end == -1:
            line_end = len(self.source)
        return self.source[line_start:line_end].decode("utf-8", errors="replace")


def _normalized_token(node: Any) -> str:
    """Normalise leaf text: collapse inner whitespace and strip the ends."""
    try:
        raw = node.text or b""
    except Exception:  # pragma: no cover - the node may already be invalid
        return ""
    text = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else str(raw)
    return " ".join(text.split())


_SKIPPED_NODE_TYPES = {"comment", "line_comment", "block_comment"}


def structural_signature(node: Any, *, depth: int = 0) -> str:
    """Reduce a subtree to a stable string (used for fingerprints).

    Rules:

    * comments are skipped entirely (``comment`` / ``line_comment`` /
      ``block_comment``);
    * ALL children are kept, including anonymous tokens such as ``+``, ``-``,
      ``:`` and ``(``; otherwise ``a + b`` and ``a - b`` would hash the same;
    * leaves carry their normalised text (identifiers, literals, operators).

    Indentation, newlines, blank lines and comments therefore never matter,
    while renaming an identifier, swapping an operator or changing a literal
    always does -- the same semantics as testmon's checksum.
    """
    if node.type in _SKIPPED_NODE_TYPES:
        return ""
    children = node.children
    if not children:
        return f"{node.type}:{_normalized_token(node)}"
    if depth >= 200:  # guard against pathologically deep trees
        return f"{node.type}#{node.end_byte - node.start_byte}"
    parts = [structural_signature(child, depth=depth + 1) for child in children]
    return f"{node.type}({''.join(part for part in parts if part)})"


def hash_signature(signature: str) -> str:
    """First 16 hex chars of sha256; used only for equality, 64 bits is plenty."""
    return hashlib.sha256(signature.encode("utf-8")).hexdigest()[:16]


def node_hash(node: Any) -> str:
    return hash_signature(structural_signature(node))


def parse_to_ast(
    code_content: Any,
    language: str = "python",
    *,
    source_path: Optional[str] = None,
) -> ASTDocument:
    """Parse source into an :class:`ASTDocument` (holding a tree-sitter tree).

    Parameters

        ``str`` (encoded as UTF-8) or ``bytes``.
        ``python`` / ``java``; ``py`` and ``.py`` are accepted too.
        Optional, used only in error messages to locate the offending file.

    Syntax errors never raise: tree-sitter is error-tolerant and returns a tree
    with ``ERROR`` nodes, in which case ``doc.has_error`` is ``True`` and the
    caller can degrade to diff-level analysis.
    """
    if code_content is None:
        raise ASTError(f"Content of {source_path or '<string>'} is None")
    if isinstance(code_content, str):
        source = code_content.encode("utf-8")
    elif isinstance(code_content, (bytes, bytearray)):
        source = bytes(code_content)
    else:
        raise ASTError(
            f"code_content must be str or bytes, got {type(code_content).__name__}"
        )

    spec = language_spec(language)
    backend_name, _ = _ts_backend()
    parser = get_parser(spec.name)
    tree = parser.parse(source)
    has_error = bool(getattr(tree.root_node, "has_error", False))
    if has_error:
        LOGGER.debug("%s contains syntax errors; continuing in error-tolerant mode", source_path or "<string>")
    return ASTDocument(
        language=spec.name,
        source=source,
        tree=tree,
        backend=backend_name,
        spec=spec,
        has_error=has_error,
    )


def parse_file(path: str, language: Optional[str] = None) -> ASTDocument:
    """Read a file and parse it; ``language`` defaults to the extension."""
    file_path = Path(path)
    if not file_path.is_file():
        raise ASTError(f"File does not exist: {file_path}")
    lang = language or language_for_path(str(file_path))
    if lang == "unknown":
        raise ASTError(f"Cannot infer the language from the extension: {file_path.name}")
    return parse_to_ast(
        file_path.read_bytes(), lang, source_path=str(file_path)
    )


def _entity_name(node: Any, source: bytes) -> str:
    name_node = None
    try:
        name_node = node.child_by_field_name("name")
    except Exception:  # pragma: no cover - some grammars lack the field
        name_node = None
    if name_node is None:
        for child in node.named_children:
            if child.type in ("identifier", "type_identifier", "field_identifier"):
                name_node = child
                break
    if name_node is None:
        return f"<anonymous@{node.start_point[0] + 1}>"
    return source[name_node.start_byte : name_node.end_byte].decode("utf-8", errors="replace")


# ---------------------------------------------------------------------------
# Three fingerprint dimensions per entity: signature / body / docstring.
# With only node_hash, "parameter changed", "body changed" and "docstring-only
# changed" would all collapse into one "changed", and downstream could not tell
# a broken calling convention from a changed implementation from a doc update.
# So each entity carries three extra fingerprints and change_category is derived
# straight from them:
#   signature_hash  -- parameters, return type, decorators/modifiers, base types
#   body_hash       -- body statements (docstring EXCLUDED)
#   docstring_hash  -- docstring text (Java javadoc is a comment, absent -> None)
_SIGNATURE_FIELD_GROUPS: Tuple[Tuple[str, Tuple[str, ...]], ...] = (
    ("parameters", ("parameters",)),
    ("return_type", ("return_type", "type")),
    ("type_parameters", ("type_parameters",)),
    ("inheritance", ("superclass", "superclasses", "interfaces")),
)


def _field_node(node: Any, field: str) -> Any:
    """Safe ``child_by_field_name`` (field names differ between grammars)."""
    try:
        return node.child_by_field_name(field)
    except Exception:  # pragma: no cover - some grammars lack the field
        return None


def _decorator_nodes(node: Any) -> List[Any]:
    """Decorators (Python) and modifiers (Java, e.g. ``public static``)."""
    found = [child for child in node.named_children if child.type == "modifiers"]
    parent = getattr(node, "parent", None)
    if parent is not None and parent.type == "decorated_definition":
        found.extend(child for child in parent.children if child.type == "decorator")
    return found


def _signature_parts(node: Any) -> Dict[str, str]:
    """Split the signature into dimensions so the report can say which part changed."""
    parts: Dict[str, str] = {}
    for label, fields in _SIGNATURE_FIELD_GROUPS:
        chosen = None
        for field in fields:
            chosen = _field_node(node, field)
            if chosen is not None:
                break
        parts[label] = (
            hash_signature(structural_signature(chosen)) if chosen is not None else ""
        )
    decorators = _decorator_nodes(node)
    parts["decorators"] = (
        hash_signature("".join(structural_signature(item) for item in decorators))
        if decorators
        else ""
    )
    return parts


def _signature_hash(parts: Dict[str, str]) -> str:
    return hash_signature("|".join(f"{key}={parts[key]}" for key in sorted(parts)))


def _body_node(node: Any) -> Any:
    return _field_node(node, "body")


def _docstring_info(body: Any, language: str) -> Tuple[Any, str]:
    """Return ``(docstring statement node, fingerprint)``; ``(None, "")`` when absent.

    Python only allows a docstring as the FIRST statement of a body, so only the
    first ``expression_statement`` is inspected. Java's javadoc is a block
    comment before the declaration (not part of the body), so this returns
    empty -- Java doc changes do not alter entity fingerprints, consistent with
    how comments are treated.
    """
    if body is None or language != "python":
        return None, ""
    first = body.named_children[0] if body.named_children else None
    if first is None or first.type != "expression_statement":
        return None, ""
    inner = first.named_children[0] if first.named_children else None
    if inner is None or inner.type not in ("string", "concatenated_string"):
        return None, ""
    return first, hash_signature(structural_signature(inner))


def _body_signature(
    body: Any, docstring_statement: Any, skip_types: frozenset = frozenset()
) -> str:
    """Body fingerprint: concatenate statement signatures, skipping the docstring.

    ``skip_types`` is for classes only: methods and nested classes have their own
    entity records, and folding them into the class body would make every method
    edit also report the class as ``Logic change`` (duplicate records that hide
    the fact that the class itself did not change).
    """
    if body is None:
        return ""
    parts: List[str] = []
    for child in body.children:
        if (
            docstring_statement is not None
            and child.start_byte == docstring_statement.start_byte
        ):
            continue
        if child.type in skip_types:
            continue
        piece = structural_signature(child)
        if piece:
            parts.append(piece)
    return f"{body.type}({''.join(parts)})"


def extract_entities(
    ast_tree: Any,
    *,
    include_methods: bool = True,
    include_nested: bool = True,
) -> List[Dict[str, Any]]:
    """Walk the AST and extract every "interesting entity" (class / function / method).

    Returns a list of dicts ordered by source position, each with:

    ==================  ====================================================
    entity_type         ``class`` / ``function`` / ``method``
    entity_name         the entity's own name (no parent prefix)
    qualname            ``Class.method`` / ``outer.inner``, isomorphic to Python's
                        ``__qualname__`` so it lines up with testmon blocks
    parent_qualname     qualname of the parent entity; ``None`` at top level
    node_type           raw tree-sitter node type (e.g. ``function_definition``)
    language            ``python`` / ``java``
    line_start/line_end 1-based, inclusive, covering the def line and the body
    start_byte/end_byte exact byte range of the node (source of ``node_text``)
    node_text           whole-line source of the entity (indent included, displayable)
    node_hash           AST subtree fingerprint (16 hex), equal iff structurally equal
    signature_parts     per-dimension signature fingerprints: parameters / return_type /
                        type_parameters / inheritance / decorators
    signature_hash      signature fingerprint merged from the parts above
    body_hash           body statement fingerprint (docstring excluded)
    docstring_hash      docstring fingerprint; ``None`` when there is none
    has_error           whether the subtree contains a syntax-error node
    ==================  ====================================================

    ``include_methods=False`` skips methods inside classes; ``include_nested=False``
    skips nested functions/classes defined inside functions.
    """
    doc: Optional[ASTDocument] = None
    if isinstance(ast_tree, ASTDocument):
        doc = ast_tree
        tree = doc.tree
        spec = doc.spec
        source = doc.source
        language = doc.language
    else:
        tree = ast_tree
        if not hasattr(tree, "root_node"):
            raise ASTError("extract_entities needs an ASTDocument or a tree-sitter Tree/Node")
        spec = LanguageSpec(
            name="unknown",
            extensions=(),
            unit_nodes={
                "class_definition": "class",
                "function_definition": "function",
                "class_declaration": "class",
                "method_declaration": "method",
            },
            container_nodes=("class_definition", "class_declaration"),
        )
        source = b""
        language = "unknown"

    root = tree.root_node if hasattr(tree, "root_node") else tree

    entities: List[Dict[str, Any]] = []
    # Stack items: (node, ancestors [(qualname, entity_type)]); iterative to avoid deep recursion
    stack: List[Tuple[Any, List[Tuple[str, str]]]] = [(root, [])]
    while stack:
        node, ancestors = stack.pop()
        base_type = spec.unit_nodes.get(node.type)
        is_container = node.type in spec.container_nodes

        if base_type is not None:
            entity_type = base_type
            # Look only at the NEAREST entity ancestor: a function inside a class is
            # a method, while a function inside a method is a nested function (its
            # qualname still carries the parent prefix).
            nearest_unit_kind = ancestors[-1][1] if ancestors else None
            if base_type == "function" and nearest_unit_kind == "class":
                entity_type = "method"
            elif base_type == "method" and nearest_unit_kind not in (None, "class"):
                entity_type = "function"
            if not include_methods and entity_type == "method":
                entity_type = ""
            if not include_nested and any(
                kind in ("function", "method") for _, kind in ancestors
            ):
                entity_type = ""

            if entity_type:
                name = _entity_name(node, source)
                # the nearest entity ancestor's qualname already has the prefix: just concatenate
                parent_qualname = ancestors[-1][0] if ancestors else None
                qualname = f"{parent_qualname}.{name}" if parent_qualname else name
                holder = node
                parent = getattr(node, "parent", None)
                if parent is not None and parent.type == "decorated_definition":
                    # A decorator is a SIBLING of the function_definition node. Hashing
                    # only `node` would report "added @property" as "no change", so both
                    # the fingerprint and the line range include the decorator.
                    holder = parent
                skip_types = (
                    frozenset(spec.unit_nodes) | {"decorated_definition"}
                    if is_container
                    else frozenset()
                )
                text = (
                    doc.lines_of(holder)
                    if doc is not None
                    else (holder.text or b"").decode("utf-8", errors="replace")
                )
                signature_parts = _signature_parts(node)
                body_node = _body_node(node)
                docstring_statement, docstring_hash = _docstring_info(
                    body_node, language
                )
                entities.append(
                    {
                        "entity_type": entity_type,
                        "entity_name": name,
                        "qualname": qualname,
                        "parent_qualname": parent_qualname,
                        "node_type": node.type,
                        "language": language,
                        "line_start": holder.start_point[0] + 1,
                        "line_end": holder.end_point[0] + 1,
                        "start_byte": holder.start_byte,
                        "end_byte": holder.end_byte,
                        "node_text": text,
                        "node_hash": node_hash(holder),
                        "signature_parts": signature_parts,
                        "signature_hash": _signature_hash(signature_parts),
                        "body_hash": hash_signature(
                            _body_signature(body_node, docstring_statement, skip_types)
                        ),
                        "docstring_hash": docstring_hash or None,
                        "has_error": bool(getattr(node, "has_error", False)),
                    }
                )
                if is_container or entity_type in ("function", "method"):
                    ancestors = ancestors + [(qualname, entity_type)]

        # push children in reverse so they pop in source order
        for child in reversed(node.children):
            stack.append((child, ancestors))

    entities.sort(key=lambda item: (item["start_byte"], item["end_byte"]))
    return entities


def entities_by_qualname(entities: Sequence[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """``qualname -> entity`` index; on redefinition the later one wins (as at runtime)."""
    return {entity["qualname"]: entity for entity in entities}


def summarize_entities(entities: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """Aggregate summary of an entity list, for logs and demos."""
    by_type: Dict[str, int] = {}
    for entity in entities:
        by_type[entity["entity_type"]] = by_type.get(entity["entity_type"], 0) + 1
    return {
        "total": len(entities),
        "by_type": by_type,
        "languages": sorted({entity["language"] for entity in entities}),
        "errors": sum(1 for entity in entities if entity["has_error"]),
    }


# ---------------------------------------------------------------------------
# Old-vs-new AST comparison: add / delete / modify + change_category
# ---------------------------------------------------------------------------
CHANGE_ADD = "add"
CHANGE_DELETE = "delete"
CHANGE_MODIFY = "modify"

CATEGORY_SIGNATURE = "Signature change"
CATEGORY_LOGIC = "Logic change"
CATEGORY_DOCUMENTATION = "Documentation change"
CATEGORY_RENAME = "Rename / Move"

# add / delete already express the category, so change_category stays None.
# If the spec ever requires a string for those too, edit this table only.
ADD_DELETE_CATEGORY: Dict[str, Optional[str]] = {
    CHANGE_ADD: None,
    CHANGE_DELETE: None,
}

_PART_LABELS: Dict[str, str] = {
    "parameters": "parameters",
    "return_type": "return type",
    "type_parameters": "type parameters",
    "inheritance": "base class/interfaces",
    "decorators": "decorators/modifiers",
}


def classify_entity_change(
    old: Dict[str, Any], new: Dict[str, Any]
) -> Tuple[str, List[str]]:
    """Classify the change of one entity (same qualname); returns ``(category, reasons)``.

    Priority order (important):

    1. equal ``node_hash`` -> ``unchanged`` (not even the docstring moved);
    2. signature changed -> ``Signature change`` (even if the body changed too,
       because it breaks callers the most; body changes are still in ``reasons``);
    3. only the body changed -> ``Logic change``;
    4. only the docstring changed -> ``Documentation change``;
    5. fallback: ``node_hash`` differs but no dimension does (e.g. an annotation
       moved structurally) -> conservatively ``Logic change`` so nothing is missed.
    """
    if old.get("node_hash") == new.get("node_hash"):
        return "unchanged", []

    reasons: List[str] = []
    signature_changed = old.get("signature_hash") != new.get("signature_hash")
    body_changed = old.get("body_hash") != new.get("body_hash")
    docstring_changed = old.get("docstring_hash") != new.get("docstring_hash")

    if signature_changed:
        old_parts = old.get("signature_parts") or {}
        new_parts = new.get("signature_parts") or {}
        labels = [
            label
            for key, label in _PART_LABELS.items()
            if old_parts.get(key) != new_parts.get(key)
        ]
        reasons.append("signature: " + (", ".join(labels) if labels else "changed"))
    if body_changed:
        reasons.append("body: AST changed")
    if docstring_changed:
        if old.get("docstring_hash") and new.get("docstring_hash"):
            reasons.append("docstring: text changed")
        elif new.get("docstring_hash"):
            reasons.append("docstring: added")
        else:
            reasons.append("docstring: removed")

    if signature_changed:
        return CATEGORY_SIGNATURE, reasons
    if body_changed:
        return CATEGORY_LOGIC, reasons
    if docstring_changed:
        return CATEGORY_DOCUMENTATION, reasons
    if new.get("entity_type") == "class":
        # The class-body fingerprint excludes methods/nested classes, so the only
        # difference comes from child entities. They get their own records, hence no
        # duplicate entry for the class.
        return "descendants", []
    return CATEGORY_LOGIC, ["body: structural change outside signature/docstring"]


def _change_summary(
    change_type: str,
    category: Optional[str],
    entity: Dict[str, Any],
    reasons: Sequence[str],
) -> str:
    """Rule-based summary text (no LLM, fixed wording, easy to assert on downstream)."""
    if change_type == CHANGE_ADD:
        return f"new {entity['entity_type']} added"
    if change_type == CHANGE_DELETE:
        return f"{entity['entity_type']} removed"
    if change_type == "unchanged":
        return "no structural change"
    if category == CATEGORY_SIGNATURE:
        detail = "signature"
        for reason in reasons:
            if reason.startswith("signature: "):
                detail = reason.split(": ", 1)[1]
                break
        extra = "; body also changed" if any(
            reason.startswith("body: ") for reason in reasons
        ) else ""
        return f"signature changed ({detail}){extra}"
    if category == CATEGORY_LOGIC:
        return f"AST structural change in {entity['entity_type']} body"
    return "docstring changed; signature and body unchanged"


def _make_change(
    change_type: str,
    category: Optional[str],
    old_entity: Optional[Dict[str, Any]],
    new_entity: Optional[Dict[str, Any]],
    reasons: Sequence[str],
    with_details: bool,
) -> Dict[str, Any]:
    entity = new_entity if new_entity is not None else old_entity
    assert entity is not None  # add/delete/modify always has at least one side
    record: Dict[str, Any] = {
        "entity_type": entity["entity_type"],
        "entity_name": entity["entity_name"],
        "change_type": change_type,
        "change_category": category,
        "line_start": entity["line_start"],
        "line_end": entity["line_end"],
        "summary": _change_summary(change_type, category, entity, reasons),
    }
    if with_details:
        record.update(
            {
                "qualname": entity["qualname"],
                "old_line_start": old_entity["line_start"] if old_entity else None,
                "old_line_end": old_entity["line_end"] if old_entity else None,
                "language": entity.get("language"),
                "reasons": list(reasons),
                "old_node_hash": old_entity.get("node_hash") if old_entity else None,
                "new_node_hash": new_entity.get("node_hash") if new_entity else None,
            }
        )
    return record


def compare_entities(
    old_entities: Sequence[Dict[str, Any]],
    new_entities: Sequence[Dict[str, Any]],
    *,
    include_unchanged: bool = False,
    with_details: bool = False,
) -> List[Dict[str, Any]]:
    """Compare two entity lists and emit add / delete / modify records.

    Matching rule: identical ``qualname`` means the same entity (isomorphic to
    Python's ``__qualname__``). A rename is indistinguishable from "one delete
    plus one add" at AST level and shows up as ``delete + add``; to recover
    rename semantics, pair the leftovers once more by ``node_hash``.

    Parameters
    ----------
    old_entities / new_entities
        Entity lists produced by :func:`extract_entities`.
    include_unchanged
        keep ``change_type="unchanged"`` records (dropped by default).
    with_details
        ``True`` adds debug fields (``qualname``, old lines, reasons, before/after
        fingerprints); the default output is exactly the seven required keys.

    Returned records are sorted by ``line_start``; ``delete`` uses base line numbers.
    """
    old_index = entities_by_qualname(old_entities)
    new_index = entities_by_qualname(new_entities)
    changes: List[Dict[str, Any]] = []

    for qualname, new_entity in new_index.items():
        old_entity = old_index.get(qualname)
        if old_entity is None:
            changes.append(
                _make_change(
                    CHANGE_ADD,
                    ADD_DELETE_CATEGORY[CHANGE_ADD],
                    None,
                    new_entity,
                    [],
                    with_details,
                )
            )
            continue
        category, reasons = classify_entity_change(old_entity, new_entity)
        if category in ("unchanged", "descendants"):
            if include_unchanged:
                changes.append(
                    _make_change(
                        "unchanged", None, old_entity, new_entity, [], with_details
                    )
                )
            continue
        changes.append(
            _make_change(
                CHANGE_MODIFY, category, old_entity, new_entity, reasons, with_details
            )
        )

    for qualname, old_entity in old_index.items():
        if qualname in new_index:
            continue
        changes.append(
            _make_change(
                CHANGE_DELETE,
                ADD_DELETE_CATEGORY[CHANGE_DELETE],
                old_entity,
                None,
                [],
                with_details,
            )
        )

    changes.sort(
        key=lambda item: (item["line_start"], item["line_end"], item["change_type"])
    )
    return changes


def compare_sources(
    old_source: Any,
    new_source: Any,
    *,
    file_path: str = "<source>",
    language: Optional[str] = None,
    include_unchanged: bool = False,
    with_details: bool = False,
) -> Dict[str, Any]:
    """Parse both versions of one file, compare them and return the required JSON.

    Returns ``{"file_path": ..., "changes": [...]}``. ``language`` defaults to the
    extension of ``file_path`` (``.py`` -> python, ``.java`` -> java).
    """
    lang = language
    if lang is None:
        if file_path in ("", "<source>"):
            lang = "python"
        else:
            lang = language_for_path(file_path)
            if lang == "unknown":
                raise ASTError(
                    f"Cannot infer the language from {file_path}; pass language='python'/'java'"
                )
    old_doc = parse_to_ast(old_source, lang, source_path=f"{file_path}@base")
    new_doc = parse_to_ast(new_source, lang, source_path=f"{file_path}@head")
    changes = compare_entities(
        extract_entities(old_doc),
        extract_entities(new_doc),
        include_unchanged=include_unchanged,
        with_details=with_details,
    )
    return {"file_path": file_path, "changes": changes}


def summarize_changes(changes: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """Count by change_type / change_category, for logs and demos."""
    by_type: Dict[str, int] = {}
    by_category: Dict[str, int] = {}
    for change in changes:
        by_type[change["change_type"]] = by_type.get(change["change_type"], 0) + 1
        category = change.get("change_category")
        if category:
            by_category[category] = by_category.get(category, 0) + 1
    return {"total": len(changes), "by_change_type": by_type, "by_category": by_category}


def _normalised_text(entity: Dict[str, Any]) -> str:
    """Collapse whitespace so indentation-only edits do not affect similarity."""
    return " ".join(str(entity.get("node_text") or "").split())


def entity_similarity(old: Dict[str, Any], new: Dict[str, Any]) -> float:
    """Similarity between two entities, in ``[0.0, 1.0]``.

    An equal ``node_hash`` short-circuits to ``1.0``: a pure move keeps the whole
    subtree (the ``def`` name included) byte-for-byte, so the match is certain.
    Otherwise the normalised source text is compared with :mod:`difflib`; a rename
    only touches the ``def`` line, so an otherwise identical body still scores
    close to ``1.0``.
    """
    old_hash = old.get("node_hash")
    if old_hash and old_hash == new.get("node_hash"):
        return 1.0
    old_text = _normalised_text(old)
    new_text = _normalised_text(new)
    if not old_text or not new_text:
        return 0.0
    return difflib.SequenceMatcher(a=old_text, b=new_text).ratio()


def find_renames(
    old_entities_by_path: Dict[str, Sequence[Dict[str, Any]]],
    new_entities_by_path: Dict[str, Sequence[Dict[str, Any]]],
    *,
    min_similarity: float = 0.9,
) -> List[Dict[str, Any]]:
    """Pair entities that disappeared from one file with entities that appeared in another.

    Inputs are the *candidates only* (entities whose ``qualname`` has no counterpart in
    the same file), so unchanged code can never be paired. Matching is greedy by score:

    1. equal ``node_hash`` (pure move / pure rename) -> ``1.0``;
    2. otherwise ``difflib`` ratio of the normalised ``node_text`` (a rename changes
       only the ``def`` line, so the ratio stays high);
    3. pairs below ``min_similarity`` are ignored, and ``entity_type`` must match.

    Returns a list of dicts (JSON-serialisable, no entity objects)::

        {"old_path", "new_path", "old_qualname", "new_qualname", "entity_type",
         "entity_name", "line_start", "line_end", "similarity", "node_hash_equal"}

    Known limitation: two unrelated one-line functions with identical text in different
    files are indistinguishable from a real move; the greedy pairing may pick either.
    """
    candidates: List[Tuple[float, str, Dict[str, Any], str, Dict[str, Any]]] = []
    for old_path, old_entities in old_entities_by_path.items():
        for old in old_entities:
            for new_path, new_entities in new_entities_by_path.items():
                for new in new_entities:
                    if old.get("entity_type") != new.get("entity_type"):
                        continue
                    score = entity_similarity(old, new)
                    if score >= min_similarity:
                        candidates.append((score, old_path, old, new_path, new))
    candidates.sort(key=lambda item: (-item[0], item[1], item[2].get("line_start", 0)))

    used_old: set = set()
    used_new: set = set()
    matches: List[Dict[str, Any]] = []
    for score, old_path, old, new_path, new in candidates:
        old_key = (old_path, old.get("qualname"))
        new_key = (new_path, new.get("qualname"))
        if old_key in used_old or new_key in used_new:
            continue
        used_old.add(old_key)
        used_new.add(new_key)
        matches.append(
            {
                "old_path": old_path,
                "new_path": new_path,
                "old_qualname": old.get("qualname"),
                "new_qualname": new.get("qualname"),
                "entity_type": new.get("entity_type"),
                "entity_name": new.get("entity_name"),
                "line_start": new.get("line_start"),
                "line_end": new.get("line_end"),
                "similarity": round(score, 4),
                "node_hash_equal": old.get("node_hash") == new.get("node_hash"),
            }
        )
    return matches


def _rename_record(match: Dict[str, Any]) -> Dict[str, Any]:
    """Turn one :func:`find_renames` match into a downstream-friendly change record."""
    return {
        "entity_type": match["entity_type"],
        "entity_name": match["entity_name"],
        "change_type": "rename",
        "change_category": CATEGORY_RENAME,
        "line_start": match["line_start"],
        "line_end": match["line_end"],
        "summary": (
            f"Moved/renamed from {match['old_path']}::{match['old_qualname']} "
            f"(similarity {match['similarity']:.2f})"
        ),
        "file_path": match["new_path"],
        "old_path": match["old_path"],
        "new_path": match["new_path"],
        "old_qualname": match["old_qualname"],
        "new_qualname": match["new_qualname"],
        "similarity": match["similarity"],
    }


def analyze_pr(
    repo_name: str,
    pr_number: int,
    *,
    token: Optional[str] = None,
    out_dir: Optional[str] = None,
    force: bool = False,
    include_unchanged: bool = False,
    with_details: bool = False,
    min_similarity: float = 0.9,
    session: Any = None,
) -> Dict[str, Any]:
    """One-shot PR analysis: mirror sources -> extract entities -> compare -> rename pass.

    Pipeline::

        get_pr_source_files()  ->  out/pr_<n>/{base,head}/<path> + manifest.json
        parse_file() x2        ->  entity lists per file (python / java only)
        deleted/added split    ->  find_renames()  (cross-file moves, greedy by similarity)
        compare_entities()     ->  add / delete / modify + change_category
        merge                  ->  per-file groups + flat ``changes`` list

    Files that are noise (``noise_reason()``) or in an unsupported language are listed
    under ``skipped`` instead of being analysed; a parse or download error only adds a
    warning, it never aborts the whole run.

    Returns::

        {
          "schema_version": "2.0",
          "mode": "pr_ast",
          "repo": {"owner", "name", "base_sha", "head_sha", "pr_number"},
          "mirror": {"out_dir": ..., "counts": {...}},
          "summary": {"files_changed", "files_analyzed", "files_skipped", "changes",
                       "renames", "by_change_type", "by_change_category", "warnings"},
          "files": [{"file_path", "language", "is_test_file", "changes": [...]}],
          "changes": [ ... flat list, each record also carries "file_path" ... ],
          "skipped": [{"path", "reason"}],
          "warnings": [ ... ],
        }

    Every record keeps the strict seven keys (``entity_type``, ``entity_name``,
    ``change_type``, ``change_category``, ``line_start``, ``line_end``, ``summary``);
    ``rename`` records additionally carry ``old_path`` / ``new_path`` / ``old_qualname``
    / ``new_qualname`` / ``similarity``, and every record carries ``file_path``.
    """
    manifest = get_pr_source_files(
        repo_name,
        pr_number,
        token=token,
        out_dir=out_dir,
        force=force,
        session=session,
    )
    root = Path(manifest["out_dir"])
    warnings: List[str] = []
    skipped: List[Dict[str, str]] = []

    # --- 1. parse both revisions of every touched file -----------------------
    layouts: List[Dict[str, Any]] = []
    old_by_path: Dict[str, List[Dict[str, Any]]] = {}
    new_by_path: Dict[str, List[Dict[str, Any]]] = {}

    for record in sorted(manifest["files"], key=lambda item: item["path"]):
        path = record["path"]
        old_path = record.get("old_path") or path
        language = language_for_path(path)
        reason = noise_reason(path)
        if reason:
            skipped.append({"path": path, "reason": f"noise: {reason}"})
            continue
        if language == "unknown":
            skipped.append({"path": path, "reason": "unsupported language"})
            continue

        old_entities: List[Dict[str, Any]] = []
        new_entities: List[Dict[str, Any]] = []
        base = record.get("base") or {}
        head = record.get("head") or {}
        if base.get("error"):
            warnings.append(f"{old_path}@base: {base['error']}")
        if head.get("error"):
            warnings.append(f"{path}@head: {head['error']}")
        if base.get("saved"):
            try:
                old_entities = extract_entities(parse_file(str(root / base["saved"]), language))
            except (ASTError, OSError, UnicodeDecodeError) as exc:
                warnings.append(f"{old_path}@base: cannot parse ({exc})")
        if head.get("saved"):
            try:
                new_entities = extract_entities(parse_file(str(root / head["saved"]), language))
            except (ASTError, OSError, UnicodeDecodeError) as exc:
                warnings.append(f"{path}@head: cannot parse ({exc})")

        old_by_path[old_path] = old_entities
        new_by_path[path] = new_entities
        layouts.append({"path": path, "old_path": old_path, "language": language})

    # --- 2. candidates for a cross-file rename / move -------------------------
    deleted_by_path: Dict[str, List[Dict[str, Any]]] = {}
    added_by_path: Dict[str, List[Dict[str, Any]]] = {}
    for layout in layouts:
        path = layout["path"]
        old_path = layout["old_path"]
        old_entities = old_by_path.get(old_path, [])
        new_entities = new_by_path.get(path, [])
        old_names = {entity.get("qualname") for entity in old_entities}
        new_names = {entity.get("qualname") for entity in new_entities}
        deleted = [entity for entity in old_entities if entity.get("qualname") not in new_names]
        added = [entity for entity in new_entities if entity.get("qualname") not in old_names]
        if deleted:
            deleted_by_path[old_path] = deleted
        if added:
            added_by_path[path] = added

    renames = find_renames(deleted_by_path, added_by_path, min_similarity=min_similarity)
    moved_old = {(match["old_path"], match["old_qualname"]) for match in renames}
    moved_new = {(match["new_path"], match["new_qualname"]) for match in renames}

    # --- 3. compare what is left, then merge --------------------------------
    file_changes: Dict[str, List[Dict[str, Any]]] = {}
    for layout in layouts:
        path = layout["path"]
        old_path = layout["old_path"]
        old_left = [
            entity
            for entity in old_by_path.get(old_path, [])
            if (old_path, entity.get("qualname")) not in moved_old
        ]
        new_left = [
            entity
            for entity in new_by_path.get(path, [])
            if (path, entity.get("qualname")) not in moved_new
        ]
        changes = compare_entities(
            old_left,
            new_left,
            include_unchanged=include_unchanged,
            with_details=with_details,
        )
        for change in changes:
            change["file_path"] = path
        if changes:
            file_changes[path] = changes

    rename_records = [_rename_record(match) for match in renames]
    for record in rename_records:
        file_changes.setdefault(record["new_path"], []).append(record)

    files: List[Dict[str, Any]] = []
    for path in sorted(file_changes):
        changes = sorted(file_changes[path], key=lambda change: change["line_start"])
        files.append(
            {
                "file_path": path,
                "language": language_for_path(path),
                "is_test_file": _is_test_file(path),
                "changes": changes,
            }
        )

    flat_changes = [change for entry in files for change in entry["changes"]]
    stats = summarize_changes(flat_changes)
    summary = {
        "files_changed": len(manifest["files"]),
        "files_analyzed": len(files),
        "files_skipped": len(skipped),
        "changes": stats["total"],
        "renames": len(rename_records),
        "by_change_type": stats["by_change_type"],
        "by_change_category": stats["by_category"],
        "warnings": len(warnings),
    }

    return {
        "schema_version": "2.0",
        "mode": "pr_ast",
        "repo": manifest["repo"],
        "mirror": {"out_dir": manifest["out_dir"], "counts": manifest["counts"]},
        "summary": summary,
        "files": files,
        "changes": flat_changes,
        "skipped": skipped,
        "warnings": warnings,
    }


def dumps(report: Dict[str, Any], indent: int = 2) -> str:
    """Stable serialisation (fixed key order, friendly to diffs and snapshot tests)."""
    return json.dumps(report, ensure_ascii=False, indent=indent, sort_keys=False)
