"""Map Schema 2.0 changes to historical testmon dependencies (DB v14).

Run from the repository root. --local-file uses Member 1's compare_sources API.
The database must describe the base revision; do not refresh it on head first.
"""

import argparse
import ast
import json
import sqlite3
import subprocess
import sys
import tempfile
from contextlib import closing
from pathlib import Path, PurePosixPath

from explain_mapping import explain


def git(repo, *args):
    result = subprocess.run(["git", "-C", str(repo), *args], capture_output=True)
    if result.returncode:
        raise ValueError(result.stderr.decode("utf-8", "replace").strip())
    return result.stdout


def safe_path(value):
    if not isinstance(value, str) or not value or "\\" in value or ":" in value:
        raise ValueError("Expected a repository-relative POSIX path")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or str(path) == ".":
        raise ValueError("Unsafe or empty repository path")
    return path


def resolve_function(source, name):
    candidates = []

    def walk(node, prefix=""):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                qualified = f"{prefix}.{child.name}" if prefix else child.name
                if not isinstance(child, ast.ClassDef):
                    if qualified == name or ("." not in name and child.name == name):
                        candidates.append(qualified)
                walk(child, qualified)
            else:
                walk(child, prefix)

    walk(ast.parse(source))
    if len(candidates) != 1:
        raise ValueError(f"Cannot uniquely identify baseline function {name!r}")
    return candidates[0]


def load_dependencies(db, environment):
    if not db.is_file():
        raise ValueError("No testmon database; collect a baseline first")
    with closing(sqlite3.connect(db.as_uri() + "?mode=ro", uri=True)) as con:
        if con.execute("PRAGMA user_version").fetchone()[0] != 14:
            raise ValueError("Expected testmon DB v14")
        row = con.execute(
            "SELECT id FROM environment WHERE environment_name=? ORDER BY id DESC LIMIT 1",
            (environment,),
        ).fetchone()
        if row is None:
            raise ValueError(f"Unknown testmon environment: {environment}")
        dependencies = {}
        for filename, test in con.execute(
            """SELECT f.filename, te.test_name FROM test_execution te
               JOIN test_execution_file_fp link ON link.test_execution_id=te.id
               JOIN file_fp f ON f.id=link.fingerprint_id
               WHERE te.environment_id=?""", (row[0],)
        ):
            dependencies.setdefault(filename.replace("\\", "/"), set()).add(test)
    return dependencies


