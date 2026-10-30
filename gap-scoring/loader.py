"""Load and validate the four reports that feed gap scoring.

The scorer consumes four JSON documents produced by different team members:

======  =========================  ===================================
Stage   Producer                   Document
======  =========================  ===================================
A       PR / Git Change Analyzer   ``output.json``      (schema 2.0)
B       Behavioral Change Detector ``behavior.json``    (schema 1.0)
C       Test Dependency Mapper     ``mapping_result.json`` (schema 1.0)
D       Test Adequacy Analyzer     ``adequacy.json``    (schema 1.0)
======  =========================  ===================================

This module owns file I/O, schema checks and shape normalisation.  It performs
**no** joining: A's change list, B's behavior list, C's per-change mapping and
D's per-behavior result stay separate here.  ``join.py`` links them.

Failure policy
--------------
A is mandatory.  B, C and D are optional: the scorer must still produce a
(coarser) report when only some stages exist, so a missing upstream file is a
warning, not an exception.  A document that is present but has the wrong
schema, is not valid JSON, or is structurally unusable raises ``LoaderError``.

Individual malformed records are dropped with a ``Problem`` recorded, so one
bad entry never takes down the whole run.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

__all__ = [
    "AdequacyEntry",
    "AdequacyReport",
    "BehaviorChange",
    "BehaviorReport",
    "Change",
    "ChangeReport",
    "LoaderError",
    "MappingEntry",
    "MappingReport",
    "PipelineInputs",
    "Problem",
    "load_pipeline",
    "parse_adequacy",
    "parse_behavior",
    "parse_changes",
    "parse_mapping",
    "read_json",
]

#: Severity levels used by :class:`Problem`.
ERROR = "error"
WARNING = "warning"


class LoaderError(Exception):
    """Raised when an input document cannot be used at all."""


@dataclass(frozen=True)
class Problem:
    """A recoverable (or fatal-but-recorded) issue found while loading."""

    level: str
    code: str
    message: str
    context: str = ""

    def __str__(self) -> str:
        where = f" [{self.context}]" if self.context else ""
        return f"{self.level}: {self.code}: {self.message}{where}"


# ---------------------------------------------------------------------------
# Records
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Change:
    """One entry of ``output.json.changes[]`` (stage A)."""

    file_path: str
    entity_name: str
    change_type: str
    change_category: str | None
    line_start: int
    line_end: int
    entity_type: str = "unknown"
    summary: str = ""

    @property
    def key(self) -> tuple[str, str, int]:
        """Stable identity used to match A against C's echoed change object."""
        return (self.file_path, self.entity_name, self.line_start)


@dataclass(frozen=True)
class BehaviorChange:
    """One entry of ``behavior.json.behavior_changes[]`` (stage B)."""

    type: str
    function: str
    file_path: str | None = None
    old_line: int | None = None
    new_line: int | None = None
    scope: str = ""
    confidence: str = ""
    details: Mapping[str, Any] = field(default_factory=dict, repr=False)

    def detail(self, name: str, default: Any = None) -> Any:
        """Return a type-specific field such as ``new_expression``."""
        return self.details.get(name, default)


@dataclass(frozen=True)
class MappingEntry:
    """One entry of ``mapping_result.json.changes[]`` (stage C)."""

    change: Change
    status: str
    tests: tuple[str, ...] = ()
    reason: str | None = None


@dataclass(frozen=True)
class AdequacyEntry:
    """One entry of ``adequacy.json.results[]`` (stage D)."""

    change: BehaviorChange
    status: str
    candidate_tests: tuple[str, ...] = ()
    evidence: tuple[Mapping[str, Any], ...] = ()
    recommendations: tuple[str, ...] = ()

    @property
    def strengths(self) -> tuple[str, ...]:
        """Distinct ``evidence[].strength`` values, useful for downgrade rules."""
        seen: list[str] = []
        for item in self.evidence:
            strength = item.get("strength")
            if isinstance(strength, str) and strength not in seen:
                seen.append(strength)
        return tuple(seen)


