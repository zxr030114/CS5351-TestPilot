"""F orchestration adapters for the existing A-E public APIs.

Work is performed in fresh temporary clones at resolved commits. The user's
checkout, tracked files and testmon database are never rewritten.
"""
from __future__ import annotations

import ast
from dataclasses import asdict, replace
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import time

TOOLS = Path(__file__).resolve().parents[1]
for directory in ("pr-analyzer", "mapping-demo", "adequacy-analyzer", "gap-scoring", "pytest-testmon"):
    sys.path.insert(0, str(TOOLS / directory))


def read_json(path):
    with Path(path).open(encoding="utf-8-sig") as stream:
        return json.load(stream)


def write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def score_reports(changes, behavior=None, mapping=None, adequacy=None):
    """Reuse E, preserving file identity when linking D evidence to B records."""
    from loader import load_pipeline
    from join import join_inputs
    from report import build_report
    from scoring import context_from_pipeline, score_units
    inputs = load_pipeline(changes, behavior=behavior, mapping=mapping, adequacy=adequacy)
    joined = join_inputs(inputs)
    if inputs.adequacy:
        index = {}
        for entry in inputs.adequacy.entries:
            key = json.dumps(asdict(entry.change), sort_keys=True)
            index.setdefault(key, []).append(entry)
        units = []
        for unit in joined.units:
            links = []
            for link in unit.links:
                entries = index.get(json.dumps(asdict(link.behavior), sort_keys=True), [])
                links.append(replace(link, adequacy=entries[0] if len(entries) == 1 else None))
            units.append(replace(unit, links=tuple(links)))
        joined = replace(joined, units=tuple(units))
    scores = score_units(joined, context=context_from_pipeline(inputs))
    return build_report(inputs, joined, scores)


def command(argv, cwd, timeout=120, env=None):
    started = time.perf_counter()
    result = subprocess.run([str(arg) for arg in argv], cwd=str(cwd), env=env,
                            capture_output=True, timeout=timeout)
    if result.returncode:
        # Avoid accidentally logging output from executed project code or git credentials.
        raise ValueError(f"{Path(str(argv[0])).name} failed with exit code {result.returncode}; no analysis conclusion is available")
    return result.stdout, (time.perf_counter() - started) * 1000


def revision(repo, value):
    raw, _ = command(["git", "rev-parse", "--verify", "--end-of-options", str(value) + "^{commit}"], repo)
    return raw.decode("ascii").strip()


def relative_path(root, text):
    path = Path(text)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError("Expected a path inside the analysed repository")
    resolved = (root / path).resolve()
    resolved.relative_to(root.resolve())
    return resolved


def checkout(repo, sha, destination, timeout):
    command(["git", "clone", "--quiet", "--no-hardlinks", "--no-checkout", "--", repo, destination], repo, timeout)
    command(["git", "checkout", "--quiet", "--detach", sha], destination, timeout)


def source(path, root):
    path.resolve().relative_to(root.resolve())
    if path.is_symlink():
        raise ValueError("Symlinked Python sources are not supported")
    return path.read_text(encoding="utf-8-sig") if path.is_file() else ""


def module_surface(code):
    tree = ast.parse(code)
    return ast.dump(ast.Module(body=[node for node in tree.body if not isinstance(
        node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))], type_ignores=[]), include_attributes=False)


def build_changes(repo, base, head, old_root, new_root, repo_name="", pr=None):
    from analyzer import compare_sources, noise_reason
    raw, _ = command(["git", "diff", "--name-only", "--no-renames", "-z", base, head, "--"], repo)
    paths = [value.decode("utf-8") for value in raw.split(b"\0") if value]
    changes, skipped, warnings = [], [], []
    for name in paths:
        if noise_reason(name):
            skipped.append({"path": name, "reason": "noise"})
            continue
        if not name.endswith(".py"):
            skipped.append({"path": name, "reason": "unsupported language"})
            warnings.append(f"{name}: only Python is supported by the F integration")
            continue
        old, new = source(old_root / name, old_root), source(new_root / name, new_root)
        for change in compare_sources(old, new, file_path=name, with_details=True)["changes"]:
            changes.append({**change, "file_path": name})
        if module_surface(old) != module_surface(new):
            # A's entity API does not cover module-level executable statements.
            changes.append({"entity_type": "module", "entity_name": "<module>", "change_type": "modify",
                "change_category": "Logic change", "line_start": 1, "line_end": max(1, len(new.splitlines())),
                "file_path": name, "summary": "Module-level change: no supported behavioral scoring"})
            warnings.append(f"{name}: module-level behavior cannot be scored")
    owner, _, name = repo_name.partition("/")
    return {"schema_version": "2.0", "mode": "pr_ast", "repo": {
        "owner": owner or None, "name": name or None, "pr_number": pr, "base_sha": base, "head_sha": head},
        "changes": changes, "skipped": skipped, "warnings": warnings,
        "summary": {"files_changed": len(paths), "changes": len(changes)}}


def analyze_behaviors(changes, old_root, new_root):
    from behavioral_change_detector import BehavioralChangeDetector
    from module_path import to_module_path
    detector = BehavioralChangeDetector(use_llm=False)
    records, errors = [], []
    for name in sorted({change["file_path"] for change in changes["changes"]}):
        report = detector.detect(source(old_root / name, old_root), source(new_root / name, new_root))
        errors.extend(f"{name}: {error}" for error in report["errors"])
        for item in report["behavior_changes"]:
            records.append({**item, "file_path": name,
                            "module_path": to_module_path(name, project_root=new_root)})
    return {"schema_version": "1.0", "behavior_changes": records, "errors": errors,
            "analysis": {"method": "python-ast", "runtime_behavior_proven": False, "llm_status": "disabled"}}


