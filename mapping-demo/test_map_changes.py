"""Offline integration checks for change-to-test mapping."""

import hashlib
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from map_changes import map_report
from testmon.process_code import Module, checksums_to_blob, read_source_sha


SOURCE = '''def withdraw(balance, amount):
    if amount < 0:
        raise ValueError("Amount must be non-negative")
    if amount > balance:
        raise ValueError("Insufficient balance")
    return round(balance - amount, 2)


def deposit(balance, amount):
    return balance + amount
'''


class MappingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="testpilot-tests-")
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name)
        self.root = self.repo / "mapping-demo"
        self.root.mkdir()
        self.source = self.root / "payment.py"
        self.source.write_text(SOURCE, encoding="utf-8")
        self.run_git("init", "-q")
        self.run_git("add", "mapping-demo/payment.py")
        self.run_git("-c", "user.name=TestPilot Tests", "-c", "user.email=tests@example.invalid", "commit", "-qm", "Baseline")
        self.sha = self.run_git("rev-parse", "HEAD").strip()
        source, fsha = read_source_sha(self.source)
        module = Module(source_code=source, fs_fsha=fsha)
        checksums = {b.name: c for b, c in zip(module.blocks, module.method_checksums)}
        self.db = self.root / ".testmondata"
        with closing(sqlite3.connect(self.db)) as con:
            con.executescript('''
                PRAGMA user_version=14;
                CREATE TABLE environment(id INTEGER PRIMARY KEY, environment_name TEXT);
                INSERT INTO environment VALUES(1, 'default');
                CREATE TABLE test_execution(id INTEGER PRIMARY KEY, environment_id INTEGER, test_name TEXT);
                CREATE TABLE file_fp(id INTEGER PRIMARY KEY, filename TEXT, method_checksums BLOB, fsha TEXT);
                CREATE TABLE test_execution_file_fp(test_execution_id INTEGER, fingerprint_id INTEGER);
            ''')
            for index, name in enumerate(["normal_withdraw", "insufficient_balance", "negative_amount", "deposit"], 1):
                function = "deposit" if name == "deposit" else "withdraw"
                con.execute("INSERT INTO test_execution VALUES(?,1,?)", (index, f"test_payment.py::test_{name}"))
                blob = checksums_to_blob([checksums[function], module.method_checksums[-1]])
                con.execute("INSERT INTO file_fp VALUES(?,?,?,?)", (index, "payment.py", blob, fsha))
                con.execute("INSERT INTO test_execution_file_fp VALUES(?,?)", (index, index))
            con.commit()

    def run_git(self, *args):
        return subprocess.check_output(["git", "-C", str(self.repo), *args], text=True)

    def change(self, **overrides):
        return {"file_path": "mapping-demo/payment.py", "entity_name": "withdraw",
                "entity_type": "function", "change_type": "modify",
                "change_category": "Logic change", "line_start": 1, "line_end": 6,
                "summary": "Function body changed", **overrides}

    def map(self, *changes):
        return map_report({"schema_version": "2.0", "repo": {"base_sha": self.sha},
                           "changes": list(changes)}, self.repo, self.db, self.root)

    def test_changed_head_uses_baseline_and_does_not_modify_database(self):
        before = hashlib.sha256(self.db.read_bytes()).hexdigest()
        self.source.write_text(SOURCE.replace("amount, 2", "amount, 1"), encoding="utf-8")
        result = self.map(self.change())
        self.assertEqual(len(result["selected_tests"]), 3)
        self.assertEqual(result["changes"][0]["status"], "matched")
        self.assertEqual(before, hashlib.sha256(self.db.read_bytes()).hexdigest())

    def test_signature_selects_all_file_dependencies(self):
        result = self.map(self.change(change_category="Signature change"))
        self.assertEqual(len(result["selected_tests"]), 4)
        self.assertEqual(result["changes"][0]["status"], "file_fallback")

    def test_documentation_is_skipped(self):
        result = self.map(self.change(change_category="Documentation change"))
        self.assertEqual(result["selected_tests"], [])
        self.assertTrue(result["selection_complete"])

    def test_new_code_requires_tests(self):
        result = self.map(self.change(change_type="add", entity_name="refund", change_category=None))
        self.assertEqual(result["changes"][0]["status"], "needs_new_tests")
        self.assertFalse(result["selection_complete"])

    def test_missing_history_is_unresolved(self):
        result = self.map(self.change(file_path="mapping-demo/missing.py"))
        self.assertEqual(result["changes"][0]["status"], "unresolved")
        self.assertFalse(result["selection_complete"])

    def test_rename_queries_old_key_and_proposes_alias(self):
        result = self.map(self.change(change_type="rename", file_path="mapping-demo/billing.py",
            old_path="mapping-demo/payment.py", old_qualname="withdraw", new_qualname="pay",
            change_category="Rename / Move"))
        self.assertEqual(len(result["selected_tests"]), 3)
        self.assertEqual(result["changes"][0]["mapping_alias"]["new_qualname"], "pay")

    def test_deleted_function_uses_old_source(self):
        self.source.unlink()
        self.assertEqual(len(self.map(self.change(change_type="delete"))["selected_tests"]), 3)

    def test_unknown_function_uses_conservative_fallback(self):
        result = self.map(self.change(entity_name="ambiguous_or_missing"))
        self.assertEqual(result["changes"][0]["status"], "file_fallback")
        self.assertEqual(len(result["selected_tests"]), 4)

    def test_wrong_schema_is_rejected(self):
        with self.assertRaises(ValueError):
            map_report({"schema_version": "1.0", "changes": []}, self.repo, self.db, self.root)

    def test_unsafe_path_is_unresolved(self):
        result = self.map(self.change(file_path="../escape.py"))
        self.assertEqual(result["changes"][0]["status"], "unresolved")
        self.assertFalse(result["selection_complete"])

    def test_empty_report_selects_nothing(self):
        result = self.map()
        self.assertEqual(result["selected_tests"], [])
        self.assertTrue(result["selection_complete"])


if __name__ == "__main__":
    unittest.main()
