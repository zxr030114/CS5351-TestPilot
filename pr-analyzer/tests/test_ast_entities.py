"""Offline unit tests for AST entity extraction (no network).

How to run (either one):

    python -m unittest discover -s tests -v
    python -m pytest tests -v
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from analyzer import (  # noqa: E402
    ASTError,
    LANGUAGE_SPECS,
    entities_by_qualname,
    extract_entities,
    language_for_path,
    language_spec,
    parse_file,
    parse_to_ast,
    summarize_entities,
)

PY_SOURCE = '''"""Bank account module (demo)."""
import math


def _audit(message):
    print(message)


class Account:
    """A bank account."""

    rate = 0.03

    def __init__(self, owner, balance=0.0):
        self.owner = owner
        self.balance = balance

    def deposit(self, amount):
        if amount <= 0:
            raise ValueError("amount must be positive")
        self.balance += amount
        _audit(f"deposit {amount}")
        return self.balance

    @property
    def rich(self):
        def _threshold():
            return 1000.0

        return self.balance > _threshold()


class Savings(Account):
    def withdraw(self, amount):
        self.balance -= amount
        return self.balance
'''

JAVA_SOURCE = '''package demo;

/** A simple account. */
public class Account {
    private double balance;

    public Account(double balance) {
        this.balance = balance;
    }

    public double deposit(double amount) {
        if (amount <= 0) {
            throw new IllegalArgumentException("amount must be positive");
        }
        this.balance += amount;
        return this.balance;
    }

    private static void audit(String message) {
        System.out.println(message);
    }
}
'''


class PythonEntityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.doc = parse_to_ast(PY_SOURCE, "python")
        self.entities = extract_entities(self.doc)
        self.by_name = entities_by_qualname(self.entities)

    def test_parse_ok(self) -> None:
        self.assertEqual(self.doc.language, "python")
        self.assertFalse(self.doc.has_error)

    def test_qualnames_and_types(self) -> None:
        got = {e["qualname"]: e["entity_type"] for e in self.entities}
        self.assertEqual(
            got,
            {
                "_audit": "function",
                "Account": "class",
                "Account.__init__": "method",
                "Account.deposit": "method",
                "Account.rich": "method",
                "Account.rich._threshold": "function",
                "Savings": "class",
                "Savings.withdraw": "method",
            },
        )

    def test_nested_function_is_not_method(self) -> None:
        # A function inside a method is a nested function, not a method
        self.assertEqual(self.by_name["Account.rich._threshold"]["entity_type"], "function")
        self.assertEqual(
            self.by_name["Account.rich._threshold"]["parent_qualname"], "Account.rich"
        )

    def test_line_ranges_match_source_slice(self) -> None:
        lines = PY_SOURCE.splitlines()
        for entity in self.entities:
            expected = "\n".join(lines[entity["line_start"] - 1 : entity["line_end"]])
            self.assertEqual(entity["node_text"], expected, entity["qualname"])

    def test_ordered_by_position(self) -> None:
        starts = [entity["start_byte"] for entity in self.entities]
        self.assertEqual(starts, sorted(starts))
        self.assertTrue(all(e["start_byte"] < e["end_byte"] for e in self.entities))

    def test_summary(self) -> None:
        stats = summarize_entities(self.entities)
        self.assertEqual(stats["total"], 8)
        self.assertEqual(stats["by_type"], {"class": 2, "function": 2, "method": 4})
        self.assertEqual(stats["languages"], ["python"])
        self.assertEqual(stats["errors"], 0)


class HashSemanticsTests(unittest.TestCase):
    def hashes(self, source: str, language: str = "python"):
        return {
            e["qualname"]: e["node_hash"]
            for e in extract_entities(parse_to_ast(source, language))
        }

    def test_reparse_is_stable(self) -> None:
        self.assertEqual(self.hashes(PY_SOURCE), self.hashes(PY_SOURCE))

    def test_comments_blank_lines_and_indent_do_not_change_hash(self) -> None:
        reformatted = PY_SOURCE.replace(
            "    def deposit(self, amount):\n        if amount <= 0:",
            "    def deposit(self, amount):\n        # one extra comment\n\n        if amount <= 0:",
        )
        self.assertNotEqual(reformatted, PY_SOURCE)
        self.assertEqual(self.hashes(reformatted), self.hashes(PY_SOURCE))

    def test_docstring_change_changes_hash(self) -> None:
        mutated = PY_SOURCE.replace('"""A bank account."""', '"""A bank account with interest."""')
        before, after = self.hashes(PY_SOURCE), self.hashes(mutated)
        self.assertNotEqual(before["Account"], after["Account"])
        self.assertEqual(before["_audit"], after["_audit"])

    def test_semantic_change_is_local(self) -> None:
        mutated = PY_SOURCE.replace("amount <= 0", "amount < 0")
        before, after = self.hashes(PY_SOURCE), self.hashes(mutated)
        self.assertNotEqual(before["Account.deposit"], after["Account.deposit"])
        # Only an operator inside deposit changes; other entities must not
        for qualname in ("_audit", "Account.__init__", "Account.rich", "Savings.withdraw"):
            self.assertEqual(before[qualname], after[qualname], qualname)

    def test_added_and_removed_entities(self) -> None:
        mutated = PY_SOURCE.replace(
            "class Savings(Account):\n",
            "class Savings(Account):\n    def total(self):\n        return self.balance\n\n",
        )
        after = self.hashes(mutated)
        self.assertIn("Savings.total", after)
        self.assertNotEqual(self.hashes(PY_SOURCE).get("Savings.total"), after["Savings.total"])


class JavaEntityTests(unittest.TestCase):
    def test_java_entities(self) -> None:
        doc = parse_to_ast(JAVA_SOURCE, "java")
        self.assertFalse(doc.has_error)
        entities = extract_entities(doc)
        got = {e["qualname"]: e["entity_type"] for e in entities}
        self.assertEqual(
            got,
            {
                "Account": "class",
                "Account.Account": "method",  # constructor
                "Account.deposit": "method",
                "Account.audit": "method",
            },
        )
        lines = JAVA_SOURCE.splitlines()
        for entity in entities:
            expected = "\n".join(lines[entity["line_start"] - 1 : entity["line_end"]])
            self.assertEqual(entity["node_text"], expected, entity["qualname"])


class ApiBehaviourTests(unittest.TestCase):
    def test_include_flags(self) -> None:
        doc = parse_to_ast(PY_SOURCE, "python")
        no_methods = extract_entities(doc, include_methods=False)
        self.assertNotIn("method", {e["entity_type"] for e in no_methods})
        self.assertIn("Account", {e["qualname"] for e in no_methods})

        flat = extract_entities(doc, include_nested=False)
        self.assertNotIn("Account.rich._threshold", {e["qualname"] for e in flat})

    def test_language_lookup(self) -> None:
        self.assertEqual(language_for_path("pkg/mod.py"), "python")
        self.assertEqual(language_for_path("src/Main.java"), "java")
        self.assertEqual(language_for_path("README.md"), "unknown")
        self.assertEqual(language_spec("py").name, "python")
        self.assertIn("python", LANGUAGE_SPECS)
        with self.assertRaises(ASTError):
            language_spec("cobol")

    def test_parse_rejects_wrong_type(self) -> None:
        with self.assertRaises(ASTError):
            parse_to_ast(123, "python")  # type: ignore[arg-type]

    def test_syntax_error_is_flagged_but_entities_survive(self) -> None:
        doc = parse_to_ast("def broken(:\n    pass\n", "python")
        self.assertTrue(doc.has_error)

    def test_parse_file(self) -> None:
        path = Path(__file__).resolve().parent.parent / "analyzer.py"
        doc = parse_file(str(path))
        self.assertEqual(doc.language, "python")
        self.assertFalse(doc.has_error)
        self.assertTrue(extract_entities(doc))


if __name__ == "__main__":
    unittest.main(verbosity=2)
