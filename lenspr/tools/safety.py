"""Safety tool handlers: NFR checks, test coverage, security scanning, architecture rules, vibecheck."""

from __future__ import annotations

import fnmatch
import json as json_mod
import re
import uuid
from pathlib import Path
from typing import TYPE_CHECKING

from lenspr import database
from lenspr import graph as graph_module
from lenspr.models import ToolResponse

if TYPE_CHECKING:
    from lenspr.context import LensContext

__all__ = [
    "handle_nfr_check",
    "handle_test_coverage",
    "handle_security_scan",
    "handle_dep_audit",
    "handle_arch_rule_add",
    "handle_arch_rule_list",
    "handle_arch_rule_delete",
    "handle_arch_check",
    "handle_vibecheck",
    "handle_fix_plan",
    "handle_generate_test_skeleton",
    "check_arch_violations",
]

# ---------------------------------------------------------------------------
# Architecture rules persistence helpers
# ---------------------------------------------------------------------------

def _arch_rules_path(ctx: LensContext) -> Path:
    return ctx.project_root / ".lens" / "arch_rules.json"


def _load_arch_rules(ctx: LensContext) -> list[dict]:
    p = _arch_rules_path(ctx)
    if not p.exists():
        return []
    try:
        return json_mod.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return []


def _save_arch_rules(rules: list[dict], ctx: LensContext) -> None:
    p = _arch_rules_path(ctx)
    p.write_text(json_mod.dumps(rules, indent=2), encoding="utf-8")


def _matches_pattern(node_id: str, pattern: str) -> bool:
    """Match a node ID against a glob-style pattern.

    Slashes are treated as dots so path-style patterns work:
      '*/api/*'     matches any node ID containing '.api.'
      '*_handler'   matches node IDs ending in '_handler'
      'auth.*'      matches IDs starting with 'auth.'
    """
    dot_pattern = pattern.replace("/", ".")
    return fnmatch.fnmatch(node_id, dot_pattern)


# ---------------------------------------------------------------------------
# check_arch_violations — called from get_proactive_warnings (no ToolResponse)
# ---------------------------------------------------------------------------

def check_arch_violations(node_id: str, ctx: LensContext) -> list[str]:
    """Return a list of human-readable violation messages for a node being changed."""
    rules = _load_arch_rules(ctx)
    if not rules:
        return []

    nx_graph = ctx.get_graph()
    messages: list[str] = []

    for rule in rules:
        rule_type = rule.get("type", "")
        desc = rule.get("description", rule_type)

        if rule_type == "no_dependency":
            from_pattern = rule.get("from_pattern", "")
            to_pattern = rule.get("to_pattern", "")
            if _matches_pattern(node_id, from_pattern):
                for successor in nx_graph.successors(node_id):
                    if _matches_pattern(str(successor), to_pattern):
                        messages.append(
                            f"Rule '{desc}': '{node_id}' depends on '{successor}' "
                            f"(forbidden: {from_pattern} → {to_pattern})"
                        )

        elif rule_type == "required_test":
            pattern = rule.get("pattern", "")
            node_data = nx_graph.nodes.get(node_id, {})
            node_name = node_data.get("name", "")
            if _matches_pattern(node_name, pattern):
                has_test = any(
                    nx_graph.nodes.get(pred_id, {}).get("name", "").startswith("test_")
                    or "test" in (nx_graph.nodes.get(pred_id, {}).get("file_path") or "").lower()
                    for pred_id in nx_graph.predecessors(node_id)
                )
                if not has_test:
                    messages.append(
                        f"Rule '{desc}': '{node_name}' matches '{pattern}' but has no tests"
                    )

    return messages


# ---------------------------------------------------------------------------
# handle_nfr_check
# ---------------------------------------------------------------------------

_IO_MARKERS = [
    "open(", "requests.", "httpx.", "aiohttp.",
    ".execute(", ".query(", ".fetchone(", ".fetchall(",
    "subprocess.", "socket.", "urllib.",
    ".read_text(", ".write_text(", ".read_bytes(", ".write_bytes(",
    ".unlink(", "shutil.",
]

_SECRET_PATTERNS = [
    (r'(?i)(password|passwd|pwd)\s*=\s*["\'][^"\']{3,}["\']', "hardcoded password"),
    (r'(?i)(api_key|apikey|secret_key)\s*=\s*["\'][^"\']{6,}["\']', "hardcoded API key"),
    (r'(?i)(token)\s*=\s*["\'][^"\']{8,}["\']', "hardcoded token"),
    (r'(?i)(secret)\s*=\s*["\'][^"\']{6,}["\']', "hardcoded secret"),
]


def _try_pytest_cov(ctx: LensContext) -> dict | None:
    """Run pytest --cov and return coverage JSON data, or None if unavailable.

    Tries two approaches:
    1. Read existing coverage.json from .lens/ (from a previous run)
    2. Run pytest --cov to generate fresh data

    Returns parsed JSON dict on success, None on any failure.
    """
    import json
    import subprocess
    import time

    cov_json = ctx.project_root / ".lens" / "coverage.json"

    # Try existing coverage data first (if recent - less than 5 min old)
    if cov_json.exists():
        age = time.time() - cov_json.stat().st_mtime
        if age < 300:  # 5 minutes
            try:
                return json.loads(cov_json.read_text())
            except (json.JSONDecodeError, OSError):
                pass

    # Run pytest --cov
    try:
        subprocess.run(
            [
                "python", "-m", "pytest",
                "--cov", "--cov-report", f"json:{cov_json}",
                "-q", "--no-header", "--tb=no",
            ],
            cwd=str(ctx.project_root),
            capture_output=True,
            text=True,
            timeout=120,
        )
        if cov_json.exists():
            return json.loads(cov_json.read_text())
    except (FileNotFoundError, subprocess.TimeoutExpired, json.JSONDecodeError, OSError):
        pass
    return None


