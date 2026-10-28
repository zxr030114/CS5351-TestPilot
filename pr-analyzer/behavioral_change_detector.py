"""Potential Python behavior changes inferred from old/new source with ``ast``.

This module is intentionally independent of the PR diff pipeline. It compares
two Python source strings, never executes them, and treats its findings as
static-analysis evidence rather than a proof of runtime equivalence.
"""

from __future__ import annotations

import ast
import copy
import json
import os
from collections import defaultdict
from difflib import SequenceMatcher
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


_OPERATOR_NAMES = {
    ast.Gt: ">",
    ast.GtE: ">=",
    ast.Lt: "<",
    ast.LtE: "<=",
    ast.Eq: "==",
    ast.NotEq: "!=",
    ast.Is: "is",
    ast.IsNot: "is not",
    ast.In: "in",
    ast.NotIn: "not in",
}
_REVERSED_OPERATORS = {
    ">": "<",
    ">=": "<=",
    "<": ">",
    "<=": ">=",
}
_VALID_CHANGE_TYPES = {"enhancement", "bugfix", "refactor", "unknown"}


def _dump(node: Optional[ast.AST]) -> str:
    return "" if node is None else ast.dump(node, include_attributes=False)


def _unparse(node: Optional[ast.AST]) -> str:
    return "None" if node is None else ast.unparse(node)


def _shape(node: Optional[ast.AST]) -> str:
    """Keep AST structure while blanking names and literal values for alignment."""
    if node is None:
        return ""

    class ShapeNormalizer(ast.NodeTransformer):
        def visit_Name(self, item: ast.Name) -> ast.AST:
            item.id = "<name>"
            return item

        def visit_Attribute(self, item: ast.Attribute) -> ast.AST:
            item.attr = "<attribute>"
            return self.generic_visit(item)

        def visit_Constant(self, item: ast.Constant) -> ast.AST:
            item.value = f"<{type(item.value).__name__}>"
            return item

    normalized = ShapeNormalizer().visit(copy.deepcopy(node))
    return ast.dump(normalized, include_attributes=False)


def _scope_table(tree: ast.Module) -> Dict[str, Dict[str, Any]]:
    scopes: Dict[str, Dict[str, Any]] = {
        "<module>": {"node": tree, "kind": "module", "function": None}
    }

    def discover(node: ast.AST, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ClassDef):
                qualname = f"{prefix}.{child.name}" if prefix else child.name
                scopes[qualname] = {"node": child, "kind": "class", "function": None}
                discover(child, qualname)
            elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                qualname = f"{prefix}.{child.name}" if prefix else child.name
                scopes[qualname] = {
                    "node": child,
                    "kind": "function",
                    "function": qualname,
                }
                discover(child, qualname)
            else:
                discover(child, prefix)

    discover(tree, "")
    return scopes


def _child_context(
    parent: ast.AST,
    field: str,
    child: ast.AST,
    key: Tuple[Tuple[str, str], ...],
    labels: Tuple[str, ...],
) -> Tuple[Tuple[Tuple[str, str], ...], Tuple[str, ...]]:
    kind = None
    condition = None
    if isinstance(parent, ast.If):
        if field == "body":
            kind, condition = "if_true", parent.test
        elif field == "orelse":
            kind, condition = "if_false", parent.test
    elif isinstance(parent, (ast.For, ast.AsyncFor, ast.While)):
        if field == "body":
            kind, condition = "loop_body", getattr(parent, "test", None)
        elif field == "orelse":
            kind, condition = "loop_else", getattr(parent, "test", None)
    elif isinstance(parent, (ast.Try, getattr(ast, "TryStar", ast.Try))):
        if field == "body":
            kind = "try_body"
        elif field == "orelse":
            kind = "try_else"
        elif field == "finalbody":
            kind = "try_finally"
        elif field == "handlers":
            kind = "except"
            condition = getattr(child, "type", None)
    elif isinstance(parent, ast.Match) and field == "cases":
        kind = "match_case"
        condition = getattr(child, "pattern", None)

    if kind is None:
        return key, labels

    shape = _shape(condition) if condition is not None else ""
    label = kind.replace("_", " ")
    if condition is not None:
        label = f"{label} {_unparse(condition)}"
    return key + ((kind, shape),), labels + (label,)


