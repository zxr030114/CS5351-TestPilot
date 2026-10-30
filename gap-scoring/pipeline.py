"""End-to-end runner for the team pipeline: A -> B -> C -> D -> E.

Each member owns one stage and each stage is a separate command with its own
conventions.  This module is the glue: it turns the shared repository layout into
the four JSON artefacts stage E needs, and it degrades one stage at a time
instead of failing the whole run.

Why a runner instead of "just run the four scripts":

* **Module paths.** Stage D matches a change against ``from payment import ...``
  and silently answers ``no_candidate_test`` when given a repository-relative
  path.  Converting A's path with :mod:`module_path` is mandatory, not cosmetic;
  doing it by hand is exactly the bug that cost a debugging session.
* **Behaviour merging.** Stage B compares two *files*, not a PR, so a PR with
  several changed files yields several reports.  They must be merged, and each
  record tagged with the module path of its own file.
* **Optional stages.** C needs a testmon database that often does not exist.  The
  runner reports it as skipped and lets E score without mapping data.

Every stage writes into ``workdir``; nothing is written into the shared repo.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from loader import ChangeReport
from module_path import to_module_path

__all__ = [
    "BehaviorJob",
    "PipelineConfig",
    "StageResult",
    "build_behavior_jobs",
    "build_commands",
    "merge_behavior_reports",
    "run_pipeline",
]

#: Stages in execution order.
STAGES: tuple[str, ...] = ("A", "B", "C", "D", "E")


@dataclass(frozen=True)
class BehaviorJob:
    """One ``old``/``new`` source pair for stage B."""

    file_path: str
    old: Path
    new: Path
    module_path: str


@dataclass(frozen=True)
class StageResult:
    """Outcome of one stage, reported to the user as a table row."""

    name: str
    ok: bool
    output: Path | None = None
    skipped: bool = False
    message: str = ""

    def row(self) -> str:
        state = "skipped" if self.skipped else ("ok" if self.ok else "FAILED")
        target = f" -> {self.output}" if self.output else ""
        detail = f"  ({self.message})" if self.message else ""
        return f"  {self.name}: {state}{target}{detail}"


@dataclass
class PipelineConfig:
    """Everything the runner needs to know.

    ``repo`` is the shared checkout (contains ``pr-analyzer/``,
    ``mapping-demo/`` and ``adequacy-analyzer/``); ``workdir`` receives the
    artefacts and may live outside it.
    """

    repo: Path
    workdir: Path
    python: str = sys.executable
    env: Mapping[str, str] = field(default_factory=dict)
    changes: Path | None = None
    pr: int | None = None
    owner: str | None = None
    name: str | None = None
    #: Reuse an artefact instead of running that stage.  Needed when a stage cannot
    #: run here: stage C needs the *analysed project's* git history plus a testmon
    #: database, which usually lives in that project's checkout, not ours.
    behavior_file: Path | None = None
    mapping_file: Path | None = None
    adequacy_file: Path | None = None
    test_root: str | None = None
    db: Path | None = None
    skip: tuple[str, ...] = ()
    dry_run: bool = False
    timeout: int = 900

    def path(self, name: str) -> Path:
        return self.workdir / name

    def skipping(self, stage: str) -> bool:
        return stage.upper() in {item.upper() for item in self.skip}


def _environment(config: PipelineConfig) -> dict[str, str]:
    env = dict(os.environ)
    env.update({str(key): str(value) for key, value in config.env.items()})
    return env


def _execute(config: PipelineConfig, command: Sequence[str], *, cwd: Path) -> subprocess.CompletedProcess:
    """Run one stage command, capturing output for the failure message."""
    if config.dry_run:
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
    return subprocess.run(
        list(command),
        cwd=str(cwd),
        env=_environment(config),
        capture_output=True,
        text=True,
        timeout=config.timeout,
    )


def _tail(process: subprocess.CompletedProcess, limit: int = 400) -> str:
    text = (process.stderr or process.stdout or "").strip()
    return text[-limit:].replace("\n", " | ") if text else ""


# ---------------------------------------------------------------------------
# Stage A: change analysis
# ---------------------------------------------------------------------------


def stage_changes(config: PipelineConfig) -> StageResult:
    """Produce (or reuse) stage A's ``output.json``."""
    if "A" in {item.upper() for item in config.skip}:
        return StageResult("A", ok=True, skipped=True, message="skipped by request")
    if config.changes is not None:
        return StageResult("A", ok=config.changes.is_file(), output=config.changes, message="reused")
    if config.pr is None or not config.owner or not config.name:
        return StageResult("A", ok=False, message="either --changes or --owner/--name/--pr is required")

    output = config.path("output.json")
    command = [
        config.python,
        "main.py",
        "--repo", f"{config.owner}/{config.name}",
        "--pr", str(config.pr),
        "--mirror-dir", str(config.path("sources")),
        "-o", str(output),
    ]
    process = _execute(config, command, cwd=config.repo / "pr-analyzer")
    ok = process.returncode == 0 and output.is_file()
    return StageResult("A", ok=ok, output=output if ok else None, message=_tail(process))