def _map_cov_to_functions(
    cov_data: dict, nodes: list, project_root: str
) -> tuple[list[dict], list[dict]]:
    """Map pytest-cov line coverage to function-level coverage using graph nodes.

    A function is 'covered' if at least 1 of its body lines was executed.
    Returns (covered_list, uncovered_list) in the same format as graph-based.
    """
    from pathlib import Path

    # Build lookup: relative_file_path → {executed_lines}
    file_coverage: dict[str, set[int]] = {}
    files_data = cov_data.get("files", {})
    for abs_path, info in files_data.items():
        # Convert absolute paths to relative
        try:
            rel = str(Path(abs_path).relative_to(project_root))
        except ValueError:
            rel = abs_path
        executed = set(info.get("executed_lines", []))
        file_coverage[rel] = executed

    covered: list[dict] = []
    uncovered: list[dict] = []

    for node in nodes:
        if node.type.value not in ("function", "method"):
            continue
        if (
            node.name.startswith("test_")
            or "test" in (node.file_path or "").lower()
            or (node.file_path or "").startswith("eval/")
        ):
            continue

        fp = node.file_path or ""
        executed = file_coverage.get(fp, set())
        # Skip the `def` line — it's always "executed" on module import.
        # Only count body lines (start_line+1 .. end_line) as real coverage.
        # For one-liners (start == end), the def line is the only line.
        end = node.end_line or node.start_line
        body_start = node.start_line + 1 if end > node.start_line else node.start_line
        node_lines = set(range(body_start, end + 1))
        hit = node_lines & executed

        if hit:
            covered.append({
                "node_id": node.id,
                "name": node.name,
                "file": fp,
                "lines_hit": len(hit),
                "lines_total": len(node_lines),
            })
        else:
            uncovered.append({
                "node_id": node.id,
                "name": node.name,
                "file": fp,
            })

    return covered, uncovered


def handle_nfr_check(params: dict, ctx: LensContext) -> ToolResponse:
    """Check a function for missing non-functional requirements (NFRs).

    Checks: error handling, logging, hardcoded secrets, input validation,
    auth on sensitive operations.
    """
    node_id = params.get("node_id")
    if not node_id:
        return ToolResponse(success=False, error="node_id is required")
    node = database.get_node(node_id, ctx.graph_db)
    if not node:
        return ToolResponse(success=False, error=f"Node not found: {node_id}")

    src = node.source_code or ""
    line_count = len(src.splitlines())
    issues: list[dict] = []

    # 1. IO without error handling (try/finally alone is NOT error handling)
    has_io = any(marker in src for marker in _IO_MARKERS)
    has_error_handling = "try:" in src and "except" in src
    if has_io and not has_error_handling:
        issues.append({
            "severity": "HIGH",
            "rule": "io_without_error_handling",
            "message": "IO/network/DB operations present but no try/except found",
        })

    # 2. No structured logging (only flag functions with meaningful body)
    if line_count > 10:
        has_logging = any(x in src for x in ["logger.", "logging.", "log.info", "log.error", "log.warning", "log.debug"])
        if not has_logging:
            issues.append({
                "severity": "MEDIUM",
                "rule": "no_logging",
                "message": "Function has no structured logging calls (logger./logging.)",
            })

    # 3. Hardcoded secrets
    for pattern, label in _SECRET_PATTERNS:
        if re.search(pattern, src):
            issues.append({
                "severity": "CRITICAL",
                "rule": "hardcoded_secret",
                "message": f"Possible {label} — use environment variables instead",
            })
            break  # one secret warning is enough

    # 4. Handler without input validation
    is_handler = any(x in node.name.lower() for x in ["handler", "endpoint", "route", "view", "_api"])
    has_validation = any(x in src for x in [
        "raise ValueError", "raise TypeError", "ValidationError",
        "validate_", "if not ", "HTTPException",
    ])
    if is_handler and not has_validation:
        issues.append({
            "severity": "MEDIUM",
            "rule": "no_input_validation",
            "message": "Handler/endpoint appears to have no input validation",
        })

    # 5. Auth-sensitive operation without auth check
    auth_sensitive = any(x in node.name.lower() for x in ["admin", "delete", "update", "create", "write", "modify"])
    has_auth = any(x in src for x in ["auth", "token", "permission", "require_auth", "login_required", "current_user"])
    if auth_sensitive and not has_auth:
        issues.append({
            "severity": "MEDIUM",
            "rule": "no_auth_check",
            "message": "Auth-sensitive operation (create/update/delete) with no visible auth check",
        })

    critical_count = sum(1 for i in issues if i["severity"] == "CRITICAL")
    high_count = sum(1 for i in issues if i["severity"] == "HIGH")
    score = "PASS" if not issues else f"FAIL ({len(issues)} issue{'s' if len(issues) != 1 else ''})"

    return ToolResponse(
        success=True,
        data={
            "node_id": node_id,
            "node_name": node.name,
            "score": score,
            "issues": issues,
            "critical_count": critical_count,
            "high_count": high_count,
        },
        warnings=[i["message"] for i in issues if i["severity"] in ("CRITICAL", "HIGH")],
    )


# ---------------------------------------------------------------------------
# handle_test_coverage
# ---------------------------------------------------------------------------

