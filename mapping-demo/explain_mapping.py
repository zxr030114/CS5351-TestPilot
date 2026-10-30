"""Explain function-body dependencies stored by pytest-testmon 2.2 (DB v14).

Run from the testmon project root, after collecting a baseline with --testmon.
This is a dependency query, not a replacement for testmon's test selection.
"""

import argparse
import ast
import json
import sqlite3
from contextlib import closing
from pathlib import Path

from testmon.process_code import Module, blob_to_checksums, read_source_sha


def find_function(tree, qualname):
    matches = []

    def visit(node, prefix=""):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                name = f"{prefix}.{child.name}" if prefix else child.name
                if name == qualname and not isinstance(child, ast.ClassDef):
                    matches.append(child)
                visit(child, name)
            else:
                visit(child, prefix)

    visit(tree)
    if len(matches) != 1:
        raise ValueError(f"Expected one function named {qualname!r}; found {len(matches)}")
    return matches[0]


def explain(db_path, source_path, function, environment="default"):
    db_path = Path(db_path).resolve()
    source_path = Path(source_path).resolve()
    if not db_path.is_file():
        raise ValueError("No dependency database. Run pytest --testmon first.")
    if not source_path.is_file() or source_path.suffix != ".py":
        raise ValueError("Source must be an existing Python file.")
    try:
        filename = source_path.relative_to(db_path.parent).as_posix()
    except ValueError as exc:
        raise ValueError("Source must be inside the testmon database's project root.") from exc

    source, fsha = read_source_sha(source_path)
    node = find_function(ast.parse(source), function)
    module = Module(source_code=source, fs_fsha=fsha)
    # testmon stores function bodies separately from definitions and module code.
    blocks = [
        (block, checksum)
        for block, checksum in zip(module.blocks, module.method_checksums)
        if node.body[0].lineno <= block.start <= node.end_lineno
    ]
    if not blocks:
        raise ValueError("No testmon body blocks found for the requested function.")

    report = {
        "file_path": filename,
        "function": function,
        "environment": environment,
        "evidence_level": "testmon_block_fingerprint",
        "status": "no_matching_dependency",
        "tests": [],
        "unresolved_tests": [],
        "limitations": [
            "Block execution evidence does not prove which branch was taken.",
            "This query does not reconstruct a runtime call chain.",
            "Unmatched historical fingerprints may require the baseline source revision.",
            "A CRC32 match uses testmon's fingerprint semantics, not proof of equivalence.",
        ],
    }
    # Avoid testmon.DB: its constructor can recreate databases of other versions.
    with closing(sqlite3.connect(db_path.as_uri() + "?mode=ro", uri=True)) as con:
        if con.execute("PRAGMA user_version").fetchone()[0] != 14:
            raise ValueError("Unsupported database version; expected testmon DB v14.")
        env = con.execute(
            "SELECT id FROM environment WHERE environment_name=? ORDER BY id DESC LIMIT 1",
            (environment,),
        ).fetchone()
        if env is None:
            raise ValueError(f"No recorded environment named {environment!r}")
        rows = con.execute(
            """SELECT te.test_name, f.method_checksums, f.fsha
               FROM test_execution AS te
               JOIN test_execution_file_fp AS link ON link.test_execution_id=te.id
               JOIN file_fp AS f ON f.id=link.fingerprint_id
               WHERE te.environment_id=? AND replace(f.filename, char(92), '/')=?
               ORDER BY te.test_name""",
            (env[0], filename),
        ).fetchall()

    by_test = {}
    unresolved = set()
    all_current_checksums = set(module.method_checksums)
    for test_name, blob, historical_fsha in rows:
        recorded = set(blob_to_checksums(blob))
        evidence = [
            {"block": block.name, "line_start": block.start,
             "line_end": block.end, "checksum": checksum}
            for block, checksum in blocks if checksum in recorded
        ]
        if evidence:
            by_test[test_name] = {
                "test": test_name,
                "reason": "Recorded dependency contains the requested function body fingerprint.",
                "same_file_revision": historical_fsha == fsha,
                "blocks": evidence,
            }
        elif recorded - all_current_checksums:
            unresolved.add(test_name)
    report["tests"] = [by_test[name] for name in sorted(by_test)]
    report["unresolved_tests"] = sorted(unresolved - by_test.keys())
    if report["tests"]:
        report["status"] = "matched_with_gaps" if report["unresolved_tests"] else "matched"
    elif report["unresolved_tests"]:
        report["status"] = "baseline_source_required"
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=".testmondata")
    parser.add_argument("--source", default="payment.py")
    parser.add_argument("--function", required=True)
    parser.add_argument("--environment", default="default")
    args = parser.parse_args()
    try:
        result = explain(args.db, args.source, args.function, args.environment)
    except (ValueError, OSError, SyntaxError, sqlite3.Error) as exc:
        parser.exit(2, f"Mapping error: {exc}\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