# ---------------------------------------------------------------------------
# Stage B: behaviour detection, one job per changed file
# ---------------------------------------------------------------------------


def _mirror_root(config: PipelineConfig, changes: Mapping[str, Any]) -> Path:
    """Where A put base/head sources."""
    mirror = changes.get("mirror") if isinstance(changes.get("mirror"), Mapping) else {}
    recorded = mirror.get("out_dir")
    if isinstance(recorded, str) and Path(recorded).is_dir():
        return Path(recorded)
    return config.path("sources")


def build_behavior_jobs(changes: ChangeReport, mirror: Path) -> list[BehaviorJob]:
    """Pair up base/head sources for every changed Python file.

    A PR touching several files produces several stage B reports; files that only
    exist on one side (pure addition or deletion) are skipped because B compares
    two versions and needs both.
    """
    jobs: dict[str, BehaviorJob] = {}
    for change in changes.changes:
        path = change.file_path
        if not path.endswith(".py") or path in jobs:
            continue
        old = mirror / "base" / path
        new = mirror / "head" / path
        if not old.is_file() or not new.is_file():
            continue
        jobs[path] = BehaviorJob(
            file_path=path,
            old=old,
            new=new,
            module_path=to_module_path(path, project_root=mirror / "head"),
        )
    return list(jobs.values())


def merge_behavior_reports(
    reports: Iterable[tuple[BehaviorJob, Mapping[str, Any]]],
) -> dict[str, Any]:
    """Merge per-file behaviour reports into one, tagging each record.

    The tag is the *module* path (``payment.py``), not the repository path: stage
    D resolves imports by module name and ignores records it cannot place.
    """
    merged: dict[str, Any] = {
        "schema_version": "1.0",
        "change_type": "unknown",
        "summary": "",
        "affected_functions": [],
        "behavioral_impact": "",
        "behavior_changes": [],
        "suggested_tests": [],
        "errors": [],
        "analysis": {"method": "python-ast", "runtime_behavior_proven": False},
    }
    summaries: list[str] = []
    functions: list[str] = []

    for job, report in reports:
        for change in report.get("behavior_changes", []) or []:
            if not isinstance(change, dict):
                continue
            tagged = dict(change)
            tagged["file_path"] = job.module_path
            tagged.setdefault("module_path", job.module_path)
            merged["behavior_changes"].append(tagged)
            function = tagged.get("function")
            if isinstance(function, str) and function not in functions:
                functions.append(function)
        summary = report.get("summary")
        if isinstance(summary, str) and summary:
            summaries.append(f"[{job.module_path}] {summary}")
        for suggestion in report.get("suggested_tests", []) or []:
            if isinstance(suggestion, str):
                merged["suggested_tests"].append(suggestion)
        for error in report.get("errors", []) or []:
            merged["errors"].append(f"[{job.module_path}] {error}")

    merged["summary"] = " ".join(summaries)
    merged["affected_functions"] = functions
    return merged