def handle_test_coverage(params: dict, ctx: LensContext) -> ToolResponse:
    """Report which functions/methods have test coverage.

    Tries pytest-cov (runtime line coverage) first, falls back to
    static call graph analysis if pytest-cov is unavailable.
    """
    file_path_filter = params.get("file_path")

    ctx.ensure_synced()
    nodes = database.get_nodes(ctx.graph_db, file_filter=file_path_filter)

    # Try runtime coverage first
    cov_data = _try_pytest_cov(ctx)
    if cov_data:
        covered, uncovered = _map_cov_to_functions(
            cov_data, nodes, str(ctx.project_root)
        )
        source = "pytest-cov (runtime)"
        method_desc = (
            "Runtime line coverage via pytest-cov. A function is covered if "
            "at least 1 of its body lines was executed during tests."
        )
    else:
        # Fallback: static call graph
        nx_graph = ctx.get_graph()
        covered = []
        uncovered = []

        for node in nodes:
            if node.type.value not in ("function", "method"):
                continue
            if (
                node.name.startswith("test_")
                or "test" in (node.file_path or "").lower()
                or (node.file_path or "").startswith("eval/")
            ):
                continue

            test_callers = [
                pred_id
                for pred_id in nx_graph.predecessors(node.id)
                if (
                    nx_graph.nodes.get(pred_id, {}).get("name", "").startswith("test_")
                    or "test" in (nx_graph.nodes.get(pred_id, {}).get("file_path") or "").lower()
                )
            ]

            if test_callers:
                covered.append({
                    "node_id": node.id,
                    "name": node.name,
                    "file": node.file_path,
                    "tests": test_callers,
                })
            else:
                uncovered.append({
                    "node_id": node.id,
                    "name": node.name,
                    "file": node.file_path,
                })

        source = "graph-based (static)"
        method_desc = (
            "Static call graph — a function counts as covered only if a test "
            "calls it via a resolved edge. Dynamic calls (getattr/importlib) "
            "may not appear as covered even if tests exist. "
            "Install pytest-cov for accurate runtime coverage."
        )

    total = len(covered) + len(uncovered)
    pct = round(len(covered) / total * 100) if total else 100

    grade = "A" if pct >= 80 else "B" if pct >= 60 else "C" if pct >= 40 else "D" if pct >= 20 else "F"

    return ToolResponse(
        success=True,
        data={
            "source": source,
            "coverage_pct": pct,
            "grade": grade,
            "covered_count": len(covered),
            "uncovered_count": len(uncovered),
            "uncovered": uncovered[:100],
            "covered": covered[:50],
            "filter": file_path_filter,
            "analysis_method": method_desc,
            "hint": (
                f"Run lens_run_tests to see if tests pass. "
                f"{len(uncovered)} functions have no tests — consider adding them."
            ) if uncovered else "Great — all functions have test coverage.",
        },
        warnings=[
            f"⚠️ Only {pct}% test coverage (grade {grade}) — "
            f"{len(uncovered)} functions untested"
        ] if pct < 50 else [],
    )


# ---------------------------------------------------------------------------
# handle_security_scan
# ---------------------------------------------------------------------------

def handle_security_scan(params: dict, ctx: LensContext) -> ToolResponse:
    """Run Bandit security scanner and map results to graph nodes.

    Requires: pip install bandit
    """
    import subprocess

    ctx.ensure_synced()

    target = params.get("file_path") or str(ctx.project_root)
    from lenspr.tools.helpers import find_containing_node

    try:
        result = subprocess.run(
            ["bandit", "-r", target, "-f", "json", "-q", "--exit-zero"],
            capture_output=True,
            text=True,
            timeout=120,
            cwd=str(ctx.project_root),
        )
        data = json_mod.loads(result.stdout)
    except FileNotFoundError:
        return ToolResponse(
            success=False,
            error="Bandit is not installed.",
            hint="Install with: pip install bandit",
        )
    except json_mod.JSONDecodeError as e:
        return ToolResponse(success=False, error=f"Failed to parse Bandit output: {e}")
    except subprocess.TimeoutExpired:
        return ToolResponse(success=False, error="Security scan timed out (>120s).")

    nx_graph = ctx.get_graph()
    issues_by_node: dict[str, list] = {}

    for issue in data.get("results", []):
        raw_file = issue["filename"]
        try:
            rel_file = str(Path(raw_file).relative_to(ctx.project_root))
        except ValueError:
            rel_file = raw_file

        line = issue["line_number"]
        node = find_containing_node(nx_graph, rel_file, line)
        node_key = node.id if node else f"{rel_file}:{line}"

        if node_key not in issues_by_node:
            issues_by_node[node_key] = []
        issues_by_node[node_key].append({
            "severity": issue["issue_severity"],
            "confidence": issue["issue_confidence"],
            "message": issue["issue_text"],
            "line": line,
            "test_id": issue["test_id"],
            "cwe": issue.get("issue_cwe", {}).get("id"),
        })

    results = data.get("results", [])
    high = sum(1 for r in results if r["issue_severity"] == "HIGH")
    medium = sum(1 for r in results if r["issue_severity"] == "MEDIUM")
    low = sum(1 for r in results if r["issue_severity"] == "LOW")

    return ToolResponse(
        success=True,
        data={
            "total_issues": len(results),
            "high": high,
            "medium": medium,
            "low": low,
            "issues_by_node": issues_by_node,
            "target": str(target),
        },
        warnings=(
            [f"🔴 {high} HIGH severity security issue(s) found — review immediately"]
            if high > 0
            else []
        ),
    )


# ---------------------------------------------------------------------------
# handle_dep_audit
# ---------------------------------------------------------------------------

