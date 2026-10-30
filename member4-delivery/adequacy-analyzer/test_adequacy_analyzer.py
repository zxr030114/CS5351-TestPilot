"""Member 4: extract pytest assertions and match potential behavior changes.

Standard library only. An evidence match is never a proof of test adequacy.
Test sources are parsed, never imported or executed.
"""

import argparse
import ast
import json
from pathlib import Path, PurePosixPath


def dotted(node):
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = dotted(node.value)
        return base + "." + node.attr if base else None
    return None


def walk_body(node):
    """Exclude nested scopes: a defined helper has not necessarily run."""
    yield node
    for child in ast.iter_child_nodes(node):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            continue
        yield from walk_body(child)


def parse_tests(source, file_path="test_module.py"):
    """Return JSON-compatible test records, including aliases and literal inputs."""
    tree = ast.parse(source, filename=file_path)
    imports = {}
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports[alias.asname or alias.name.split('.')[0]] = alias.name if alias.asname else alias.name.split('.')[0]
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name != '*':
                    imports[alias.asname or alias.name] = '.'.join(filter(None, [node.module, alias.name]))

    def resolve(node):
        name = dotted(node)
        if not name:
            return None
        first, sep, rest = name.partition('.')
        return imports.get(first, first) + (sep + rest if sep else '')

    def call_record(node):
        arguments = []
        for argument in list(node.args) + [kw.value for kw in node.keywords]:
            try:
                arguments.append(ast.literal_eval(argument))
            except (ValueError, TypeError):
                pass
        # Literal bytes/sets/complex are represented as source for JSON safety.
        return {"target": resolve(node.func), "expression": ast.unparse(node),
                "line": node.lineno, "literal_arguments": [
                    value if type(value) in (str, int, float, bool, type(None)) else repr(value)
                    for value in arguments]}

    tests = []

    def collect(container, prefix=""):
        for function in container.body:
            if isinstance(function, ast.ClassDef) and function.name.startswith('Test'):
                collect(function, prefix + function.name + '::')
                continue
            if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)) or not function.name.startswith('test_'):
                continue
            assertions, raises = [], []
            # Track only straight-line local assignments; compound statements
            # invalidate bindings rather than assuming which branch executed.
            bindings = {}
            for statement in function.body:
                if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    bindings.clear()
                    continue
                for node in walk_body(statement):
                    if isinstance(node, ast.Assert):
                        calls = [call_record(n) for n in walk_body(node.test) if isinstance(n, ast.Call)]
                        for ref in walk_body(node.test):
                            if isinstance(ref, ast.Name):
                                calls.extend(bindings.get(ref.id, []))
                        assertions.append({"kind": "assert", "line": node.lineno,
                                           "expression": ast.unparse(node.test),
                                           "strength": "comparison" if isinstance(node.test, ast.Compare) else "truthiness",
                                           "calls": calls})
                    elif isinstance(node, (ast.With, ast.AsyncWith)):
                        for item in node.items:
                            call = item.context_expr
                            if not isinstance(call, ast.Call) or resolve(call.func) != 'pytest.raises':
                                continue
                            exceptions = []
                            if call.args:
                                exc = call.args[0]
                                exceptions = [resolve(e) for e in exc.elts] if isinstance(exc, ast.Tuple) else [resolve(exc)]
                            raises.append({"kind": "pytest.raises", "line": node.lineno,
                                           "expected_exceptions": exceptions,
                                           "match": next((ast.unparse(kw.value) for kw in call.keywords if kw.arg == 'match'), None),
                                           "calls": [call_record(n) for body in node.body for n in walk_body(body) if isinstance(n, ast.Call)]})
                    elif isinstance(node, ast.Call) and resolve(node.func) == 'pytest.raises' and len(node.args) >= 2:
                        # pytest.raises(Error, callable, *args, **kwargs).
                        target = node.args[1]
                        exc = node.args[0]
                        raises.append({"kind": "pytest.raises", "line": node.lineno,
                                       "expected_exceptions": [resolve(e) for e in exc.elts] if isinstance(exc, ast.Tuple) else [resolve(exc)],
                                       "match": None,
                                       "calls": [{"target": resolve(target), "line": node.lineno,
                                                  "expression": ast.unparse(node), "literal_arguments": []}]})
                if isinstance(statement, (ast.Assign, ast.AnnAssign)):
                    targets = statement.targets if isinstance(statement, ast.Assign) else [statement.target]
                    value = statement.value
                    # Only direct results are linked; wrappers may discard output.
                    calls = [call_record(value)] if isinstance(value, ast.Call) else bindings.get(value.id, []) if isinstance(value, ast.Name) else []
                    for target in targets:
                        for name in ast.walk(target):
                            if isinstance(name, ast.Name):
                                bindings[name.id] = calls
                elif not isinstance(statement, (ast.Assert, ast.Expr, ast.Pass)):
                    bindings.clear()
            tests.append({"nodeid": file_path + '::' + prefix + function.name,
                          "line": function.lineno,
                          "calls": [call_record(n) for n in walk_body(function) if isinstance(n, ast.Call)],
                          "assertions": assertions, "raises": raises})
    collect(tree)
    return tests


