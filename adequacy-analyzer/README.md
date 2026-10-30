# Test Adequacy Analyzer (Member 4)

Python AST evidence for whether candidate pytest tests contain assertions related
to a changed behavior. This module adds the fourth stage without modifying the
Member 1/2 analyzer, Member 3 mapper, or bundled pytest-testmon source.

## Run

Use Python **3.10+** for integration with Member 2 (validated on Python 3.12).
This module has no third-party dependencies and does not require an API key.
Run from the repository root:

```sh
python -m unittest discover -s adequacy-analyzer/tests -v
python adequacy-analyzer/demo.py
```

The offline demo calls the real `BehavioralChangeDetector.detect` API, compares
two explicitly synthetic withdrawal implementations, and parses the existing
`mapping-demo/test_payment.py`. It writes `behavior.json` and `adequacy.json`
under the ignored `adequacy-analyzer/out/` directory. It does not build a
testmon database, invoke Member 3, or analyze a real pytest-testmon PR.

To analyze Member 2 and Member 3 outputs:

```sh
python adequacy-analyzer/test_adequacy_analyzer.py --behavior behavior.json --mapping mapping_result.json --test-root . --output adequacy_result.json
```

`--test-root` is the repository root used for the mapper's output node IDs,
**not necessarily the pytest root that created the testmon database**. The CLI
reads `test_*.py` and `*_test.py` beneath it. Omit `--mapping` to inspect all
discovered test definitions. Supply source strings through the API for projects
with custom test filename patterns. Generated JSON should not be committed.

## API and data contracts

```python
from test_adequacy_analyzer import TestAdequacyAnalyzer, parse_tests

report = TestAdequacyAnalyzer().analyze(
    behavior_report,  # Member 2: {"behavior_changes": [...], "errors": [...]}
    {"tests/test_payment.py": test_source},
    mapping_report,   # Optional Member 3: mode=test_dependency_mapping,
                     # selected_tests=[repository-relative pytest node IDs],
                     # selection_complete=true/false
)
```

The supported Member 2 `type` values are `NEW_BRANCH`, `DELETED_BRANCH`,
`MODIFIED_BRANCH`, `BOUNDARY_CHANGE`, `EXCEPTION_BEHAVIOR_CHANGE`, and
`RETURN_BEHAVIOR_CHANGE`. `function` identifies the target; the original behavior
record is retained in every result. Optional `file_path` on each behavior record
disambiguates imports using a repository-relative module path, e.g. `payment.py`.
The caller must attach the correct source path when processing multiple modules.
Without it, function suffix matching is heuristic and can match unrelated modules.

The parser supports module-level import aliases, direct calls in assertions,
straight-line assignments of direct call results, ordinary test functions,
async test functions, and methods in `Test*` classes. Nested helper definitions
are excluded. Reassignment replaces local evidence, and compound statements
invalidate bindings. Imported modules and test files are never executed.

Exception extraction supports:

```python
with pytest.raises(ValueError, match="balance"):
    withdraw(100, 150)

pytest.raises(ValueError, withdraw, 100, 150)
```

Aliases such as `import pytest as pt` and `from pytest import raises as fails`
are resolved, as are exception tuples. An exception assertion only refers to
calls inside its context body or to the callable in functional form. A nearby
unrelated `pytest.raises` does not validate a target call elsewhere in the test.

## Output

Output has `schema_version="1.0"` and `mode="test_adequacy_analysis"`:

| Field | Meaning |
| --- | --- |
| `results[].change` | Original Member 2 behavior record |
| `results[].candidate_tests` | Tests containing a syntactically matching call |
| `results[].evidence` | Test node ID, line, assertion kind/expression, target calls, strength |
| `results[].recommendations` | Targeted boundary, exception, result or precision checks |
| `tests` | Extracted assertion and exception records |
| `errors` | Upstream errors, syntax errors, unavailable selected definitions |
| `analysis.input_complete` | Input parsing/selection completeness only; never test adequacy |
| `analysis.runtime_adequacy_proven` | Always false |

| Result status | Meaning |
| --- | --- |
| `assertion_evidence_found` | A related comparison or matching exception assertion was found |
| `weak_assertions` | Related assertion exists but is truthiness-only or wrong assertion kind/type |
| `no_matching_assertion` | Target call exists but no assertion was linked to it |
| `no_candidate_test` | No matching call in the supplied/selected test definitions |
| `unsupported_behavior` | Unknown behavior type or missing function identity |

For an added/changed exception, `pytest.raises` must name the expected new type.
Broad superclass matching is deliberately not inferred. For removed exceptions
and return changes, result comparisons are sought. Branch/boundary comparisons
are only candidates; neither path execution nor boundary input coverage is proven.

## Limits and interpretation

This is a static prototype, not a proof of semantic adequacy. It does not verify
that a comparison's expected value reflects the new behavior. In particular, an
integer withdrawal assertion can syntactically match a rounding change while
missing its precision defect. Reports recommend distinguishing fractional inputs.
Boundary suggestions use +/-1 as examples; continuous domains need suitable steps.

Fixtures, helper calls, `unittest` assertion methods, assertion wrappers, dynamic
imports, alias shadowing, monkeypatches, instance dispatch, and complex data flow
are not resolved. Parametrized selected node IDs map to the base test definition;
decorators and individual parameter cases are not expanded. No runtime branch
coverage, mutation testing, automatic test generation, or test execution is claimed.
All matching must use sources from the revision whose tests are being assessed.

## Validation

20 offline unit/integration checks cover direct and assigned assertions, unrelated
assertions, truthiness, overwritten results, conditional bindings, nested helpers,
import aliases, exception tuples/messages/types/scope/removal, callable raises,
module disambiguation, async class methods, parametrized selection, missing tests,
syntax failures, source non-execution, boundary suggestions, input validation,
and the real Member 2 API with the repository's payment tests.
