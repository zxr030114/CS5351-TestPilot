"""Behavioral checks for F's public contract; all GitHub traffic is faked."""
from __future__ import annotations

import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import urllib.error
from urllib.parse import urlparse, parse_qs

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from rendering import MARKER, LIMIT, render_comment, render_failure, render_html, validate_report
from github_client import GitHubClient, GitHubError
from integration import TOOLS, score_reports, write_json, relative_path
from evaluation import metrics, evaluate
from pr_reporter import main, demo

SHA = "a" * 40


def report(scored=1, high=0):
    return {"schema_version": "1.0", "module": "test_gap_scoring",
        "pr": {"repo": "example/project", "number": 7, "head_sha": SHA},
        "inputs": {"behavior": "loaded", "mapping": "loaded", "adequacy": "loaded"},
        "summary": {"changes_total": 1, "changes_scored": scored, "gaps_high": high, "gaps_medium": 0, "gaps_low": 0},
        "gaps": [], "unscored": [], "problems": []}


def gap(score=90):
    return {"file_path": "payment.py", "entity_name": "withdraw", "risk": "HIGH", "confidence": "LOW", "gap_score": score,
        "affected_tests": ["test_payment.py::test_x"], "explanation": {"changed_behavior": "new exception",
        "problem": "no related exception assertion", "suggested_test_input": "withdraw(100, 101)"}}


class RenderingTests(unittest.TestCase):
    def test_no_data_is_not_a_clean_bill_of_health(self):
        text = render_comment(report(scored=0))
        self.assertIn("No changes could be scored", text)
        self.assertNotIn("adequately tested", text)

    def test_empty_results_remain_qualified(self):
        self.assertIn("does not prove test adequacy", render_comment(report()))

    def test_missing_inputs_and_confidence_are_visible(self):
        data = report(high=1)
        data["inputs"]["mapping"] = "missing"
        data["gaps"] = [gap()]
        text = render_comment(data)
        self.assertIn("Degraded analysis", text)
        self.assertIn("Confidence: **LOW**", text)

    def test_mentions_and_markup_are_not_executable(self):
        data = report(high=1)
        data["gaps"] = [gap()]
        data["gaps"][0]["explanation"]["problem"] = '@everyone <script>alert(1)</script> [click](https://bad.invalid)'
        text = render_comment(data)
        self.assertNotIn("@everyone", text)
        self.assertNotIn("<script>", text)
        self.assertNotIn("[click](https://bad.invalid)", text)
        self.assertNotIn("<script>", render_html(data, text))

    def test_top_n_is_bounded_and_omissions_disclosed(self):
        data = report(high=8)
        data["summary"].update(changes_total=8, changes_scored=8)
        data["gaps"] = [gap() for _ in range(8)]
        text = render_comment(data, 2)
        self.assertEqual(text.count("### payment"), 2)
        self.assertIn("6 additional", text)

    def test_unscored_and_problems_are_disclosed(self):
        data = report()
        data["unscored"] = [{"reason": "unsupported"}]
        data["problems"] = [{"message": "test sources unavailable"}]
        self.assertIn("test sources unavailable", render_comment(data))

    def test_comment_stays_below_github_limit(self):
        data = report(high=20)
        data["summary"].update(changes_total=20, changes_scored=20)
        data["gaps"] = [gap() for _ in range(20)]
        for item in data["gaps"]:
            item["explanation"] = {key: "x" * 100000 for key in item["explanation"]}
        self.assertLessEqual(len(render_comment(data, 20)), LIMIT + 1)

    def test_wrong_schema_is_rejected(self):
        data = report()
        data["schema_version"] = "2.0"
        with self.assertRaises(ValueError):
            validate_report(data)

    def test_nan_is_rejected(self):
        data = report(high=1)
        data["gaps"] = [gap(float("nan"))]
        with self.assertRaises(ValueError):
            validate_report(data)

    def test_inconsistent_gate_counts_are_rejected(self):
        data = report()
        data["gaps"] = [gap()]
        with self.assertRaises(ValueError):
            validate_report(data)

    def test_negative_and_boolean_counts_are_rejected(self):
        for value in (-1, True):
            data = report()
            data["summary"]["gaps_high"] = value
            with self.assertRaises(ValueError):
                validate_report(data)

    def test_untrusted_extra_input_fields_cannot_inject_mentions(self):
        data = report()
        data["inputs"]["@everyone"] = "<script>"
        self.assertNotIn("@everyone", render_comment(data))
        self.assertNotIn("<script>", render_comment(data))


class FakeClient(GitHubClient):
    def __init__(self, comments=(), sha=SHA):
        super().__init__("dummy-not-a-credential")
        self.comments, self.sha, self.calls = list(comments), sha, []

    def request(self, method, path, body=None):
        self.calls.append((method, path, body))
        if path.endswith("/pulls/7"):
            return {"head": {"sha": self.sha}, "state": "open", "base": {"repo": {"full_name": "example/project"}}}
        if path == "/user":
            return {"login": "testpilot-bot"}
        if method == "GET":
            return self.comments
        return {"id": 9}


