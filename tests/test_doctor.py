"""Tests for lenspr/doctor.py — run_doctor, format_doctor_report."""

from __future__ import annotations

from pathlib import Path

import pytest

from lenspr.doctor import DoctorReport, format_doctor_report, run_doctor


# ---------------------------------------------------------------------------
# run_doctor
# ---------------------------------------------------------------------------


class TestRunDoctor:
    def test_returns_report_object(self, tmp_path: Path) -> None:
        """run_doctor returns a DoctorReport with checks."""
        report = run_doctor(tmp_path)

        assert isinstance(report, DoctorReport)
        assert len(report.checks) > 0

    def test_python_version_passes(self, tmp_path: Path) -> None:
        """On the current system, Python version check should pass."""
        report = run_doctor(tmp_path)

        python_check = next(
            (c for c in report.checks if c.name == "Python version"), None
        )
        assert python_check is not None
        assert python_check.status == "ok"

    def test_tree_sitter_check_present(self, tmp_path: Path) -> None:
        """Tree-sitter check is included in the report."""
        report = run_doctor(tmp_path)

        ts_check = next(
            (c for c in report.checks if c.name == "TypeScript parser"), None
        )
        assert ts_check is not None
        # Status may be ok or warning depending on installation
        assert ts_check.status in ("ok", "warning", "error")

    def test_graph_exists_check_on_fresh_dir(self, tmp_path: Path) -> None:
        """Fresh directory with no .lens → graph check indicates no graph."""
        report = run_doctor(tmp_path)

        graph_check = next(
            (c for c in report.checks if c.name == "Graph database"), None
        )
        assert graph_check is not None
        # No .lens directory → should be warning or error
        assert graph_check.status in ("warning", "error")

    def test_has_errors_property(self, tmp_path: Path) -> None:
        """has_errors is a boolean property."""
        report = run_doctor(tmp_path)

        assert isinstance(report.has_errors, bool)

    def test_has_warnings_property(self, tmp_path: Path) -> None:
        """has_warnings is a boolean property."""
        report = run_doctor(tmp_path)

        assert isinstance(report.has_warnings, bool)


# ---------------------------------------------------------------------------
# format_doctor_report
# ---------------------------------------------------------------------------


class TestFormatDoctorReport:
    def test_produces_readable_output(self, tmp_path: Path) -> None:
        """Formatted report is a non-empty string with sections."""
        report = run_doctor(tmp_path)
        output = format_doctor_report(report)

        assert isinstance(output, str)
        assert len(output) > 0
        assert "Project Health Check" in output

    def test_includes_environment_section(self, tmp_path: Path) -> None:
        """Formatted report includes Environment section."""
        report = run_doctor(tmp_path)
        output = format_doctor_report(report)

        assert "Environment" in output

    def test_includes_status_line(self, tmp_path: Path) -> None:
        """Formatted report ends with a status summary."""
        report = run_doctor(tmp_path)
        output = format_doctor_report(report)

        assert "Status:" in output

    def test_includes_check_icons(self, tmp_path: Path) -> None:
        """Formatted report uses check/warning/error icons."""
        report = run_doctor(tmp_path)
        output = format_doctor_report(report)

        # At least one icon should be present
        assert any(icon in output for icon in ("✓", "⚠", "✗"))
