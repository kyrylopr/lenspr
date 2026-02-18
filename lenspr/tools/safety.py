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
]

_SECRET_PATTERNS = [
    (r'(?i)(password|passwd|pwd)\s*=\s*["\'][^"\']{3,}["\']', "hardcoded password"),
    (r'(?i)(api_key|apikey|secret_key)\s*=\s*["\'][^"\']{6,}["\']', "hardcoded API key"),
    (r'(?i)(token)\s*=\s*["\'][^"\']{8,}["\']', "hardcoded token"),
    (r'(?i)(secret)\s*=\s*["\'][^"\']{6,}["\']', "hardcoded secret"),
]


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

    # 1. IO without error handling
    has_io = any(marker in src for marker in _IO_MARKERS)
    if has_io and "try:" not in src:
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
    """Report which functions/methods have test coverage in the graph.

    Uses the call graph: a function is 'covered' if at least one test function
    calls it directly.
    """
    file_path_filter = params.get("file_path")

    ctx.ensure_synced()
    nx_graph = ctx.get_graph()
    nodes = database.get_nodes(ctx.graph_db, file_filter=file_path_filter)

    covered: list[dict] = []
    uncovered: list[dict] = []

    for node in nodes:
        if node.type.value not in ("function", "method"):
            continue
        # Skip test functions themselves and eval/ test projects
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

    total = len(covered) + len(uncovered)
    pct = round(len(covered) / total * 100) if total else 100

    grade = "A" if pct >= 80 else "B" if pct >= 60 else "C" if pct >= 40 else "D" if pct >= 20 else "F"

    return ToolResponse(
        success=True,
        data={
            "coverage_pct": pct,
            "grade": grade,
            "covered_count": len(covered),
            "uncovered_count": len(uncovered),
            "uncovered": uncovered[:100],
            "covered": covered[:50],
            "filter": file_path_filter,
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
    # Filter to production code only — exclude eval/, test files, and dynamic-dispatch entry points
    _DYNAMIC_ENTRY_PATTERNS = ("run_server", "main", "cmd_", "_sync_loop", "_poll_loop")
    all_dead = graph_module.find_dead_code(nx_graph, entry_points=[])
    dead = [
        d for d in all_dead
        if not d.startswith("eval.")
        and "test" not in d.lower()
        and not any(pat in d for pat in _DYNAMIC_ENTRY_PATTERNS)
    ]
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
    total_edges = nx_graph.number_of_edges()
    if total_edges > 0:
        resolved = sum(
            1 for _, _, d in nx_graph.edges(data=True)
            if d.get("confidence") == "resolved"
        )
        conf_pct = round(resolved / total_edges * 100)
    else:
        conf_pct = 100
    conf_score = round(conf_pct / 100 * 15)
    breakdown["graph_confidence"] = {
        "score": conf_score,
        "max": 15,
        "detail": f"{conf_pct}% of call/import edges are resolved (higher = more accurate analysis)",
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