class PublishingTests(unittest.TestCase):
    def test_first_publication_creates_comment(self):
        client = FakeClient()
        result = client.publish("example/project", 7, MARKER + "\nreport", expected_head=SHA)
        self.assertEqual(result["action"], "created")
        self.assertEqual(client.calls[-1][0], "POST")

    def test_rerun_updates_own_comment(self):
        client = FakeClient([{"id": 3, "body": MARKER + "old", "user": {"login": "testpilot-bot"}}])
        self.assertEqual(client.publish("example/project", 7, MARKER + "new", expected_head=SHA)["action"], "updated")
        self.assertEqual(client.calls[-1][0], "PATCH")

    def test_same_body_causes_no_write(self):
        client = FakeClient([{"id": 3, "body": MARKER, "user": {"login": "testpilot-bot"}}])
        self.assertEqual(client.publish("example/project", 7, MARKER, expected_head=SHA)["action"], "unchanged")
        self.assertFalse(any(method in {"POST", "PATCH"} for method, _, _ in client.calls))

    def test_cannot_edit_someone_elses_marker(self):
        client = FakeClient([{"id": 3, "body": MARKER, "user": {"login": "someone-else"}}])
        self.assertEqual(client.publish("example/project", 7, MARKER, expected_head=SHA)["action"], "created")

    def test_stale_report_cannot_be_published(self):
        client = FakeClient(sha="b" * 40)
        with self.assertRaises(GitHubError):
            client.publish("example/project", 7, MARKER, expected_head=SHA)
        self.assertFalse(any(method in {"POST", "PATCH"} for method, _, _ in client.calls))

    def test_head_race_is_checked_again_before_write(self):
        client = FakeClient()
        original = client.request
        count = 0
        def changing(method, path, body=None):
            nonlocal count
            if path.endswith("/pulls/7"):
                count += 1
                if count == 2:
                    client.sha = "b" * 40
            return original(method, path, body)
        client.request = changing
        with self.assertRaises(GitHubError):
            client.publish("example/project", 7, MARKER, expected_head=SHA)

    def test_invalid_target_rejected_before_network(self):
        client = FakeClient()
        with self.assertRaises(ValueError):
            client.publish("../bad", 7, MARKER, expected_head=SHA)
        self.assertEqual(client.calls, [])

    def test_permission_error_never_echoes_response_or_token(self):
        def forbidden(request, timeout):
            raise urllib.error.HTTPError(request.full_url, 403, "forbidden", {}, io.BytesIO(b"private-data"))
        client = GitHubClient("dummy-sensitive-value", opener=forbidden)
        with self.assertRaises(GitHubError) as ctx:
            client.request("GET", "/user")
        self.assertNotIn("private-data", str(ctx.exception))
        self.assertNotIn("dummy-sensitive-value", str(ctx.exception))

    def test_pagination_finds_existing_comment_on_second_page(self):
        client = FakeClient()
        original = client.request
        def pages(method, path, body=None):
            if parse_qs(urlparse(path).query).get("page") == ["1"]:
                return [{"body": "other", "user": {"login": "other"}}] * 100
            if parse_qs(urlparse(path).query).get("page") == ["2"]:
                return [{"id": 4, "body": MARKER + "old", "user": {"login": "testpilot-bot"}}]
            return original(method, path, body)
        client.request = pages
        self.assertEqual(client.publish("example/project", 7, MARKER, expected_head=SHA)["action"], "updated")


class CliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="testpilot-f-tests-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def invoke(self, *args):
        with patch("sys.stdout", new=io.StringIO()), patch("sys.stderr", new=io.StringIO()):
            return main(list(args))

    def test_report_outputs_json_markdown_html(self):
        write_json(self.root / "input.json", report())
        self.assertEqual(self.invoke("report", "--report", str(self.root / "input.json"), "--out", str(self.root / "out")), 0)
        for extension in ("json", "md", "html"):
            self.assertTrue((self.root / "out" / f"gap_report.{extension}").is_file())

    def test_high_gate_returns_three_after_saving_report(self):
        data = report(high=1)
        data["gaps"] = [gap()]
        write_json(self.root / "input.json", data)
        self.assertEqual(self.invoke("report", "--report", str(self.root / "input.json"), "--out", str(self.root / "out"), "--fail-on-high"), 3)
        self.assertTrue((self.root / "out" / "gap_report.md").exists())

    def test_default_does_not_block_on_high(self):
        write_json(self.root / "input.json", report(high=1))
        self.assertEqual(self.invoke("report", "--report", str(self.root / "input.json"), "--out", str(self.root / "out")), 0)

    def test_invalid_input_is_not_a_gate_failure(self):
        write_json(self.root / "input.json", {})
        self.assertEqual(self.invoke("report", "--report", str(self.root / "input.json"), "--out", str(self.root / "out")), 2)
        self.assertTrue((self.root / "out" / "failure.txt").is_file())

    def test_failure_comment_makes_no_clean_claim(self):
        (self.root / "failure.txt").write_text("Upstream failure", encoding="utf-8")
        self.assertEqual(self.invoke("report", "--failure-file", str(self.root / "failure.txt"), "--out", str(self.root / "out")), 1)
        self.assertIn("No test-adequacy conclusion", (self.root / "out" / "gap_report.md").read_text(encoding="utf-8"))

    def test_success_clears_old_failure_marker(self):
        out = self.root / "out"
        out.mkdir()
        (out / "failure.txt").write_text("old failure")
        write_json(self.root / "input.json", report())
        self.invoke("report", "--report", str(self.root / "input.json"), "--out", str(out))
        self.assertFalse((out / "failure.txt").exists())

    def test_publication_target_cannot_override_report(self):
        write_json(self.root / "input.json", report())
        with patch("github_client.GitHubClient.publish") as post:
            code = self.invoke("report", "--report", str(self.root / "input.json"), "--out", str(self.root / "out"),
                "--publish", "--repo-name", "other/project", "--pr", "7", "--expected-head", SHA)
        self.assertEqual(code, 2)
        post.assert_not_called()

    def test_comment_published_before_high_exit(self):
        write_json(self.root / "input.json", report(high=1))
        with patch.dict("os.environ", {"GITHUB_TOKEN": "dummy-not-a-credential"}), patch("github_client.GitHubClient.publish", return_value={"action": "created"}) as post:
            code = self.invoke("report", "--report", str(self.root / "input.json"), "--out", str(self.root / "out"), "--publish",
                "--repo-name", "example/project", "--pr", "7", "--expected-head", SHA, "--fail-on-high")
        self.assertEqual(code, 3)
        post.assert_called_once()

    def test_path_traversal_is_rejected(self):
        with self.assertRaises(ValueError):
            relative_path(self.root, "../escape")
        self.assertEqual(relative_path(self.root, "."), self.root.resolve())

    def test_real_end_to_end_demo_collects_baseline_and_detects_precision_gap(self):
        outside = self.root / "outside.sqlite"
        with patch.dict("os.environ", {"TESTMON_DATAFILE": str(outside)}):
            result = demo(self.root / "demo")
        self.assertFalse(outside.exists())
        self.assertEqual(result["execution"]["baseline"], "collected")
        self.assertFalse(result["execution"]["head_tests_executed"])
        self.assertEqual(result["inputs"], {"behavior": "loaded", "mapping": "loaded", "adequacy": "loaded"})
        self.assertEqual(result["gaps"][0]["gap_score"], 43.55)


class IntegrationTests(unittest.TestCase):
    def test_same_named_functions_in_different_files_keep_separate_evidence(self):
        with tempfile.TemporaryDirectory(prefix="testpilot-link-") as temp:
            root = Path(temp)
            changes, behaviors, entries = [], [], []
            for name in ("a.py", "b.py"):
                changes.append({"file_path": name, "entity_name": "f", "entity_type": "function", "change_type": "modify",
                    "change_category": "Logic change", "line_start": 1, "line_end": 3, "summary": "return changed"})
                b = {"file_path": name, "type": "RETURN_BEHAVIOR_CHANGE", "function": "f", "old_line": 2, "new_line": 2}
                behaviors.append(b)
                entries.append({"change": b, "status": "assertion_evidence_found" if name == "a.py" else "no_candidate_test",
                    "candidate_tests": [], "evidence": [{"strength": "potential_match"}] if name == "a.py" else []})
            write_json(root / "a.json", {"schema_version": "2.0", "mode": "pr_ast", "changes": changes})
            write_json(root / "b.json", {"schema_version": "1.0", "behavior_changes": behaviors})
            write_json(root / "d.json", {"schema_version": "1.0", "mode": "test_adequacy_analysis", "results": entries})
            result = score_reports(root / "a.json", root / "b.json", adequacy=root / "d.json")
            self.assertEqual([item["file_path"] for item in result["gaps"]], ["b.py"])
            self.assertGreater(result["gaps"][0]["gap_score"], 0)

    def test_metrics_exclude_unknown_truth_and_show_zero_recall(self):
        result = metrics([(True, False), (False, False), (None, True)])
        self.assertEqual(result["evaluated"], 2)
        self.assertEqual(result["recall"], 0)
        self.assertIsNone(result["precision"])

    def test_frozen_evaluation_produces_evidence_and_hash(self):
        with tempfile.TemporaryDirectory(prefix="testpilot-eval-") as temp:
            result = evaluate(TOOLS / "pr-reporter" / "benchmarks" / "contract.json", temp)
            self.assertTrue(result["passed"])
            self.assertEqual(result["unknown_ground_truth_excluded"], 1)
            self.assertEqual(result["testpilot"]["evaluated"], 5)
            self.assertEqual(len(result["contract_sha256"]), 64)

    def test_empty_test_corpus_is_marked_incomplete(self):
        from integration import analyze_adequacy
        with tempfile.TemporaryDirectory(prefix="testpilot-corpus-") as temp:
            result = analyze_adequacy({"behavior_changes": [], "errors": []}, Path(temp), None)
            self.assertFalse(result["analysis"]["input_complete"])
            self.assertEqual(result["errors"][0]["type"], "MissingTestCorpus")


if __name__ == "__main__":
    unittest.main()