def _walk_scope(
    root: ast.AST,
) -> Iterable[Tuple[ast.AST, Tuple[Tuple[str, str], ...], Tuple[str, ...]]]:
    scope_nodes = (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)

    def visit(
        node: ast.AST,
        key: Tuple[Tuple[str, str], ...],
        labels: Tuple[str, ...],
        is_root: bool,
    ) -> Iterable[Tuple[ast.AST, Tuple[Tuple[str, str], ...], Tuple[str, ...]]]:
        if not is_root and isinstance(node, scope_nodes):
            return
        yield node, key, labels
        for field, value in ast.iter_fields(node):
            children = value if isinstance(value, list) else [value]
            for child in children:
                if not isinstance(child, ast.AST):
                    continue
                next_key, next_labels = _child_context(
                    node, field, child, key, labels
                )
                yield from visit(child, next_key, next_labels, False)

    return visit(root, (), (), True)


def _entries(scope: Dict[str, Any], node_type: type) -> List[Dict[str, Any]]:
    return [
        {"node": node, "context": context, "labels": labels}
        for node, context, labels in _walk_scope(scope["node"])
        if isinstance(node, node_type)
    ]


def _branch_kind(node: ast.AST) -> str:
    return "while" if isinstance(node, ast.While) else "if"


def _branch_body_signature(nodes: Sequence[ast.stmt]) -> str:
    return "|".join(_dump(node) for node in nodes)


def _exception_type(node: Optional[ast.AST]) -> Optional[str]:
    if node is None:
        return None
    if isinstance(node, ast.Call):
        return _unparse(node.func)
    return _unparse(node)


def _effect(nodes: Sequence[ast.stmt]) -> str:
    if not nodes:
        return "no direct effect"
    first = nodes[0]
    if isinstance(first, ast.Raise):
        exception = _exception_type(first.exc)
        return f"raise {exception}" if exception else "raise"
    if isinstance(first, ast.Return):
        return f"return {_unparse(first.value)}"
    return _unparse(first).splitlines()[0]