def _target_matches(target, change):
    function = change.get('function')
    if not target or not function:
        return False
    path = change.get('file_path')
    if path:
        module = str(PurePosixPath(path).with_suffix('')).replace('/', '.')
        if module.endswith('.__init__'):
            module = module[:-9]
        return target == module + '.' + function
    return target == function or target.endswith('.' + function)


class TestAdequacyAnalyzer:
    """Consume Member 2 behavior reports and Member 3 selected pytest node IDs."""

    def analyze(self, behavior_report, test_sources, mapping_report=None):
        if not isinstance(behavior_report, dict) or not isinstance(behavior_report.get('behavior_changes'), list):
            raise ValueError('Expected a Member 2 report with behavior_changes')
        selected = None
        if mapping_report is not None:
            if mapping_report.get('mode') != 'test_dependency_mapping' or not isinstance(mapping_report.get('selected_tests'), list):
                raise ValueError('Expected a Member 3 test_dependency_mapping report')
            selected = set(mapping_report['selected_tests'])
        tests, errors = [], []
        for path, source in sorted(test_sources.items()):
            try:
                tests.extend(parse_tests(source, path))
            except SyntaxError as exc:
                errors.append({"file_path": path, "type": "SyntaxError", "line": exc.lineno, "message": exc.msg})
        if selected is not None:
            # testmon may include parametrized node IDs; retain base-test AST.
            tests = [test for test in tests if any(
                node == test['nodeid'] or node.startswith(test['nodeid'] + '[') for node in selected)]
            known = {test['nodeid'] for test in tests}
            for node in sorted(selected):
                if not any(node == base or node.startswith(base + '[') for base in known):
                    errors.append({"nodeid": node, "type": "MissingTest", "message": "Selected test source or definition unavailable"})
        results = []
        supported = {'RETURN_BEHAVIOR_CHANGE', 'EXCEPTION_BEHAVIOR_CHANGE', 'BOUNDARY_CHANGE',
                     'NEW_BRANCH', 'DELETED_BRANCH', 'MODIFIED_BRANCH'}
        for change in behavior_report['behavior_changes']:
            if not isinstance(change, dict):
                raise ValueError('Each behavior change must be an object')
            kind = change.get('type')
            candidates, evidence = [], []
            for test in tests:
                if any(_target_matches(call['target'], change) for call in test['calls']):
                    candidates.append(test['nodeid'])
                for assertion in test['assertions'] + test['raises']:
                    relevant = [call for call in assertion['calls'] if _target_matches(call['target'], change)]
                    if not relevant:
                        continue
                    if kind == 'EXCEPTION_BEHAVIOR_CHANGE' and change.get('new_exception_type'):
                        expected = change['new_exception_type']
                        matches = assertion['kind'] == 'pytest.raises' and expected in assertion['expected_exceptions']
                    else:
                        matches = assertion['kind'] == 'assert' and assertion['strength'] == 'comparison'
                    evidence.append({"nodeid": test['nodeid'], "line": assertion['line'],
                                     "kind": assertion['kind'], "strength": "potential_match" if matches else "weak_or_mismatched",
                                     "assertion": {key: value for key, value in assertion.items() if key != 'calls'},
                                     "calls": relevant})
            status = ('unsupported_behavior' if kind not in supported or not change.get('function') else
                      'assertion_evidence_found' if any(e['strength'] == 'potential_match' for e in evidence) else
                      'weak_assertions' if evidence else
                      'no_matching_assertion' if candidates else 'no_candidate_test')
            recommendations = []
            if kind == 'BOUNDARY_CHANGE':
                values = set()
                for key in ('old_boundary', 'new_boundary'):
                    value = change.get(key)
                    if type(value) in (int, float):
                        values.update([value - 1, value, value + 1])
                recommendations.append('Check values around old/new boundaries: ' + ', '.join(map(str, sorted(values))) + '. Adapt offsets to the input domain.')
            elif kind == 'EXCEPTION_BEHAVIOR_CHANGE' and change.get('new_exception_type'):
                recommendations.append('Assert pytest.raises(' + change['new_exception_type'] + ') around the target call; check trigger inputs and message when relevant.')
            else:
                recommendations.append('Assert the changed expected result and exercise each affected branch, including a contrasting input.')
                if kind == 'RETURN_BEHAVIOR_CHANGE' and 'round(' in str(change.get('new_expression', '')):
                    recommendations.append('Use fractional inputs that distinguish the old and new precision; an integer-only example cannot establish rounding adequacy.')
            results.append({"change": change, "status": status, "candidate_tests": candidates,
                            "evidence": evidence, "recommendations": recommendations})
        all_errors = list(behavior_report.get('errors', [])) + errors
        return {"schema_version": "1.0", "mode": "test_adequacy_analysis",
                "analysis": {"method": "python-ast", "runtime_adequacy_proven": False,
                             "input_complete": not all_errors and (mapping_report is None or mapping_report.get('selection_complete') is True),
                             "selection_complete": mapping_report.get('selection_complete') if mapping_report else None},
                "results": results, "tests": tests, "errors": all_errors,
                "summary": {"behavior_changes": len(results), "tests_analyzed": len(tests),
                            "with_assertion_evidence": sum(r['status'] == 'assertion_evidence_found' for r in results)},
                "limitations": [
                    "Matches are syntactic candidates, not proof of branch execution or correct expected values.",
                    "Function suffix matching is ambiguous without file_path; supply repository-relative file_path when possible.",
                    "Fixtures, helpers, dynamic imports, instance dispatch and parametrization are not expanded.",
                    "Only straight-line direct-result assignments are tracked; alias reassignment and shadowing are not resolved.",
                    "Boundary inputs and changed expected values need manual or runtime validation."]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--behavior', required=True, help='Member 2 JSON report')
    parser.add_argument('--mapping', help='Member 3 JSON report; omit to inspect all test files')
    parser.add_argument('--test-root', required=True, help='Repository root for node IDs')
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    try:
        root = Path(args.test_root).resolve()
        if not root.is_dir():
            raise ValueError('--test-root must be an existing directory')
        sources = {}
        for path in sorted(root.rglob('*.py')):
            relative = path.relative_to(root)
            if any(part.startswith('.') or part in ('venv', 'node_modules', '__pycache__') for part in relative.parts):
                continue
            if path.name.startswith('test_') or path.name.endswith('_test.py'):
                sources[relative.as_posix()] = path.read_text(encoding='utf-8-sig')
        behavior = json.loads(Path(args.behavior).read_text(encoding='utf-8-sig'))
        mapping = json.loads(Path(args.mapping).read_text(encoding='utf-8-sig')) if args.mapping else None
        result = TestAdequacyAnalyzer().analyze(behavior, sources, mapping)
        Path(args.output).write_text(json.dumps(result, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
        print(json.dumps(result['summary']))
    except (OSError, ValueError, TypeError) as exc:
        parser.exit(2, 'Adequacy analysis error: ' + str(exc) + '\n')


if __name__ == '__main__':
    main()
