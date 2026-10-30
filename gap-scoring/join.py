"""Link the four loaded reports into per-change scoring units.

``loader`` keeps A/B/C/D separate.  This module performs the four joins the
field map describes (``docs/field-map.zh.md`` §5):

======  ================================  ==================================
Join    Left                              Right
======  ================================  ==================================
J1      A ``Change``                      C entry echoing the same object
J2      B ``BehaviorChange``              D result echoing the same object
J3      A ``Change``                      B ``BehaviorChange``  (heuristic)
J4      C test node IDs                   D evidence node IDs   (suffix)
======  ================================  ==================================

J1 and J2 are exact: C and D copy the upstream object verbatim, so their records
are matched on identity rather than re-derived.  J3 is the only heuristic one --
A reports *entities* while B reports *behaviors*, and one entity may carry
several behavior records.

J3 is resolved **globally**, not per change: each behavior record is assigned to
at most one change, and when two changes are equally good candidates the
behavior is left unattached and reported as ambiguous.  Assigning greedily per
change would silently duplicate one behavior onto every same-named function.

Path shapes are deliberately not normalised.  A emits repository-relative paths
(``mapping-demo/payment.py``) while B and D may emit module paths
(``payment.py``); :func:`same_file` accepts either because the shorter form is a
suffix of the longer one.  ``module_path.to_module_path`` is still required
before calling D, but joining does not depend on knowing the project root.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field

from loader import (
    WARNING,
    AdequacyEntry,
    AdequacyReport,
    BehaviorChange,
    Change,
    MappingEntry,
    MappingReport,
    PipelineInputs,
    Problem,
)

__all__ = [
    "BehaviorLink",
    "JoinResult",
    "ScoringUnit",
    "join_inputs",
    "same_file",
    "same_test",
]

#: Join methods, ordered from strongest to weakest evidence.
METHOD_EXACT = "exact"
METHOD_LINE = "line_window"
METHOD_FUNCTION = "function_only"
METHOD_UNMATCHED = "unmatched"

_RANK = {METHOD_EXACT: 0, METHOD_LINE: 1, METHOD_FUNCTION: 2}


def _clean(path: str) -> str:
    text = path.replace("\\", "/").strip()
    while text.startswith("./"):
        text = text[2:]
    return text.strip("/")


def same_file(left: str | None, right: str | None) -> bool:
    """Return True when two path spellings point at the same source file.

    Accepts repository-relative against module-relative paths: the shorter one
    must be a slash-delimited suffix of the longer one.

    >>> same_file("mapping-demo/payment.py", "payment.py")
    True
    >>> same_file("payment.py", "test_payment.py")
    False
    """
    if not left or not right:
        return False
    a, b = _clean(left), _clean(right)
    if a == b:
        return True
    return a.endswith("/" + b) or b.endswith("/" + a)


def _split_node(node_id: str) -> tuple[str, str]:
    path, _, name = node_id.partition("::")
    return _clean(path), name


def same_test(left: str, right: str) -> bool:
    """Return True when two pytest node IDs refer to the same test.

    C emits IDs relative to the test root (``test_payment.py::test_x``) while D
    emits them relative to the repository
    (``mapping-demo/test_payment.py::test_x``).

    >>> same_test("test_payment.py::test_x", "mapping-demo/test_payment.py::test_x")
    True
    >>> same_test("test_payment.py::test_x", "mapping-demo/test_payment.py::test_y")
    False
    """
    left_path, left_name = _split_node(left)
    right_path, right_name = _split_node(right)
    if left_name != right_name:
        return False
    if not left_path or not right_path:
        return True
    return same_file(left_path, right_path)


@dataclass(frozen=True)
class BehaviorLink:
    """One B behavior record paired with its optional D result."""

    behavior: BehaviorChange
    adequacy: AdequacyEntry | None = None
    method: str = METHOD_UNMATCHED


@dataclass(frozen=True)
class ScoringUnit:
    """Everything known about one structural change (one A record)."""

    change: Change
    mapping: MappingEntry | None = None
    links: tuple[BehaviorLink, ...] = ()
    method: str = METHOD_UNMATCHED

    @property
    def has_behavior(self) -> bool:
        return bool(self.links)

    @property
    def mapped(self) -> bool:
        return self.mapping is not None

    @property
    def behaviors(self) -> tuple[BehaviorChange, ...]:
        return tuple(link.behavior for link in self.links)

    @property
    def adequacy_entries(self) -> tuple[AdequacyEntry, ...]:
        return tuple(link.adequacy for link in self.links if link.adequacy is not None)

    @property
    def tests(self) -> tuple[str, ...]:
        """Test IDs from C, falling back to D's candidate list."""
        if self.mapping is not None and self.mapping.tests:
            return self.mapping.tests
        seen: list[str] = []
        for entry in self.adequacy_entries:
            for test in entry.candidate_tests:
                if test not in seen:
                    seen.append(test)
        return tuple(seen)


