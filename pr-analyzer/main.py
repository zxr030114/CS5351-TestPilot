#!/usr/bin/env python3
"""pr-analyzer module entry point.

Usage (cd into pr-analyzer first, or add it to PYTHONPATH):

    # 0) Offline: built-in sample diff, demoing noise filtering -> splitting -> line ranges
    python main.py --demo

    # 0b) Parse any .diff/.patch file (e.g. a PR diff downloaded from GitHub)
    python main.py --diff-file path/to/pr.diff --context-lines 3

    # 1) Offline: analyse two commits of a local repository
    python main.py --repo ../pytest-testmon --base HEAD~1 --head HEAD

    # 2) Online: analyse a GitHub PR (needs GITHUB_TOKEN)
    python main.py --pr 12 --owner zxr030114 --name CS5351-TestPilot

    # 3) AST only: parse source and print the extracted entities (class / function / method)
    python main.py --demo-ast

    # 3b) Switch language (python / java supported)
    python main.py --demo-ast --demo-lang java

    # 3c) Compare old/new AST and print add/delete/modify with change_category
    python main.py --demo-compare

    # 3d) Download a PR's full base/head sources (mirror for offline AST work)
    python main.py --pr 12 --owner zxr030114 --name CS5351-TestPilot --save-sources out/pr_12

    # 3e) Full PR analysis: mirror -> entities -> compare -> renames -> output.json
    python main.py --repo zxr030114/CS5351-TestPilot --pr 12
    python main.py --repo pallets/flask --pr 6168 -o out/flask_6168.json

    # 4) Write to a file and print the summary only
    python main.py --repo . --base main --head HEAD -o out/change.json --summary-only

Exit codes: 0 success; 1 unexpected error; 2 expected analysis failure (not a git repo, API error, ...).
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

# Allow `python main.py` to run directly (script directory comes first on sys.path)
sys.path.insert(0, str(Path(__file__).resolve().parent))

try:  # .env is optional; missing python-dotenv is fine
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # pragma: no cover
    pass

from analyzer import (  # noqa: E402  (must be imported after load_dotenv)
    AnalysisError,
    analyze_local,
    analyze_pr,
    analyze_pull_request,
    compare_sources,
    dumps,
    extract_entities,
    filter_noise_files,
    flatten_affected_scopes,
    get_pr_source_files,
    parse_diff,
    parse_repo_name,
    parse_to_ast,
    split_diff,
    summarize_changes,
    summarize_entities,
)

LOGGER = logging.getLogger("pr-analyzer")

# ---------------------------------------------------------------------------
# Built-in sample diff: a realistic-looking PR covering both kept and noise files.
# It deliberately contains package-lock.json / README.md / .gitignore / an image to demo filtering;
# the first hunk of src/app/core.py has 5 leading context lines to demo context trimming.
# ---------------------------------------------------------------------------
SAMPLE_DIFF = '''\
diff --git a/package-lock.json b/package-lock.json
index 1111111..2222222 100644
--- a/package-lock.json
+++ b/package-lock.json
@@ -1,4 +1,4 @@
 {
-  "lockfileVersion": 2,
+  "lockfileVersion": 3,
   "requires": true,
   "packages": {}
 }
diff --git a/README.md b/README.md
index 3333333..4444444 100644
--- a/README.md
+++ b/README.md
@@ -1,3 +1,4 @@
 # TestPilot
 
-Old tagline.
+New tagline for PR #128.
+Second line added by the PR.
diff --git a/assets/logo.png b/assets/logo.png
new file mode 100644
index 0000000..5555555
Binary files /dev/null and b/assets/logo.png differ
diff --git a/.gitignore b/.gitignore
index 6666666..7777777 100644
--- a/.gitignore
+++ b/.gitignore
@@ -1,2 +1,3 @@
 *.log
-*.tmp
+*.tmp
+.DS_Store
diff --git a/src/app/core.py b/src/app/core.py
index aaaaaaa..bbbbbbb 100644
--- a/src/app/core.py
+++ b/src/app/core.py
@@ -10,6 +10,7 @@ def compute_total(items, tax_rate=0.0):
     total = 0
     for item in items:
         total += item.price * item.qty
     discount = float(tax_rate or 0)
     total = total * (1 - discount)
-    return round(total, 2)
+    total = round(total, 2)
+    return total
@@ -20,3 +21,4 @@ class Order:
     def __init__(self, order_id):
         self.order_id = order_id
-        self.status = "new"
+        self.status = "pending"
+        self.retries = 0
diff --git a/src/app/util.py b/src/app/util.py
new file mode 100644
index 0000000..8888888
--- /dev/null
+++ b/src/app/util.py
@@ -0,0 +1,4 @@
+"""Small helpers."""
+
+def clamp(value, low, high):
+    return max(low, min(value, high))
diff --git a/src/app/legacy.py b/src/app/legacy.py
deleted file mode 100644
index 9999999..0000000
--- a/src/app/legacy.py
+++ /dev/null
@@ -1,4 +0,0 @@
-def old_helper(x):
-    return x + 1
-def unused():
-    pass
'''


# ---------------------------------------------------------------------------
# AST entity extraction demo (offline, only needs tree-sitter)
# ---------------------------------------------------------------------------

AST_DEMO_PY = '''"""Bank account module (demo only; deliberately contains many entity shapes)."""
import math


def _audit(message):
    print(message)


class Account:
    """A bank account."""

    rate = 0.03

    def __init__(self, owner, balance=0.0):
        self.owner = owner
        self.balance = balance

    def deposit(self, amount):
        if amount <= 0:
            raise ValueError("amount must be positive")
        self.balance += amount
        _audit(f"deposit {amount}")
        return self.balance

    @property
    def rich(self):
        def _threshold():
            return 1000.0

        return self.balance > _threshold()


class Savings(Account):
    def withdraw(self, amount):
        self.balance -= amount
        return self.balance
'''

AST_DEMO_JAVA = '''package demo;

/** A simple account. */
public class Account {
    private double balance;

    public Account(double balance) {
        this.balance = balance;
    }

    public double deposit(double amount) {
        if (amount <= 0) {
            throw new IllegalArgumentException("amount must be positive");
        }
        this.balance += amount;
        return this.balance;
    }

    private static void audit(String message) {
        System.out.println(message);
    }
}
'''


def run_entity_demo(language: str = "python") -> int:
    """Demo entry: parse source -> extract entities -> print table + fingerprint self-checks."""
    language = (language or "python").strip().lower()
    if language not in ("python", "java"):
        raise AnalysisError(
            f"--demo-lang supports python / java only, got {language!r}"
        )
    source = AST_DEMO_JAVA if language == "java" else AST_DEMO_PY

    print(f"=== AST entity extraction demo ({language}) ===")
    doc = parse_to_ast(source, language)
    entities = extract_entities(doc)
    stats = summarize_entities(entities)

    lines = doc.source_text.count("\n") + 1
    print(
        f"backend={doc.backend}  language={doc.language}  parse_ok={not doc.has_error}  "
        f"source {len(doc.source)} bytes / {lines} lines"
    )
    print(f"extracted {stats['total']} entities: {stats['by_type']}")
    print()
    print(f"{'kind':<9}{'qualname':<32}{'lines':<12}{'hash':<18}{'bytes':>7}")
    print("-" * 82)
    for entity in entities:
        span = f"L{entity['line_start']}-{entity['line_end']}"
        print(
            f"{entity['entity_type']:<9}{entity['qualname']:<32}{span:<12}"
            f"{entity['node_hash']:<18}{entity['end_byte'] - entity['start_byte']:>7}"
        )

    print()
    print("Full record of the first entity (the JSON shape downstream consumes):")
    print(json.dumps(entities[0], ensure_ascii=False, indent=2))

    # ---- self-checks ----
    problems: list = []
    for entity in entities:
        text_lines = entity["node_text"].splitlines()
        expected = entity["line_end"] - entity["line_start"] + 1
        if len(text_lines) != expected:
            problems.append(
                f"{entity['qualname']} line count mismatch: node_text has {len(text_lines)} lines vs "
                f"L{entity['line_start']}-{entity['line_end']} ({expected} lines)"
            )
        if "node_text" in entity and not entity["node_text"].strip():
            problems.append(f"{entity['qualname']} has an empty node_text")

    # Pure formatting changes (blank lines + comments + spaces around operators) must not change hashes
    comment = "// note\n" if language == "java" else "# note\n"
    reformatted = source.replace("\n\nclass ", "\n\n\n// separator comment\nclass ", 1)
    reformatted = reformatted.replace("\n\n\n", "\n\n", 1) if reformatted == source else reformatted
    reformatted = reformatted.rstrip("\n") + "\n\n" + comment
    hashes_after_format = {
        entity["qualname"]: entity["node_hash"]
        for entity in extract_entities(parse_to_ast(reformatted, language))
    }
    hashes_before = {entity["qualname"]: entity["node_hash"] for entity in entities}
    format_drift = [
        name for name, digest in hashes_before.items() if hashes_after_format.get(name) != digest
    ]
    if format_drift:
        problems.append(f"formatting/comment changes altered hashes: {', '.join(format_drift)}")

    # Semantic changes must change the hash
    if language == "python":
        mutated = source.replace("amount <= 0", "amount < 0")
        changed = {
            entity["qualname"]: entity["node_hash"]
            for entity in extract_entities(parse_to_ast(mutated, language))
        }
        if changed.get("Account.deposit") == hashes_before.get("Account.deposit"):
            problems.append("changing <= to < did not change the hash of Account.deposit")
        if changed.get("_audit") != hashes_before.get("_audit"):
            problems.append("editing deposit changed the hash of unrelated entity _audit")

    print()
    if problems:
        for problem in problems:
            LOGGER.error("self-check failed: %s", problem)
        return 2
    print("self-check passed: line spans match node_text; formatting/comments keep hashes; semantics change them.")
    return 0


# ---------------------------------------------------------------------------
# AST comparison demo: old/new versions of one file, covering add / delete / the three modify kinds.
# ---------------------------------------------------------------------------
COMPARE_DEMO_OLD = '''\
"""Order helpers (old version)."""

TAX_RATE = 0.08


def calculate_total(items, tax_rate=TAX_RATE):
    """Compute the total given a tax rate."""
    total = 0.0
    for item in items:
        total += item["price"] * item["qty"]
    return round(total * (1 + tax_rate), 2)


def format_price(value):
    text = f"{value:.2f}"
    return f"${text}"


def validate_user(user):
    """Validate that all user fields are present."""
    return bool(user.get("id"))


def legacy_export(rows):
    return ";".join(str(row) for row in rows)


class Invoice:
    def __init__(self, items):
        self.items = items

    def total(self):
        return calculate_total(self.items)
'''

COMPARE_DEMO_NEW = '''\
"""Order helpers (new version)."""

TAX_RATE = 0.08


def calculate_total(items, tax_rate=TAX_RATE):
    """Compute the total given a tax rate."""
    subtotal = sum(item["price"] * item["qty"] for item in items)
    return round(subtotal * (1 + tax_rate), 2)


def format_price(value, currency="USD"):
    text = f"{value:.2f}"
    return f"${text}"


def validate_user(user):
    """Validate user fields (id required, the rest optional)."""
    return bool(user.get("id"))


def refund_total(items):
    return round(calculate_total(items), 2)


class Invoice:
    def __init__(self, items):
        self.items = items

    def total(self):
        return calculate_total(self.items)
'''


def run_compare_demo() -> int:
    """Demo old/new AST comparison: add / delete / modify + change_category."""
    print("=== old/new AST comparison demo (src/utils.py) ===")
    detailed = compare_sources(
        COMPARE_DEMO_OLD,
        COMPARE_DEMO_NEW,
        file_path="src/utils.py",
        with_details=True,
    )
    changes = detailed["changes"]
    summary = summarize_changes(changes)
    print(f"{len(changes)} changes: {summary}")
    print()
    for change in changes:
        print(
            f"  {change['change_type']:<7} {str(change['change_category']):<20} "
            f"{change['entity_type']:<8} {change['entity_name']:<16} "
            f"L{change['line_start']}-{change['line_end']}"
        )
        print(f"          summary: {change['summary']}")
        if change.get("reasons"):
            print(f"          reasons: {'; '.join(change['reasons'])}")

    print()
    print("Strict format (the JSON downstream consumes directly):")
    strict = compare_sources(
        COMPARE_DEMO_OLD, COMPARE_DEMO_NEW, file_path="src/utils.py"
    )
    print(json.dumps(strict, ensure_ascii=False, indent=2))
    print()

    expected = {
        "calculate_total": ("modify", "Logic change"),
        "format_price": ("modify", "Signature change"),
        "validate_user": ("modify", "Documentation change"),
        "refund_total": ("add", None),
        "legacy_export": ("delete", None),
    }
    actual = {
        change["entity_name"]: (change["change_type"], change["change_category"])
        for change in strict["changes"]
    }
    problems: list[str] = []
    for name, want in expected.items():
        if actual.get(name) != want:
            problems.append(f"{name}: expected {want}, got {actual.get(name)}")
    missing = sorted(set(actual) - set(expected))
    if missing:
        problems.append(f"unexpected extra changes: {missing}")
    if problems:
        for problem in problems:
            LOGGER.error("self-check failed: %s", problem)
        return 2
    print("self-check passed: all 5 change kinds hit; untouched Invoice / __init__ / total are absent.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pr-analyzer",
        description="Turn a PR / git diff into JSON that downstream test-dependency mapping can consume.",
    )
    source = parser.add_argument_group("change source (pick one)")
    source.add_argument(
        "--pr",
        type=int,
        metavar="N",
        help="GitHub PR number; when given, online mode is used, otherwise local git diff.",
    )
    source.add_argument("--owner", help="repository owner (required in online mode)")
    source.add_argument("--name", help="repository name (required in online mode)")
    source.add_argument(
        "--repo",
        default=".",
        help="local repository path, defaults to the current directory; online mode uses it to read source (optional).",
    )
    source.add_argument(
        "--base", default="HEAD~1", help="base revision for offline mode, defaults to HEAD~1"
    )
    source.add_argument(
        "--head", default="HEAD", help="head revision for offline mode, defaults to HEAD"
    )
    source.add_argument(
        "--two-dot",
        action="store_true",
        help="use A..B instead of A...B (default is three dots, i.e. the merge-base).",
    )
    demo = parser.add_argument_group("diff parsing demo (no network)")
    demo.add_argument(
        "--demo",
        action="store_true",
        help="use the built-in sample diff to demo filtering -> splitting -> line ranges, no network.",
    )
    demo.add_argument(
        "--diff-file",
        metavar="PATH",
        help="parse a .diff/.patch file directly and print the resulting blocks.",
    )
    demo.add_argument(
        "--context-lines",
        type=int,
        default=3,
        help="context lines kept per block, defaults to 3 (same as git).",
    )

    parser.add_argument("-o", "--output", help="path of the output JSON file; defaults to stdout")
    ast_demo = parser.add_argument_group("AST entity demo (no network)")
    ast_demo.add_argument(
        "--demo-ast",
        action="store_true",
        help="parse the built-in sample source and print entities with line numbers and AST hashes.",
    )
    ast_demo.add_argument(
        "--demo-lang",
        metavar="LANG",
        help="language for --demo-ast, python / java, defaults to python.",
    )
    ast_demo.add_argument(
        "--demo-compare",
        action="store_true",
        help="compare the built-in old/new source and demo add/delete/modify with change_category.",
    )

    mirror = parser.add_argument_group("PR source mirror (needs --pr and network)")
    mirror.add_argument(
        "--save-sources",
        metavar="DIR",
        help="download every changed file at both base_sha and head_sha into DIR/base|head "
        "and write DIR/manifest.json (DIR defaults to out/pr_<N> when passed as an empty value).",
    )
    mirror.add_argument(
        "--force",
        action="store_true",
        help="with --save-sources/--analyze, re-download and overwrite files that already exist.",
    )

    analyze = parser.add_argument_group("PR AST analysis (needs --pr and network)")
    analyze.add_argument(
        "--analyze",
        action="store_true",
        help="run the full pipeline for --pr (mirror sources -> AST entities -> compare -> renames) "
        "and write the result to --output (default: output.json). Alias of the default behaviour "
        "when --repo is given as owner/repo.",
    )
    analyze.add_argument(
        "--mirror-dir",
        metavar="DIR",
        help="where to keep the downloaded base/head sources, defaults to out/pr_<N>.",
    )
    analyze.add_argument(
        "--min-similarity",
        type=float,
        default=0.9,
        metavar="0.0-1.0",
        help="minimum text similarity for treating a delete+add as a rename/move, defaults to 0.9.",
    )
    parser.add_argument(
        "--summary-only", action="store_true", help="print summary and warnings only (the full file is still written)"
    )
    parser.add_argument(
        "--indent", type=int, default=2, help="JSON indent, defaults to 2"
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="enable debug logging"
    )
    return parser


def _repo_name_from_args(args: argparse.Namespace) -> str:
    """Return ``owner/repo`` when the CLI points at a GitHub repository, else an empty string.

    ``--repo`` is shared with offline mode (a local path), so the value only counts as a
    repository name when it looks like ``owner/repo`` (or a GitHub URL) and is not an
    existing directory on disk.
    """
    if args.owner and args.name:
        return f"{args.owner}/{args.name}"
    candidate = (args.repo or "").strip()
    if not candidate or candidate in (".", "..") or Path(candidate).is_dir():
        return ""
    if "://" in candidate or candidate.startswith("git@"):
        return candidate
    normalised = candidate.replace("\\", "/")
    head = normalised.split("/", 1)[0]
    if "/" not in normalised or head in ("", ".", "..") or ":" in head:
        return ""
    return candidate


def _validate(args: argparse.Namespace, parser: argparse.ArgumentParser) -> None:
    if args.pr is not None:
        if not _repo_name_from_args(args):
            parser.error("online mode requires --repo owner/repo (or both --owner and --name)")
        if args.demo or args.diff_file:
            parser.error("--pr cannot be combined with --demo/--diff-file")
    elif args.owner or args.name:
        parser.error("--owner/--name are only valid together with --pr")
    if args.analyze:
        if args.pr is None:
            parser.error("--analyze needs --pr")
        if args.demo or args.diff_file or args.demo_ast or args.demo_compare:
            parser.error("--analyze cannot be combined with the demo flags")
        if args.save_sources is not None:
            parser.error("use either --analyze or --save-sources, not both")
    if not 0.0 <= args.min_similarity <= 1.0:
        parser.error("--min-similarity must be within [0, 1]")
    if args.demo and args.diff_file:
        parser.error("choose either --demo or --diff-file")
    if args.demo_ast or args.demo_compare:
        if args.pr is not None or args.demo or args.diff_file:
            parser.error("--demo-ast/--demo-compare cannot be combined with --pr/--demo/--diff-file")
        if args.demo_ast and args.demo_compare:
            parser.error("choose either --demo-ast or --demo-compare")
    elif args.demo_lang:
        parser.error("--demo-lang is only valid with --demo-ast")
    if args.save_sources is not None and args.pr is None:
        parser.error("--save-sources needs --pr (it downloads a GitHub PR's sources)")
    if args.force and args.save_sources is None and not args.analyze:
        parser.error("--force is only valid with --save-sources/--analyze")
    if args.context_lines < 0:
        parser.error("--context-lines must not be negative")


def run_diff_preview(diff_text: str, *, label: str, context_lines: int) -> int:
    """Demo entry: filter noise -> split -> print each block's path and line range."""
    files = parse_diff(diff_text)
    kept, skipped = filter_noise_files(files)
    blocks = split_diff(diff_text, context_lines=context_lines)
    keep_paths = [f.path for f in kept]

    print(f"=== {label} ===")
    print(f"diff has {len(diff_text)} chars / {len(diff_text.splitlines())} lines")
    print(f"{len(files)} files: kept {len(kept)}, skipped {len(skipped)} (noise)")
    for path, reason in skipped:
        print(f"  - {path:<28} [{reason}]")

    print()
    print(f"blocks (context_lines={context_lines}): {len(blocks)} / {len(keep_paths)} files")
    width = max((len(block.path) for block in blocks), default=0)
    for index, block in enumerate(blocks, start=1):
        flag = "" if block.path in keep_paths else "  (!! not kept)"
        print(f"  [{index}] {block.path:<{width}} {block.context.new_range:<10} "
              f"old {block.context.old_range:<10} "
              f"+{block.context.added} -{block.context.removed}{flag}")
        for line in block.context.text.splitlines():
            print(f"        {line}")

    total_added = sum(block.context.added for block in blocks)
    total_removed = sum(block.context.removed for block in blocks)
    print()
    print(f"total: +{total_added} / -{total_removed}; "
          f"files {', '.join(sorted(keep_paths)) if keep_paths else '(none)'}")

    # lightweight self-checks: every block must be in a kept file and line ranges must not be reversed
    problems = [block.path for block in blocks if block.path not in keep_paths]
    reversed_ranges = [
        block.header
        for block in blocks
        if block.context.new_end < block.context.new_start - 1
    ]
    if problems or reversed_ranges:
        if problems:
            LOGGER.error("blocks came from filtered files: %s", ", ".join(problems))
        if reversed_ranges:
            LOGGER.error("blocks have invalid line ranges: %s", ", ".join(reversed_ranges))
        return 2
    print("self-check passed: all blocks come from kept files and line ranges are valid.")
    return 0