def handle_dep_audit(params: dict, ctx: LensContext) -> ToolResponse:
    """Audit project dependencies for known vulnerabilities.

    Tries pip-audit (Python) then npm audit (Node.js) depending on project type.
    Requires: pip install pip-audit  OR  npm (for JS projects)
    """
    import subprocess

    # Try pip-audit first
    try:
        result = subprocess.run(
            ["pip-audit", "--format=json", "--progress-spinner=off"],
            capture_output=True,
            text=True,
            timeout=120,
            cwd=str(ctx.project_root),
        )
        data = json_mod.loads(result.stdout)
        vulns = [
            {
                "package": dep["name"],
                "version": dep["version"],
                "vulnerabilities": [
                    {
                        "id": v["id"],
                        "fix_versions": v.get("fix_versions", []),
                        "description": v.get("description", ""),
                    }
                    for v in dep.get("vulns", [])
                ],
            }
            for dep in data.get("dependencies", [])
            if dep.get("vulns")
        ]
        return ToolResponse(
            success=True,
            data={
                "tool": "pip-audit",
                "vulnerable_packages": len(vulns),
                "vulnerabilities": vulns,
                "hint": "Run `pip install --upgrade <package>` to fix vulnerabilities" if vulns else None,
            },
            warnings=(
                [f"⚠️ {len(vulns)} Python package(s) have known vulnerabilities"]
                if vulns
                else []
            ),
        )
    except FileNotFoundError:
        pass
    except json_mod.JSONDecodeError:
        pass

    # Try npm audit
    pkg_json = ctx.project_root / "package.json"
    if pkg_json.exists():
        try:
            result = subprocess.run(
                ["npm", "audit", "--json"],
                capture_output=True,
                text=True,
                timeout=120,
                cwd=str(ctx.project_root),
            )
            data = json_mod.loads(result.stdout)
            summary = data.get("metadata", {}).get("vulnerabilities", {})
            critical = summary.get("critical", 0)
            high = summary.get("high", 0)
            total = sum(summary.values()) if summary else 0
            return ToolResponse(
                success=True,
                data={
                    "tool": "npm audit",
                    "total_vulnerabilities": total,
                    "summary": summary,
                    "hint": "Run `npm audit fix` to automatically fix compatible issues" if total else None,
                },
                warnings=(
                    [f"⚠️ {total} npm package vulnerabilities ({critical} critical, {high} high)"]
                    if total > 0
                    else []
                ),
            )
        except (FileNotFoundError, json_mod.JSONDecodeError):
            pass

    return ToolResponse(
        success=False,
        error="No dependency audit tool found.",
        hint=(
            "For Python: pip install pip-audit\n"
            "For Node.js: npm audit (built into npm)"
        ),
    )


# ---------------------------------------------------------------------------
# Architecture fitness functions
# ---------------------------------------------------------------------------

def handle_arch_rule_add(params: dict, ctx: LensContext) -> ToolResponse:
    """Add an architecture rule that is checked on every code change.

    Rule types:
    - no_dependency: forbid a dependency between two module patterns
    - max_class_methods: limit class size
    - required_test: require tests for functions matching a pattern
    - no_circular_imports: flag any circular import chains
    """
    rule_type = params.get("rule_type", "")
    config = params.get("config", {})
    description = params.get("description", "")

    valid_types = {
        "no_dependency": "from_pattern + to_pattern required",
        "max_class_methods": "threshold required (int)",
        "required_test": "pattern required (glob for function names)",
        "no_circular_imports": "no extra config needed",
    }

    if rule_type not in valid_types:
        return ToolResponse(
            success=False,
            error=f"Unknown rule type: '{rule_type}'",
            hint=f"Valid types: {', '.join(f'{k} ({v})' for k, v in valid_types.items())}",
        )

    rules = _load_arch_rules(ctx)
    rule = {
        "id": str(uuid.uuid4())[:8],
        "type": rule_type,
        "description": description or rule_type,
        **config,
    }
    rules.append(rule)
    _save_arch_rules(rules, ctx)

    return ToolResponse(
        success=True,
        data={
            "rule": rule,
            "total_rules": len(rules),
            "message": f"Rule added. It will be checked on every lens_update_node call.",
        },
    )


def handle_arch_rule_list(params: dict, ctx: LensContext) -> ToolResponse:
    """List all defined architecture rules."""
    rules = _load_arch_rules(ctx)
    return ToolResponse(
        success=True,
        data={
            "rules": rules,
            "count": len(rules),
            "hint": "Use lens_arch_rule_add to add rules, lens_arch_rule_delete to remove them.",
        },
    )


def handle_arch_rule_delete(params: dict, ctx: LensContext) -> ToolResponse:
    """Delete an architecture rule by ID."""
    rule_id = params.get("rule_id", "")
    rules = _load_arch_rules(ctx)
    before = len(rules)
    rules = [r for r in rules if r.get("id") != rule_id]
    if len(rules) == before:
        return ToolResponse(
            success=False,
            error=f"Rule not found: '{rule_id}'",
            hint="Use lens_arch_rule_list to see rule IDs.",
        )
    _save_arch_rules(rules, ctx)
    return ToolResponse(
        success=True,
        data={"deleted_id": rule_id, "remaining_rules": len(rules)},
    )