@dataclass(frozen=True)
class ChangeReport:
    """Stage A document."""

    changes: tuple[Change, ...]
    repo: Mapping[str, Any] = field(default_factory=dict, repr=False)
    mirror_dir: str | None = None
    skipped: tuple[Mapping[str, Any], ...] = ()
    warnings: tuple[str, ...] = ()
    summary: Mapping[str, Any] = field(default_factory=dict, repr=False)

    @property
    def pr_number(self) -> int | None:
        number = self.repo.get("pr_number")
        return number if isinstance(number, int) else None

    @property
    def repo_name(self) -> str:
        owner, name = self.repo.get("owner"), self.repo.get("name")
        if isinstance(owner, str) and isinstance(name, str):
            return f"{owner}/{name}"
        return ""


@dataclass(frozen=True)
class BehaviorReport:
    """Stage B document."""

    changes: tuple[BehaviorChange, ...]
    summary: str = ""
    errors: tuple[str, ...] = ()
    suggested_tests: tuple[str, ...] = ()


@dataclass(frozen=True)
class MappingReport:
    """Stage C document."""

    entries: tuple[MappingEntry, ...]
    selected_tests: tuple[str, ...] = ()
    selection_complete: bool = True
    limitations: tuple[str, ...] = ()

    def status_of(self, change: Change) -> MappingEntry | None:
        """Return the entry echoing *change*, matched by its identity key."""
        for entry in self.entries:
            if entry.change.key == change.key:
                return entry
        return None


@dataclass(frozen=True)
class AdequacyReport:
    """Stage D document."""

    entries: tuple[AdequacyEntry, ...]
    errors: tuple[str, ...] = ()
    input_complete: bool = True
    selection_complete: bool | None = None
    limitations: tuple[str, ...] = ()

    def results_for(self, function: str, file_path: str | None = None) -> tuple[AdequacyEntry, ...]:
        """Return entries for *function*, optionally filtered by module path."""
        matches = [entry for entry in self.entries if entry.change.function == function]
        if file_path is None:
            return tuple(matches)
        exact = [entry for entry in matches if entry.change.file_path in (None, file_path)]
        return tuple(exact)


@dataclass(frozen=True)
class PipelineInputs:
    """Everything the scorer receives, plus the problems found while loading."""

    changes: ChangeReport
    behavior: BehaviorReport | None = None
    mapping: MappingReport | None = None
    adequacy: AdequacyReport | None = None
    problems: tuple[Problem, ...] = ()

    @property
    def errors(self) -> tuple[Problem, ...]:
        return tuple(p for p in self.problems if p.level == ERROR)

    @property
    def warnings(self) -> tuple[Problem, ...]:
        return tuple(p for p in self.problems if p.level == WARNING)


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------


def _is_sequence(value: Any) -> bool:
    return isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray))


def _require_document(value: Any, what: str, source: str) -> Mapping[str, Any]:
    """Validate a whole document; a bad document aborts the load."""
    if not isinstance(value, Mapping):
        raise LoaderError(f"{source}: {what} must be a JSON object, got {type(value).__name__}")
    return value


def _require_record(value: Any, context: str, source: str) -> Mapping[str, Any]:
    """Validate one list element; a bad element is dropped, not fatal."""
    if not isinstance(value, Mapping):
        raise ValueError(f"{source}: {context} must be a JSON object, got {type(value).__name__}")
    return value


def _check_schema(
    data: Mapping[str, Any],
    source: str,
    *,
    version: str,
    mode: str | None = None,
    strict_version: bool = True,
    problems: list[Problem],
) -> None:
    found_version = data.get("schema_version")
    if found_version != version:
        message = f"{source}: schema_version is {found_version!r}, expected {version!r}"
        if strict_version:
            if found_version == "1.0" and version == "2.0":
                message += " (this looks like the diff report; only the AST pipeline is supported)"
            raise LoaderError(message)
        problems.append(Problem(WARNING, "schema.version", message, source))
    if mode is not None:
        found_mode = data.get("mode")
        if found_mode != mode:
            raise LoaderError(f"{source}: mode is {found_mode!r}, expected {mode!r}")


