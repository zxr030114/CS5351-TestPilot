"""Tests for ``module_path.to_module_path``.

The fixture tree is built on disk at setUpClass time because the resolver reads
``__init__.py`` markers from the filesystem.  ``tempfile`` is deliberately
avoided: on the Windows machine this project is developed on, ``mkdtemp``
creates a directory the current process cannot afterwards write into, which
makes every such test fail for reasons unrelated to the code under test.
"""

from __future__ import annotations

import shutil
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from module_path import normalize_path, to_module_path  # noqa: E402


class ModulePathTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.root = Path(__file__).resolve().parent / "_tree"
        if cls.root.exists():
            shutil.rmtree(cls.root)
        # flat layout: no package markers anywhere
        cls._write("flat/payment.py")
        # plain package chain
        cls._write("pkg/__init__.py")
        cls._write("pkg/mod.py")
        cls._write("pkg/sub/__init__.py")
        cls._write("pkg/sub/deep.py")
        # package above, non-package below: the lower directory is the import root
        cls._write("mixed/__init__.py")
        cls._write("mixed/plain/mod.py")
        # src layout without __init__.py next to src
        cls._write("src/pkg2/__init__.py")
        cls._write("src/pkg2/mod.py")
        # src-like directory that *is* itself a package (needs import_roots)
        cls._write("srcpkg/__init__.py")
        cls._write("srcpkg/pkg3/__init__.py")
        cls._write("srcpkg/pkg3/mod.py")
        cls._write("root_mod.py")

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.root, ignore_errors=True)

    @classmethod
    def _write(cls, relative: str) -> None:
        path = cls.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("", encoding="utf-8")

    def module(self, relative: str, **kwargs) -> str:
        return to_module_path(relative, project_root=self.root, **kwargs)

    # -- layout rules -----------------------------------------------------

    def test_flat_file_keeps_only_name(self) -> None:
        self.assertEqual(self.module("flat/payment.py"), "payment.py")

    def test_top_level_file_keeps_only_name(self) -> None:
        self.assertEqual(self.module("root_mod.py"), "root_mod.py")

    def test_package_chain_is_kept(self) -> None:
        self.assertEqual(self.module("pkg/sub/deep.py"), "pkg/sub/deep.py")

    def test_single_package(self) -> None:
        self.assertEqual(self.module("pkg/mod.py"), "pkg/mod.py")

    def test_non_package_directory_resets_chain(self) -> None:
        # mixed/ has __init__.py but mixed/plain/ does not, so plain/ is the root.
        self.assertEqual(self.module("mixed/plain/mod.py"), "mod.py")

    def test_src_layout_drops_src(self) -> None:
        self.assertEqual(self.module("src/pkg2/mod.py"), "pkg2/mod.py")

    def test_import_roots_override_package_detection(self) -> None:
        self.assertEqual(
            self.module("srcpkg/pkg3/mod.py", import_roots=("srcpkg",)),
            "pkg3/mod.py",
        )

    def test_import_roots_do_not_affect_unrelated_paths(self) -> None:
        self.assertEqual(self.module("pkg/mod.py", import_roots=("src",)), "pkg/mod.py")

    # -- input shapes -----------------------------------------------------

    def test_backslashes_are_normalised(self) -> None:
        self.assertEqual(self.module("pkg\\sub\\deep.py"), "pkg/sub/deep.py")

    def test_leading_dot_slash_is_dropped(self) -> None:
        self.assertEqual(self.module("./pkg/mod.py"), "pkg/mod.py")

    def test_absolute_path_is_made_relative_to_root(self) -> None:
        absolute = self.root / "pkg" / "sub" / "deep.py"
        self.assertEqual(self.module(str(absolute)), "pkg/sub/deep.py")

    def test_missing_project_root_falls_back_to_file_name(self) -> None:
        self.assertEqual(to_module_path("pkg/sub/deep.py"), "deep.py")

    def test_normalize_path_returns_posix(self) -> None:
        self.assertEqual(normalize_path("a\\b\\c.py").as_posix(), "a/b/c.py")

    # -- rejections -------------------------------------------------------

    def test_empty_path_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            to_module_path("   ", project_root=self.root)

    def test_parent_escape_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            to_module_path("../outside.py", project_root=self.root)

    def test_absolute_path_outside_root_is_rejected(self) -> None:
        outside = Path(__file__).resolve().parents[2] / "elsewhere.py"
        with self.assertRaises(ValueError):
            to_module_path(str(outside), project_root=self.root)

    # -- real-world sample from the project --------------------------------

    def test_payment_demo_case(self) -> None:
        """The exact conversion that broke Member 4 without an error message."""
        self.assertEqual(self.module("mapping-demo/payment.py"), "payment.py")


if __name__ == "__main__":
    unittest.main()