def handle_arch_check(params: dict, ctx: LensContext) -> ToolResponse:
    """Check all architecture rules against the current codebase.

    Returns violations grouped by rule. Run after refactoring or to audit
    an existing project.
    """
    ctx.ensure_synced()
    rules = _load_arch_rules(ctx)

    if not rules:
        return ToolResponse(
            success=True,
            data={
                "violations": [],
                "rules_checked": 0,
                "passed": True,
                "message": "No architecture rules defined. Use lens_arch_rule_add to create rules.",
            },
        )

    nx_graph = ctx.get_graph()
    violations: list[dict] = []

    for rule in rules:
        rule_type = rule.get("type", "")
        rule_id = rule.get("id", "?")
        desc = rule.get("description", rule_type)

        if rule_type == "no_dependency":
            from_pattern = rule.get("from_pattern", "")
            to_pattern = rule.get("to_pattern", "")
            for u, v in nx_graph.edges():
                if _matches_pattern(str(u), from_pattern) and _matches_pattern(str(v), to_pattern):
                    violations.append({
                        "rule_id": rule_id,
                        "rule_type": rule_type,
                        "description": desc,
                        "violation": f"{u} → {v}",
                        "message": f"Forbidden dependency: '{u}' imports/calls '{v}'",
                    })

        elif rule_type == "max_class_methods":
            threshold = int(rule.get("threshold", 20))
            for nid, ndata in nx_graph.nodes(data=True):
                if ndata.get("type") != "class":
                    continue
                node_obj = database.get_node(nid, ctx.graph_db)
                if node_obj and node_obj.metrics:
                    count = node_obj.metrics.get("method_count", 0)
                    if count > threshold:
                        violations.append({
                            "rule_id": rule_id,
                            "rule_type": rule_type,
                            "description": desc,
                            "violation": nid,
                            "message": f"Class '{ndata.get('name', nid)}' has {count} methods (max: {threshold})",
                        })

        elif rule_type == "required_test":
            pattern = rule.get("pattern", "")
            all_nodes = database.get_nodes(ctx.graph_db)
            for node_obj in all_nodes:
                if node_obj.type.value not in ("function", "method"):
                    continue
                if not _matches_pattern(node_obj.name, pattern):
                    continue
                has_test = any(
                    nx_graph.nodes.get(pred_id, {}).get("name", "").startswith("test_")
                    or "test" in (nx_graph.nodes.get(pred_id, {}).get("file_path") or "").lower()
                    for pred_id in nx_graph.predecessors(node_obj.id)
                )
                if not has_test:
                    violations.append({
                        "rule_id": rule_id,
                        "rule_type": rule_type,
                        "description": desc,
                        "violation": node_obj.id,
                        "message": f"'{node_obj.name}' matches '{pattern}' but has no tests",
                    })

        elif rule_type == "no_circular_imports":
            cycles = graph_module.detect_circular_imports(nx_graph)
            for cycle in cycles:
                violations.append({
                    "rule_id": rule_id,
                    "rule_type": rule_type,
                    "description": desc,
                    "violation": " → ".join(cycle),
                    "message": f"Circular import: {' → '.join(cycle)}",
                })

    return ToolResponse(
        success=True,
        data={
            "violations": violations,
            "violation_count": len(violations),
            "rules_checked": len(rules),
            "passed": len(violations) == 0,
        },
        warnings=(
            [f"⚠️ {len(violations)} architecture rule violation(s) — run lens_arch_check for details"]
            if violations
            else []
        ),
    )


# ---------------------------------------------------------------------------
# handle_vibecheck — overall vibecoding health score
# ---------------------------------------------------------------------------

