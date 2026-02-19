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

def handle_resume(params: dict, ctx) -> "ToolResponse":
    """Reconstruct previous session context from the auto-generated action log.

    Every successful lens_update_node / lens_patch_node / lens_add_node /
    lens_delete_node call writes a structured entry to the session log
    automatically.  Call this at the start of a new session to immediately
    understand what changed in the last session and why — no manual handoff
    needed.

    Returns:
        A formatted markdown summary of all logged actions, grouped by file,
        plus any user-written session notes.
    """
    import json

    from lenspr.models import ToolResponse

    notes = database.read_session_notes(ctx.session_db)

    # Split auto-log entries (key starts with '_log_') from user notes
    action_entries = []
    user_notes = []
    for note in notes:
        if note["key"].startswith("_log_"):
            try:
                action_entries.append(json.loads(note["value"]))
            except Exception:
                pass  # Skip malformed entries
        elif note["key"] not in ("handoff", "handoff_at"):
            user_notes.append(note)

    # Sort actions chronologically (keys are _log_<timestamp>_... so lexical sort works)
    action_entries.sort(key=lambda e: e.get("timestamp", ""))

    lines: list[str] = ["# Session Resume\n"]

    if action_entries:
        lines.append(f"## Action log — {len(action_entries)} change(s)\n")
        for i, entry in enumerate(action_entries, 1):
            ts = entry.get("timestamp", "")[:19].replace("T", " ")
            action = entry.get("action", "?")
            node_id = entry.get("node_id", "?")
            reasoning = entry.get("reasoning", "(no reasoning)")
            impact = entry.get("impact_summary", "")

            lines.append(f"**{i}. {action.upper()}** `{node_id}` — {ts}")
            lines.append(f"   - Why: {reasoning}")
            if impact and impact not in ("added", "deleted"):
                lines.append(f"   - Impact: {impact}")
            lines.append("")
    else:
        lines.append("_No auto-logged actions found._\n")
        lines.append(
            "Actions are logged automatically by lens_update_node, "
            "lens_patch_node, lens_add_node, and lens_delete_node.\n"
        )

    if user_notes:
        lines.append("## Session notes\n")
        for note in user_notes:
            ts = note.get("updated_at", "")[:19].replace("T", " ")
            lines.append(f"### {note['key']} _(updated {ts})_")
            lines.append(note["value"])
            lines.append("")

    summary = "\n".join(lines)

    hint = (
        "Use lens_session_write(key, value) to add notes, "
        "lens_session_read() to list all notes."
    )
    if not action_entries and not user_notes:
        hint = "Start working — actions will be logged automatically."

    return ToolResponse(
        success=True,
        data={
            "summary": summary,
            "actions_logged": len(action_entries),
            "user_notes": len(user_notes),
        },
        hint=hint,
    )

