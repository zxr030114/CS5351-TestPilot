"""Offline checks for extraction, evidence isolation and upstream integration."""

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from test_adequacy_analyzer import TestAdequacyAnalyzer, parse_tests


class AdequacyTests(unittest.TestCase):
    def analyze(self, source, kind='RETURN_BEHAVIOR_CHANGE', **fields):
        change = dict(type=kind, function='withdraw', **fields)
        return TestAdequacyAnalyzer().analyze({'behavior_changes': [change]}, {'test_pay.py': source})

    def status(self, source, **kwargs):
        return self.analyze(source, **kwargs)['results'][0]['status']

    def test_import_alias_and_assigned_result(self):
        source = 'from payment import withdraw as pay\ndef test_pay():\n    result = pay(10, 2)\n    assert result == 8\n'
        report = self.analyze(source, file_path='payment.py')
        self.assertEqual(report['results'][0]['status'], 'assertion_evidence_found')
        self.assertEqual(report['tests'][0]['assertions'][0]['calls'][0]['target'], 'payment.withdraw')
        json.dumps(report)

    def test_unrelated_assertion_does_not_validate_target(self):
        self.assertEqual(self.status('def test_pay():\n    withdraw(10, 2)\n    assert 1 == 1\n'), 'no_matching_assertion')

    def test_bare_call_is_not_assertion(self):
        self.assertEqual(self.status('def test_pay():\n    withdraw(10, 2)\n'), 'no_matching_assertion')

    def test_truthiness_is_weak(self):
        self.assertEqual(self.status('def test_pay():\n    assert withdraw(10, 2)\n'), 'weak_assertions')

    def test_reassignment_invalidates_result(self):
        self.assertEqual(self.status('def test_pay():\n    result = withdraw(10, 2)\n    result = 8\n    assert result == 8\n'), 'no_matching_assertion')

    def test_conditional_assignment_not_assumed(self):
        self.assertEqual(self.status('def test_pay():\n    if ready:\n        result = withdraw(10, 2)\n    assert result == 8\n'), 'no_matching_assertion')

    def test_unexecuted_helper_not_counted(self):
        self.assertEqual(self.status('def test_pay():\n    def helper():\n        assert withdraw(10, 2) == 8\n'), 'no_candidate_test')

    def test_raises_alias_and_tuple(self):
        source = 'from pytest import raises as fails\nfrom payment import withdraw\ndef test_pay():\n    with fails((ValueError, TypeError), match="balance"):\n        withdraw(1, 2)\n'
        report = self.analyze(source, kind='EXCEPTION_BEHAVIOR_CHANGE', new_exception_type='ValueError')
        self.assertEqual(report['results'][0]['status'], 'assertion_evidence_found')
        self.assertEqual(report['tests'][0]['raises'][0]['match'], "'balance'")

    def test_wrong_exception_is_weak(self):
        source = 'import pytest\ndef test_pay():\n    with pytest.raises(TypeError):\n        withdraw(1, 2)\n'
        self.assertEqual(self.status(source, kind='EXCEPTION_BEHAVIOR_CHANGE', new_exception_type='ValueError'), 'weak_assertions')

    def test_raises_must_enclose_target(self):
        source = 'import pytest\ndef test_pay():\n    withdraw(1, 2)\n    with pytest.raises(ValueError):\n        other()\n'
        self.assertEqual(self.status(source, kind='EXCEPTION_BEHAVIOR_CHANGE', new_exception_type='ValueError'), 'no_matching_assertion')

    def test_callable_raises_form(self):
        source = 'import pytest as pt\ndef test_pay():\n    pt.raises(ValueError, withdraw, 1, 2)\n'
        self.assertEqual(self.status(source, kind='EXCEPTION_BEHAVIOR_CHANGE', new_exception_type='ValueError'), 'assertion_evidence_found')

    def test_exception_removed_requires_result_assertion(self):
        source = 'import pytest\ndef test_pay():\n    with pytest.raises(ValueError):\n        withdraw(1, 2)\n'
        self.assertEqual(self.status(source, kind='EXCEPTION_BEHAVIOR_CHANGE', new_exception_type=None), 'weak_assertions')

    def test_module_path_disambiguates(self):
        source = 'import unrelated\ndef test_pay():\n    assert unrelated.withdraw(1, 2) == 3\n'
        self.assertEqual(self.status(source, file_path='payment.py'), 'no_candidate_test')

    def test_class_async_and_parameterized_selection(self):
        source = 'class TestPay:\n    async def test_pay(self):\n        assert withdraw(1, 2) == 3\n'
        mapping = {'mode': 'test_dependency_mapping', 'selected_tests': ['test_pay.py::TestPay::test_pay[small]'], 'selection_complete': True}
        report = TestAdequacyAnalyzer().analyze({'behavior_changes': [{'type': 'NEW_BRANCH', 'function': 'withdraw'}]}, {'test_pay.py': source}, mapping)
        self.assertEqual(report['results'][0]['status'], 'assertion_evidence_found')
        self.assertTrue(report['analysis']['input_complete'])

    def test_missing_selected_source_is_reported(self):
        mapping = {'mode': 'test_dependency_mapping', 'selected_tests': ['test_missing.py::test_x'], 'selection_complete': False}
        report = TestAdequacyAnalyzer().analyze({'behavior_changes': []}, {}, mapping)
        self.assertEqual(report['errors'][0]['type'], 'MissingTest')
        self.assertFalse(report['analysis']['input_complete'])

    def test_syntax_error_preserves_other_files(self):
        report = TestAdequacyAnalyzer().analyze({'behavior_changes': []}, {'broken.py': 'def broken(:', 'test_ok.py': 'def test_ok():\n    assert True\n'})
        self.assertEqual(len(report['tests']), 1)
        self.assertEqual(report['errors'][0]['type'], 'SyntaxError')

    def test_real_member2_report_and_payment_tests(self):
        root = Path(__file__).resolve().parents[2]
        sys.path.insert(0, str(root / 'pr-analyzer'))
        from behavioral_change_detector import BehavioralChangeDetector
        behavior = BehavioralChangeDetector(use_llm=False).detect(
            'def withdraw(balance, amount):\n    return balance - amount\n',
            'def withdraw(balance, amount):\n    if amount < 0:\n        raise ValueError()\n    return round(balance - amount, 1)\n')
        source = (root / 'mapping-demo' / 'test_payment.py').read_text(encoding='utf-8')
        report = TestAdequacyAnalyzer().analyze(behavior, {'mapping-demo/test_payment.py': source})
        exception = next(r for r in report['results'] if r['change']['type'] == 'EXCEPTION_BEHAVIOR_CHANGE')
        self.assertEqual(exception['status'], 'assertion_evidence_found')
        self.assertFalse(report['analysis']['runtime_adequacy_proven'])

    def test_boundary_recommendations_are_not_proof(self):
        report = self.analyze('def test_pay():\n    assert withdraw(10, 2) == 8\n', kind='BOUNDARY_CHANGE', old_boundary=18, new_boundary=21)
        self.assertIn('17, 18, 19, 20, 21, 22', report['results'][0]['recommendations'][0])
        self.assertFalse(report['analysis']['runtime_adequacy_proven'])

    def test_invalid_input(self):
        with self.assertRaises(ValueError):
            TestAdequacyAnalyzer().analyze({'changes': []}, {})

    def test_source_never_executed(self):
        self.assertEqual(len(parse_tests('raise RuntimeError("must not execute")\ndef test_ok():\n    assert True\n')), 1)


if __name__ == '__main__':
    unittest.main()