def handle_vibecheck(params: dict, ctx: LensContext) -> ToolResponse:
    """Comprehensive vibecoding health score for the project.

    Aggregates: test coverage, dead code, circular imports, architecture rules,
    documentation, and graph confidence into a single 0-100 score with grade A–F.

    Use this to track whether the codebase is improving or degrading over time.
    """
    ctx.ensure_synced()
    nx_graph = ctx.get_graph()
    all_nodes = database.get_nodes(ctx.graph_db)

    # Production functions only (exclude test files and eval/ scripts)
    func_nodes = [
        n for n in all_nodes
        if n.type.value in ("function", "method")
        and "test" not in (n.file_path or "").lower()
        and not n.name.startswith("test_")
        and not (n.file_path or "").startswith("eval/")
    ]
    total_funcs = len(func_nodes)

    score = 0
    breakdown: dict[str, dict] = {}
    top_risks: list[str] = []

    # --- 1. Test coverage (0-25 points) ---
    # Prefer pytest-cov (runtime) over graph-based (static) when available.
    cov_data = _try_pytest_cov(ctx)
    if cov_data:
        cov_covered, cov_uncovered = _map_cov_to_functions(
            cov_data, all_nodes, str(ctx.project_root)
        )
        covered = len(cov_covered)
    else:
        covered = 0
        for node in func_nodes:
            has_test = any(
                nx_graph.nodes.get(pred_id, {}).get("name", "").startswith("test_")
                or "test" in (nx_graph.nodes.get(pred_id, {}).get("file_path") or "").lower()
                for pred_id in nx_graph.predecessors(node.id)
            )
            if has_test:
                covered += 1

    test_pct = round(covered / total_funcs * 100) if total_funcs else 100
    test_score = round(test_pct / 100 * 25)
    breakdown["test_coverage"] = {
        "score": test_score,
        "max": 25,
        "detail": f"{test_pct}% tested ({covered}/{total_funcs} functions)",
    }
    score += test_score
    if test_pct < 30:
        top_risks.append(f"🔴 Only {test_pct}% test coverage — bugs go undetected")

    # --- 2. Dead code (0-20 points) ---
    # Reuse handle_dead_code which has full entry-point auto-detection (1000+ patterns).
    # Calling find_dead_code(entry_points=[]) would give a false 200%+ figure since
    # everything with no predecessor is "dead" without proper entry points.
    from lenspr.tools.analysis import handle_dead_code as _handle_dead_code  # noqa: PLC0415
    dead_resp = _handle_dead_code({}, ctx)
    if dead_resp.success and dead_resp.data:
        all_dead_ids: list[str] = dead_resp.data.get("dead_code", [])
        dead = [
            d for d in all_dead_ids
            if not d.startswith("eval.")
            and "test" not in d.lower()
        ]
    else:
        dead = []
    dead_pct = round(len(dead) / total_funcs * 100) if total_funcs else 0
    dead_score = max(0, 20 - dead_pct)  # lose 1 point per 1% dead code
    breakdown["dead_code"] = {
        "score": dead_score,
        "max": 20,
        "detail": f"{len(dead)} dead nodes ({dead_pct}% of production functions)",
    }
    score += dead_score
    if dead_pct > 15:
        top_risks.append(f"🟠 {dead_pct}% dead code — unused functions accumulating")

    # --- 3. Circular imports (0-15 points) ---
    cycles = graph_module.detect_circular_imports(nx_graph)
    circular_score = max(0, 15 - len(cycles) * 5)
    breakdown["circular_imports"] = {
        "score": circular_score,
        "max": 15,
        "detail": f"{len(cycles)} circular import chain(s)",
    }
    score += circular_score
    if cycles:
        top_risks.append(f"🟠 {len(cycles)} circular import(s) — architectural debt")

    # --- 4. Architecture rules compliance (0-15 points) ---
    rules = _load_arch_rules(ctx)
    if not rules:
        arch_score = 8  # partial: no rules = no violations, but no governance either
        arch_detail = "No rules defined (use lens_arch_rule_add to enforce boundaries)"
    else:
        violations_resp = handle_arch_check({}, ctx)
        violations = (
            violations_resp.data.get("violations", [])
            if violations_resp.data
            else []
        )
        arch_score = max(0, 15 - len(violations) * 3)
        arch_detail = f"{len(violations)} violation(s) across {len(rules)} rule(s)"
        if violations:
            top_risks.append(f"🟡 {len(violations)} architecture rule violation(s)")
    breakdown["architecture"] = {"score": arch_score, "max": 15, "detail": arch_detail}
    score += arch_score

    # --- 5. Documentation / annotations (0-10 points) ---
    annotated = sum(1 for n in func_nodes if n.summary or n.docstring)
    ann_pct = round(annotated / total_funcs * 100) if total_funcs else 100
    ann_score = round(ann_pct / 100 * 10)
    breakdown["documentation"] = {
        "score": ann_score,
        "max": 10,
        "detail": f"{ann_pct}% of functions have descriptions",
    }
    score += ann_score

    # --- 6. Graph confidence (0-15 points) ---
    # Only count internal edges. External edges (stdlib/third-party) are expected
    # to be unresolved and should not penalize the confidence score.
    internal_edges = [
        (u, v, d) for u, v, d in nx_graph.edges(data=True)
        if d.get("confidence") != "external"
    ]
    if internal_edges:
        resolved = sum(
            1 for _, _, d in internal_edges
            if d.get("confidence") == "resolved"
        )
        conf_pct = round(resolved / len(internal_edges) * 100)
    else:
        conf_pct = 100
    conf_score = round(conf_pct / 100 * 15)
    breakdown["graph_confidence"] = {
        "score": conf_score,
        "max": 15,
        "detail": f"{conf_pct}% of internal edges resolved (excludes stdlib/third-party)",
    }
    score += conf_score

    # --- Grade ---
    grade = (
        "A" if score >= 90
        else "B" if score >= 75
        else "C" if score >= 60
        else "D" if score >= 45
        else "F"
    )

    total_files = len({n.file_path for n in all_nodes if n.file_path})

    return ToolResponse(
        success=True,
        data={
            "score": score,
            "max_score": 100,
            "grade": grade,
            "summary": f"Vibecoding Health: {grade} ({score}/100)",
            "breakdown": breakdown,
            "top_risks": top_risks[:5],
            "stats": {
                "total_functions": total_funcs,
                "total_files": total_files,
            },
            "hint": (
                "Use lens_test_coverage, lens_security_scan, lens_arch_check "
                "for detailed breakdowns of individual categories."
            ),
        },
    )

