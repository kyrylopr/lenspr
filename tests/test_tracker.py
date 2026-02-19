"""Tests for lenspr/tracker.py — record_change, get_history."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from lenspr.tracker import get_history, record_change


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def history_db(tmp_path: Path) -> Path:
    """An initialised history.db file."""
    db_path = tmp_path / "history.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """CREATE TABLE IF NOT EXISTS changes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            node_id TEXT NOT NULL,
            action TEXT NOT NULL,
            old_source TEXT,
            new_source TEXT,
            old_hash TEXT NOT NULL,
            new_hash TEXT NOT NULL,
            affected_nodes TEXT NOT NULL,
            description TEXT NOT NULL,
            reasoning TEXT NOT NULL DEFAULT ''
        )"""
    )
    conn.commit()
    conn.close()
    return db_path


# ---------------------------------------------------------------------------
# record_change
# ---------------------------------------------------------------------------


class TestRecordChange:
    def test_records_and_returns_id(self, history_db: Path) -> None:
        """Recording a change returns a positive change ID."""
        change_id = record_change(
            node_id="app.main",
            action="update",
            old_source="def main(): pass",
            new_source="def main(): return 1",
            old_hash="abc",
            new_hash="def",
            affected_nodes=["app.run"],
            description="Changed return value",
            db_path=history_db,
        )

        assert change_id > 0

    def test_recorded_change_is_retrievable(self, history_db: Path) -> None:
        """Recorded change appears in get_history."""
        record_change(
            node_id="app.main",
            action="update",
            old_source="old",
            new_source="new",
            old_hash="a",
            new_hash="b",
            affected_nodes=[],
            description="test change",
            db_path=history_db,
        )

        history = get_history(history_db)
        assert len(history) == 1
        assert history[0].node_id == "app.main"
        assert history[0].action == "update"
        assert history[0].description == "test change"

    def test_reasoning_is_stored(self, history_db: Path) -> None:
        """Reasoning field is persisted and retrievable."""
        record_change(
            node_id="app.main",
            action="update",
            old_source="old",
            new_source="new",
            old_hash="a",
            new_hash="b",
            affected_nodes=[],
            description="test",
            db_path=history_db,
            reasoning="Fixed off-by-one error",
        )

        history = get_history(history_db)
        assert history[0].reasoning == "Fixed off-by-one error"


# ---------------------------------------------------------------------------
# get_history
# ---------------------------------------------------------------------------


class TestGetHistory:
    def test_filters_by_node_id(self, history_db: Path) -> None:
        """node_id filter → only changes for that node."""
        record_change(
            node_id="app.main", action="update",
            old_source="", new_source="", old_hash="a", new_hash="b",
            affected_nodes=[], description="change 1",
            db_path=history_db,
        )
        record_change(
            node_id="app.helper", action="update",
            old_source="", new_source="", old_hash="c", new_hash="d",
            affected_nodes=[], description="change 2",
            db_path=history_db,
        )

        history = get_history(history_db, node_id="app.main")
        assert len(history) == 1
        assert history[0].node_id == "app.main"

    def test_limit_parameter(self, history_db: Path) -> None:
        """limit=2 → at most 2 records returned."""
        for i in range(5):
            record_change(
                node_id=f"app.func_{i}", action="update",
                old_source="", new_source="", old_hash="a", new_hash="b",
                affected_nodes=[], description=f"change {i}",
                db_path=history_db,
            )

        history = get_history(history_db, limit=2)
        assert len(history) == 2

    def test_returns_newest_first(self, history_db: Path) -> None:
        """History is ordered by ID descending (newest first)."""
        record_change(
            node_id="app.first", action="update",
            old_source="", new_source="", old_hash="a", new_hash="b",
            affected_nodes=[], description="first",
            db_path=history_db,
        )
        record_change(
            node_id="app.second", action="update",
            old_source="", new_source="", old_hash="c", new_hash="d",
            affected_nodes=[], description="second",
            db_path=history_db,
        )

        history = get_history(history_db)
        assert history[0].node_id == "app.second"
        assert history[1].node_id == "app.first"

    def test_empty_history(self, history_db: Path) -> None:
        """No changes recorded → empty list."""
        history = get_history(history_db)
        assert history == []

    def test_affected_nodes_deserialized(self, history_db: Path) -> None:
        """affected_nodes JSON is deserialized to a list."""
        record_change(
            node_id="app.main", action="update",
            old_source="", new_source="", old_hash="a", new_hash="b",
            affected_nodes=["app.helper", "app.run"],
            description="test",
            db_path=history_db,
        )

        history = get_history(history_db)
        assert history[0].affected_nodes == ["app.helper", "app.run"]