def stage_behavior(config: PipelineConfig, changes: ChangeReport) -> StageResult:
    """Run stage B once per changed file and merge the reports."""
    if config.skipping("B"):
        return StageResult("B", ok=True, skipped=True, message="skipped by request")
    if config.behavior_file is not None:
        if not config.behavior_file.is_file():
            return StageResult("B", ok=False, message=f"reused report not found: {config.behavior_file}")
        return StageResult("B", ok=True, output=config.behavior_file, message="reused")
    if not changes.changes:
        return StageResult("B", ok=True, skipped=True, message="no changes to analyse")

    mirror = config.path("sources")
    if not mirror.is_dir():
        recorded = changes.mirror_dir
        mirror = Path(recorded) if recorded else mirror
    jobs = build_behavior_jobs(changes, mirror)
    if not jobs:
        return StageResult("B", ok=True, skipped=True, message="no Python file with both revisions")
    if config.dry_run:
        return StageResult(
            "B", ok=True, output=config.path("behavior.json"), message=f"dry run: {len(jobs)} file(s) to compare"
        )

    collected: list[tuple[BehaviorJob, Mapping[str, Any]]] = []
    failures = 0
    for index, job in enumerate(jobs):
        target = config.path(f"behavior_{index}.json")
        command = [
            config.python,
            "main.py",
            "--behavior-old", str(job.old),
            "--behavior-new", str(job.new),
            "-o", str(target),
        ]
        process = _execute(config, command, cwd=config.repo / "pr-analyzer")
        if process.returncode != 0 or not target.is_file():
            failures += 1
            continue
        collected.append((job, json.loads(target.read_text(encoding="utf-8"))))

    merged = merge_behavior_reports(collected)
    output = config.path("behavior.json")
    if not config.dry_run:
        output.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")

    message = f"{len(merged['behavior_changes'])} behavior change(s) from {len(collected)}/{len(jobs)} file(s)"
    if failures:
        message += f", {failures} file(s) failed"
    return StageResult("B", ok=bool(collected) or not jobs, output=output, message=message)


# ---------------------------------------------------------------------------
# Stage C: dependency mapping (optional, needs a testmon database)
# ---------------------------------------------------------------------------


def _mapping_command(config: PipelineConfig, report: Path, db: Path, test_root: str, output: Path) -> list[str]:
    return [
        config.python,
        "mapping-demo/map_changes.py",
        "--report", str(report),
        "--db", str(db),
        "--test-root", test_root,
        "--output", str(output),
    ]


def stage_mapping(config: PipelineConfig, output_json: Path) -> StageResult:
    """Run stage C when a testmon database is available."""
    if config.skipping("C"):
        return StageResult("C", ok=True, skipped=True, message="skipped by request")
    if config.mapping_file is not None:
        if not config.mapping_file.is_file():
            return StageResult("C", ok=False, message=f"reused report not found: {config.mapping_file}")
        return StageResult("C", ok=True, output=config.mapping_file, message="reused")
    if config.db is None or not Path(config.db).is_file():
        return StageResult("C", ok=True, skipped=True, message="no testmon database (--db)")
    test_root = config.test_root or "."
    output = config.path("mapping_result.json")
    command = _mapping_command(config, output_json, Path(config.db), test_root, output)
    process = _execute(config, command, cwd=config.repo)
    ok = process.returncode == 0 and output.is_file()
    return StageResult("C", ok=ok, output=output if ok else None, message=_tail(process))


# ---------------------------------------------------------------------------
# Stage D: adequacy analysis (optional)
# ---------------------------------------------------------------------------


def _adequacy_command(
    config: PipelineConfig, behavior: Path, mapping: Path | None, test_root: str, output: Path
) -> list[str]:
    command = [
        config.python,
        "adequacy-analyzer/test_adequacy_analyzer.py",
        "--behavior", str(behavior),
        "--test-root", test_root,
        "--output", str(output),
    ]
    if mapping is not None:
        command.extend(["--mapping", str(mapping)])
    return command


def stage_adequacy(config: PipelineConfig, behavior: Path, mapping: Path | None) -> StageResult:
    """Run stage D on the merged behaviour report."""
    if config.skipping("D"):
        return StageResult("D", ok=True, skipped=True, message="skipped by request")
    if config.adequacy_file is not None:
        if not config.adequacy_file.is_file():
            return StageResult("D", ok=False, message=f"reused report not found: {config.adequacy_file}")
        return StageResult("D", ok=True, output=config.adequacy_file, message="reused")
    if not behavior.is_file():
        return StageResult("D", ok=True, skipped=True, message="no behaviour report")
    test_root = config.test_root or "."
    output = config.path("adequacy.json")
    command = _adequacy_command(config, behavior, mapping, test_root, output)
    process = _execute(config, command, cwd=config.repo)
    ok = process.returncode == 0 and output.is_file()
    return StageResult("D", ok=ok, output=output if ok else None, message=_tail(process))


# ---------------------------------------------------------------------------
# Stage E: scoring + report (always runs last)
# ---------------------------------------------------------------------------