def handle_fix_plan(params: dict, ctx: LensContext) -> ToolResponse:
    """Generate an ordered, actionable remediation plan to improve the vibecheck score.

    Returns a prioritized list of concrete actions, each with action_type,
    target_node_id, reason, priority, and expected_score_impact.

    Work through actions in priority order. Run lens_vibecheck() after each
    batch to track progress. Use lens_generate_test_skeleton(node_id) for
    test writing guidance.
    """
    target_grade = params.get("target_grade", "B")
    max_items = int(params.get("max_items", 20))
    focus = params.get("focus")  # optional: "tests" | "docs" | "arch" | "dead_code"

    ctx.ensure_synced()
    nx_graph = ctx.get_graph()
    all_nodes = database.get_nodes(ctx.graph_db)

    # Production functions only
    func_nodes = [
        n for n in all_nodes
        if n.type.value in ("function", "method")
        and "test" not in (n.file_path or "").lower()
        and not n.name.startswith("test_")
        and not (n.file_path or "").startswith("eval/")
    ]
    total_funcs = len(func_nodes)

    # Score impact per unit — must mirror vibecheck's exact scoring formula.
    # test_score  = covered/total * 25  → +1 covered = +25/total pts       ✓
    # ann_score   = annotated/total * 10 → +1 annotated = +10/total pts     ✓
    # dead_score  = max(0, 20 - dead_pct) where dead_pct = N/total*100
    #             → +1 deleted = dead_pct drops 100/total pts
    #             → dead_score gains 100/total pts  (NOT 20/total)
    pts_per_test = round(25 / total_funcs, 3) if total_funcs else 0
    pts_per_doc = round(10 / total_funcs, 3) if total_funcs else 0
    pts_per_dead = round(100 / total_funcs, 3) if total_funcs else 0

    actions: list[dict] = []

    # --- 1. Arch violations (highest impact: 3 pts/violation, LOW effort) ---
    if not focus or focus == "arch":
        violations_resp = handle_arch_check({}, ctx)
        if violations_resp.success and violations_resp.data:
            for v in violations_resp.data.get("violations", []):
                viol_id = v.get("violation", "")
                # Look up node in graph for name/file (works for max_class_methods, required_test)
                viol_node_data = nx_graph.nodes.get(viol_id, {})
                actions.append({
                    "action_type": "fix_arch_violation",
                    "target_node_id": viol_id,
                    "target_name": viol_node_data.get("name", viol_id),
                    "file": viol_node_data.get("file_path", ""),
                    "reason": v.get("message", v.get("description", "Architecture rule violated")),
                    "priority": "CRITICAL",
                    "expected_score_impact": 3.0,
                    "hint": f"Rule: {v.get('description', '')}",
                })

    # --- 2. NFR: IO without error handling (HIGH severity, affects reliability) ---
    if not focus or focus == "nfr":
        for node in func_nodes:
            src = node.source_code or ""
            has_io = any(marker in src for marker in _IO_MARKERS)
            has_error_handling = "try:" in src and "except" in src
            if has_io and not has_error_handling:
                # Count callers (higher = more urgent)
                caller_count = sum(
                    1 for pred_id in nx_graph.predecessors(node.id)
                    if not (nx_graph.nodes.get(pred_id, {}).get("file_path") or "").startswith("tests/")
                )
                actions.append({
                    "action_type": "add_error_handling",
                    "target_node_id": node.id,
                    "target_name": node.name,
                    "file": node.file_path,
                    "reason": f"IO/network/DB operations without try/except ({caller_count} callers)",
                    "priority": "HIGH" if caller_count > 2 else "MEDIUM",
                    "expected_score_impact": 0.0,  # NFR doesn't affect vibecheck score directly
                    "hint": "Wrap IO operations in try/except; log errors with logger.error()",
                })

    # --- 3. Test coverage: uncovered functions sorted by caller count ---
    if not focus or focus == "tests":
        for node in func_nodes:
            has_test = any(
                nx_graph.nodes.get(pred_id, {}).get("name", "").startswith("test_")
                or "test" in (nx_graph.nodes.get(pred_id, {}).get("file_path") or "").lower()
                for pred_id in nx_graph.predecessors(node.id)
            )
            if not has_test:
                prod_callers = sum(
                    1 for pred_id in nx_graph.predecessors(node.id)
                    if "test" not in (nx_graph.nodes.get(pred_id, {}).get("file_path") or "").lower()
                )
                actions.append({
                    "action_type": "add_tests",
                    "target_node_id": node.id,
                    "target_name": node.name,
                    "file": node.file_path,
                    "reason": f"No test coverage ({prod_callers} production callers)",
                    "priority": "HIGH" if prod_callers > 5 else "MEDIUM" if prod_callers > 1 else "LOW",
                    "expected_score_impact": pts_per_test,
                    "hint": f"lens_generate_test_skeleton('{node.id}')",
                })

    # --- 4. Dead code: truly unused functions ---
    if not focus or focus == "dead_code":
        from lenspr.tools.analysis import handle_dead_code as _hdc  # noqa: PLC0415
        dead_resp = _hdc({}, ctx)
        if dead_resp.success and dead_resp.data:
            dead_ids = [
                d for d in dead_resp.data.get("dead_code", [])
                if not d.startswith("eval.") and "test" not in d.lower()
            ]
            for dead_id in dead_ids:
                node_data = nx_graph.nodes.get(dead_id, {})
                actions.append({
                    "action_type": "delete_dead_code",
                    "target_node_id": dead_id,
                    "target_name": node_data.get("name", dead_id),
                    "file": node_data.get("file_path", ""),
                    "reason": "No callers found in call graph",
                    "priority": "LOW",
                    "expected_score_impact": pts_per_dead,
                    "hint": f"Verify first: lens_find_usages('{dead_id}'). May be called via dynamic dispatch.",
                })

    # --- 5. Missing docstrings (LOW effort, small but real impact) ---
    if not focus or focus == "docs":
        for node in func_nodes:
            if not node.docstring and not node.summary:
                actions.append({
                    "action_type": "add_docstring",
                    "target_node_id": node.id,
                    "target_name": node.name,
                    "file": node.file_path,
                    "reason": "Function has no docstring",
                    "priority": "LOW",
                    "expected_score_impact": pts_per_doc,
                    "hint": "Add a single-line docstring describing what the function does",
                })

    # Sort: CRITICAL > HIGH > MEDIUM > LOW, then by score impact descending
    _order = {"CRITICAL": 4, "HIGH": 3, "MEDIUM": 2, "LOW": 1}
    actions.sort(
        key=lambda a: (_order.get(a["priority"], 0), a["expected_score_impact"]),
        reverse=True,
    )
    # Calculate total impact across ALL actions before slicing (not just the top N shown)
    total_impact = round(sum(a["expected_score_impact"] for a in actions), 1)
    total_actions = len(actions)
    actions = actions[:max_items]

    # Current grade context
    _grade_targets = {"A": 90, "B": 75, "C": 60, "D": 45}
    target_pts = _grade_targets.get(target_grade.upper(), 75)

    hidden = total_actions - len(actions)
    scope_note = f" (showing {len(actions)} of {total_actions})" if hidden > 0 else ""

    return ToolResponse(
        success=True,
        data={
            "actions": actions,
            "count": len(actions),
            "total_actions": total_actions,
            "target_grade": target_grade.upper(),
            "estimated_score_gain": total_impact,
            "summary": (
                f"{total_actions} actions identified{scope_note}. "
                f"Estimated +{total_impact} pts toward grade {target_grade.upper()} (needs {target_pts}/100)."
            ),
            "protocol": (
                "1. Work through actions in priority order (CRITICAL first). "
                "2. For add_tests: call lens_generate_test_skeleton(node_id) first. "
                "3. Apply changes via lens_update_node or lens_patch_node. "
                "4. Run lens_run_tests() to verify. "
                "5. After each batch of 5-10 fixes, call lens_vibecheck() to track progress."
            ),
        },
    )

