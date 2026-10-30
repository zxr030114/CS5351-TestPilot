"""Confidence in a gap score.

A gap score is only as trustworthy as the data behind it.  This module turns the
qualitative warnings collected while loading, joining and scoring into one of
three levels:

``HIGH``    every stage that fed this number was complete and matched cleanly
``MEDIUM``  one caveat applies (assumed level, file-level mapping, weak join...)
``LOW``     two or more caveats, or a missing upstream stage

The model is deliberately a simple penalty count: each distinct signal costs
points, ``0`` points is HIGH, ``1`` is MEDIUM, ``2+`` is LOW.  Keeping it dumb
makes it explainable in a PR comment, which is the whole point -- a reviewer who
sees ``LOW`` needs to know which caveat dragged it down.

Signals are strings so they can be logged, tested and later rendered without a
lookup table drifting out of sync with the code that raises them.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

__all__ = [
    "Confidence",
    "LEVELS",
    "SIGNAL_PENALTIES",
    "assess",
]

#: How much each caveat costs.  Missing stage D is the most serious because the
#: adequacy level is then entirely assumed.
SIGNAL_PENALTIES: dict[str, int] = {
    "no_adequacy": 2,
    "assumed_level": 1,
    "no_mapping": 1,
    "selection_incomplete": 1,
    "file_fallback": 1,
    "mapping_unresolved": 1,
    "weak_join": 1,
    "uncertain_evidence": 1,
    "input_incomplete": 1,
}

#: Ordered from best to worst.
LEVELS: tuple[str, str, str] = ("HIGH", "MEDIUM", "LOW")

#: Penalty applied to a signal this module does not know yet.
UNKNOWN_SIGNAL_PENALTY = 1


@dataclass(frozen=True)
class Confidence:
    """The level, the total penalty, and the signals that caused it."""

    level: str
    penalty: int
    signals: tuple[str, ...] = ()

    @property
    def high(self) -> bool:
        return self.level == LEVELS[0]

    def __str__(self) -> str:
        if not self.signals:
            return self.level
        return f"{self.level} ({', '.join(self.signals)})"


def assess(signals: Iterable[str]) -> Confidence:
    """Combine caveat *signals* into a confidence level.

    Duplicates count once: two join heuristics firing is one caveat, not two.

    >>> assess([]).level
    'HIGH'
    >>> assess(["file_fallback"]).level
    'MEDIUM'
    >>> assess(["file_fallback", "weak_join"]).level
    'LOW'
    """
    unique: list[str] = []
    for signal in signals:
        if signal not in unique:
            unique.append(signal)

    penalty = sum(SIGNAL_PENALTIES.get(signal, UNKNOWN_SIGNAL_PENALTY) for signal in unique)
    if penalty <= 0:
        level = LEVELS[0]
    elif penalty == 1:
        level = LEVELS[1]
    else:
        level = LEVELS[2]
    return Confidence(level=level, penalty=penalty, signals=tuple(unique))
