"""Frozen acceptance evaluation; never label synthetic results as field accuracy."""
from __future__ import annotations

import hashlib
import math
from pathlib import Path
import statistics
import time

from integration import TOOLS, read_json, score_reports, write_json
from rendering import render_comment, render_html


def metrics(pairs):
    known = [(truth, prediction) for truth, prediction in pairs if type(truth) is bool and type(prediction) is bool]
    tp = sum(truth and prediction for truth, prediction in known)
    fp = sum(not truth and prediction for truth, prediction in known)
    tn = sum(not truth and not prediction for truth, prediction in known)
    fn = sum(truth and not prediction for truth, prediction in known)
    return {"evaluated": len(known), "tp": tp, "fp": fp, "tn": tn, "fn": fn,
            "precision": tp / (tp + fp) if tp + fp else None,
            "recall": tp / (tp + fn) if tp + fn else None,
            "specificity": tn / (tn + fp) if tn + fp else None}


def coverage_presence_baseline(changes, mapping):
    """Explicit comparator: treat historical related tests as sufficient.

    This is deliberately a limited heuristic built from testmon data, not a
    claim that testmon itself predicts adequacy. Unknown mappings abstain.
    """
    if changes["changes"] and all(item.get("change_category") == "Documentation change" for item in changes["changes"]):
        return False
    entries = mapping.get("changes", [])
    if any(item.get("status") in {"unresolved", "needs_new_tests"} for item in entries) or not entries:
        return None
    if all(item.get("tests") or item.get("status") == "skipped_documentation" for item in entries):
        return False
    return True


def evaluate(manifest_path, out):
    manifest_path, out = Path(manifest_path).resolve(), Path(out).resolve()
    manifest = read_json(manifest_path)
    repetitions = manifest.get("repetitions")
    threshold = manifest.get("prediction_threshold")
    if manifest.get("schema_version") != "1.0" or type(repetitions) is not int or not 1 <= repetitions <= 100:
        raise ValueError("Invalid evaluation contract/repetitions")
    if type(threshold) not in (int, float) or not 0 <= threshold <= 100:
        raise ValueError("Invalid frozen prediction threshold")
    criteria = manifest["criteria"]
    required = ("precision_min", "recall_min", "specificity_min", "p95_ms_max")
    if any(type(criteria.get(key)) not in (int, float) or not math.isfinite(criteria[key]) for key in required):
        raise ValueError("Invalid acceptance criteria")
    if not 0 <= min(criteria[key] for key in required[:3]) or max(criteria[key] for key in required[:3]) > 1 or criteria["p95_ms_max"] <= 0:
        raise ValueError("Acceptance criteria are outside their valid ranges")
    cases, samples, predictions, baseline = [], [], [], []
    identities = set()
    out.mkdir(parents=True, exist_ok=True)
    for case in manifest["cases"]:
        identifier = case["id"]
        if identifier in identities or not isinstance(identifier, str) or not identifier.replace("_", "").isalnum():
            raise ValueError("Invalid or duplicate evaluation case ID")
        identities.add(identifier)
        if case.get("gap") is not None and type(case["gap"]) is not bool:
            raise ValueError("Ground truth must be true, false, or null")
        directory = (TOOLS / case["directory"]).resolve()
        directory.relative_to(TOOLS)
        report, durations = None, []
        for _ in range(repetitions):
            start = time.perf_counter()
            report = score_reports(directory / "changes.json", directory / case.get("behavior", "behavior.json"),
                                   directory / "mapping_result.json", directory / "adequacy.json")
            comment = render_comment(report)
            render_html(report, comment)
            durations.append((time.perf_counter() - start) * 1000)
        prediction = any(gap["gap_score"] >= threshold for gap in report["gaps"])
        predictions.append((case.get("gap"), prediction))
        baseline_prediction = coverage_presence_baseline(read_json(directory / "changes.json"), read_json(directory / "mapping_result.json"))
        if baseline_prediction is not case.get("baseline_gap"):
            raise ValueError(f"Baseline contract changed for case {identifier}")
        baseline.append((case.get("gap"), baseline_prediction))
        disclosure = case.get("gap") is not None or any(gap["confidence"] != "HIGH" for gap in report["gaps"]) or bool(report["problems"])
        samples.extend(durations)
        write_json(out / f"{identifier}.json", report)
        cases.append({"id": identifier, "ground_truth": case.get("gap"), "prediction": prediction,
            "baseline_prediction": baseline_prediction, "rationale": case["rationale"],
            "uncertainty_disclosed": disclosure, "mean_ms": round(statistics.mean(durations), 3)})
    if not samples:
        raise ValueError("Evaluation requires at least one case")
    measured = metrics(predictions)
    p95 = sorted(samples)[math.ceil(len(samples) * .95) - 1]
    checks = {name: measured[metric] is not None and measured[metric] >= criteria[name]
              for name, metric in (("precision_min", "precision"), ("recall_min", "recall"), ("specificity_min", "specificity"))}
    checks["p95_ms_max"] = p95 <= criteria["p95_ms_max"]
    checks["uncertainty_disclosed"] = all(case["uncertainty_disclosed"] for case in cases)
    result = {"schema_version": "1.0", "contract_id": manifest["contract_id"],
        "contract_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
        "dataset_kind": manifest["dataset_kind"], "threshold": threshold, "criteria": criteria,
        "testpilot": measured, "baseline_kind": manifest["baseline"], "baseline": metrics(baseline),
        "unknown_ground_truth_excluded": sum(case["ground_truth"] is None for case in cases),
        "timing": {"scope": "load A-D + score E + render F; excludes git, pytest and network",
                   "samples": len(samples), "p95_ms": round(p95, 3), "mean_ms": round(statistics.mean(samples), 3)},
        "checks": checks, "passed": all(checks.values()), "cases": cases,
        "limitations": ["Six synthetic acceptance cases are not a representative benchmark.",
            "Baseline predictions describe an explicit coverage-presence heuristic, not a native testmon gap detector.",
            "Labels are fixed contract judgements; independent real-project annotation and user feedback remain necessary."]}
    write_json(out / "evaluation.json", result)
    lines = ["# TestPilot F acceptance evaluation", "", f"Contract: {manifest['contract_id']}",
             f"Passed: {result['passed']}", "", "Synthetic acceptance cases; not real-world accuracy.", "",
             "| Metric | TestPilot | Coverage-presence heuristic |", "| --- | --- | --- |"]
    for metric in ("precision", "recall", "specificity"):
        lines.append(f"| {metric} | {measured[metric]} | {result['baseline'][metric]} |")
    lines += ["", f"p95: {p95:.3f} ms ({len(samples)} samples). Scope: {result['timing']['scope']}.",
              "", f"Unknown labels excluded: {result['unknown_ground_truth_excluded']}.", "", "## Acceptance checks", ""]
    lines += [f"- {name}: {'PASS' if passed else 'FAIL'}" for name, passed in checks.items()]
    (out / "evaluation.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return result

