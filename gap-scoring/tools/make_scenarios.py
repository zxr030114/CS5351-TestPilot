"""Generate the scenario fixtures under ``fixtures/scenarios/``.

Each scenario is a self-consistent set of the four stage reports describing one
small change, so stage E can be exercised without running A-D.  They exist to pin
down *behaviour per risk class*, not to mirror any particular project:

======  ==========================================  ======================
id      what changed                                expected outcome
======  ==========================================  ======================
00      return precision (round 2 -> 1)             LOW 43.55, evidence review
01      exception added, no test reaches it          HIGH 96.25
02      boundary 18 -> 21, executed but unasserted   MEDIUM ~79.9
03      new branch, properly asserted                NONE (suppressed to 0)
04      docstring only                               not scored
05      dependency mapping unresolved                MEDIUM ~79.3, confidence MEDIUM
======  ==========================================  ======================

Run ``python tools/make_scenarios.py`` to regenerate (output is committed, so the
tests do not depend on this script at run time).
"""

from __future__ import annotations

import json
from pathlib import Path

BASE = Path(__file__).resolve().parents[1] / "fixtures" / "scenarios"


def change(
    *,
    file_path: str,
    entity_name: str,
    category: str,
    line_start: int = 1,
    line_end: int = 10,
) -> dict:
    return {
        "entity_type": "function",
        "entity_name": entity_name,
        "change_type": "modify",
        "change_category": category,
        "line_start": line_start,
        "line_end": line_end,
        "summary": "AST structural change in function body",
        "file_path": file_path,
    }


def behavior(kind: str, function: str, **details) -> dict:
    payload = {
        "type": kind,
        "function": function,
        "scope": function,
        "old_line": None,
        "new_line": details.pop("new_line", 6),
        "confidence": "potential",
    }
    payload.update(details)
    return payload


def changes_doc(change_record: dict, *, owner: str = "demo", name: str = "shop", number: int = 1) -> dict:
    return {
        "schema_version": "2.0",
        "mode": "pr_ast",
        "repo": {
            "owner": owner,
            "name": name,
            "base_sha": "0" * 40,
            "head_sha": "1" * 40,
            "pr_number": number,
        },
        "mirror": {"out_dir": "", "counts": {"files": 1, "downloaded": 2, "kept": 0, "failed": 0}},
        "summary": {
            "files_changed": 1,
            "files_analyzed": 1,
            "files_skipped": 0,
            "changes": 1,
            "renames": 0,
            "by_change_type": {change_record["change_type"]: 1},
            "by_change_category": {change_record["change_category"]: 1},
            "warnings": 0,
        },
        "files": [
            {
                "file_path": change_record["file_path"],
                "language": "python",
                "is_test_file": False,
                "changes": [change_record],
            }
        ],
        "changes": [change_record],
        "skipped": [],
        "warnings": [],
    }


def behavior_doc(records: list[dict]) -> dict:
    return {
        "schema_version": "1.0",
        "change_type": "unknown",
        "summary": "scenario fixture",
        "affected_functions": sorted({record["function"] for record in records}),
        "behavioral_impact": "Potential impact only; static AST analysis does not prove runtime behavior.",
        "behavior_changes": records,
        "suggested_tests": [],
        "errors": [],
        "analysis": {"method": "python-ast", "runtime_behavior_proven": False, "llm_status": "disabled"},
    }


def mapping_doc(change_record: dict, status: str, tests: list[str]) -> dict:
    return {
        "schema_version": "1.0",
        "mode": "test_dependency_mapping",
        "base_sha": "0" * 40,
        "environment": "default",
        "selected_tests": list(tests),
        "selection_complete": status != "unresolved",
        "changes": [
            {
                "change": change_record,
                "status": status,
                "tests": list(tests),
                "evidence": {
                    "file_path": change_record["file_path"].split("/")[-1],
                    "function": change_record["entity_name"],
                    "environment": "default",
                    "evidence_level": "testmon_block_fingerprint",
                    "status": status,
                    "tests": [],
                    "unresolved_tests": [],
                    "limitations": [],
                },
            }
        ],
        "limitations": [],
        "summary": {
            "changes": 1,
            "selected_tests": len(tests),
            "unresolved_changes": int(status == "unresolved"),
            "file_fallbacks": int(status == "file_fallback"),
        },
    }


def adequacy_doc(behavior_record: dict, status: str, evidence: list[dict], tests: list[str]) -> dict:
    return {
        "schema_version": "1.0",
        "mode": "test_adequacy_analysis",
        "analysis": {
            "method": "python-ast",
            "runtime_adequacy_proven": False,
            "input_complete": True,
            "selection_complete": True,
        },
        "results": [
            {
                "change": behavior_record,
                "status": status,
                "candidate_tests": list(tests),
                "evidence": evidence,
                "recommendations": [],
            }
        ],
        "tests": {},
        "errors": [],
        "summary": {"behavior_changes": 1, "tests_analyzed": len(tests), "with_assertion_evidence": 0},
        "limitations": [],
    }


