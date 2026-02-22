"""Test runner tool handler."""

from __future__ import annotations

import json
import re
import subprocess
from typing import TYPE_CHECKING

from lenspr import database
from lenspr.models import ToolResponse

if TYPE_CHECKING:
    from lenspr.context import LensContext

guard = __name__ == "__main__"


def handle_run_tests(params: dict, ctx: LensContext) -> ToolResponse:
    """Run pytest in the project root and return structured results."""
    path: str = params.get("path", "")
    filter_k: str = params.get("filter_k", "")
    timeout: int = int(params.get("timeout", 120))
    max_output_lines: int = int(params.get("max_output_lines", 150))

    project_root = str(ctx.project_root)

    cmd = ["python", "-m", "pytest", "--tb=short", "-q", "--no-header"]

    # Auto-enable coverage when pytest-cov is available
    cov_json = ctx.project_root / ".lens" / "coverage.json"
    try:
        import pytest_cov  # noqa: F401
        cmd.extend(["--cov", "--cov-report", f"json:{cov_json}"])
    except ImportError:
        pass

    # Auto-enable runtime tracing when Python 3.12+ is available
    import sys as _sys
    if _sys.version_info >= (3, 12):
        cmd.extend(["-p", "lenspr.pytest_tracer"])

    if path:
        cmd.append(path)
    if filter_k:
        cmd.extend(["-k", filter_k])

    try:
        proc = subprocess.run(
            cmd,
            cwd=project_root,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return ToolResponse(
            success=False,
            error=f"Tests timed out after {timeout}s. Use 'timeout' param to increase.",
        )
    except FileNotFoundError:
        return ToolResponse(
            success=False,
            error="pytest not found. Run: pip install pytest",
        )

    stdout = proc.stdout or ""
    stderr = proc.stderr or ""
    combined = stdout + ("\n" + stderr if stderr.strip() else "")
    lines = combined.splitlines()

    # --- Parse summary line ---
    # "5 passed, 2 failed, 1 error in 0.23s"
    # "237 passed, 5 skipped, 1 warning in 16.87s"
    summary_re = re.compile(
        r"(\d+) passed"
        r"(?:,\s+(\d+) failed)?"
        r"(?:,\s+(\d+) errors?)?"
        r"(?:,\s+(\d+) skipped)?"
        r"(?:,\s+\d+ warnings?)?"
        r"\s+in\s+([\d.]+)s"
    )
    passed = failed = errors = skipped = 0
    duration = 0.0

    for line in reversed(lines):
        m = summary_re.search(line)
        if m:
            passed = int(m.group(1) or 0)
            failed = int(m.group(2) or 0)
            errors = int(m.group(3) or 0)
            skipped = int(m.group(4) or 0)
            duration = float(m.group(5) or 0)
            break

    # --- Parse individual failures ---
    # Format with -q --tb=short: "FAILED tests/foo.py::test_bar - AssertionError: ..."
    failures: list[dict] = []
    failed_line_re = re.compile(r"^FAILED\s+(.+?)\s+-\s+(.+)$")
    failed_bare_re = re.compile(r"^FAILED\s+(.+)$")

    for line in lines:
        m = failed_line_re.match(line)
        if m:
            failures.append({
                "test": m.group(1).strip(),
                "reason": m.group(2).strip(),
            })
            continue
        m = failed_bare_re.match(line)
        if m and " - " not in m.group(1):
            failures.append({
                "test": m.group(1).strip(),
                "reason": "",
            })

    # Deduplicate (bare + full can both match the same test)
    seen: set[str] = set()
    unique_failures: list[dict] = []
    for f in failures:
        if f["test"] not in seen:
            seen.add(f["test"])
            unique_failures.append(f)
    failures = unique_failures

    # --- Trim output ---
    if len(lines) > max_output_lines:
        output = (
            f"[... {len(lines) - max_output_lines} lines omitted, "
            f"showing last {max_output_lines} ...]\n"
            + "\n".join(lines[-max_output_lines:])
        )
    else:
        output = "\n".join(lines)

    # Merge runtime trace edges if tracer produced output
    trace_output = ctx.project_root / ".lens" / "trace_edges.json"
    trace_merge: dict | None = None
    if trace_output.exists():
        try:
            trace_data = json.loads(trace_output.read_text(encoding="utf-8"))
            raw_edges = trace_data.get("edges", [])
            if raw_edges:
                edge_tuples = [
                    (e["from"], e["to"], e.get("count", 1)) for e in raw_edges
                ]
                trace_merge = database.save_runtime_edges(edge_tuples, ctx.graph_db)
                ctx._graph = None  # force graph reload
        except Exception:
            pass  # tracing is best-effort

    # return_code 5 = no tests collected (not an error)
    all_passed = failed == 0 and errors == 0 and proc.returncode in (0, 5)

    data: dict = {
        "passed": passed,
        "failed": failed,
        "errors": errors,
        "skipped": skipped,
        "duration_s": round(duration, 2),
        "all_passed": all_passed,
        "return_code": proc.returncode,
        "failures": failures,
        "output": output,
    }

    if trace_merge:
        data["trace_edges"] = trace_merge

    warnings: list[str] = []
    if not all_passed:
        total_bad = failed + errors
        warnings.append(
            f"\u26a0\ufe0f {total_bad} test(s) failing. "
            "Fix failures before making further changes."
        )
    if proc.returncode == 5:
        warnings.append("No tests collected. Check path/filter_k parameters.")

    return ToolResponse(success=True, data=data, warnings=warnings)