def _require_str(record: Mapping[str, Any], name: str, *, source: str, context: str) -> str:
    value = record.get(name)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{source}: {context}.{name} must be a non-empty string, got {value!r}")
    return value.strip()


def _optional_str(record: Mapping[str, Any], name: str) -> str | None:
    value = record.get(name)
    return value.strip() if isinstance(value, str) and value.strip() else None


def _require_int(record: Mapping[str, Any], name: str, *, source: str, context: str) -> int:
    value = record.get(name)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{source}: {context}.{name} must be an integer, got {value!r}")
    return value


def _optional_int(record: Mapping[str, Any], name: str) -> int | None:
    value = record.get(name)
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _string_tuple(value: Any, *, source: str, context: str, problems: list[Problem]) -> tuple[str, ...]:
    if value is None:
        return ()
    if not _is_sequence(value):
        problems.append(Problem(WARNING, "field.type", f"{context} must be a list of strings", source))
        return ()
    items = [item for item in value if isinstance(item, str)]
    if len(items) != len(value):
        problems.append(Problem(WARNING, "field.type", f"{context}: dropped non-string items", source))
    return tuple(items)


# ---------------------------------------------------------------------------
# Stage parsers
# ---------------------------------------------------------------------------


def parse_change(record: Any, source: str, index: int) -> Change:
    """Convert one A ``changes[]`` element; raise ``ValueError`` when unusable."""
    context = f"changes[{index}]"
    data = _require_record(record, context, source)
    return Change(
        file_path=_require_str(data, "file_path", source=source, context=context),
        entity_name=_require_str(data, "entity_name", source=source, context=context),
        change_type=_require_str(data, "change_type", source=source, context=context),
        change_category=_optional_str(data, "change_category"),
        line_start=_require_int(data, "line_start", source=source, context=context),
        line_end=_require_int(data, "line_end", source=source, context=context),
        entity_type=_optional_str(data, "entity_type") or "unknown",
        summary=_optional_str(data, "summary") or "",
    )


def parse_behavior_change(record: Any, source: str, index: int) -> BehaviorChange:
    """Convert one B ``behavior_changes[]`` element."""
    context = f"behavior_changes[{index}]"
    data = _require_record(record, context, source)
    known = {"type", "function", "file_path", "old_line", "new_line", "scope", "confidence"}
    details = {key: value for key, value in data.items() if key not in known}
    return BehaviorChange(
        type=_require_str(data, "type", source=source, context=context),
        function=_require_str(data, "function", source=source, context=context),
        file_path=_optional_str(data, "file_path"),
        old_line=_optional_int(data, "old_line"),
        new_line=_optional_int(data, "new_line"),
        scope=_optional_str(data, "scope") or "",
        confidence=_optional_str(data, "confidence") or "",
        details=details,
    )


def parse_changes(data: Mapping[str, Any], source: str = "<changes>") -> tuple[ChangeReport, list[Problem]]:
    """Parse stage A (``output.json``)."""
    problems: list[Problem] = []
    data = _require_document(data, "document", source)
    _check_schema(data, source, version="2.0", mode="pr_ast", problems=problems)

    raw_changes = data.get("changes")
    if not _is_sequence(raw_changes):
        raise LoaderError(f"{source}: 'changes' must be a list")

    changes: list[Change] = []
    for index, record in enumerate(raw_changes):
        try:
            changes.append(parse_change(record, source, index))
        except ValueError as exc:
            problems.append(Problem(ERROR, "change.malformed", str(exc), source))

    mirror = data.get("mirror") if isinstance(data.get("mirror"), Mapping) else {}
    repo = data.get("repo") if isinstance(data.get("repo"), Mapping) else {}
    summary = data.get("summary") if isinstance(data.get("summary"), Mapping) else {}
    skipped_raw = data.get("skipped") if _is_sequence(data.get("skipped")) else []
    warnings_raw = data.get("warnings") if _is_sequence(data.get("warnings")) else []

    report = ChangeReport(
        changes=tuple(changes),
        repo=repo,
        mirror_dir=mirror.get("out_dir") if isinstance(mirror.get("out_dir"), str) else None,
        skipped=tuple(item for item in skipped_raw if isinstance(item, Mapping)),
        warnings=tuple(item for item in warnings_raw if isinstance(item, str)),
        summary=summary,
    )
    if not report.changes:
        problems.append(Problem(WARNING, "changes.empty", "no usable change records", source))
    return report, problems


