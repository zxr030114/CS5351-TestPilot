"""Resolve repository-relative source paths into import-module paths.

Member 4's adequacy analyzer (D) matches a change's ``file_path`` against the
module names appearing in a test's import statements, e.g. ``from payment import
withdraw``.  Member 1's analyzer (A) reports *repository-relative* paths such as
``mapping-demo/payment.py``.  Passing A's value straight to D does not raise:
D silently answers ``no_candidate_test`` because the module name never matches.

This module performs that conversion.  The rule is the one CPython itself uses
when resolving an import: walk up from the file's directory and keep every
directory that carries an ``__init__.py``; the first directory without one is an
import root and is dropped from the result.

    >>> to_module_path("mapping-demo/payment.py")          # no __init__.py above
    'payment.py'
    >>> to_module_path("src/pkg/mod.py")                   # src is the import root
    'pkg/mod.py'
    >>> to_module_path("pkg/sub/mod.py")                   # pkg/sub/__init__.py exists
    'pkg/sub/mod.py'

Directory knowledge requires the sources on disk, so pass ``project_root``.
Without it the result degrades to the file name, which is still correct for
flat layouts but wrong for packages.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path, PurePosixPath
from typing import Iterable, Sequence

__all__ = ["to_module_path", "normalize_path"]

_INIT = "__init__.py"


def normalize_path(file_path: str | os.PathLike[str]) -> PurePosixPath:
    """Return *file_path* as a clean POSIX path.

    Backslashes become slashes, a leading ``./`` is dropped, and repeated
    separators collapse.  Nothing is resolved against the filesystem.
    """
    raw = os.fspath(file_path)
    if not isinstance(raw, str):  # pragma: no cover - defensive
        raise TypeError("file_path must be a string path")
    text = raw.replace("\\", "/").strip()
    if not text:
        raise ValueError("file_path must be a non-empty path")
    while text.startswith("./"):
        text = text[2:]
    if not text:
        raise ValueError("file_path must not be empty after normalisation")
    return PurePosixPath(text)


def _strip_root(posix: PurePosixPath, project_root: str | os.PathLike[str] | None) -> PurePosixPath:
    """Make *posix* relative to *project_root* when the former is absolute.

    Absoluteness is judged on the host filesystem, not with POSIX rules: a
    Windows path such as ``D:/x/y.py`` is not "absolute" to ``PurePosixPath``
    yet must still be rebased onto the project root.
    """
    if project_root is None:
        return posix
    root = Path(os.fspath(project_root))
    candidate = Path(posix.as_posix())
    if not candidate.is_absolute():
        return posix
    try:
        return PurePosixPath(candidate.resolve().relative_to(root.resolve()).as_posix())
    except ValueError as exc:
        raise ValueError(f"path {posix} is outside project root {root}") from exc


def to_module_path(
    file_path: str | os.PathLike[str],
    *,
    project_root: str | os.PathLike[str] | None = None,
    import_roots: Sequence[str] = (),
) -> str:
    """Convert a repository-relative *file_path* to an import-module path.

    Args:
        file_path: Path as reported by Member 1, e.g. ``mapping-demo/payment.py``.
            Absolute paths are accepted when *project_root* is given.
        project_root: Directory the path is relative to; used to look for
            ``__init__.py``.  When omitted, the file name alone is returned.
        import_roots: Directory names that always act as import roots even when
            they contain ``__init__.py`` (``("src",)`` covers the common layout).

    Returns:
        A POSIX path suitable for Member 4's ``file_path`` field.

    Raises:
        ValueError: empty path, path outside *project_root*, or a path whose
            ``..`` segments climb above the root.
    """
    posix = _strip_root(normalize_path(file_path), project_root)
    parts = posix.parts
    if not parts:  # pragma: no cover - normalize_path already guards this
        raise ValueError("file_path must contain at least one path component")

    filename = parts[-1]
    directories = parts[:-1]

    if project_root is None:
        return filename

    climbing = [part for part in directories if part == ".."]
    if climbing:
        raise ValueError(f"file_path escapes the project root: {file_path}")

    root = Path(os.fspath(project_root))
    chain: list[str] = []
    current = root
    for name in directories:
        current = current / name
        if name in import_roots or not (current / _INIT).is_file():
            # This directory is an import root: everything above it is dropped.
            chain = []
        else:
            chain.append(name)

    return PurePosixPath(*chain, filename).as_posix()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("file_path", help="repository-relative path, e.g. mapping-demo/payment.py")
    parser.add_argument("--root", default=".", help="project root used to find __init__.py (default: .)")
    parser.add_argument(
        "--import-root",
        action="append",
        default=[],
        metavar="NAME",
        help="directory name that always counts as an import root (repeatable)",
    )
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    args = _build_parser().parse_args(list(argv) if argv is not None else None)
    try:
        result = to_module_path(args.file_path, project_root=args.root, import_roots=tuple(args.import_root))
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(result)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
