"""Offline integration demo: real Member 2 API and existing payment tests."""

import argparse
import json
import sys
from pathlib import Path

from test_adequacy_analyzer import TestAdequacyAnalyzer

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'pr-analyzer'))
from behavioral_change_detector import BehavioralChangeDetector


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', default='adequacy-analyzer/out')
    args = parser.parse_args()
    old = 'def withdraw(balance, amount):\n    return balance - amount\n'
    new = ('def withdraw(balance, amount):\n'
           '    if amount < 0:\n'
           '        raise ValueError("amount must be non-negative")\n'
           '    return round(balance - amount, 1)\n')
    behavior = BehavioralChangeDetector(use_llm=False).detect(old, new)
    # Optional context improves target disambiguation; Member 2 does not emit it.
    for change in behavior['behavior_changes']:
        change['file_path'] = 'payment.py'
    source = (ROOT / 'mapping-demo' / 'test_payment.py').read_text(encoding='utf-8')
    result = TestAdequacyAnalyzer().analyze(behavior, {'mapping-demo/test_payment.py': source})
    output = Path(args.output_dir)
    output.mkdir(parents=True, exist_ok=True)
    for filename, payload in [('behavior.json', behavior), ('adequacy.json', result)]:
        (output / filename).write_text(json.dumps(payload, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(result['summary']))
    for item in result['results']:
        print(item['change']['type'] + ': ' + item['status'])


if __name__ == '__main__':
    main()
