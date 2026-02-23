# Plan: LensPR Tool Output Quality Overhaul

## Problem Statement

LensPR tools inject excessive noise into LLM context at multiple layers:
1. **System prompt**: 65KB of session notes (97 action logs + multi-KB handoffs) loaded on every MCP server start
2. **Every tool response**: null-field boilerplate + "ACTION REQUIRED" pending annotations
3. **Specific tools**: lens_explain dumps 3000+ lines, lens_find_usages is unbounded, tests in lens_context uncapped
4. **Broken tools**: security_scan/dep_audit always fail, still registered and visible to LLM

## Architecture Principle

Each fix follows the same pattern: **budget-based output, passive metadata, truncation with drill-down hints.** No data is lost — just moved from "always shown" to "available on request."

---

## Phase 1: System Prompt Session Notes Budget

**Files**: `lenspr/__init__.py` (get_system_prompt), `lenspr/database.py` (read_session_notes)

**Current behavior**: `get_system_prompt()` calls `read_session_notes()` which returns ALL 101 notes (65KB). All are appended verbatim to the system prompt.

**Root cause**: `_log_*` entries (97 of them, 35KB) are action logs for `lens_resume()`. They should never appear in the system prompt. Additionally, `self_evaluation` (16KB) and `handoff` (12KB) are archival documents not meant for every conversation.

**Changes**:

