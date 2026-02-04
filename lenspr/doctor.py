"""Project health diagnostics for LensPR."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class CheckResult:
    """Result of a single health check."""

    name: str
    status: str  # "ok", "warning", "error"
    message: str
    details: str | None = None
    recommendation: str | None = None


@dataclass
class DoctorReport:
    """Complete health report for a project."""

    project_root: Path
    checks: list[CheckResult] = field(default_factory=list)
    recommendations: list[str] = field(default_factory=list)

    @property
    def has_errors(self) -> bool:
        return any(c.status == "error" for c in self.checks)

    @property
    def has_warnings(self) -> bool:
        return any(c.status == "warning" for c in self.checks)


def run_doctor(project_root: Path) -> DoctorReport:
    """Run all health checks on a project."""
    report = DoctorReport(project_root=project_root)

    # Environment checks
    _check_python_version(report)
    _check_node_version(report)
    _check_tree_sitter(report)
    _check_mcp_installed(report)

    # Project configuration checks
    _check_tsconfig(report)
    _check_jsconfig(report)
    _check_node_modules(report)
    _check_path_aliases(report)

    # Graph checks
    _check_graph_exists(report)
    _check_graph_freshness(report)
    _check_resolution_quality(report)

    # Collect recommendations
    for check in report.checks:
        if check.recommendation:
            report.recommendations.append(check.recommendation)

    return report


def _check_python_version(report: DoctorReport) -> None:
    """Check Python version is 3.11+."""
    version = sys.version_info
    if version >= (3, 11):
        report.checks.append(
            CheckResult(
                name="Python version",
                status="ok",
                message=f"Python {version.major}.{version.minor}.{version.micro}",
            )
        )
    else:
        report.checks.append(
            CheckResult(
                name="Python version",
                status="error",
                message=f"Python {version.major}.{version.minor} (3.11+ required)",
                recommendation="Upgrade to Python 3.11 or later",
            )
        )


def _check_node_version(report: DoctorReport) -> None:
    """Check Node.js version is 18+."""
    node_path = shutil.which("node")
    if not node_path:
        report.checks.append(
            CheckResult(
                name="Node.js",
                status="warning",
                message="Not installed",
                details="TypeScript resolution will be degraded",
                recommendation="Install Node.js 18+ for full TypeScript support",
            )
        )
        return

    try:
        result = subprocess.run(
            ["node", "--version"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        version_str = result.stdout.strip()
        # Parse version like v20.10.0
        if version_str.startswith("v"):
            parts = version_str[1:].split(".")
            major = int(parts[0])
            if major >= 18:
                report.checks.append(
                    CheckResult(
                        name="Node.js",
                        status="ok",
                        message=version_str,
                    )
                )
            else:
                report.checks.append(
                    CheckResult(
                        name="Node.js",
                        status="warning",
                        message=f"{version_str} (18+ recommended)",
                        recommendation="Upgrade Node.js to version 18+",
                    )
                )
    except Exception:
        report.checks.append(
            CheckResult(
                name="Node.js",
                status="warning",
                message="Could not determine version",
            )
        )


def _check_tree_sitter(report: DoctorReport) -> None:
    """Check if tree-sitter is installed."""
    try:
        import tree_sitter  # noqa: F401
        from lenspr.parsers import TYPESCRIPT_AVAILABLE

        if TYPESCRIPT_AVAILABLE:
            report.checks.append(
                CheckResult(
                    name="TypeScript parser",
                    status="ok",
                    message="tree-sitter available",
                )
            )
        else:
            report.checks.append(
                CheckResult(
                    name="TypeScript parser",
                    status="warning",
                    message="tree-sitter installed but parser unavailable",
                )
            )
    except ImportError:
        report.checks.append(
            CheckResult(
                name="TypeScript parser",
                status="warning",
                message="Not installed",
                details="TypeScript/JavaScript files won't be parsed",
                recommendation="Install: pip install 'lenspr[typescript]'",
            )
        )


def _check_mcp_installed(report: DoctorReport) -> None:
    """Check if MCP server dependencies are installed."""
    try:
        import mcp  # noqa: F401

        report.checks.append(
            CheckResult(
                name="MCP server",
                status="ok",
                message="Available",
            )
        )
    except ImportError:
        report.checks.append(
            CheckResult(
                name="MCP server",
                status="warning",
                message="Not installed",
                details="Claude Code integration unavailable",
                recommendation="Install: pip install 'lenspr[mcp]'",
            )
        )


def _check_tsconfig(report: DoctorReport) -> None:
    """Check for tsconfig.json."""
    tsconfig = report.project_root / "tsconfig.json"
    if tsconfig.exists():
        try:
            config = json.loads(tsconfig.read_text())
            paths = config.get("compilerOptions", {}).get("paths", {})
            path_count = len(paths)
            if path_count > 0:
                report.checks.append(
                    CheckResult(
                        name="tsconfig.json",
                        status="ok",
                        message=f"Found ({path_count} path aliases)",
                    )
                )
            else:
                report.checks.append(
                    CheckResult(
                        name="tsconfig.json",
                        status="ok",
                        message="Found (no path aliases)",
                    )
                )
        except json.JSONDecodeError:
            report.checks.append(
                CheckResult(
                    name="tsconfig.json",
                    status="warning",
                    message="Invalid JSON",
                    recommendation="Fix JSON syntax in tsconfig.json",
                )
            )
    else:
        # Check if there are any TS files
        ts_files = list(report.project_root.glob("**/*.ts"))
        tsx_files = list(report.project_root.glob("**/*.tsx"))
        if ts_files or tsx_files:
            report.checks.append(
                CheckResult(
                    name="tsconfig.json",
                    status="warning",
                    message="Not found",
                    details=f"Found {len(ts_files) + len(tsx_files)} TypeScript files",
                    recommendation="Create tsconfig.json for better resolution",
                )
            )


def _check_jsconfig(report: DoctorReport) -> None:
    """Check for jsconfig.json."""
    jsconfig = report.project_root / "jsconfig.json"
    tsconfig = report.project_root / "tsconfig.json"

    if jsconfig.exists():
        report.checks.append(
            CheckResult(
                name="jsconfig.json",
                status="ok",
                message="Found",
            )
        )
    elif not tsconfig.exists():
        # Check if there are any JS files
        js_files = list(report.project_root.glob("**/*.js"))
        jsx_files = list(report.project_root.glob("**/*.jsx"))
        if js_files or jsx_files:
            # This is informational, not a warning
            pass  # JS files work without jsconfig


def _check_node_modules(report: DoctorReport) -> None:
    """Check if node_modules exists."""
    node_modules = report.project_root / "node_modules"
    package_json = report.project_root / "package.json"

    if package_json.exists():
        if node_modules.exists():
            # Count installed packages
            try:
                packages = [
                    d for d in node_modules.iterdir()
                    if d.is_dir() and not d.name.startswith(".")
                ]
                report.checks.append(
                    CheckResult(
                        name="node_modules",
                        status="ok",
                        message=f"Installed ({len(packages)} packages)",
                    )
                )
            except Exception:
                report.checks.append(
                    CheckResult(
                        name="node_modules",
                        status="ok",
                        message="Installed",
                    )
                )
        else:
            report.checks.append(
                CheckResult(
                    name="node_modules",
                    status="warning",
                    message="Not installed",
                    details="Type resolution will be degraded",
                    recommendation="Run: npm install",
                )
            )


def _check_path_aliases(report: DoctorReport) -> None:
    """Check path aliases configuration."""
    tsconfig = report.project_root / "tsconfig.json"
    if not tsconfig.exists():
        return

    try:
        config = json.loads(tsconfig.read_text())
        paths = config.get("compilerOptions", {}).get("paths", {})
        base_url = config.get("compilerOptions", {}).get("baseUrl", ".")

        if paths:
            # Verify paths resolve correctly
            missing = []
            for alias, targets in paths.items():
                for target in targets:
                    # Remove glob suffix
                    clean_target = target.rstrip("*")
                    target_path = report.project_root / base_url / clean_target
                    if not target_path.exists():
                        missing.append(f"{alias} -> {target}")

            if missing:
                report.checks.append(
                    CheckResult(
                        name="Path aliases",
                        status="warning",
                        message=f"{len(missing)} unresolved",
                        details=", ".join(missing[:3]),
                        recommendation="Check paths in tsconfig.json",
                    )
                )
    except Exception:
        pass


def _check_graph_exists(report: DoctorReport) -> None:
    """Check if the graph database exists."""
    lens_dir = report.project_root / ".lens"
    graph_db = lens_dir / "graph.db"

    if graph_db.exists():
        size_kb = graph_db.stat().st_size / 1024
        if size_kb > 1024:
            size_str = f"{size_kb / 1024:.1f} MB"
        else:
            size_str = f"{size_kb:.0f} KB"

        report.checks.append(
            CheckResult(
                name="Graph database",
                status="ok",
                message=f"Exists ({size_str})",
            )
        )
    else:
        report.checks.append(
            CheckResult(
                name="Graph database",
                status="error",
                message="Not found",
                recommendation="Run: lenspr init .",
            )
        )


def _check_graph_freshness(report: DoctorReport) -> None:
    """Check if graph is up to date."""
    lens_dir = report.project_root / ".lens"
    config_path = lens_dir / "config.json"

    if not config_path.exists():
        return

    try:
        from datetime import UTC, datetime, timedelta

        config = json.loads(config_path.read_text())
        last_sync_str = config.get("last_sync")
        if last_sync_str:
            last_sync = datetime.fromisoformat(last_sync_str.replace("Z", "+00:00"))
            now = datetime.now(UTC)
            age = now - last_sync

            if age < timedelta(minutes=5):
                report.checks.append(
                    CheckResult(
                        name="Graph freshness",
                        status="ok",
                        message="Up to date",
                    )
                )
            elif age < timedelta(hours=1):
                minutes = int(age.total_seconds() / 60)
                report.checks.append(
                    CheckResult(
                        name="Graph freshness",
                        status="ok",
                        message=f"Synced {minutes} minutes ago",
                    )
                )
            else:
                hours = int(age.total_seconds() / 3600)
                report.checks.append(
                    CheckResult(
                        name="Graph freshness",
                        status="warning",
                        message=f"Last sync: {hours} hours ago",
                        recommendation="Run: lenspr sync .",
                    )
                )
    except Exception:
        pass


def _check_resolution_quality(report: DoctorReport) -> None:
    """Check edge resolution quality."""
    lens_dir = report.project_root / ".lens"
    graph_db = lens_dir / "graph.db"

    if not graph_db.exists():
        return

    try:
        from lenspr import database

        _, edges = database.load_graph(graph_db)
        if not edges:
            return

        # Count by confidence
        resolved = sum(1 for e in edges if e.confidence.value == "resolved")
        external = sum(1 for e in edges if e.confidence.value == "external")
        total = len(edges)

        if total > 0:
            pct = (resolved + external) / total * 100
            if pct >= 90:
                report.checks.append(
                    CheckResult(
                        name="Resolution quality",
                        status="ok",
                        message=f"{pct:.0f}% ({resolved + external}/{total} edges)",
                    )
                )
            elif pct >= 70:
                report.checks.append(
                    CheckResult(
                        name="Resolution quality",
                        status="warning",
                        message=f"{pct:.0f}% (target: 90%+)",
                        details=f"{total - resolved - external} unresolved edges",
                    )
                )
            else:
                report.checks.append(
                    CheckResult(
                        name="Resolution quality",
                        status="warning",
                        message=f"{pct:.0f}% (below target)",
                        recommendation="Check tsconfig.json and node_modules",
                    )
                )
    except Exception:
        pass


def format_doctor_report(report: DoctorReport) -> str:
    """Format doctor report as human-readable text."""
    lines = []

    lines.append("Project Health Check")
    lines.append("=" * 50)
    lines.append("")

    # Group checks by category
    env_checks = ["Python version", "Node.js", "TypeScript parser", "MCP server"]
    config_checks = ["tsconfig.json", "jsconfig.json", "node_modules", "Path aliases"]
    graph_checks = ["Graph database", "Graph freshness", "Resolution quality"]

    def format_section(title: str, check_names: list[str]) -> None:
        section_checks = [c for c in report.checks if c.name in check_names]
        if not section_checks:
            return

        lines.append(f"{title}:")
        for check in section_checks:
            if check.status == "ok":
                icon = "✓"
            elif check.status == "warning":
                icon = "⚠"
            else:
                icon = "✗"

            lines.append(f"  {icon} {check.name}: {check.message}")
            if check.details:
                lines.append(f"      {check.details}")

        lines.append("")

    format_section("Environment", env_checks)
    format_section("Project Configuration", config_checks)
    format_section("Graph Status", graph_checks)

    # Recommendations
    if report.recommendations:
        lines.append("Recommendations:")
        for i, rec in enumerate(report.recommendations, 1):
            lines.append(f"  {i}. {rec}")
        lines.append("")

    # Summary
    if report.has_errors:
        lines.append("Status: ISSUES FOUND - fix errors above")
    elif report.has_warnings:
        lines.append("Status: OK with warnings")
    else:
        lines.append("Status: All checks passed!")

    return "\n".join(lines)
