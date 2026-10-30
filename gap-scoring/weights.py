"""All tunable numbers used by gap scoring, in one place.

Scoring must stay explainable, so every magic number lives here and is cited in
the contract (``docs/gap-scoring-contract.zh.md`` §3).  Calibration is expected
to change these constants only -- never the formulas in ``scoring.py``.

Three factors feed the base score:

``BehaviorRisk``      how dangerous the *kind* of behavior change is (stage B)
``ChangeImportance``  how structurally significant the change is (stage A)
``AdequacyDeficit``   1 - how well a test verifies it (stage D)

``Base = 100 * (0.45 * BehaviorRisk * ChangeImportance + 0.55 * AdequacyDeficit)``

The weights 0.45 / 0.55 are calibrated so the reference case in the contract
(``withdraw``: exception change, ``Logic change``, executed-but-unasserted)
lands on 79 before rule bonuses and 87 after them.
"""

from __future__ import annotations

from typing import Final

# ---------------------------------------------------------------------------
# Factor tables
# ---------------------------------------------------------------------------

#: ``change_category`` (stage A) -> importance.  ``Documentation change`` is 0.0
#: because A already proved the runtime behaviour did not change.
CHANGE_IMPORTANCE: Final[dict[str, float]] = {
    "Signature change": 1.00,
    "Logic change": 0.90,
    "add": 0.70,
    "delete": 0.60,
    "rename": 0.30,
    "Documentation change": 0.00,
}

#: ``change_type`` (stage A) -> importance, used when ``change_category`` is null.
CHANGE_TYPE_IMPORTANCE: Final[dict[str, float]] = {
    "add": 0.70,
    "delete": 0.60,
    "rename": 0.30,
    "modify": 0.90,
}

#: ``behavior_changes[].type`` (stage B) -> risk.
BEHAVIOR_RISK: Final[dict[str, float]] = {
    "EXCEPTION_BEHAVIOR_CHANGE": 1.00,
    "BOUNDARY_CHANGE": 0.85,
    "NEW_BRANCH": 0.70,
    "MODIFIED_BRANCH": 0.70,
    "DELETED_BRANCH": 0.60,
    "RETURN_BEHAVIOR_CHANGE": 0.60,
}

#: Fallback risk for a behaviour type B introduces after this module was written.
DEFAULT_BEHAVIOR_RISK: Final[float] = 0.70

#: Stage D status -> adequacy level on the 0..3 scale (``None`` = cannot score).
#: The contract's Level 1 means "a related test exists but never reaches the
#: change"; D encodes that as ``no_candidate_test`` on a matched mapping, which
#: ``scoring`` resolves from the C entry rather than from this table alone.
#:
#: ``weak_assertions`` sits at 2.5, between "executed but not asserted" (2.0)
#: and "asserted" (3.0): an assertion exists, it just does not check the changed
#: effect.  Mapping it below 2.0 would rank weak evidence worse than none, which
#: inverts the ranking (caught by tests/test_scoring.py::Ordering).
ADEQUACY_STATUS_LEVEL: Final[dict[str, float | None]] = {
    "no_candidate_test": 0.0,
    "no_matching_assertion": 2.0,
    "weak_assertions": 2.5,
    "assertion_evidence_found": 3.0,
    "unsupported_behavior": None,
}

#: Level -> deficit (1 - confidence that a test verifies the change).  The
#: values must decrease strictly as the level rises; ``test_scoring`` enforces it.
ADEQUACY_DEFICIT: Final[dict[float, float]] = {
    0.0: 1.00,
    1.0: 0.85,
    2.0: 0.70,
    2.5: 0.35,
    3.0: 0.00,
}

#: Used when stage D is absent.  Level 2 is "executed but not asserted", the
#: conservative middle ground documented in the contract (§7.4).
DEFAULT_LEVEL: Final[float] = 2.0

#: Used for the deficit when the level is outside :data:`ADEQUACY_DEFICIT`.
DEFAULT_DEFICIT: Final[float] = 0.70

# ---------------------------------------------------------------------------
# Formula weights
# ---------------------------------------------------------------------------

#: Weight of the structural term (risk x importance) in the base score.
WEIGHT_STRUCTURAL: Final[float] = 0.45

#: Weight of the coverage term (deficit) in the base score.
WEIGHT_COVERAGE: Final[float] = 0.55

#: Maximum total rule bonus; keeps a pile of small rules from dominating.
BONUS_CAP: Final[float] = 15.0

# ---------------------------------------------------------------------------
# Rule bonuses (see contract §3.2)
# ---------------------------------------------------------------------------

RULE_EXCEPTION_WITHOUT_RAISES: Final[float] = 5.0
RULE_BOUNDARY_WITHOUT_TEST: Final[float] = 4.0
RULE_EXECUTED_WITHOUT_ASSERTION: Final[float] = 3.0
RULE_RELATED_TESTS_DO_NOT_REACH: Final[float] = 4.0
RULE_NEW_CODE_WITHOUT_BASELINE: Final[float] = 3.0
RULE_FILE_LEVEL_FALLBACK: Final[float] = 2.0

# ---------------------------------------------------------------------------
# Reporting bands
# ---------------------------------------------------------------------------

#: ``(minimum score, band)`` ordered high to low.  A band of ``NONE`` means the
#: change is adequately covered and should not appear in the PR report.
BANDS: Final[tuple[tuple[float, str], ...]] = (
    (80.0, "HIGH"),
    (50.0, "MEDIUM"),
    (20.0, "LOW"),
    (0.0, "NONE"),
)

#: A change whose behaviour a test verifies carries no gap, no matter how
#: important the change is structurally.  Applied only when no rule fired.
SUPPRESS_WHEN_COVERED: Final[bool] = True

# ---------------------------------------------------------------------------
# Evidence review (stage D status is optimistic; verify it against evidence[])
# ---------------------------------------------------------------------------

#: ``evidence[].strength`` values that do not actually confirm a behaviour.
EVIDENCE_WEAK: Final[frozenset[str]] = frozenset({"weak_or_mismatched"})

#: ``evidence[].strength`` values that confirm *a* call, but not necessarily the
#: changed effect.
EVIDENCE_MATCH: Final[frozenset[str]] = frozenset({"potential_match"})

#: Level applied when every piece of evidence is weak.
LEVEL_ALL_EVIDENCE_WEAK: Final[float] = 2.0

#: Level applied when the only matching assertion cannot observe the change
#: (an integer example against a precision change).
LEVEL_PRECISION_BLIND: Final[float] = 2.5