1. **`get_system_prompt()`** — filter and budget session notes:
   - Skip ALL `_log_*` entries (they're for `lens_resume()`, not for prompt)
   - Skip `handoff_at` (internal timestamp)
   - Skip `handoff` if it's just a copy of what's already in recent changes
   - For remaining user notes: apply a **total budget of 4KB**
   - If a single note exceeds 2KB, truncate it with `\n... (truncated — use lens_session_read() for full text)`
   - Priority order: `current_session` > other user notes (alphabetical)

2. **No changes to `read_session_notes()`** — it correctly returns everything, the filtering belongs in the consumer.

**Expected result**: System prompt drops from ~65KB to ~3-5KB of session context.

**Tests to verify**:
- Existing tests for `get_system_prompt` (if any) still pass
- New test: system prompt with 100 `_log_*` entries contains none of them
- New test: system prompt with a 10KB user note truncates to 2KB
- New test: `current_session` note always appears first

---

## Phase 2: Tool Response Envelope Cleanup

**Files**: `lenspr/__init__.py` (handle_tool), `lenspr/mcp_server.py` (_wrap_result_with_pending)

### 2a: Omit null/empty fields from ToolResponse

**Current behavior**: Every response includes 5 fields that are usually null/empty:
```json
{"success": true, "data": {...}, "error": null, "hint": null, "warnings": [], "affected_nodes": []}
```

**Change `handle_tool()`**: Only include fields that have values:
```python
result = {}
result["success"] = response.success
if response.data is not None:
    result["data"] = response.data
if response.error:
    result["error"] = response.error
if response.hint:
    result["hint"] = response.hint
if response.warnings:
    result["warnings"] = response.warnings
if response.affected_nodes:
    result["affected_nodes"] = response.affected_nodes
return result
```

**Savings**: ~30 tokens per successful tool call with no warnings.

### 2b: Replace "ACTION REQUIRED" with passive metadata

**Current behavior**: `_wrap_result_with_pending()` adds a 200+ character imperative hint that hijacks LLM attention away from the current task.

**Change**: Replace with minimal passive metadata:
```python
data["_meta"] = {"unannotated_nodes": len(pending)}
```

No hint text, no node list, no "ACTION REQUIRED." The LLM sees `"_meta": {"unannotated_nodes": 3}` and can choose to annotate or not. The full list of pending nodes is accessible via `lens_annotate_batch(unannotated_only=True)` when the LLM decides to act on it.

**Tests to update**: `test_wrap_result_with_pending` in test_annotations.py — update assertions to expect `_meta` instead of `_pending_annotations`.

---

## Phase 3: lens_explain Source Reduction

**Files**: `lenspr/tools/explain.py` (_get_callers_context, handle_explain)

**Current behavior**: `_get_callers_context()` returns full `source_code` for each of 5 callers. Combined with target source, this can be 3000+ lines.

**Changes**:

1. **`_get_callers_context()`**: Replace `source_code` with `source_preview` — first 3 lines of source (enough to see function signature + first statement):
   ```python
   source_lines = (pred_node.source_code or "").splitlines()
   callers.append({
       "id": pred_id,
       "name": pred_node.name,
       "type": pred_node.type.value,
       "file_path": pred_node.file_path,
       "signature": pred_node.signature,
       "source_preview": "\n".join(source_lines[:3]) + ("..." if len(source_lines) > 3 else ""),
   })
   ```

2. **`handle_explain()`**: Remove `llm_hint` field (patronizing, wastes tokens — LLM knows how to explain code).

**Why not remove target source_code**: The target's source IS the thing being explained. Without it, LLM has nothing to explain. Keep it.

**Expected savings**: From ~3000 lines to ~200 lines for a typical function with 5 callers.

**Tests**: Run existing explain tests — they test structure, not exact field contents. Add test that `source_code` key is NOT in callers output.

---

## Phase 4: Output Caps for lens_context and lens_find_usages

**Files**: `lenspr/tools/navigation.py` (handle_context), `lenspr/tools/analysis.py` (_find_usages_for_node, handle_find_usages)

### 4a: Cap tests in lens_context

**Current behavior**: Tests array is unbounded. A function like `resolve_or_fail` has 19 tests returned.

**Change**: Cap tests at 15. Add `tests_truncated` boolean if over cap.
```python
MAX_TESTS = 15
# ... after collecting tests ...
if len(tests) > MAX_TESTS:
    tests = tests[:MAX_TESTS]
    result["tests_truncated"] = True
    result["tests_note"] = f"Showing first {MAX_TESTS} tests. Use lens_search('test_{node_name}') for all."
```

### 4b: Cap lens_find_usages results

**Current behavior**: `_find_usages_for_node()` returns ALL predecessors with no limit. For utility functions, this can be 100+ entries.

**Change**: Add `max_usages` parameter to `_find_usages_for_node()`, default 50. Apply AFTER grouping into callers/importers/inheritors so each category gets fair representation.

```python
# After collecting all usages:
if len(usages) > max_usages:
    usages = usages[:max_usages]
    result["truncated"] = True
    result["total_in_graph"] = actual_total  # show real count
```

Also pass `max_usages` through from handle_find_usages params so user can override.

**Tests**: Existing find_usages tests pass. Add test with a heavily-referenced node to verify truncation.

---

## Phase 5: Don't Register Unavailable Tools

**Files**: `lenspr/mcp_server.py` (run_server — tool registration section)

**Current behavior**: `lens_security_scan` and `lens_dep_audit` are always registered. When called, they return `{"error": "Bandit is not installed"}`. This wastes tool description tokens (~200 per tool) and confuses the LLM.

**Change**: At server startup, check if the required binary is available:
```python
import shutil
if shutil.which("bandit"):
    # register lens_security_scan
if shutil.which("pip-audit") or shutil.which("npm"):
    # register lens_dep_audit
```

If not available, simply don't register the tool. LLM never sees it, never wastes a call.

**Fallback**: If user asks about security scanning, other tools (lens_nfr_check, lens_vibecheck) still work and provide partial coverage.

**Tests**: Add test that verifies security_scan is not in tool list when bandit is not installed.

---

## Phase 6: lens_resume / lens_session_handoff Output Caps

**Files**: `lenspr/tools/session.py` (handle_resume, handle_session_handoff)

### 6a: Cap lens_resume action log

**Current behavior**: `handle_resume()` renders ALL `_log_*` entries as markdown. With 97 entries, this is a massive output.

**Change**: Add `max_actions` parameter (default 30). After sorting chronologically, take only the last N:
```python
max_actions = int(params.get("max_actions", 30))
action_entries = action_entries[-max_actions:]  # most recent
```

Also truncate each user note's value to 3KB max in the summary.

### 6b: Cap lens_session_handoff

**Current behavior**: `handle_session_handoff()` includes ALL session notes verbatim.

**Change**: For user notes, truncate each to 3KB. Skip `_log_*` entries (they're already covered by the "Recent changes" section from history.db).

Actually, looking at the code: `handle_session_handoff()` already reads from `history.db` for recent changes (separate from `_log_*` session notes). But then it also includes ALL session notes — including `_log_*` entries and the massive `self_evaluation`.

Fix: Filter session notes the same way as system prompt — skip `_log_*`, truncate large notes.

**Tests**: Existing session tests pass. Add test that resume with 100 actions returns only 30.

---

## Execution Order

1. **Phase 1** first — biggest impact (16K tokens saved per conversation)
2. **Phase 2** next — affects every tool call, easy to implement
3. **Phase 3-4** together — tool-specific output fixes
4. **Phase 5** — conditional tool registration
5. **Phase 6** — session output caps

Each phase is independently testable. Run `lens_run_tests()` after each phase.

## What This Plan Does NOT Change

- **Tool functionality** — all tools still return the same data, just capped/filtered
- **Database schema** — no migrations needed
- **API contracts** — fields may be absent (null→omitted) but never renamed
- **Tool descriptions/schemas** — no changes to `schemas.py`
- **Existing tests** — all should pass (minor assertions may need updating for renamed fields in pending annotations)