def _branch_record(
    change_type: str,
    scope_name: str,
    scope: Dict[str, Any],
    old_entry: Optional[Dict[str, Any]],
    new_entry: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    old_node = old_entry["node"] if old_entry else None
    new_node = new_entry["node"] if new_entry else None
    node = new_node or old_node
    old_condition = _unparse(old_node.test) if old_node else None
    new_condition = _unparse(new_node.test) if new_node else None
    return {
        "type": change_type,
        "function": scope["function"],
        "scope": scope_name,
        "condition": new_condition or old_condition,
        "old_condition": old_condition,
        "new_condition": new_condition,
        "effect": _effect(new_node.body if new_node else old_node.body),
        "old_effect": _effect(old_node.body) if old_node else None,
        "new_effect": _effect(new_node.body) if new_node else None,
        "alternate_effect": _effect(new_node.orelse if new_node else old_node.orelse),
        "branch_kind": _branch_kind(node),
        "old_line": getattr(old_node, "lineno", None),
        "new_line": getattr(new_node, "lineno", None),
        "context": list((new_entry or old_entry)["labels"]),
        "confidence": "potential",
    }


class BranchDetector:
    """Detect added, removed, or structurally modified if/while conditions."""

    def detect(
        self,
        old_scopes: Dict[str, Dict[str, Any]],
        new_scopes: Dict[str, Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        changes: List[Dict[str, Any]] = []
        for scope_name in sorted(set(old_scopes) | set(new_scopes)):
            old_scope = old_scopes.get(scope_name)
            new_scope = new_scopes.get(scope_name)
            scope = new_scope or old_scope
            old_branches = (
                [e for e in _entries(old_scope, ast.If) + _entries(old_scope, ast.While)]
                if old_scope
                else []
            )
            new_branches = (
                [e for e in _entries(new_scope, ast.If) + _entries(new_scope, ast.While)]
                if new_scope
                else []
            )
            unmatched_old = set(range(len(old_branches)))
            unmatched_new = set(range(len(new_branches)))

            # Exact conditions remain the same branch even if their body changed.
            for new_index, new_entry in enumerate(new_branches):
                new_node = new_entry["node"]
                key = (new_entry["context"], _branch_kind(new_node), _dump(new_node.test))
                for old_index in sorted(unmatched_old):
                    old_entry = old_branches[old_index]
                    old_node = old_entry["node"]
                    old_key = (
                        old_entry["context"],
                        _branch_kind(old_node),
                        _dump(old_node.test),
                    )
                    if key == old_key:
                        unmatched_old.remove(old_index)
                        unmatched_new.remove(new_index)
                        break

            candidates = []
            for old_index in unmatched_old:
                old_entry = old_branches[old_index]
                old_node = old_entry["node"]
                old_body = _branch_body_signature(old_node.body)
                old_else = _branch_body_signature(old_node.orelse)
                for new_index in unmatched_new:
                    new_entry = new_branches[new_index]
                    new_node = new_entry["node"]
                    if (
                        old_entry["context"] != new_entry["context"]
                        or _branch_kind(old_node) != _branch_kind(new_node)
                    ):
                        continue
                    new_body = _branch_body_signature(new_node.body)
                    new_else = _branch_body_signature(new_node.orelse)
                    similarity = SequenceMatcher(
                        None, _dump(old_node.test), _dump(new_node.test)
                    ).ratio()
                    score = 0
                    if old_body == new_body and old_else == new_else:
                        score = 3
                    elif _shape(old_node.test) == _shape(new_node.test):
                        score = 2
                    elif similarity >= 0.45:
                        score = 1
                    if score:
                        candidates.append((score, old_index, new_index))

            used_old: set[int] = set()
            used_new: set[int] = set()
            for _, old_index, new_index in sorted(candidates, reverse=True):
                if old_index in used_old or new_index in used_new:
                    continue
                used_old.add(old_index)
                used_new.add(new_index)
                unmatched_old.remove(old_index)
                unmatched_new.remove(new_index)
                changes.append(
                    _branch_record(
                        "MODIFIED_BRANCH",
                        scope_name,
                        scope,
                        old_branches[old_index],
                        new_branches[new_index],
                    )
                )

            changes.extend(
                _branch_record(
                    "DELETED_BRANCH", scope_name, scope, old_branches[index], None
                )
                for index in sorted(unmatched_old)
            )
            changes.extend(
                _branch_record(
                    "NEW_BRANCH", scope_name, scope, None, new_branches[index]
                )
                for index in sorted(unmatched_new)
            )
        return changes


def _operator_name(node: ast.AST) -> str:
    return _OPERATOR_NAMES.get(type(node), type(node).__name__)


def _json_value(node: ast.AST) -> Any:
    if isinstance(node, ast.Constant):
        try:
            json.dumps(node.value)
            return node.value
        except (TypeError, ValueError):
            return repr(node.value)
    return _unparse(node)


def _comparison_details(
    left: ast.AST, operator: ast.AST, right: ast.AST
) -> Tuple[str, str, Any]:
    if isinstance(left, ast.Constant) and isinstance(right, (ast.Name, ast.Attribute)):
        op = _operator_name(operator)
        return _unparse(right), _REVERSED_OPERATORS.get(op, op), _json_value(left)
    variable = _unparse(left)
    if isinstance(right, ast.Constant):
        boundary = _json_value(right)
    else:
        boundary = _unparse(right)
    return variable, _operator_name(operator), boundary


class BoundaryDetector:
    """Compare matched comparison ASTs, including operator and literal changes."""

    def detect(
        self,
        old_scopes: Dict[str, Dict[str, Any]],
        new_scopes: Dict[str, Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        changes: List[Dict[str, Any]] = []
        for scope_name in sorted(set(old_scopes) & set(new_scopes)):
            old_scope, new_scope = old_scopes[scope_name], new_scopes[scope_name]
            old_groups: Dict[Any, List[Dict[str, Any]]] = defaultdict(list)
            new_groups: Dict[Any, List[Dict[str, Any]]] = defaultdict(list)
            for entry in _entries(old_scope, ast.Compare):
                node = entry["node"]
                key = (
                    entry["context"],
                    _shape(node.left),
                    tuple(_shape(item) for item in node.comparators),
                )
                old_groups[key].append(entry)
            for entry in _entries(new_scope, ast.Compare):
                node = entry["node"]
                key = (
                    entry["context"],
                    _shape(node.left),
                    tuple(_shape(item) for item in node.comparators),
                )
                new_groups[key].append(entry)

            for key in sorted(old_groups.keys() & new_groups.keys(), key=repr):
                for old_entry, new_entry in zip(old_groups[key], new_groups[key]):
                    old_node = old_entry["node"]
                    new_node = new_entry["node"]
                    if len(old_node.ops) != len(new_node.ops):
                        continue
                    for index, (old_op, new_op) in enumerate(
                        zip(old_node.ops, new_node.ops)
                    ):
                        old_variable, old_operator, old_boundary = _comparison_details(
                            old_node.left,
                            old_op,
                            old_node.comparators[index],
                        )
                        new_variable, new_operator, new_boundary = _comparison_details(
                            new_node.left,
                            new_op,
                            new_node.comparators[index],
                        )
                        if (
                            old_variable == new_variable
                            and old_operator == new_operator
                            and old_boundary == new_boundary
                        ):
                            continue
                        changes.append(
                            {
                                "type": "BOUNDARY_CHANGE",
                                "function": new_scope["function"],
                                "scope": scope_name,
                                "variable": new_variable,
                                "old_variable": old_variable,
                                "new_variable": new_variable,
                                "operator": new_operator,
                                "old_operator": old_operator,
                                "new_operator": new_operator,
                                "old_boundary": old_boundary,
                                "new_boundary": new_boundary,
                                "comparison_index": index,
                                "old_line": old_node.lineno,
                                "new_line": new_node.lineno,
                                "confidence": "potential",
                            }
                        )
        return changes


def _exception_behavior(node: ast.Raise) -> str:
    if node.exc is None:
        return "raise"
    return f"raise {_unparse(node.exc)}"


def _return_behavior(node: ast.Return) -> str:
    return f"return {_unparse(node.value)}"


def _exception_record(
    scope_name: str,
    scope: Dict[str, Any],
    old_node: Optional[ast.AST],
    new_node: Optional[ast.AST],
    old_entry: Optional[Dict[str, Any]] = None,
    new_entry: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    old_behavior = (
        _exception_behavior(old_node)
        if isinstance(old_node, ast.Raise)
        else _return_behavior(old_node)
        if isinstance(old_node, ast.Return)
        else None
    )
    new_behavior = (
        _exception_behavior(new_node)
        if isinstance(new_node, ast.Raise)
        else _return_behavior(new_node)
        if isinstance(new_node, ast.Return)
        else None
    )
    exc_node = new_node if isinstance(new_node, ast.Raise) else old_node
    exception = _exception_type(exc_node.exc) if isinstance(exc_node, ast.Raise) else None
    entry = new_entry or old_entry or {}
    labels = list(entry.get("labels", ()))
    return {
        "type": "EXCEPTION_BEHAVIOR_CHANGE",
        "operation": "added" if old_node is None else "removed" if new_node is None else "modified",
        "function": scope["function"],
        "scope": scope_name,
        "exception_type": exception,
        "old_exception_type": (
            _exception_type(old_node.exc) if isinstance(old_node, ast.Raise) else None
        ),
        "new_exception_type": (
            _exception_type(new_node.exc) if isinstance(new_node, ast.Raise) else None
        ),
        "old_behavior": old_behavior,
        "new_behavior": new_behavior,
        "trigger_condition": labels[-1] if labels else None,
        "old_line": getattr(old_node, "lineno", None),
        "new_line": getattr(new_node, "lineno", None),
        "confidence": "potential",
    }


class ExceptionDetector:
    """Detect raised-exception additions, removals, and exception transitions."""

    def detect(
        self,
        old_scopes: Dict[str, Dict[str, Any]],
        new_scopes: Dict[str, Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        changes: List[Dict[str, Any]] = []
        for scope_name in sorted(set(old_scopes) | set(new_scopes)):
            old_scope, new_scope = old_scopes.get(scope_name), new_scopes.get(scope_name)
            scope = new_scope or old_scope
            old_raises = _entries(old_scope, ast.Raise) if old_scope else []
            new_raises = _entries(new_scope, ast.Raise) if new_scope else []
            unmatched_old = set(range(len(old_raises)))
            unmatched_new = set(range(len(new_raises)))

            for new_index, new_entry in enumerate(new_raises):
                new_node = new_entry["node"]
                exact = (new_entry["context"], _dump(new_node.exc), _dump(new_node.cause))
                for old_index in sorted(unmatched_old):
                    old_entry = old_raises[old_index]
                    old_node = old_entry["node"]
                    if exact == (
                        old_entry["context"],
                        _dump(old_node.exc),
                        _dump(old_node.cause),
                    ):
                        unmatched_old.remove(old_index)
                        unmatched_new.remove(new_index)
                        break

            old_by_context: Dict[Any, List[int]] = defaultdict(list)
            new_by_context: Dict[Any, List[int]] = defaultdict(list)
            for index in unmatched_old:
                old_by_context[old_raises[index]["context"]].append(index)
            for index in unmatched_new:
                new_by_context[new_raises[index]["context"]].append(index)

            for context in sorted(old_by_context.keys() & new_by_context.keys(), key=repr):
                old_indexes = sorted(old_by_context[context])
                new_indexes = sorted(new_by_context[context])
                for old_index, new_index in zip(old_indexes, new_indexes):
                    unmatched_old.remove(old_index)
                    unmatched_new.remove(new_index)
                    changes.append(
                        _exception_record(
                            scope_name,
                            scope,
                            old_raises[old_index]["node"],
                            new_raises[new_index]["node"],
                            old_raises[old_index],
                            new_raises[new_index],
                        )
                    )

            # A same-region return-to-raise transition is one exception behavior change.
            old_returns = _entries(old_scope, ast.Return) if old_scope else []
            old_returns_by_context: Dict[Any, List[Dict[str, Any]]] = defaultdict(list)
            for entry in old_returns:
                old_returns_by_context[entry["context"]].append(entry)
            for context in sorted(new_by_context, key=repr) if unmatched_new else ():
                if context not in old_returns_by_context:
                    continue
                new_indexes = [
                    i for i in unmatched_new if new_raises[i]["context"] == context
                ]
                returns = old_returns_by_context[context]
                if len(new_indexes) == 1 and len(returns) == 1:
                    new_index = new_indexes[0]
                    unmatched_new.remove(new_index)
                    changes.append(
                        _exception_record(
                            scope_name,
                            scope,
                            returns[0]["node"],
                            new_raises[new_index]["node"],
                            returns[0],
                            new_raises[new_index],
                        )
                    )

            # Also retain the normal behavior when a raise becomes a return.
            new_returns = _entries(new_scope, ast.Return) if new_scope else []
            new_returns_by_context: Dict[Any, List[Dict[str, Any]]] = defaultdict(list)
            for entry in new_returns:
                new_returns_by_context[entry["context"]].append(entry)
            for context in sorted(old_by_context, key=repr) if unmatched_old else ():
                if context not in new_returns_by_context:
                    continue
                old_indexes = [
                    i for i in unmatched_old if old_raises[i]["context"] == context
                ]
                returns = new_returns_by_context[context]
                if len(old_indexes) == 1 and len(returns) == 1:
                    old_index = old_indexes[0]
                    unmatched_old.remove(old_index)
                    changes.append(
                        _exception_record(
                            scope_name,
                            scope,
                            old_raises[old_index]["node"],
                            returns[0]["node"],
                            old_raises[old_index],
                            returns[0],
                        )
                    )

            changes.extend(
                _exception_record(
                    scope_name,
                    scope,
                    old_raises[index]["node"],
                    None,
                    old_raises[index],
                    None,
                )
                for index in sorted(unmatched_old)
            )
            changes.extend(
                _exception_record(
                    scope_name,
                    scope,
                    None,
                    new_raises[index]["node"],
                    None,
                    new_raises[index],
                )
                for index in sorted(unmatched_new)
            )
        return changes


class ReturnDetector:
    """Detect changed return expressions where old/new control regions align."""

    def detect(
        self,
        old_scopes: Dict[str, Dict[str, Any]],
        new_scopes: Dict[str, Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        changes: List[Dict[str, Any]] = []
        for scope_name in sorted(set(old_scopes) & set(new_scopes)):
            old_scope, new_scope = old_scopes[scope_name], new_scopes[scope_name]
            old_returns = _entries(old_scope, ast.Return)
            new_returns = _entries(new_scope, ast.Return)
            old_groups: Dict[Any, List[Dict[str, Any]]] = defaultdict(list)
            new_groups: Dict[Any, List[Dict[str, Any]]] = defaultdict(list)
            for entry in old_returns:
                old_groups[entry["context"]].append(entry)
            for entry in new_returns:
                new_groups[entry["context"]].append(entry)

            for context in sorted(old_groups.keys() & new_groups.keys(), key=repr):
                old_entries, new_entries = old_groups[context], new_groups[context]
                old_remaining = set(range(len(old_entries)))
                new_remaining = set(range(len(new_entries)))
                for new_index, new_entry in enumerate(new_entries):
                    signature = _dump(new_entry["node"].value)
                    for old_index in sorted(old_remaining):
                        if _dump(old_entries[old_index]["node"].value) == signature:
                            old_remaining.remove(old_index)
                            new_remaining.remove(new_index)
                            break
                if len(old_remaining) != len(new_remaining):
                    continue
                for old_index, new_index in zip(
                    sorted(old_remaining), sorted(new_remaining)
                ):
                    old_node, new_node = (
                        old_entries[old_index]["node"],
                        new_entries[new_index]["node"],
                    )
                    changes.append(
                        {
                            "type": "RETURN_BEHAVIOR_CHANGE",
                            "function": new_scope["function"],
                            "scope": scope_name,
                            "old_expression": _unparse(old_node.value),
                            "new_expression": _unparse(new_node.value),
                            "old_line": old_node.lineno,
                            "new_line": new_node.lineno,
                            "confidence": "potential",
                        }
                    )
        return changes


def _format_value(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)


def _boundary_values(old: Any, new: Any) -> Optional[List[Any]]:
    if (
        isinstance(old, bool)
        or isinstance(new, bool)
        or not isinstance(old, (int, float))
        or not isinstance(new, (int, float))
    ):
        return None
    step = 1 if isinstance(old, int) and isinstance(new, int) else 0.01
    values = {old - step, old, old + step, new - step, new, new + step}
    return sorted(values)


def _suggestions(changes: Sequence[Dict[str, Any]]) -> List[str]:
    suggestions: List[str] = []
    for change in changes:
        kind = change["type"]
        function = change.get("function") or change.get("scope", "the module")
        if kind == "BOUNDARY_CHANGE":
            variable = change["variable"]
            old, new = change["old_boundary"], change["new_boundary"]
            values = _boundary_values(old, new)
            if values is not None:
                rendered = ", ".join(_format_value(value) for value in values)
                suggestions.append(
                    f"Test {variable} at values {rendered} in {function}."
                )
            else:
                suggestions.append(
                    f"Test {variable} at the old boundary {_format_value(old)} "
                    f"and new boundary {_format_value(new)} in {function}."
                )
        elif kind in {"NEW_BRANCH", "DELETED_BRANCH", "MODIFIED_BRANCH"}:
            old_condition, new_condition = (
                change.get("old_condition"),
                change.get("new_condition"),
            )
            if old_condition and new_condition and old_condition != new_condition:
                condition = f"`{old_condition}` and `{new_condition}`"
            else:
                condition = f"`{change.get('condition')}`"
            suggestions.append(
                f"Test {function} with condition {condition} evaluating both True and False."
            )
        elif kind == "EXCEPTION_BEHAVIOR_CHANGE":
            exception = change.get("new_exception_type") or change.get("old_exception_type")
            target = f"{exception}" if exception else "the changed exception behavior"
            condition = change.get("trigger_condition")
            trigger = f"when {condition}" if condition else "on the input path that triggers it"
            if change.get("operation") == "removed":
                suggestions.append(
                    f"Test {function} {trigger} and assert the removed {target} is no longer raised."
                )
            else:
                suggestions.append(
                    f"Test {function} {trigger} for {target}, and test a normal input path that should not raise."
                )
        elif kind == "RETURN_BEHAVIOR_CHANGE":
            old_expr = change.get("old_expression", "")
            new_expr = change.get("new_expression", "")
            expressions = f"`{old_expr}` to `{new_expr}`" if old_expr and new_expr else f"`{new_expr or old_expr}`"
            if "round(" in old_expr or "round(" in new_expr:
                detail = " Include numeric inputs around the precision used by round()."
            else:
                detail = " Use representative inputs and inputs referenced by the changed expression."
            suggestions.append(
                f"Assert the return value for {function} after changing {expressions}.{detail}"
            )
    return list(dict.fromkeys(suggestions))


def _rule_summary(changes: Sequence[Dict[str, Any]]) -> Tuple[str, str]:
    summaries = []
    impacts = []
    for change in changes:
        kind = change["type"]
        function = change.get("function") or change.get("scope", "module scope")
        if kind == "BOUNDARY_CHANGE":
            old, new = change["old_boundary"], change["new_boundary"]
            summaries.append(
                f"Changed {change['variable']} boundary from {_format_value(old)} "
                f"to {_format_value(new)} in {function}."
            )
            impacts.append(
                f"Inputs near the changed {_format_value(old)} and {_format_value(new)} "
                "boundaries may follow a different path."
            )
        elif kind in {"NEW_BRANCH", "DELETED_BRANCH", "MODIFIED_BRANCH"}:
            action = {
                "NEW_BRANCH": "Added",
                "DELETED_BRANCH": "Removed",
                "MODIFIED_BRANCH": "Modified",
            }[kind]
            summaries.append(
                f"{action} a conditional branch ({_branch_kind_from_record(change)}) "
                f"in {function}."
            )
            impacts.append(
                f"The condition `{change.get('condition')}` may direct inputs to a different path."
            )
        elif kind == "EXCEPTION_BEHAVIOR_CHANGE":
            old = change.get("old_behavior") or "no exception"
            new = change.get("new_behavior") or "no exception"
            summaries.append(f"Changed exception behavior in {function}: {old} -> {new}.")
            impacts.append("Callers may need different exception handling or normal-path assertions.")
        elif kind == "RETURN_BEHAVIOR_CHANGE":
            summaries.append(
                f"Changed return expression in {function} from "
                f"{change['old_expression']} to {change['new_expression']}."
            )
            impacts.append("Returned values or types may differ for some inputs.")
    summary = " ".join(dict.fromkeys(summaries))
    impact = " ".join(dict.fromkeys(impacts))
    impact = f"Potential impact only; static AST analysis does not prove runtime behavior. {impact}"
    return summary, impact


def _branch_kind_from_record(change: Dict[str, Any]) -> str:
    return change.get("branch_kind", "conditional")


class _OpenAICompatibleClient:
    def __init__(self) -> None:
        self.api_key = os.getenv("BEHAVIOR_LLM_API_KEY", "").strip()
        self.model = os.getenv("BEHAVIOR_LLM_MODEL", "gpt-4o-mini").strip()
        self.base_url = os.getenv(
            "BEHAVIOR_LLM_BASE_URL", "https://api.openai.com/v1"
        ).strip().rstrip("/")

    def summarize(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        import requests

        endpoint = (
            self.base_url
            if self.base_url.endswith("/chat/completions")
            else f"{self.base_url}/chat/completions"
        )
        prompt = {
            "old_code": payload["old_code"],
            "new_code": payload["new_code"],
            "ast_behavior_changes": payload["behavior_changes"],
            "affected_functions": payload["affected_functions"],
            "change_types": sorted({c["type"] for c in payload["behavior_changes"]}),
        }
        response = requests.post(
            endpoint,
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={
                "model": self.model,
                "temperature": 0,
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "Explain only the supplied AST evidence. Do not invent code changes. "
                            "If intent cannot be established, use change_type=unknown. "
                            "Return one JSON object with change_type, summary, "
                            "affected_functions, behavioral_impact."
                        ),
                    },
                    {"role": "user", "content": json.dumps(prompt, ensure_ascii=False)},
                ],
            },
            timeout=20,
        )
        response.raise_for_status()
        content = response.json()["choices"][0]["message"]["content"]
        result = json.loads(content)
        if not isinstance(result, dict):
            raise ValueError("LLM returned a non-object response")
        return result


class BehavioralChangeDetector:
    """Coordinate Python AST detectors and return a JSON-serializable report."""

    def __init__(
        self,
        *,
        use_llm: Optional[bool] = None,
        llm_client: Any = None,
    ) -> None:
        self.use_llm = (
            bool(os.getenv("BEHAVIOR_LLM_API_KEY", "").strip())
            if use_llm is None
            else use_llm
        )
        self.llm_client = llm_client
        self.branch_detector = BranchDetector()
        self.boundary_detector = BoundaryDetector()
        self.exception_detector = ExceptionDetector()
        self.return_detector = ReturnDetector()

    def detect(self, old_code: str, new_code: str) -> Dict[str, Any]:
        errors = []
        trees = []
        for side, code in (("old", old_code), ("new", new_code)):
            try:
                trees.append(ast.parse(code))
            except SyntaxError as exc:
                errors.append(
                    {
                        "side": side,
                        "type": "SyntaxError",
                        "message": exc.msg,
                        "line": exc.lineno,
                        "offset": exc.offset,
                    }
                )
                trees.append(None)

        if errors:
            return self._result(
                "unknown",
                "Behavioral analysis could not parse the supplied Python source.",
                [],
                "Unknown: syntax errors prevented AST comparison; no runtime conclusion was made.",
                [],
                errors,
                "not_run",
                None,
            )

        old_scopes, new_scopes = _scope_table(trees[0]), _scope_table(trees[1])
        changes = []
        changes.extend(self.branch_detector.detect(old_scopes, new_scopes))
        changes.extend(self.boundary_detector.detect(old_scopes, new_scopes))
        changes.extend(self.exception_detector.detect(old_scopes, new_scopes))
        changes.extend(self.return_detector.detect(old_scopes, new_scopes))
        changes.sort(
            key=lambda item: (
                item.get("function") or "",
                item.get("new_line") or item.get("old_line") or 0,
                item["type"],
            )
        )

        affected_functions = list(
            dict.fromkeys(
                item["function"] for item in changes if item.get("function")
            )
        )
        if changes:
            summary, impact = _rule_summary(changes)
        else:
            summary = "No supported behavioral pattern changes were detected."
            impact = (
                "No differences were found in the supported AST patterns; this does not "
                "prove runtime equivalence."
            )

        llm_status, llm_error = "disabled", None
        change_type = "unknown"
        if self.use_llm:
            client = self.llm_client or _OpenAICompatibleClient()
            if not self.llm_client and not client.api_key:
                llm_status = "fallback"
                llm_error = "BEHAVIOR_LLM_API_KEY is not configured."
            else:
                try:
                    interpretation = client.summarize(
                        {
                            "old_code": old_code,
                            "new_code": new_code,
                            "behavior_changes": changes,
                            "affected_functions": affected_functions,
                        }
                    )
                    model_type = interpretation.get("change_type")
                    if model_type in _VALID_CHANGE_TYPES:
                        change_type = model_type
                    if isinstance(interpretation.get("summary"), str) and interpretation[
                        "summary"
                    ].strip():
                        summary = interpretation["summary"].strip()
                    if isinstance(
                        interpretation.get("behavioral_impact"), str
                    ) and interpretation["behavioral_impact"].strip():
                        impact = (
                            "Potential impact only; static AST evidence remains unchanged. "
                            + interpretation["behavioral_impact"].strip()
                        )
                    llm_status = "used"
                except Exception as exc:  # Optional explanation must never block AST output.
                    llm_status = "fallback"
                    llm_error = f"LLM explanation failed ({type(exc).__name__})."

        return self._result(
            change_type,
            summary,
            affected_functions,
            impact,
            changes,
            errors,
            llm_status,
            llm_error,
        )

    @staticmethod
    def _result(
        change_type: str,
        summary: str,
        affected_functions: List[str],
        behavioral_impact: str,
        changes: List[Dict[str, Any]],
        errors: List[Dict[str, Any]],
        llm_status: str,
        llm_error: Optional[str],
    ) -> Dict[str, Any]:
        return {
            "schema_version": "1.0",
            "change_type": change_type,
            "summary": summary,
            "affected_functions": affected_functions,
            "behavioral_impact": behavioral_impact,
            "behavior_changes": changes,
            "suggested_tests": _suggestions(changes),
            "errors": errors,
            "analysis": {
                "method": "python-ast",
                "runtime_behavior_proven": False,
                "llm_status": llm_status,
                "llm_error": llm_error,
            },
        }


__all__ = [
    "BehavioralChangeDetector",
    "BranchDetector",
    "BoundaryDetector",
    "ExceptionDetector",
    "ReturnDetector",
]
