"""Unit tests for old/new AST comparison (add / delete / modify + change_category).

Run: ``python -m unittest discover -s tests -v``
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from analyzer import (  # noqa: E402
    CATEGORY_DOCUMENTATION,
    CATEGORY_LOGIC,
    CATEGORY_SIGNATURE,
    compare_sources,
    summarize_changes,
)

STRICT_KEYS = [
    "entity_type",
    "entity_name",
    "change_type",
    "change_category",
    "line_start",
    "line_end",
    "summary",
]


def diff(old: str, new: str, *, file_path: str = "src/utils.py", **kwargs):
    return compare_sources(old, new, file_path=file_path, **kwargs)["changes"]


class LogicChangeTests(unittest.TestCase):
    OLD = """
def total(items):
    result = 0
    for item in items:
        result += item
    return result
"""

    NEW = """
def total(items):
    return sum(items)
"""

    def test_body_change_is_logic(self) -> None:
        changes = diff(self.OLD, self.NEW)
        self.assertEqual(len(changes), 1)
        change = changes[0]
        self.assertEqual(change["change_type"], "modify")
        self.assertEqual(change["change_category"], CATEGORY_LOGIC)
        self.assertEqual(change["entity_name"], "total")
        self.assertIn("function body", change["summary"])

    def test_comment_only_change_is_not_a_change(self) -> None:
        commented = """
def total(items):
    # only one comment line added
    result = 0
    for item in items:
        result += item
    return result
"""
        self.assertEqual(diff(self.OLD, commented), [])

    def test_reformat_only_change_is_not_a_change(self) -> None:
        reformatted = """
def total(items):
    result = 0

    for item in items:
        result += item

    return result
"""
        self.assertEqual(diff(self.OLD, reformatted), [])


class SignatureChangeTests(unittest.TestCase):
    OLD = """
def format_price(value):
    text = f"{value:.2f}"
    return f"${text}"
"""

    NEW = """
def format_price(value, currency="USD"):
    text = f"{value:.2f}"
    return f"${text}"
"""

    def test_parameter_added_is_signature_change(self) -> None:
        changes = diff(self.OLD, self.NEW)
        self.assertEqual(len(changes), 1)
        self.assertEqual(changes[0]["change_category"], CATEGORY_SIGNATURE)
        self.assertIn("parameters", changes[0]["summary"])

    def test_return_annotation_is_signature_change(self) -> None:
        annotated = self.OLD.replace("def format_price(value):", "def format_price(value) -> str:")
        changes = diff(self.OLD, annotated)
        self.assertEqual(changes[0]["change_category"], CATEGORY_SIGNATURE)
        self.assertIn("return type", changes[0]["summary"])

    def test_decorator_is_signature_change(self) -> None:
        decorated = "import functools\n\n@functools.lru_cache(maxsize=None)\n" + self.OLD.strip() + "\n"
        changes = diff(self.OLD, decorated)
        self.assertEqual(len(changes), 1)
        self.assertEqual(changes[0]["change_category"], CATEGORY_SIGNATURE)
        self.assertIn("decorators", changes[0]["summary"])

    def test_signature_and_body_changed_prefers_signature(self) -> None:
        both = self.NEW.replace('f"${text}"', 'f"{currency}{text}"')
        changes = diff(self.OLD, both)
        self.assertEqual(changes[0]["change_category"], CATEGORY_SIGNATURE)
        self.assertIn("body also changed", changes[0]["summary"])


class DocumentationChangeTests(unittest.TestCase):
    OLD = '''
def validate(user):
    """Validate fields."""
    return bool(user.get("id"))


class Account:
    """A bank account."""

    def balance(self):
        return 0
'''

    NEW = '''
def validate(user):
    """Validate that all fields are present."""
    return bool(user.get("id"))


class Account:
    """A bank account (multi-currency)."""

    def balance(self):
        return 0
'''

    def test_docstring_only_is_documentation_change(self) -> None:
        changes = diff(self.OLD, self.NEW)
        self.assertEqual([c["entity_name"] for c in changes], ["validate", "Account"])
        for change in changes:
            self.assertEqual(change["change_category"], CATEGORY_DOCUMENTATION)
            self.assertEqual(change["change_type"], "modify")

    def test_docstring_whitespace_change_is_ignored(self) -> None:
        wrapped = self.OLD.replace('"""Validate fields."""', '"""Validate fields.\n\n    One more paragraph does not matter."""')
        self.assertNotEqual(wrapped, self.OLD)
        # The text changed, so this is a Documentation change, not "unchanged"
        changes = diff(self.OLD, wrapped)
        self.assertEqual(changes[0]["change_category"], CATEGORY_DOCUMENTATION)


class AddDeleteTests(unittest.TestCase):
    OLD = "def keep():\n    return 1\n\ndef gone():\n    return 2\n"
    NEW = "def keep():\n    return 1\n\ndef fresh():\n    return 3\n"

    def test_add_and_delete(self) -> None:
        changes = diff(self.OLD, self.NEW)
        by_name = {c["entity_name"]: c for c in changes}
        self.assertEqual(by_name["fresh"]["change_type"], "add")
        self.assertEqual(by_name["gone"]["change_type"], "delete")
        self.assertIsNone(by_name["fresh"]["change_category"])
        self.assertIsNone(by_name["gone"]["change_category"])
        self.assertEqual(by_name["fresh"]["summary"], "new function added")
        self.assertEqual(by_name["gone"]["summary"], "function removed")
        self.assertNotIn("keep", by_name)

    def test_rename_is_delete_plus_add(self) -> None:
        renamed = self.OLD.replace("def gone():", "def gone_v2():")
        changes = diff(self.OLD, renamed)
        self.assertEqual(
            sorted(c["change_type"] for c in changes), ["add", "delete"]
        )


class SchemaAndOrderingTests(unittest.TestCase):
    OLD = """