def main(argv: list | None = None) -> int:
    # Force UTF-8 when redirected to a file/pipe: Windows cp936 mangles non-ASCII output
    if not sys.stdout.isatty():
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except (AttributeError, OSError):  # pragma: no cover - old Python / special streams
            pass

    parser = build_parser()
    args = parser.parse_args(argv)
    _validate(args, parser)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s: %(message)s",
    )

    try:
        if args.demo_ast:
            return run_entity_demo(args.demo_lang or "python")
        if args.demo_compare:
            return run_compare_demo()

        if args.demo or args.diff_file:
            if args.diff_file:
                diff_path = Path(args.diff_file)
                if not diff_path.is_file():
                    raise AnalysisError(f"--diff-file does not exist: {diff_path}")
                text = diff_path.read_text(encoding="utf-8", errors="replace")
                return run_diff_preview(
                    text, label=f"diff file {diff_path}", context_lines=args.context_lines
                )
            return run_diff_preview(
                SAMPLE_DIFF, label="built-in sample PR diff", context_lines=args.context_lines
            )

        analyze_mode = args.analyze or (
            args.pr is not None
            and not (args.owner and args.name)
            and args.save_sources is None
            and bool(_repo_name_from_args(args))
        )
        if analyze_mode:
            repo_name = _repo_name_from_args(args)
            result = analyze_pr(
                repo_name,
                args.pr,
                out_dir=args.mirror_dir or None,
                force=args.force,
                min_similarity=args.min_similarity,
            )
            output_path = Path(args.output or "output.json")
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(dumps(result, indent=args.indent), encoding="utf-8")
            summary = result["summary"]
            LOGGER.info(
                "PR analysis: %s files analysed, %s changes (%s renames) -> %s",
                summary["files_analyzed"],
                summary["changes"],
                summary["renames"],
                output_path.resolve(),
            )
            if args.summary_only:
                print(
                    json.dumps(
                        {"summary": summary, "warnings": result["warnings"]},
                        ensure_ascii=False,
                        indent=args.indent,
                    )
                )
            return 0

        if args.pr is not None and args.save_sources is not None:
            manifest = get_pr_source_files(
                f"{args.owner}/{args.name}",
                args.pr,
                out_dir=args.save_sources or None,
                force=args.force,
            )
            print(
                json.dumps(
                    {
                        "out_dir": manifest["out_dir"],
                        "base_sha": manifest["repo"]["base_sha"],
                        "head_sha": manifest["repo"]["head_sha"],
                        "counts": manifest["counts"],
                        "files": [item["path"] for item in manifest["files"]],
                    },
                    ensure_ascii=False,
                    indent=args.indent,
                )
            )
            return 0

        if args.pr is not None:
            owner, name = (
                (args.owner, args.name)
                if args.owner and args.name
                else parse_repo_name(_repo_name_from_args(args))
            )
            report = analyze_pull_request(
                owner,
                name,
                args.pr,
                repo_path=args.repo,
            )
        else:
            report = analyze_local(
                base=args.base,
                head=args.head,
                repo_path=args.repo,
                three_dot=not args.two_dot,
            )

        payload = dumps(report, indent=args.indent)

        if args.output:
            output_path = Path(args.output)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(payload, encoding="utf-8")
            LOGGER.info("report written to %s", output_path.resolve())
        elif not args.summary_only:
            print(payload)

        summary = report.get("summary", {})
        LOGGER.info(
            "%s files / %s hunks / %s affected scopes",
            summary.get("files_changed", 0),
            summary.get("hunks", 0),
            len(flatten_affected_scopes(report)),
        )
        if args.summary_only:
            print(
                json.dumps(
                    {
                        "summary": summary,
                        "affected_scopes": flatten_affected_scopes(report),
                        "warnings": report.get("warnings", []),
                    },
                    ensure_ascii=False,
                    indent=args.indent,
                )
            )
        for warning in report.get("warnings", []):
            LOGGER.warning(warning)
        return 0

    except AnalysisError as exc:
        LOGGER.error("%s", exc)
        return 2
    except KeyboardInterrupt:  # pragma: no cover
        LOGGER.error("interrupted")
        return 1
    except Exception as exc:  # noqa: BLE001 - single catch-all at the CLI boundary
        LOGGER.exception("unexpected error: %s", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
