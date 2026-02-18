"""Session memory tool handlers — persistent notes that survive context resets."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING

from lenspr import database
from lenspr.models import ToolResponse
from lenspr.tracker import get_history

if TYPE_CHECKING:
    from lenspr.context import LensContext

__all__ = [
    "handle_session_write",
    "handle_session_read",
    "handle_session_handoff",
]

# guard
if __name__ == "__main__":
    pass


def handle_session_write(params: dict, ctx: LensContext) -> ToolResponse:
    """Write or overwrite a persistent session note by key."""
    key = params.get("key", "").strip()
    value = params.get("value", "").strip()

    if not key:
        return ToolResponse(success=False, error="key is required.")
    if not value:
        return ToolResponse(success=False, error="value is required.")

    database.write_session_note(key, value, ctx.session_db)

    return ToolResponse(
        success=True,
        data={"key": key, "saved": True},
        hint="Read all notes with lens_session_read().",
    )


def handle_session_read(params: dict, ctx: LensContext) -> ToolResponse:
    """Read all persistent session notes."""
    notes = database.read_session_notes(ctx.session_db)

    if not notes:
        return ToolResponse(
            success=True,
            data={"notes": [], "count": 0},
            hint="No session notes yet. Use lens_session_write(key, value) to add notes.",
        )

    return ToolResponse(
        success=True,
        data={"notes": notes, "count": len(notes)},
    )


def handle_session_handoff(params: dict, ctx: LensContext) -> ToolResponse:
    """
    Generate a handoff document summarising recent work and current session notes.

    Combines:
    - Last N changes from history.db (what was changed and why)
    - All current session notes (task state, TODOs, decisions)

    Saves the result as the 'handoff' session note so next session reads it automatically.
    """
    limit = int(params.get("limit", 10))

    # 1. Recent changes
    changes = []
    if ctx.history_db.exists():
        raw = get_history(ctx.history_db, limit=limit)
        for ch in raw:
            entry: dict = {
                "node": ch.node_id,
                "action": ch.action,
                "when": ch.timestamp[:19].replace("T", " "),
                "description": ch.description,
            }
            if ch.reasoning:
                entry["reasoning"] = ch.reasoning
            changes.append(entry)

    # 2. Current notes
    notes = database.read_session_notes(ctx.session_db)

    # 3. Build handoff text
    lines = ["# Session Handoff", ""]

    if changes:
        lines.append(f"## Recent changes (last {len(changes)})")
        for ch in changes:
            lines.append(f"- **{ch['action']}** `{ch['node']}` at {ch['when']}")
            if ch.get("reasoning"):
                lines.append(f"  - Why: {ch['reasoning']}")
        lines.append("")

    if notes:
        lines.append("## Session notes")
        for note in notes:
            lines.append(f"### {note['key']}")
            lines.append(note["value"])
            lines.append(f"_(updated {note['updated_at'][:19].replace('T', ' ')})_")
            lines.append("")
    else:
        lines.append("## Session notes")
        lines.append("_(none)_")
        lines.append("")

    lines.append("## Start next session with")
    lines.append("```")
    lines.append("lens_session_read()   # restore this context")
    lines.append("```")

    handoff_text = "\n".join(lines)

    # Save as a session note so next session picks it up automatically
    now = datetime.now(timezone.utc).isoformat()
    database.write_session_note("handoff", handoff_text, ctx.session_db)
    database.write_session_note("handoff_at", now[:19].replace("T", " "), ctx.session_db)

    return ToolResponse(
        success=True,
        data={
            "handoff": handoff_text,
            "changes_included": len(changes),
            "notes_included": len(notes),
        },
        hint="Handoff saved as session note 'handoff'. Next session: lens_session_read().",
    )