def a():
    return 1


def b(x):
    return x


def c():
    return 3
"""

    NEW = """
def a():
    return 2


def b(x, y):
    return x


def c():
    '''New docs.'''
    return 3
"""

    def test_strict_record_has_exactly_seven_keys(self) -> None:
        changes = diff(self.OLD, self.NEW)
        self.assertTrue(changes)
        for change in changes:
            self.assertEqual(list(change.keys()), STRICT_KEYS)

    def test_file_level_shape(self) -> None:
        result = compare_sources(self.OLD, self.NEW, file_path="pkg/mod.py")
        self.assertEqual(list(result.keys()), ["file_path", "changes"])
        self.assertEqual(result["file_path"], "pkg/mod.py")

    def test_ordering_by_line(self) -> None:
        changes = diff(self.OLD, self.NEW)
        starts = [c["line_start"] for c in changes]
        self.assertEqual(starts, sorted(starts))
        self.assertEqual([c["entity_name"] for c in changes], ["a", "b", "c"])

    def test_include_unchanged(self) -> None:
        unchanged = "def keep():\n    return 1\n"
        self.assertEqual(diff(unchanged, unchanged), [])
        records = diff(unchanged, unchanged, include_unchanged=True)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["change_type"], "unchanged")

    def test_with_details_adds_fields_without_disturbing_schema(self) -> None:
        change = diff(self.OLD, self.NEW, with_details=True)[0]
        self.assertEqual(list(change.keys())[:7], STRICT_KEYS)
        self.assertIn("reasons", change)
        self.assertIn("qualname", change)
        self.assertIn("old_node_hash", change)

    def test_summarize_changes(self) -> None:
        summary = summarize_changes(diff(self.OLD, self.NEW))
        self.assertEqual(summary["total"], 3)
        self.assertEqual(summary["by_change_type"], {"modify": 3})
        self.assertEqual(
            summary["by_category"],
            {CATEGORY_LOGIC: 1, CATEGORY_SIGNATURE: 1, CATEGORY_DOCUMENTATION: 1},
        )


class MethodAndClassTests(unittest.TestCase):
    OLD = """
class Calculator:
    def add(self, a, b):
        return a + b

    def reset(self):
        self.value = 0
"""

    NEW = """
class Calculator:
    def add(self, a, b, c=0):
        return a + b

    def reset(self):
        self.value = 0
        self.count = 0
"""

    def test_method_qualname_and_category(self) -> None:
        changes = diff(self.OLD, self.NEW)
        by_name = {c["entity_name"]: c for c in changes}
        self.assertEqual(by_name["add"]["change_category"], CATEGORY_SIGNATURE)
        self.assertEqual(by_name["reset"]["change_category"], CATEGORY_LOGIC)
        self.assertEqual(by_name["add"]["entity_type"], "method")
        detailed = diff(self.OLD, self.NEW, with_details=True)
        self.assertEqual(
            {c["entity_name"]: c["qualname"] for c in detailed},
            {"add": "Calculator.add", "reset": "Calculator.reset"},
        )


class JavaTests(unittest.TestCase):
    OLD = """
public class Calc {
    public int add(int a, int b) {
        return a + b;
    }
}
"""

    NEW_PARAMS = """
public class Calc {
    public int add(int a, int b, int c) {
        return a + b;
    }
}
"""

    NEW_BODY = """
public class Calc {
    public int add(int a, int b) {
        return b + a;
    }
}
"""

    NEW_JAVADOC = """
public class Calc {
    /** Add two numbers. */
    public int add(int a, int b) {
        return a + b;
    }
}
"""

    def test_java_parameter_change(self) -> None:
        change = diff(self.OLD, self.NEW_PARAMS, file_path="Calc.java")[0]
        self.assertEqual(change["change_category"], CATEGORY_SIGNATURE)
        self.assertEqual(change["entity_type"], "method")

    def test_java_body_change(self) -> None:
        change = diff(self.OLD, self.NEW_BODY, file_path="Calc.java")[0]
        self.assertEqual(change["change_category"], CATEGORY_LOGIC)

    def test_java_javadoc_change_is_invisible_to_ast(self) -> None:
        # A javadoc is a comment node outside the method subtree: AST comparison cannot see it.
        # Known limitation; Java doc changes must be recovered from the diff layer.
        self.assertEqual(diff(self.OLD, self.NEW_JAVADOC, file_path="Calc.java"), [])


if __name__ == "__main__":
    unittest.main()