@dataclass(frozen=True)
class JoinResult:
    """Scoring units plus the leftovers and the health of the join."""

    units: tuple[ScoringUnit, ...]
    orphan_behaviors: tuple[BehaviorChange, ...] = ()
    coverage: float | None = None
    stats: Mapping[str, int] = field(default_factory=dict, repr=False)
    problems: tuple[Problem, ...] = ()

    @property
    def mapped_units(self) -> tuple[ScoringUnit, ...]:
        return tuple(unit for unit in self.units if unit.mapped)


# ---------------------------------------------------------------------------
# J2: behavior -> adequacy result
# ---------------------------------------------------------------------------


def _behavior_signature(behavior: BehaviorChange) -> tuple:
    """Identity of a behavior record, ignoring fields D may have added.

    D echoes B's dict, but ``file_path`` is injected between the two stages, so
    it cannot take part in the signature.
    """
    return (behavior.type, behavior.function, behavior.old_line, behavior.new_line)


def _match_adequacy(
    behavior: BehaviorChange, entries: Sequence[AdequacyEntry]
) -> AdequacyEntry | None:
    signature = _behavior_signature(behavior)
    for entry in entries:
        if _behavior_signature(entry.change) == signature:
            return entry
    return None


def _selected_with_evidence(selected: Sequence[str], adequacy: Iterable[AdequacyEntry]) -> int:
    evidence_ids = [
        item.get("nodeid")
        for entry in adequacy
        for item in entry.evidence
        if isinstance(item.get("nodeid"), str)
    ]
    return sum(1 for node in selected if any(same_test(node, other) for other in evidence_ids))


# ---------------------------------------------------------------------------
# J3: A change -> B behaviors (global assignment)
# ---------------------------------------------------------------------------


def _short_name(name: str) -> str:
    """Last component of a (possibly qualified) function name.

    Stage A reports ``entity_name`` as a short name (``raise_any``) while stage B
    reports ``function`` as a qualified name (``_CollectErrors.raise_any``), so
    comparing the raw strings would silently drop every method.
    """
    return name.rsplit(".", 1)[-1]


def _pair_score(change: Change, behavior: BehaviorChange) -> tuple[str, int] | None:
    """Return ``(method, rank)`` when *behavior* may belong to *change*."""
    if _short_name(behavior.function) != _short_name(change.entity_name):
        return None
    file_known = bool(behavior.file_path)
    if file_known and not same_file(behavior.file_path, change.file_path):
        return None
    line_inside = (
        behavior.new_line is not None and change.line_start <= behavior.new_line <= change.line_end
    )
    if file_known and line_inside:
        return METHOD_EXACT, _RANK[METHOD_EXACT]
    if line_inside:
        return METHOD_LINE, _RANK[METHOD_LINE]
    if file_known:
        return METHOD_LINE, _RANK[METHOD_LINE]
    return METHOD_FUNCTION, _RANK[METHOD_FUNCTION]


def _assign_behaviors(
    changes: Sequence[Change],
    behaviors: Sequence[BehaviorChange],
    problems: list[Problem],
) -> tuple[dict[int, list[tuple[int, BehaviorChange]]], set[int]]:
    """Assign each behavior to at most one change.

    Returns:
        A mapping ``change_index -> [(rank, behavior), ...]`` and the set of
        behavior indices that found no unique owner.
    """
    assignments: dict[int, list[tuple[int, BehaviorChange]]] = {}
    unassigned: set[int] = set()

    for index, behavior in enumerate(behaviors):
        candidates: list[tuple[int, int]] = []  # (rank, change_index)
        for change_index, change in enumerate(changes):
            score = _pair_score(change, behavior)
            if score is not None:
                candidates.append((score[1], change_index))
        if not candidates:
            unassigned.add(index)
            continue

        best_rank = min(rank for rank, _ in candidates)
        best = [change_index for rank, change_index in candidates if rank == best_rank]
        if len(best) > 1:
            problems.append(
                Problem(
                    WARNING,
                    "join.ambiguous_behavior",
                    f"behavior {behavior.type} in {behavior.function} matches "
                    f"{len(best)} changes equally well; left unattached",
                    behavior.file_path or behavior.function,
                )
            )
            unassigned.add(index)
            continue

        owner = best[0]
        assignments.setdefault(owner, []).append((best_rank, behavior))

    return assignments, unassigned


