"""Tests for potential behavior changes reported from Python AST structure."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from behavioral_change_detector import BehavioralChangeDetector


@pytest.fixture
def detector() -> BehavioralChangeDetector:
    return BehavioralChangeDetector(use_llm=False)


def by_type(report: dict, change_type: str) -> list[dict]:
    return [change for change in report["behavior_changes"] if change["type"] == change_type]


def test_new_branch_has_function_line_and_effect(detector: BehavioralChangeDetector) -> None:
    report = detector.detect(
        "def validate(user):\n    return True\n",
        "def validate(user):\n    if user.is_blocked:\n        raise PermissionError()\n    return True\n",
    )
    branch = by_type(report, "NEW_BRANCH")[0]
    assert branch["function"] == "validate"
    assert branch["new_line"] == 2
    assert branch["condition"] == "user.is_blocked"
    assert branch["effect"] == "raise PermissionError"
    assert "validate" in report["affected_functions"]
    assert any("True and False" in item for item in report["suggested_tests"])


def test_deleted_branch_is_reported(detector: BehavioralChangeDetector) -> None:
    report = detector.detect(
        "def check(value):\n    if value < 0:\n        return False\n    return True\n",
        "def check(value):\n    return True\n",
    )
    branch = by_type(report, "DELETED_BRANCH")[0]
    assert branch["condition"] == "value < 0"
    assert branch["old_line"] == 2
    assert branch["new_line"] is None


def test_modified_branch_condition_is_reported(detector: BehavioralChangeDetector) -> None:
    report = detector.detect(
        "def check(user):\n    if user.is_admin:\n        return True\n    return False\n",
        "def check(user):\n    if user.is_active:\n        return True\n    return False\n",
    )
    branch = by_type(report, "MODIFIED_BRANCH")[0]
    assert branch["old_condition"] == "user.is_admin"
    assert branch["new_condition"] == "user.is_active"


def test_numeric_boundary_change_and_targeted_tests(detector: BehavioralChangeDetector) -> None:
    report = detector.detect(
        "def check_age(age):\n    return age >= 18\n",
        "def check_age(age):\n    return age >= 21\n",
    )
    change = by_type(report, "BOUNDARY_CHANGE")[0]
    assert (change["variable"], change["operator"]) == ("age", ">=")
    assert (change["old_boundary"], change["new_boundary"]) == (18, 21)
    assert "17, 18, 19, 20, 21, 22" in report["suggested_tests"][0]


def test_comparison_operator_change(detector: BehavioralChangeDetector) -> None:
    report = detector.detect(
        "def check_age(age):\n    return age > 18\n",
        "def check_age(age):\n    return age >= 18\n",
    )
    change = by_type(report, "BOUNDARY_CHANGE")[0]
    assert change["old_operator"] == ">"
    assert change["new_operator"] == ">="
    assert change["old_boundary"] == change["new_boundary"] == 18


def test_exception_addition(detector: BehavioralChangeDetector) -> None:
    report = detector.detect(
        "def read_token(token):\n    return token\n",
        "def read_token(token):\n    if not token:\n        raise InvalidTokenError()\n    return token\n",
    )
    change = by_type(report, "EXCEPTION_BEHAVIOR_CHANGE")[0]
    assert change["operation"] == "added"
    assert change["new_exception_type"] == "InvalidTokenError"
    assert change["old_behavior"] is None


def test_exception_removal(detector: BehavioralChangeDetector) -> None:
    report = detector.detect(
        "def read_token(token):\n    raise InvalidTokenError()\n",
        "def read_token(token):\n    return token\n",
    )
    change = by_type(report, "EXCEPTION_BEHAVIOR_CHANGE")[0]
    assert change["operation"] == "modified"
    assert change["old_exception_type"] == "InvalidTokenError"
    assert change["old_behavior"] == "raise InvalidTokenError()"
    assert change["new_behavior"] == "return token"


def test_exception_type_change(detector: BehavioralChangeDetector) -> None:
    report = detector.detect(
        "def read_token(token):\n    raise ValueError()\n",
        "def read_token(token):\n    raise InvalidTokenError()\n",
    )
    change = by_type(report, "EXCEPTION_BEHAVIOR_CHANGE")[0]
    assert change["operation"] == "modified"
    assert change["old_exception_type"] == "ValueError"
    assert change["new_exception_type"] == "InvalidTokenError"


def test_return_expression_change(detector: BehavioralChangeDetector) -> None:
    report = detector.detect(
        "def calculate(amount):\n    return amount\n",
        "def calculate(amount):\n    return round(amount, 2)\n",
    )
    change = by_type(report, "RETURN_BEHAVIOR_CHANGE")[0]
    assert change["old_expression"] == "amount"
    assert change["new_expression"] == "round(amount, 2)"
    assert any("precision" in item for item in report["suggested_tests"])


def test_return_none_to_raise_is_an_exception_transition(
    detector: BehavioralChangeDetector,
) -> None:
    report = detector.detect(
        "def parse_token(token):\n    return None\n",
        "def parse_token(token):\n    raise InvalidTokenError()\n",
    )
    change = by_type(report, "EXCEPTION_BEHAVIOR_CHANGE")[0]
    assert change["old_behavior"] == "return None"
    assert change["new_behavior"] == "raise InvalidTokenError()"
    assert not by_type(report, "RETURN_BEHAVIOR_CHANGE")


def test_formatting_and_comments_do_not_change_ast(detector: BehavioralChangeDetector) -> None:
    report = detector.detect(
        "def total(items):\n    return sum(items)\n",
        "def total(items):\n    # unchanged calculation\n\n    return sum(items)\n",
    )
    assert report["behavior_changes"] == []
    assert "does not prove runtime equivalence" in report["behavioral_impact"]


def test_identical_source_has_stable_empty_schema(detector: BehavioralChangeDetector) -> None:
    code = "def identity(value):\n    return value\n"
    report = detector.detect(code, code)
    assert report["change_type"] == "unknown"
    assert report["behavior_changes"] == []
    assert report["suggested_tests"] == []
    assert {
        "change_type",
        "summary",
        "affected_functions",
        "behavioral_impact",
        "behavior_changes",
        "suggested_tests",
    } <= report.keys()
    json.dumps(report)


def test_syntax_error_is_reported_without_raising(detector: BehavioralChangeDetector) -> None:
    report = detector.detect("def valid():\n    return 1\n", "def broken(:\n")
    assert report["errors"][0]["side"] == "new"
    assert report["errors"][0]["type"] == "SyntaxError"
    assert report["behavior_changes"] == []
    assert report["analysis"]["llm_status"] == "not_run"


def test_multiple_functions_are_matched_by_scope(detector: BehavioralChangeDetector) -> None:
    old = "def first(x):\n    return x + 1\n\ndef second(y):\n    return y > 3\n"
    new = "def added():\n    return 0\n\ndef first(x):\n    return x + 1\n\ndef second(y):\n    return y >= 3\n"
    report = detector.detect(old, new)
    assert report["affected_functions"] == ["second"]
    assert {item["type"] for item in report["behavior_changes"]} == {
        "BOUNDARY_CHANGE",
        "RETURN_BEHAVIOR_CHANGE",
    }
    assert all(item["function"] == "second" for item in report["behavior_changes"])
    boundary = by_type(report, "BOUNDARY_CHANGE")[0]
    assert boundary["new_line"] == 8


def test_llm_is_not_called_without_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BEHAVIOR_LLM_API_KEY", raising=False)
    import requests

    def unexpected_request(*args, **kwargs):
        raise AssertionError("LLM request should not be made without an API key")

    monkeypatch.setattr(requests, "post", unexpected_request)
    report = BehavioralChangeDetector().detect(
        "def f(x):\n    return x\n", "def f(x):\n    return x + 1\n"
    )
    assert report["analysis"]["llm_status"] == "disabled"
    assert by_type(report, "RETURN_BEHAVIOR_CHANGE")


def test_llm_failure_falls_back_to_rule_summary(detector: BehavioralChangeDetector) -> None:
    class FailedClient:
        def summarize(self, payload: dict) -> dict:
            raise RuntimeError("service unavailable")

    result = BehavioralChangeDetector(use_llm=True, llm_client=FailedClient()).detect(
        "def f(x):\n    return x\n", "def f(x):\n    return round(x, 2)\n"
    )
    assert result["analysis"]["llm_status"] == "fallback"
    assert "RuntimeError" in result["analysis"]["llm_error"]
    assert "Changed return expression" in result["summary"]
    assert by_type(result, "RETURN_BEHAVIOR_CHANGE")


def test_llm_explanation_cannot_add_affected_functions_or_evidence() -> None:
    class ExaggeratingClient:
        def summarize(self, payload: dict) -> dict:
            return {
                "change_type": "enhancement",
                "summary": "Explained the detected change.",
                "affected_functions": ["invented_function"],
                "behavioral_impact": "Additional explanation.",
            }

    report = BehavioralChangeDetector(
        use_llm=True, llm_client=ExaggeratingClient()
    ).detect("def f(x):\n    return x\n", "def f(x):\n    return x + 1\n")
    assert report["change_type"] == "enhancement"
    assert report["affected_functions"] == ["f"]
    assert {item["type"] for item in report["behavior_changes"]} == {
        "RETURN_BEHAVIOR_CHANGE"
    }
    assert report["analysis"]["llm_status"] == "used"