def analyze_adequacy(behavior, head_root, mapping):
    from test_adequacy_analyzer import TestAdequacyAnalyzer
    sources = {}
    for path in head_root.rglob("*.py"):
        relative = path.relative_to(head_root)
        if any(part.startswith(".") or part in {"venv", "node_modules", "__pycache__"} for part in relative.parts):
            continue
        if path.name.startswith("test_") or path.name.endswith("_test.py"):
            sources[relative.as_posix()] = source(path, head_root)
    adapted = []
    for item in behavior["behavior_changes"]:
        adapted.append({**item, "file_path": item["module_path"]})
    result = TestAdequacyAnalyzer().analyze({**behavior, "behavior_changes": adapted}, sources, mapping)
    for entry, original in zip(result["results"], behavior["behavior_changes"]):
        entry["change"] = original
    if not sources:
        result["analysis"]["input_complete"] = False
        result["errors"].append({"type": "MissingTestCorpus", "message": "No test sources found in the full head checkout"})
    return result


def run_analysis(repo, base, head, out, *, repo_name="", pr=None, collect_baseline=False,
                 db=None, db_base_sha=None, testmon_root=".", pytest_targets=(), timeout=120):
    """Run real A-E APIs; baseline execution must be explicitly requested."""
    started = time.perf_counter()
    repo, out = Path(repo).resolve(), Path(out).resolve()
    base, head = revision(repo, base), revision(repo, head)
    if db and collect_baseline:
        raise ValueError("Use either --db or --collect-baseline")
    if db and (not db_base_sha or revision(repo, db_base_sha) != base):
        raise ValueError("An external database requires --db-base-sha matching --base")
    out.mkdir(parents=True, exist_ok=True)
    run_id = head[:12] + "-" + str(time.time_ns())
    timings = {}
    # TemporaryDirectory owns only directories created by this invocation.
    with tempfile.TemporaryDirectory(prefix="testpilot-f-") as temp:
        workspace = Path(temp).resolve()
        old_root, new_root = workspace / "base", workspace / "head"
        checkout(repo, base, old_root, timeout)
        checkout(repo, head, new_root, timeout)
        db_root = relative_path(old_root, testmon_root)
        database = db_root / ".testmondata"
        if collect_baseline:
            for target in pytest_targets:
                if target.startswith("-"):
                    raise ValueError("pytest targets must be paths or node IDs, not options")
                relative_path(db_root, target.split("::", 1)[0])
            env = {key: value for key, value in os.environ.items() if not any(
                word in key.upper() for word in ("TOKEN", "SECRET", "PASSWORD", "API_KEY"))}
            env.update({"PYTHONPATH": str(TOOLS / "pytest-testmon"), "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
                        "PYTHONDONTWRITEBYTECODE": "1", "PYTEST_ADDOPTS": "", "PYTEST_PLUGINS": "",
                        "TESTMON_DATAFILE": str(database), "COVERAGE_FILE": str(db_root / ".coverage")})
            # The private clone may itself track a database; collection must start fresh.
            database.unlink(missing_ok=True)
            _, timings["baseline_testmon_ms"] = command([sys.executable, "-m", "pytest", "-p", "testmon.pytest_testmon",
                "--testmon", "--rootdir", str(db_root), "-q", *pytest_targets], db_root, timeout, env)
            if not database.is_file():
                raise ValueError("Baseline finished without a testmon database")
        elif db:
            if not Path(db).is_file():
                raise ValueError("External testmon database does not exist")
            db_root.mkdir(parents=True, exist_ok=True)
            with sqlite3.connect(Path(db).resolve().as_uri() + "?mode=ro", uri=True) as src:
                with sqlite3.connect(database) as dst:
                    src.backup(dst)
        stage = time.perf_counter()
        changes = build_changes(repo, base, head, old_root, new_root, repo_name, pr)
        behavior = analyze_behaviors(changes, old_root, new_root)
        mapping = None
        if database.is_file():
            from map_changes import map_report
            mapping = map_report(changes, old_root, database, db_root, base=base)
        adequacy = analyze_adequacy(behavior, new_root, mapping)
        for name, value in (("changes", changes), ("behavior", behavior), ("mapping", mapping), ("adequacy", adequacy)):
            if value is not None:
                write_json(out / f"{name}.json", value)
            else:
                (out / f"{name}.json").unlink(missing_ok=True)
        report = score_reports(out / "changes.json", out / "behavior.json",
                               out / "mapping.json" if mapping is not None else None, out / "adequacy.json")
        timings["analysis_a_to_e_ms"] = (time.perf_counter() - stage) * 1000
        for warning in changes["warnings"]:
            report["problems"].append({"level": "warning", "code": "f.unsupported_change", "message": warning})
        if not adequacy["analysis"]["input_complete"]:
            report["problems"].append({"level": "warning", "code": "f.incomplete_adequacy", "message": "Adequacy inputs are incomplete; inspect adequacy.json"})
    timings["total_ms"] = (time.perf_counter() - started) * 1000
    report["execution"] = {"run_id": run_id, "base_sha": base, "head_sha": head,
        "baseline": "collected" if collect_baseline else "caller_asserted" if db else "unavailable",
        "head_tests_executed": False, "timings_ms": {key: round(value, 3) for key, value in timings.items()}}
    write_json(out / "gap_report.json", report)
    return report