# ---------------------------------------------------------------------------
# J1: A change -> C entry
# ---------------------------------------------------------------------------


def _match_mapping(
    change: Change, mapping: MappingReport | None, problems: list[Problem]
) -> MappingEntry | None:
    if mapping is None:
        return None
    direct = mapping.status_of(change)
    if direct is not None:
        return direct
    # Fall back to file + name when A and C came from different runs.
    candidates = [
        entry
        for entry in mapping.entries
        if entry.change.entity_name == change.entity_name
        and same_file(entry.change.file_path, change.file_path)
    ]
    if len(candidates) == 1:
        return candidates[0]
    if len(candidates) > 1:
        problems.append(
            Problem(
                WARNING,
                "join.ambiguous_mapping",
                f"several mapping entries match {change.entity_name} in {change.file_path}",
                change.file_path,
            )
        )
    return None


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def join_inputs(inputs: PipelineInputs, *, min_coverage: float = 0.5) -> JoinResult:
    """Build one :class:`ScoringUnit` per A change.

    Args:
        inputs: Result of :func:`loader.load_pipeline`.
        min_coverage: Warn when fewer than this fraction of A's changes have a C
            entry.  A low ratio usually means A and C describe different runs.

    Returns:
        Units in A's order, the behaviors that matched no change, the mapping
        coverage ratio, join statistics, and join-level problems.  Loader
        problems stay on ``inputs.problems`` and are not repeated here.
    """
    problems: list[Problem] = []
    changes = inputs.changes.changes
    behaviors = inputs.behavior.changes if inputs.behavior else ()
    adequacy = inputs.adequacy

    assignments, unassigned = _assign_behaviors(changes, behaviors, problems)

    units: list[ScoringUnit] = []
    for index, change in enumerate(changes):
        pairs = assignments.get(index, [])
        links = []
        for _, behavior in pairs:
            entry = _match_adequacy(behavior, adequacy.entries) if adequacy else None
            method = _pair_score(change, behavior)
            links.append(
                BehaviorLink(
                    behavior=behavior,
                    adequacy=entry,
                    method=method[0] if method else METHOD_UNMATCHED,
                )
            )
        links.sort(key=lambda link: _RANK.get(link.method, 99))
        units.append(
            ScoringUnit(
                change=change,
                mapping=_match_mapping(change, inputs.mapping, problems),
                links=tuple(links),
                method=links[0].method if links else METHOD_UNMATCHED,
            )
        )

    orphans = tuple(behavior for index, behavior in enumerate(behaviors) if index in unassigned)
    for behavior in orphans:
        problems.append(
            Problem(
                WARNING,
                "join.orphan_behavior",
                f"behavior change in {behavior.function} matched no structural change",
                behavior.file_path or behavior.function,
            )
        )

    coverage: float | None = None
    if inputs.mapping is not None and units:
        matched = sum(1 for unit in units if unit.mapped)
        coverage = matched / len(units)
        if coverage < min_coverage:
            problems.append(
                Problem(
                    WARNING,
                    "join.low_coverage",
                    f"only {matched}/{len(units)} changes have a mapping entry; "
                    "A and C may describe different revisions",
                )
            )

    stats = {
        "changes": len(units),
        "with_behaviors": sum(1 for unit in units if unit.has_behavior),
        "with_mapping": sum(1 for unit in units if unit.mapped),
        "with_adequacy": sum(1 for unit in units if unit.adequacy_entries),
        "orphan_behaviors": len(orphans),
        "selected_with_evidence": _selected_with_evidence(
            inputs.mapping.selected_tests if inputs.mapping else (),
            (entry for unit in units for entry in unit.adequacy_entries),
        ),
    }

    return JoinResult(
        units=tuple(units),
        orphan_behaviors=orphans,
        coverage=coverage,
        stats=stats,
        problems=tuple(problems),
    )