def map_report(report, repo, db, test_root, base=None, environment="default"):
    if not isinstance(report, dict) or report.get("schema_version") != "2.0" or not isinstance(report.get("changes"), list):
        raise ValueError("Expected a Schema 2.0 report with a changes list")
    repo, db, test_root = Path(repo).resolve(), Path(db).resolve(), Path(test_root).resolve()
    prefix = test_root.relative_to(repo)
    if db.parent != test_root:
        raise ValueError("--test-root must be the pytest root that owns --db")
    revision = base or report.get("repo", {}).get("base_sha")
    if not revision:
        raise ValueError("Provide report.repo.base_sha or --base")
    sha = git(repo, "rev-parse", "--verify", "--end-of-options", f"{revision}^{{commit}}").decode().strip()
    dependencies = load_dependencies(db, environment)

    def nodeid(test):
        path, separator, name = test.partition("::")
        return (prefix / Path(path)).as_posix() + separator + name

    output = {
        "schema_version": "1.0", "mode": "test_dependency_mapping",
        "base_sha": sha, "environment": environment,
        "selected_tests": [], "selection_complete": True, "changes": [],
        "limitations": [
            "Database provenance is not tied to Git: collect it on the base revision.",
            "Historical block evidence does not prove branch execution or a call chain.",
            "File-level fallbacks deliberately include extra tests.",
            "Missing mappings are unresolved, not proof that no tests are needed.",
        ],
    }
    selected = set()
    with tempfile.TemporaryDirectory(prefix="testpilot-mapping-") as temp:
        snapshot = Path(temp) / ".testmondata"
        # SQLite backup includes committed WAL data and leaves the source unchanged.
        with closing(sqlite3.connect(db.as_uri() + "?mode=ro", uri=True)) as src:
            with closing(sqlite3.connect(snapshot)) as dst:
                src.backup(dst)
        for change in report["changes"]:
            if not isinstance(change, dict):
                raise ValueError("Each change must be an object")
            item = {"change": change, "status": "unresolved", "tests": []}
            output["changes"].append(item)
            try:
                path = safe_path(change["file_path"])
                kind = change["change_type"]
                category = change.get("change_category")
                if kind not in {"add", "delete", "modify", "rename"}:
                    raise ValueError(f"Unsupported change type: {kind}")
                if category == "Documentation change":
                    item["status"] = "skipped_documentation"
                    continue
                if kind == "add":
                    item["status"] = "needs_new_tests"
                    output["selection_complete"] = False
                    continue
                if path.suffix != ".py":
                    raise ValueError("Only Python mapping is supported")
                old_path = safe_path(change.get("old_path", str(path))) if kind == "rename" else path
                relative = Path(str(old_path)).relative_to(prefix).as_posix()
                available = dependencies.get(relative, set())
                if not available:
                    raise ValueError("No historical file dependency in this test root")
                coarse_reason = None
                if category == "Signature change" or change.get("entity_type") == "class":
                    coarse_reason = "Signature or class change: conservatively select file dependencies."
                else:
                    try:
                        source = git(repo, "show", f"{sha}:{old_path}")
                        name = change.get("old_qualname") if kind == "rename" else None
                        name = resolve_function(source, name or change["entity_name"])
                        saved_source = Path(temp) / relative
                        saved_source.parent.mkdir(parents=True, exist_ok=True)
                        saved_source.write_bytes(source)
                        result = explain(snapshot, saved_source, name, environment)
                        item["evidence"] = result
                        if result["status"] != "matched":
                            coarse_reason = "Baseline block mapping has gaps; select file dependencies."
                        else:
                            item["tests"] = [nodeid(test["test"]) for test in result["tests"]]
                            item["status"] = "matched"
                    except (ValueError, SyntaxError, KeyError) as exc:
                        coarse_reason = f"Function mapping unavailable ({exc}); select file dependencies."
                if coarse_reason:
                    item["status"] = "file_fallback"
                    item["reason"] = coarse_reason
                    item["tests"] = sorted(nodeid(test) for test in available)
                if kind == "rename":
                    item["mapping_alias"] = {
                        "old_path": str(old_path), "old_qualname": change.get("old_qualname"),
                        "new_path": str(path), "new_qualname": change.get("new_qualname"),
                        "status": "proposed_only_database_unchanged",
                    }
                selected.update(item["tests"])
            except (ValueError, KeyError, SyntaxError) as exc:
                item["reason"] = str(exc)
                output["selection_complete"] = False
    output["selected_tests"] = sorted(selected)
    output["summary"] = {
        "changes": len(output["changes"]), "selected_tests": len(selected),
        "unresolved_changes": sum(c["status"] == "unresolved" for c in output["changes"]),
        "file_fallbacks": sum(c["status"] == "file_fallback" for c in output["changes"]),
    }
    return output


def local_report(repo, filename, base):
    """Use the upstream API; never reimplement its change classification."""
    path = safe_path(filename)
    if path.suffix != ".py":
        raise ValueError("--local-file supports Python files only")
    sha = git(repo, "rev-parse", "--verify", "--end-of-options", f"{base}^{{commit}}").decode().strip()
    sys.path.insert(0, str(repo / "pr-analyzer"))
    from analyzer import compare_sources
    old = git(repo, "show", f"{sha}:{path}").decode("utf-8-sig")
    new = (repo / str(path)).read_text(encoding="utf-8-sig")
    compared = compare_sources(old, new, file_path=str(path))
    return {
        "schema_version": "2.0", "mode": "local_ast",
        "repo": {"base_sha": sha, "head_sha": None, "working_tree": True},
        "changes": [{**c, "file_path": str(path)} for c in compared["changes"]],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    inputs = parser.add_mutually_exclusive_group(required=True)
    inputs.add_argument("--report", help="Member 1's Schema 2.0 output.json")
    inputs.add_argument("--local-file", help="Compare a working-tree Python file against --base")
    parser.add_argument("--repo", default=".")
    parser.add_argument("--base", help="Baseline Git revision; defaults to HEAD for --local-file")
    parser.add_argument("--db", required=True)
    parser.add_argument("--test-root", required=True)
    parser.add_argument("--environment", default="default")
    parser.add_argument("--output")
    args = parser.parse_args()
    try:
        repo = Path(args.repo).resolve()
        report = local_report(repo, args.local_file, args.base or "HEAD") if args.local_file else json.loads(Path(args.report).read_text(encoding="utf-8-sig"))
        result = map_report(report, repo, args.db, args.test_root, args.base, args.environment)
        payload = json.dumps(result, indent=2)
        if args.output:
            Path(args.output).write_text(payload + "\n", encoding="utf-8")
        print(payload)
    except (ValueError, OSError, sqlite3.Error, SyntaxError, ImportError) as exc:
        parser.exit(2, f"Mapping error: {exc}\n")


if __name__ == "__main__":
    main()
