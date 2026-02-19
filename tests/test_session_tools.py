"""Tests for lenspr/tools/session.py — write, read, handoff, resume."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lenspr import database
from lenspr.context import LensContext
from lenspr.tools.session import (
    handle_resume,
    handle_session_handoff,
    handle_session_read,
    handle_session_write,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def project(tmp_path: Path) -> LensContext:
    """Minimal project with session DB initialized."""
    (tmp_path / "app.py").write_text("def main():\n    pass\n")

    lens_dir = tmp_path / ".lens"
    lens_dir.mkdir()
    database.init_database(lens_dir)

    ctx = LensContext(project_root=tmp_path, lens_dir=lens_dir)
    ctx.full_sync()
    return ctx


# ---------------------------------------------------------------------------
# handle_session_write + handle_session_read
# ---------------------------------------------------------------------------


class TestSessionWriteRead:
    def test_write_and_read_roundtrip(self, project: LensContext) -> None:
        """Write a note → read it back → value matches."""
        handle_session_write(
            {"key": "current_task", "value": "Implement login"},
            project,
        )

        result = handle_session_read({}, project)

        assert result.success
        notes = result.data["notes"]
        assert any(
            n["key"] == "current_task" and n["value"] == "Implement login"
            for n in notes
        )

    def test_overwrite_existing_key(self, project: LensContext) -> None:
        """Writing to the same key → overwrites previous value."""
        handle_session_write(
            {"key": "status", "value": "in progress"}, project
        )
        handle_session_write(
            {"key": "status", "value": "completed"}, project
        )

        result = handle_session_read({}, project)

        assert result.success
        status_notes = [n for n in result.data["notes"] if n["key"] == "status"]
        assert len(status_notes) == 1
        assert status_notes[0]["value"] == "completed"

    def test_empty_key_returns_error(self, project: LensContext) -> None:
        """Empty key → error response."""
        result = handle_session_write({"key": "", "value": "val"}, project)

        assert not result.success
        assert "key" in result.error.lower()

    def test_empty_value_returns_error(self, project: LensContext) -> None:
        """Empty value → error response."""
        result = handle_session_write(
            {"key": "task", "value": ""}, project
        )

        assert not result.success
        assert "value" in result.error.lower()

    def test_read_empty_session(self, project: LensContext) -> None:
        """No notes written → read returns empty list with hint."""
        result = handle_session_read({}, project)

        assert result.success
        assert result.data["count"] == 0
        assert result.hint is not None

    def test_multiple_notes(self, project: LensContext) -> None:
        """Multiple different keys → all present in read."""
        handle_session_write({"key": "task1", "value": "Do A"}, project)
        handle_session_write({"key": "task2", "value": "Do B"}, project)
        handle_session_write({"key": "task3", "value": "Do C"}, project)

        result = handle_session_read({}, project)

        assert result.success
        assert result.data["count"] == 3
        keys = {n["key"] for n in result.data["notes"]}
        assert keys == {"task1", "task2", "task3"}


# ---------------------------------------------------------------------------
# handle_session_handoff
# ---------------------------------------------------------------------------


class TestSessionHandoff:
    def test_handoff_produces_markdown(self, project: LensContext) -> None:
        """Handoff generates a markdown document."""
        handle_session_write(
            {"key": "progress", "value": "Fixed bug #123"}, project
        )

        result = handle_session_handoff({}, project)

        assert result.success
        assert "# Session Handoff" in result.data["handoff"]

    def test_handoff_includes_notes(self, project: LensContext) -> None:
        """Handoff includes session notes in the output."""
        handle_session_write(
            {"key": "todo", "value": "Write more tests"}, project
        )

        result = handle_session_handoff({}, project)

        assert result.success
        assert result.data["notes_included"] >= 1
        assert "Write more tests" in result.data["handoff"]

    def test_handoff_saves_as_note(self, project: LensContext) -> None:
        """Handoff saves itself as a session note for next session."""
        handle_session_handoff({}, project)

        notes = handle_session_read({}, project)
        keys = [n["key"] for n in notes.data["notes"]]
        assert "handoff" in keys
        assert "handoff_at" in keys

    def test_empty_handoff(self, project: LensContext) -> None:
        """Handoff with no notes or changes → still succeeds."""
        result = handle_session_handoff({}, project)

        assert result.success
        assert "# Session Handoff" in result.data["handoff"]


# ---------------------------------------------------------------------------
# handle_resume
# ---------------------------------------------------------------------------


class TestResume:
    def test_resume_with_no_history(self, project: LensContext) -> None:
        """Fresh project → resume returns empty summary."""
        result = handle_resume({}, project)

        assert result.success
        assert result.data["actions_logged"] == 0
        assert "No auto-logged actions" in result.data["summary"]

    def test_resume_shows_user_notes(self, project: LensContext) -> None:
        """User-written notes appear in resume output."""
        handle_session_write(
            {"key": "current_task", "value": "Implement auth"}, project
        )

        result = handle_resume({}, project)

        assert result.success
        assert result.data["user_notes"] >= 1
        assert "Implement auth" in result.data["summary"]

    def test_resume_shows_action_log(self, project: LensContext) -> None:
        """Auto-logged actions appear in resume summary."""
        # Simulate an action log entry
        log_entry = json.dumps({
            "timestamp": "2026-02-19T10:00:00",
            "action": "update",
            "node_id": "app.main",
            "file_path": "app.py",
            "reasoning": "Added error handling",
            "impact_summary": "1 caller affected",
        })
        database.write_session_note(
            "_log_20260219_100000_app_main", log_entry, project.session_db
        )

        result = handle_resume({}, project)

        assert result.success
        assert result.data["actions_logged"] == 1
        assert "app.main" in result.data["summary"]
        assert "Added error handling" in result.data["summary"]

    def test_resume_excludes_handoff_from_user_notes(
        self, project: LensContext
    ) -> None:
        """handoff/handoff_at keys are not shown as user notes."""
        handle_session_write(
            {"key": "real_note", "value": "Important"}, project
        )
        database.write_session_note(
            "handoff", "old handoff text", project.session_db
        )
        database.write_session_note(
            "handoff_at", "2026-02-19 00:00:00", project.session_db
        )

        result = handle_resume({}, project)

        assert result.success
        assert result.data["user_notes"] == 1  # Only real_note
        assert "Important" in result.data["summary"]
        assert "old handoff text" not in result.data["summary"]