def handle_generate_test_skeleton(params: dict, ctx: LensContext) -> ToolResponse:
    """Generate a structured test specification for a function using graph context.

    Uses callers (real usage examples), callees (what to mock), and the
    function's signature to build a test spec. Does NOT write test code —
    returns an intelligence spec the AI agent uses to write targeted tests.

    The spec includes: scenarios, setup_hints, example_callers, mocks_needed.
    """
    node_id = params.get("node_id")
    if not node_id:
        return ToolResponse(success=False, error="node_id is required")

    ctx.ensure_synced()
    node = database.get_node(node_id, ctx.graph_db)
    if node is None:
        return ToolResponse(success=False, error=f"Node not found: {node_id}")

    nx_graph = ctx.get_graph()
    source = node.source_code or ""
    name = node.name
    sig = node.signature or name
    docstring = node.docstring or ""

    # --- Callers: real usage examples ---
    caller_ids = list(nx_graph.predecessors(node_id))
    example_callers: list[dict] = []
    for cid in caller_ids[:5]:  # cap at 5
        caller_node = database.get_node(cid, ctx.graph_db)
        if caller_node and caller_node.source_code:
            # Extract lines that contain the call
            call_lines = [
                line.strip()
                for line in caller_node.source_code.splitlines()
                if name in line and not line.strip().startswith("#")
            ][:3]
            if call_lines:
                example_callers.append({
                    "caller_id": cid,
                    "caller_name": caller_node.name,
                    "call_examples": call_lines,
                })

    # --- Callees: what the function calls (mock candidates) ---
    callee_ids = list(nx_graph.successors(node_id))
    mocks_needed: list[dict] = []
    for cid in callee_ids[:10]:
        callee_node = database.get_node(cid, ctx.graph_db)
        if callee_node:
            file_path = callee_node.file_path or ""
            # External / IO / DB callees are mock candidates
            is_io = any(m in (callee_node.source_code or "") for m in _IO_MARKERS)
            is_external = not file_path.startswith(str(ctx.project_root))
            if is_io or is_external or "open(" in source or callee_node.name in ("open", "connect"):
                mocks_needed.append({
                    "callee_id": cid,
                    "callee_name": callee_node.name,
                    "reason": "IO/network/DB" if is_io else "external dependency",
                })

    # --- Infer test scenarios from source code ---
    scenarios: list[dict] = []

    # Happy path — always needed
    scenarios.append({
        "name": f"test_{name}_happy_path",
        "description": f"Verify {name} returns the expected result for valid input",
        "kind": "happy_path",
    })

    # Detect conditional branches — extract actual condition text via AST
    import ast as _ast
    _if_conditions: list[str] = []
    try:
        for _node in _ast.walk(_ast.parse(source)):
            if isinstance(_node, _ast.If):
                try:
                    _if_conditions.append(_ast.unparse(_node.test))
                except Exception:
                    pass
    except SyntaxError:
        pass
    for _cond in _if_conditions[:3]:
        _slug = re.sub(r"[^a-z0-9]+", "_", _cond.lower())[:40].strip("_")
        scenarios.append({
            "name": f"test_{name}_when_{_slug}",
            "description": f"When `{_cond}` is true — verify the corresponding branch behaviour",
            "kind": "branch",
            "condition": _cond,
        })

    # Detect error paths
    if "raise " in source or "except " in source:
        scenarios.append({
            "name": f"test_{name}_raises_on_invalid_input",
            "description": f"Verify {name} raises the appropriate exception for bad input",
            "kind": "error_path",
        })

    # Detect loops / empty input
    if "for " in source or "while " in source:
        scenarios.append({
            "name": f"test_{name}_empty_input",
            "description": f"Verify {name} handles empty collections gracefully",
            "kind": "edge_case",
        })

    # Detect None / optional handling
    if "is None" in source or "if not " in source:
        scenarios.append({
            "name": f"test_{name}_none_input",
            "description": f"Verify {name} handles None or falsy input correctly",
            "kind": "edge_case",
        })

    # --- Setup hints ---
    setup_hints: list[str] = []
    if mocks_needed:
        callee_names = ", ".join(m["callee_name"] for m in mocks_needed)
        setup_hints.append(f"Mock external dependencies: {callee_names}")
    if "database" in source.lower() or "db" in source.lower():
        setup_hints.append("Use an in-memory or temporary database fixture")
    if "ctx" in source or "LensContext" in source:
        setup_hints.append("Provide a LensContext fixture (see tests/conftest.py patterns)")
    if not setup_hints:
        setup_hints.append("No external dependencies detected — straightforward unit test")

    # --- Module to place the test in ---
    file_path = node.file_path or ""
    suggested_test_file = "tests/test_" + file_path.replace("lenspr/", "", 1).replace("/", "_")
    if not suggested_test_file.endswith(".py"):
        suggested_test_file += ".py"

    return ToolResponse(
        success=True,
        data={
            "node_id": node_id,
            "function_name": name,
            "signature": sig,
            "docstring": docstring,
            "suggested_test_file": suggested_test_file,
            "scenarios": scenarios,
            "setup_hints": setup_hints,
            "example_callers": example_callers,
            "mocks_needed": mocks_needed,
            "hint": (
                f"Write {len(scenarios)} test functions in {suggested_test_file}. "
                "Use example_callers for realistic input values. "
                "Mock everything in mocks_needed."
            ),
        },
    )