def parse_behavior(data: Mapping[str, Any], source: str = "<behavior>") -> tuple[BehaviorReport, list[Problem]]:
    """Parse stage B (``behavior.json``)."""
    problems: list[Problem] = []
    data = _require_document(data, "document", source)
    _check_schema(data, source, version="1.0", strict_version=False, problems=problems)

    raw = data.get("behavior_changes")
    if raw is None:
        raw = []
    if not _is_sequence(raw):
        raise LoaderError(f"{source}: 'behavior_changes' must be a list")

    changes: list[BehaviorChange] = []
    for index, record in enumerate(raw):
        try:
            changes.append(parse_behavior_change(record, source, index))
        except ValueError as exc:
            problems.append(Problem(ERROR, "behavior.malformed", str(exc), source))

    # B does not emit file_path; warn once so the caller knows to inject it.
    if changes and all(change.file_path is None for change in changes):
        problems.append(
            Problem(
                WARNING,
                "behavior.missing_file_path",
                "no behavior record carries file_path; module-path matching needs it injected",
                source,
            )
        )

    report = BehaviorReport(
        changes=tuple(changes),
        summary=_optional_str(data, "summary") or "",
        errors=_string_tuple(data.get("errors"), source=source, context="errors", problems=problems),
        suggested_tests=_string_tuple(
            data.get("suggested_tests"), source=source, context="suggested_tests", problems=problems
        ),
    )
    return report, problems


def parse_mapping(data: Mapping[str, Any], source: str = "<mapping>") -> tuple[MappingReport, list[Problem]]:
    """Parse stage C (``mapping_result.json``)."""
    problems: list[Problem] = []
    data = _require_document(data, "document", source)
    _check_schema(data, source, version="1.0", mode="test_dependency_mapping", problems=problems)

    raw = data.get("changes", [])
    if not _is_sequence(raw):
        raise LoaderError(f"{source}: 'changes' must be a list")

    entries: list[MappingEntry] = []
    for index, record in enumerate(raw):
        context = f"changes[{index}]"
        try:
            item = _require_record(record, context, source)
            status = _require_str(item, "status", source=source, context=context)
            change = parse_change(item.get("change"), source, index)
        except ValueError as exc:
            problems.append(Problem(ERROR, "mapping.malformed", str(exc), source))
            continue
        entries.append(
            MappingEntry(
                change=change,
                status=status,
                tests=_string_tuple(item.get("tests"), source=source, context=f"{context}.tests", problems=problems),
                reason=_optional_str(item, "reason"),
            )
        )

    selection_complete = data.get("selection_complete", True)
    if not isinstance(selection_complete, bool):
        problems.append(Problem(WARNING, "field.type", "selection_complete is not a boolean", source))
        selection_complete = False

    return (
        MappingReport(
            entries=tuple(entries),
            selected_tests=_string_tuple(
                data.get("selected_tests"), source=source, context="selected_tests", problems=problems
            ),
            selection_complete=selection_complete,
            limitations=_string_tuple(
                data.get("limitations"), source=source, context="limitations", problems=problems
            ),
        ),
        problems,
    )