SCENARIOS: list[dict] = [
    {
        "name": "01-exception-uncovered",
        "note": "a new exception type appears and no test reaches the changed branch",
        "change": change(file_path="shop/cart.py", entity_name="checkout", category="Logic change"),
        "behavior": behavior(
            "EXCEPTION_BEHAVIOR_CHANGE",
            "checkout",
            operation="added",
            new_exception_type="InsufficientFunds",
            old_exception_type=None,
            new_behavior="raise InsufficientFunds('cart is empty')",
            new_line=6,
        ),
        "mapping_status": "matched",
        "mapping_tests": ["tests/test_cart.py::test_checkout_basic"],
        "adequacy_status": "no_candidate_test",
        "adequacy_evidence": [],
        "expected": {"band": "HIGH", "score": 96.25, "confidence": "HIGH", "scored": True},
    },
    {
        "name": "02-boundary-no-assertion",
        "note": "a comparison boundary moves and a test runs through it without asserting",
        "change": change(file_path="shop/discount.py", entity_name="apply_discount", category="Logic change"),
        "behavior": behavior(
            "BOUNDARY_CHANGE",
            "apply_discount",
            variable="age",
            operator=">=",
            old_boundary=18,
            new_boundary=21,
            new_line=4,
        ),
        "mapping_status": "matched",
        "mapping_tests": ["tests/test_discount.py::test_apply_discount"],
        "adequacy_status": "no_matching_assertion",
        "adequacy_evidence": [],
        "expected": {"band": "MEDIUM", "score": 79.92, "confidence": "HIGH", "scored": True},
    },
    {
        "name": "03-new-branch-covered",
        "note": "a new branch is added and an existing test asserts its effect",
        "change": change(file_path="shop/cart.py", entity_name="total", category="Logic change"),
        "behavior": behavior(
            "NEW_BRANCH",
            "total",
            condition="item.gift",
            new_condition="item.gift",
            effect="skip tax",
            new_effect="skip tax",
            branch_kind="if",
            new_line=8,
        ),
        "mapping_status": "matched",
        "mapping_tests": ["tests/test_cart.py::test_gift_items_skip_tax"],
        "adequacy_status": "assertion_evidence_found",
        "adequacy_evidence": [
            {
                "nodeid": "tests/test_cart.py::test_gift_items_skip_tax",
                "line": 12,
                "kind": "assert",
                "strength": "potential_match",
                "assertion": {"kind": "assert", "line": 12, "expression": "total(cart) == 0", "strength": "comparison"},
                "calls": [{"target": "shop.cart.total", "expression": "total(cart)", "line": 12, "literal_arguments": []}],
            }
        ],
        "expected": {"band": "NONE", "score": 0.0, "confidence": "HIGH", "scored": True},
    },
    {
        "name": "04-documentation-only",
        "note": "only a docstring changed; the report must not turn this into a gap",
        "change": change(file_path="shop/shipping.py", entity_name="quote", category="Documentation change"),
        "behavior": behavior(
            "RETURN_BEHAVIOR_CHANGE",
            "quote",
            old_expression="weight * rate",
            new_expression="weight * rate",
            new_line=5,
        ),
        "mapping_status": "skipped_documentation",
        "mapping_tests": [],
        "adequacy_status": "assertion_evidence_found",
        "adequacy_evidence": [],
        "expected": {"band": "NONE", "score": 0.0, "confidence": "HIGH", "scored": False},
    },
    {
        "name": "05-mapping-unresolved",
        "note": "dependency mapping cannot place the change, so coverage is unknown",
        "change": change(file_path="shop/report.py", entity_name="render", category="Logic change"),
        "behavior": behavior(
            "RETURN_BEHAVIOR_CHANGE",
            "render",
            old_expression="render_rows(rows)",
            new_expression="render_rows(sorted(rows))",
            new_line=9,
        ),
        "mapping_status": "unresolved",
        "mapping_tests": [],
        "adequacy_status": "no_candidate_test",
        "adequacy_evidence": [],
        # unresolved implies selection_complete=false, so two caveats stack: the
        # mapping is unusable *and* the selection it produced is known incomplete.
        "expected": {"band": "MEDIUM", "score": 79.3, "confidence": "LOW", "scored": True},
    },
]


def write_scenario(spec: dict) -> dict:
    target = BASE / spec["name"]
    target.mkdir(parents=True, exist_ok=True)
    change_record = spec["change"]
    behavior_record = spec["behavior"]

    (target / "changes.json").write_text(
        json.dumps(changes_doc(change_record), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (target / "behavior.json").write_text(
        json.dumps(behavior_doc([behavior_record]), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (target / "mapping_result.json").write_text(
        json.dumps(
            mapping_doc(change_record, spec["mapping_status"], spec["mapping_tests"]),
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    (target / "adequacy.json").write_text(
        json.dumps(
            adequacy_doc(behavior_record, spec["adequacy_status"], spec["adequacy_evidence"], spec["mapping_tests"]),
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return {
        "name": spec["name"],
        "note": spec["note"],
        "expected": spec["expected"],
    }


def main() -> int:
    BASE.mkdir(parents=True, exist_ok=True)
    manifest = [write_scenario(spec) for spec in SCENARIOS]
    # Scenario 00 reuses the original payment fixtures one directory up.
    manifest.insert(
        0,
        {
            "name": "00-precision-blind",
            "note": "return precision changed; the only assertion uses integers",
            "path": ".",
            "expected": {"band": "LOW", "score": 43.55, "confidence": "HIGH", "scored": True},
        },
    )
    (BASE / "expected.json").write_text(
        json.dumps({"scenarios": manifest}, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"wrote {len(manifest)} scenarios to {BASE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