def stage_scoring(config: PipelineConfig, artefacts: Mapping[str, Path | None], *, output_format: str = "json") -> StageResult:
    """Run this package on whatever the previous stages produced."""
    from gap_scoring import main as scoring_main  # imported late: keeps stage code testable

    output = config.path("gap_report.md" if output_format == "markdown" else "gap_report.json")
    argv = ["--changes", str(artefacts["A"]), "-o", str(output), "--format", output_format]
    for stage, flag in (("B", "--behavior"), ("C", "--mapping"), ("D", "--adequacy")):
        value = artefacts.get(stage)
        if value is not None and Path(value).is_file():
            argv.extend([flag, str(value)])
    if config.dry_run:
        return StageResult("E", ok=True, output=output, message="dry run: " + " ".join(argv))
    code = scoring_main(argv)
    return StageResult("E", ok=code == 0, output=output if code == 0 else None, message=f"exit {code}")


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def build_commands(config: PipelineConfig, changes: ChangeReport) -> list[list[str]]:
    """Return the exact argv of every stage, for ``--dry-run`` and tests."""
    commands: list[list[str]] = []
    if config.changes is None:
        commands.append(
            [
                config.python, "main.py",
                "--repo", f"{config.owner}/{config.name}",
                "--pr", str(config.pr),
                "--mirror-dir", str(config.path("sources")),
                "-o", str(config.path("output.json")),
            ]
        )
    mirror = config.path("sources")
    if config.behavior_file is None:
        for index, job in enumerate(build_behavior_jobs(changes, mirror)):
            commands.append(
                [
                    config.python, "main.py",
                    "--behavior-old", str(job.old),
                    "--behavior-new", str(job.new),
                    "-o", str(config.path(f"behavior_{index}.json")),
                ]
            )
    if config.db and config.mapping_file is None:
        commands.append(
            _mapping_command(
                config,
                config.path("output.json"),
                Path(config.db),
                config.test_root or ".",
                config.path("mapping_result.json"),
            )
        )
    if config.adequacy_file is None:
        commands.append(
            _adequacy_command(
                config,
                config.path("behavior.json"),
                config.path("mapping_result.json") if config.db else None,
                config.test_root or ".",
                config.path("adequacy.json"),
            )
        )
    return commands


def _resolve_test_root(config: PipelineConfig, changes: ChangeReport) -> str:
    """Pick the directory stages C/D should scan for tests.

    Prefer the mirror this run produced, then the one A recorded in its report
    (used when ``--changes`` reuses an earlier run), and only then the current
    directory.  Pointing D at the shared checkout would analyse *our* tests
    instead of the PR's, which is how a run ends up reporting 275 unrelated
    test cases.
    """
    candidates = [config.path("sources") / "head"]
    if changes.mirror_dir:
        candidates.append(Path(changes.mirror_dir) / "head")
    for candidate in candidates:
        if candidate.is_dir():
            return str(candidate)
    return "."


def run_pipeline(config: PipelineConfig, *, output_format: str = "json") -> list[StageResult]:
    """Run all five stages, tolerating the optional ones.

    Returns one :class:`StageResult` per stage.  Stage E is only attempted when
    stage A produced something, because there is nothing to score otherwise.

    When ``test_root`` is unset it defaults to the *analysed project's* head
    mirror, not to the shared checkout: stage D scans that directory for tests,
    and pointing it at our own repository would analyse our tests instead of the
    PR's.
    """
    config.workdir.mkdir(parents=True, exist_ok=True)
    results: list[StageResult] = []

    changes_result = stage_changes(config)
    results.append(changes_result)
    if not changes_result.ok or changes_result.output is None:
        for stage in STAGES[1:]:
            results.append(StageResult(stage, ok=False, skipped=True, message="stage A unavailable"))
        return results

    from loader import parse_changes, read_json

    report = parse_changes(read_json(changes_result.output), str(changes_result.output))[0]

    if config.test_root is None:
        config.test_root = _resolve_test_root(config, report)

    behavior = stage_behavior(config, report)
    results.append(behavior)

    mapping = stage_mapping(config, changes_result.output)
    results.append(mapping)

    adequacy = stage_adequacy(
        config,
        behavior.output or config.path("behavior.json"),
        mapping.output,
    )
    results.append(adequacy)

    artefacts = {
        "A": changes_result.output,
        "B": behavior.output,
        "C": mapping.output,
        "D": adequacy.output,
    }
    results.append(stage_scoring(config, artefacts, output_format=output_format))
    return results