def parse_adequacy(data: Mapping[str, Any], source: str = "<adequacy>") -> tuple[AdequacyReport, list[Problem]]:
    """Parse stage D (``adequacy.json``)."""
    problems: list[Problem] = []
    data = _require_document(data, "document", source)
    _check_schema(data, source, version="1.0", mode="test_adequacy_analysis", problems=problems)

    raw = data.get("results", [])
    if not _is_sequence(raw):
        raise LoaderError(f"{source}: 'results' must be a list")

    entries: list[AdequacyEntry] = []
    for index, record in enumerate(raw):
        context = f"results[{index}]"
        try:
            item = _require_record(record, context, source)
            status = _require_str(item, "status", source=source, context=context)
            change = parse_behavior_change(item.get("change"), source, index)
        except ValueError as exc:
            problems.append(Problem(ERROR, "adequacy.malformed", str(exc), source))
            continue
        evidence_raw = item.get("evidence") if _is_sequence(item.get("evidence")) else []
        entries.append(
            AdequacyEntry(
                change=change,
                status=status,
                candidate_tests=_string_tuple(
                    item.get("candidate_tests"), source=source, context=f"{context}.candidate_tests", problems=problems
                ),
                evidence=tuple(ev for ev in evidence_raw if isinstance(ev, Mapping)),
                recommendations=_string_tuple(
                    item.get("recommendations"), source=source, context=f"{context}.recommendations", problems=problems
                ),
            )
        )

    analysis = data.get("analysis") if isinstance(data.get("analysis"), Mapping) else {}
    input_complete = analysis.get("input_complete", True)
    selection_complete = analysis.get("selection_complete")
    if not isinstance(input_complete, bool):
        problems.append(Problem(WARNING, "field.type", "analysis.input_complete is not a boolean", source))
        input_complete = False

    return (
        AdequacyReport(
            entries=tuple(entries),
            errors=_string_tuple(data.get("errors"), source=source, context="errors", problems=problems),
            input_complete=input_complete,
            selection_complete=selection_complete if isinstance(selection_complete, bool) else None,
            limitations=_string_tuple(
                data.get("limitations"), source=source, context="limitations", problems=problems
            ),
        ),
        problems,
    )


# ---------------------------------------------------------------------------
# File loading
# ---------------------------------------------------------------------------


def read_json(path: str | os.PathLike[str]) -> Any:
    """Read a UTF-8 JSON file, tolerating a byte-order mark."""
    location = Path(os.fspath(path))
    try:
        text = location.read_text(encoding="utf-8-sig")
    except FileNotFoundError as exc:
        raise LoaderError(f"input not found: {location}") from exc
    except OSError as exc:
        raise LoaderError(f"cannot read {location}: {exc}") from exc
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise LoaderError(f"{location}: invalid JSON at line {exc.lineno} column {exc.colno}: {exc.msg}") from exc


def _load_optional(
    path: str | os.PathLike[str] | None,
    parser,
    label: str,
    problems: list[Problem],
):
    if path is None:
        problems.append(Problem(WARNING, "input.missing", f"{label} not supplied", label))
        return None
    location = Path(os.fspath(path))
    if not location.is_file():
        problems.append(Problem(WARNING, "input.missing", f"{label} not found at {location}", label))
        return None
    report, found = parser(read_json(location), str(location))
    problems.extend(found)
    return report


def load_pipeline(
    changes: str | os.PathLike[str],
    behavior: str | os.PathLike[str] | None = None,
    mapping: str | os.PathLike[str] | None = None,
    adequacy: str | os.PathLike[str] | None = None,
) -> PipelineInputs:
    """Load all four reports, keeping whatever is available.

    Args:
        changes: Path to stage A's ``output.json``.  Required.
        behavior: Path to stage B's ``behavior.json``; omitted means no risk model.
        mapping: Path to stage C's ``mapping_result.json``.
        adequacy: Path to stage D's ``adequacy.json``.

    Raises:
        LoaderError: stage A is missing, unreadable, or has the wrong schema.
            A present-but-broken B/C/D document also raises.
    """
    problems: list[Problem] = []
    change_report, found = parse_changes(read_json(changes), str(changes))
    problems.extend(found)

    return PipelineInputs(
        changes=change_report,
        behavior=_load_optional(behavior, parse_behavior, "behavior", problems),
        mapping=_load_optional(mapping, parse_mapping, "mapping", problems),
        adequacy=_load_optional(adequacy, parse_adequacy, "adequacy", problems),
        problems=tuple(problems),
    )
