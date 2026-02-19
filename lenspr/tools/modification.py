"""Code modification tool handlers."""

from __future__ import annotations

import hashlib
import logging
from typing import TYPE_CHECKING

from lenspr import database, graph
from lenspr.models import PatchError, ToolResponse
from lenspr.patcher import insert_code, remove_lines
from lenspr.tools.helpers import get_proactive_warnings
from lenspr.validator import validate_full, validate_syntax

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from lenspr.context import LensContext


def handle_update_node(params: dict, ctx: LensContext) -> ToolResponse:
    """Update the source code of a node."""
    node_id = params["node_id"]
    new_source = params["new_source"]
    reasoning = params.get("reasoning", "")

    node = database.get_node(node_id, ctx.graph_db)
    if not node:
        return ToolResponse(
            success=False,
            error=f"Node not found: {node_id}",
        )

    old_src_len = len(node.source_code or "")
    new_src_len = len(new_source)

    # Guard 1: Block direct updates on large container nodes.
    # These must be updated via their individual method/function children.
    _CONTAINER_THRESHOLD = 10_000
    if node.type.value in ("class", "module") and old_src_len > _CONTAINER_THRESHOLD:
        total_lines = len((node.source_code or "").splitlines())
        return ToolResponse(
            success=False,
            error=(
                f"Node '{node_id}' is a large {node.type.value} "
                f"({total_lines} lines, {old_src_len:,} chars). "
                f"Direct updates are blocked — too risky."
            ),
            hint=(
                f"Work on individual children instead:\n"
                f"  • Modify a method: lens_update_node on the specific method node\n"
                f"  • Add a method: lens_add_node(file_path='{node.file_path}', "
                f"source_code=..., after_node='<existing_method_id>')\n"
                f"  • List methods: lens_list_nodes(file_path='{node.file_path}', type='method')"
            ),
        )

    # Guard 2: Source integrity check.
    # If new source is less than 20% of old source on a non-trivial node,
    # it's almost certainly truncated output from the LLM.
    _INTEGRITY_RATIO = 0.20
    if old_src_len > 2_000 and new_src_len < old_src_len * _INTEGRITY_RATIO:
        return ToolResponse(
            success=False,
            error=(
                f"Source integrity check failed: new source ({new_src_len:,} chars) "
                f"is less than {int(_INTEGRITY_RATIO * 100)}% of the original "
                f"({old_src_len:,} chars). The new source appears to be truncated."
            ),
            hint=(
                "Provide the COMPLETE source code for the node. "
                "If you need to make a small change, include the entire function/method body."
            ),
        )

    # Compute impact FIRST - BEFORE any changes
    nx_graph = ctx.get_graph()
    impact = graph.get_impact_zone(nx_graph, node_id, depth=2)

    # Calculate severity for visibility
    total = impact.get("total_affected", 0)
    has_inheritors = len(impact.get("inheritors", [])) > 0
    if has_inheritors or total > 20:
        severity = "CRITICAL"
    elif total > 10:
        severity = "HIGH"
    elif total > 5:
        severity = "MEDIUM"
    else:
        severity = "LOW"
    impact["severity"] = severity

    # Get proactive warnings BEFORE making changes
    proactive_warnings = get_proactive_warnings(node_id, new_source, ctx)

    # Three-level validation
    validation = validate_full(new_source, node)
    if not validation.valid:
        all_warnings = proactive_warnings + validation.warnings
        return ToolResponse(
            success=False,
            error=validation.errors[0] if validation.errors else "Validation failed.",
            hint="Fix the issues and try again.",
            warnings=all_warnings,
            data={"impact": impact},  # Always show impact even on failure
        )

    all_warnings = proactive_warnings + validation.warnings

    # Buffer the patch
    file_path = ctx.project_root / node.file_path
    ctx.patch_buffer.add(file_path, node, new_source)

    # Save old content for rollback
    old_content = file_path.read_text(encoding="utf-8")

    # Apply patch to file
    try:
        ctx.patch_buffer.flush()
    except PatchError as e:
        ctx.patch_buffer.discard()
        return ToolResponse(
            success=False, error=str(e), warnings=all_warnings, data={"impact": impact}
        )

    # ATOMIC: Sync graph - if this fails, rollback file
    try:
        ctx.reparse_file(file_path)
    except Exception as e:
        # Rollback: restore old file content
        file_path.write_text(old_content, encoding="utf-8")
        return ToolResponse(
            success=False,
            error=f"Graph sync failed, file rolled back: {e}",
            hint="This is likely a parser bug. Please report it.",
            warnings=all_warnings,
            data={"impact": impact},
        )

    # Record history (only after successful sync)
    from lenspr.tracker import record_change

    new_hash = hashlib.sha256(new_source.encode()).hexdigest()
    record_change(
        node_id=node_id,
        action="modified",
        old_source=node.source_code,
        new_source=new_source,
        old_hash=node.hash,
        new_hash=new_hash,
        affected_nodes=impact.get("direct_callers", []),
        description=f"Updated {node.name}",
        db_path=ctx.history_db,
        reasoning=reasoning,
        file_path=node.file_path,
    )

    # Synchronous hot-reload: if a lenspr module file was just patched,
    # reload it in sys.modules immediately — no file-watcher debounce needed.
    # This ensures the next tool call sees the updated handler right away.
    _reload_lenspr_module_if_needed(node.file_path)

    # Auto-log: write a structured entry so lens_resume() can reconstruct history.
    _log_modification(
        action="modified",
        node_id=node_id,
        file_path=node.file_path,
        reasoning=reasoning,
        impact_summary=f"{severity} — {total} node(s) affected",
        ctx=ctx,
    )

    return ToolResponse(
        success=True,
        data={
            "node_id": node_id,
            "new_hash": new_hash,
            "impact": impact,  # Always show what was affected
        },
        warnings=all_warnings,
        affected_nodes=impact.get("direct_callers", []),
    )


def handle_patch_node(params: dict, ctx: LensContext) -> ToolResponse:
    """Apply a surgical find/replace within a node's source code.

    Safer than lens_update_node for large functions — you only provide the
    fragment that changes, not the entire source. The old_fragment must be
    unique within the node's source.

    Args:
        node_id: The node to patch.
        old_fragment: Exact text to find within the node source. Must appear
            exactly once (provide more context if ambiguous).
        new_fragment: Text to replace old_fragment with.
        reasoning: Why this change is being made.
    """
    node_id = params["node_id"]
    old_fragment = params["old_fragment"]
    new_fragment = params["new_fragment"]
    reasoning = params.get("reasoning", "")

    node = database.get_node(node_id, ctx.graph_db)
    if not node:
        return ToolResponse(success=False, error=f"Node not found: {node_id}")

    source = node.source_code or ""

    # Validate old_fragment exists and is unambiguous
    count = source.count(old_fragment)
    if count == 0:
        # Give a helpful hint with surrounding context
        lines = source.splitlines()
        sample = "\n".join(lines[:10]) + ("\n..." if len(lines) > 10 else "")
        return ToolResponse(
            success=False,
            error=f"old_fragment not found in node '{node_id}'.",
            hint=(
                f"The fragment you provided does not appear in this node's source. "
                f"First 10 lines of source:\n{sample}"
            ),
        )
    if count > 1:
        return ToolResponse(
            success=False,
            error=(
                f"old_fragment is ambiguous — found {count} times in node '{node_id}'. "
                f"Provide more surrounding context to make it unique."
            ),
            hint=(
                "Include additional lines before/after your target fragment "
                "so that the match is unique within the function."
            ),
        )

    # Apply the replacement
    new_source = source.replace(old_fragment, new_fragment, 1)

    # Delegate to the existing update pipeline:
    # impact analysis → validation → patch → graph sync → history
    return handle_update_node(
        {
            "node_id": node_id,
            "new_source": new_source,
            "reasoning": reasoning or f"patch: replaced fragment in {node_id}",
        },
        ctx,
    )



def handle_add_node(params: dict, ctx: LensContext) -> ToolResponse:
    """Add a new function or class to a file."""
    file_path = ctx.project_root / params["file_path"]
    source_code = params["source_code"]

    # Validate syntax before inserting
    syntax_check = validate_syntax(source_code)
    if not syntax_check.valid:
        return ToolResponse(
            success=False,
            error=syntax_check.errors[0] if syntax_check.errors else "Syntax error.",
            hint="Fix the syntax and try again.",
        )

    if not file_path.exists():
        return ToolResponse(
            success=False,
            error=f"File not found: {params['file_path']}",
        )

    after_node_id = params.get("after_node")
    after_line = 0

    if after_node_id:
        after_node = database.get_node(after_node_id, ctx.graph_db)
        if after_node:
            after_line = after_node.end_line
        else:
            return ToolResponse(
                success=False, error=f"Node not found: {after_node_id}"
            )
    else:
        content = file_path.read_text(encoding="utf-8")
        after_line = len(content.splitlines())

    # Save old content for rollback
    old_content = file_path.read_text(encoding="utf-8")

    new_content = insert_code(file_path, source_code, after_line)
    file_path.write_text(new_content, encoding="utf-8")

    # ATOMIC: Sync graph - if this fails, rollback file
    try:
        ctx.reparse_file(file_path)
    except Exception as e:
        # Rollback: restore old file content
        file_path.write_text(old_content, encoding="utf-8")
        return ToolResponse(
            success=False,
            error=f"Graph sync failed, file rolled back: {e}",
            hint="This is likely a parser bug. Please report it.",
        )

    reasoning = params.get("reasoning", "")

    # Record in history.db (was previously missing — only session log was written)
    from lenspr.tracker import record_change

    new_hash = hashlib.sha256(source_code.encode()).hexdigest()
    record_change(
        node_id=params["file_path"],
        action="created",
        old_source=None,
        new_source=source_code,
        old_hash="",
        new_hash=new_hash,
        affected_nodes=[],
        description=f"Added code to {params['file_path']}",
        db_path=ctx.history_db,
        reasoning=reasoning,
        file_path=params["file_path"],
    )

    _log_modification(
        action="added",
        node_id=params["file_path"],
        file_path=params["file_path"],
        reasoning=reasoning,
        impact_summary="added",
        ctx=ctx,
    )

    return ToolResponse(
        success=True,
        data={"file": params["file_path"], "inserted_after_line": after_line},
    )


def handle_delete_node(params: dict, ctx: LensContext) -> ToolResponse:
    """Delete a node from the codebase."""
    node_id = params["node_id"]
    node = database.get_node(node_id, ctx.graph_db)

    if not node:
        return ToolResponse(success=False, error=f"Node not found: {node_id}")

    file_path = ctx.project_root / node.file_path

    # Save old content for rollback
    old_content = file_path.read_text(encoding="utf-8")

    new_content = remove_lines(file_path, node.start_line, node.end_line)
    file_path.write_text(new_content, encoding="utf-8")

    # ATOMIC: Sync graph - if this fails, rollback file
    try:
        ctx.reparse_file(file_path)
    except Exception as e:
        # Rollback: restore old file content
        file_path.write_text(old_content, encoding="utf-8")
        return ToolResponse(
            success=False,
            error=f"Graph sync failed, file rolled back: {e}",
            hint="This is likely a parser bug. Please report it.",
        )

    # Record deletion (only after successful sync)
    from lenspr.tracker import record_change

    record_change(
        node_id=node_id,
        action="deleted",
        old_source=node.source_code,
        new_source=None,
        old_hash=node.hash,
        new_hash="",
        affected_nodes=[],
        description=f"Deleted {node.name}",
        db_path=ctx.history_db,
        file_path=node.file_path,
    )

    database.delete_node(node_id, ctx.graph_db)

    _log_modification(
        action="deleted",
        node_id=node_id,
        file_path=node.file_path,
        reasoning=params.get("reasoning", ""),
        impact_summary="deleted",
        ctx=ctx,
    )

    return ToolResponse(success=True, data={"deleted": node_id})


def handle_rename(params: dict, ctx: LensContext) -> ToolResponse:
    """Rename a function/class/method across the entire project."""
    from lenspr.parsers import get_supported_extensions

    node_id = params["node_id"]
    new_name = params["new_name"]

    node = database.get_node(node_id, ctx.graph_db)
    if not node:
        return ToolResponse(success=False, error=f"Node not found: {node_id}")

    old_name = node.name

    # Find all incoming edges (callers/importers)
    edges = database.get_edges(node_id, ctx.graph_db, direction="incoming")
    warnings: list[str] = []

    # Update definition
    file_path = ctx.project_root / node.file_path
    content = file_path.read_text(encoding="utf-8")
    lines = content.splitlines()
    for i in range(node.start_line - 1, min(node.end_line, len(lines))):
        if old_name in lines[i]:
            lines[i] = lines[i].replace(old_name, new_name, 1)
            break
    file_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    # Update callers
    files_modified = {node.file_path}
    refs_updated = 1

    for edge in edges:
        caller = database.get_node(edge.from_node, ctx.graph_db)
        if not caller:
            continue

        caller_file = ctx.project_root / caller.file_path
        caller_content = caller_file.read_text(encoding="utf-8")

        if old_name in caller_content:
            caller_content = caller_content.replace(old_name, new_name)
            caller_file.write_text(caller_content, encoding="utf-8")
            files_modified.add(caller.file_path)
            refs_updated += caller_content.count(new_name)

    # Scan for string references not auto-renamed in all supported files
    needs_review: list[dict] = []
    extensions = get_supported_extensions()

    for ext in extensions:
        for src_file in ctx.project_root.rglob(f"*{ext}"):
            rel = str(src_file.relative_to(ctx.project_root))
            if rel in files_modified:
                continue
            # Skip common directories
            if any(part in src_file.parts for part in (
                "node_modules", "__pycache__", ".git", ".venv", "venv",
                ".mypy_cache", ".pytest_cache", "dist", "build", ".lens"
            )):
                continue
            try:
                text = src_file.read_text(encoding="utf-8")
            except Exception:
                continue
            for i, line in enumerate(text.splitlines(), 1):
                if old_name in line:
                    needs_review.append({
                        "file": rel,
                        "line": i,
                        "context": line.strip(),
                    })

    if needs_review:
        warnings.append(
            f"Found {len(needs_review)} possible string references not auto-renamed. "
            f"Review these manually."
        )

    # Reparse all modified files
    for f in files_modified:
        ctx.reparse_file(ctx.project_root / f)

    # Record in history.db
    from lenspr.tracker import record_change

    record_change(
        node_id=node_id,
        action="renamed",
        old_source=None,
        new_source=None,
        old_hash="",
        new_hash="",
        affected_nodes=list(files_modified),
        description=f"Renamed {old_name} → {new_name}",
        db_path=ctx.history_db,
        reasoning=params.get("reasoning", ""),
        file_path=node.file_path,
    )

    _log_modification(
        action="renamed",
        node_id=node_id,
        file_path=node.file_path,
        reasoning=params.get("reasoning", ""),
        impact_summary=f"{len(files_modified)} file(s) modified",
        ctx=ctx,
    )

    return ToolResponse(
        success=True,
        data={
            "old_name": old_name,
            "new_name": new_name,
            "files_modified": len(files_modified),
            "references_updated": refs_updated,
            "needs_review": needs_review,
        },
        warnings=warnings,
    )


def handle_batch(params: dict, ctx: LensContext) -> ToolResponse:
    """Apply multiple node updates atomically with multi-file rollback.

    All affected files are saved before patching. If ANY step fails —
    flush, graph sync, or optional test verification — ALL files are
    restored to their pre-batch state.
    """
    from typing import Any

    updates = params["updates"]
    verify_tests: bool = params.get("verify_tests", False)
    timeout: int = int(params.get("timeout", 120))

    if not updates:
        return ToolResponse(success=False, error="No updates provided.")

    logger.info("batch: validating %d updates", len(updates))

    # Phase 1: Validate all updates
    nodes_to_update: list[tuple[Any, str]] = []
    for upd in updates:
        node_id = upd["node_id"]
        new_source = upd["new_source"]

        node = database.get_node(node_id, ctx.graph_db)
        if not node:
            return ToolResponse(
                success=False,
                error=f"Node not found: {node_id}",
                hint="All updates aborted. Fix the node_id and retry.",
            )

        validation = validate_full(new_source, node)
        if not validation.valid:
            return ToolResponse(
                success=False,
                error=(
                    f"Validation failed for {node_id}: "
                    f"{validation.errors[0] if validation.errors else 'unknown'}"
                ),
                hint="All updates aborted. Fix the source and retry.",
                warnings=validation.warnings,
            )

        nodes_to_update.append((node, new_source))

    # Phase 2: Save old file contents + buffer patches
    files_to_reparse: set[str] = set()
    old_contents: dict[Path, str] = {}
    for node, new_source in nodes_to_update:
        file_path = ctx.project_root / node.file_path
        if file_path not in old_contents:
            old_contents[file_path] = file_path.read_text(encoding="utf-8")
        ctx.patch_buffer.add(file_path, node, new_source)
        files_to_reparse.add(node.file_path)

    # Phase 2.5 (optional): Capture test baseline BEFORE changes
    test_baseline: dict | None = None
    if verify_tests:
        test_baseline = _run_test_baseline(ctx.project_root, timeout)

    # Phase 3: Apply all patches — rollback ALL on failure
    logger.info("batch: flushing patches to %d files", len(files_to_reparse))
    try:
        ctx.patch_buffer.flush()
    except PatchError as e:
        logger.error("batch: flush failed, rolling back %d files: %s",
                      len(old_contents), e)
        ctx.patch_buffer.discard()
        _rollback_files(old_contents, files_to_reparse, ctx)
        return ToolResponse(
            success=False,
            error=f"Patch failed: {e}",
            hint="All files rolled back.",
        )

    # Phase 4: Reparse all files — rollback ALL if ANY fails
    reparse_error = ""
    for rel_path in files_to_reparse:
        try:
            ctx.reparse_file(ctx.project_root / rel_path)
        except Exception as e:
            reparse_error = f"Graph sync failed for {rel_path}: {e}"
            logger.error("batch: reparse failed for %s: %s", rel_path, e)
            break

    if reparse_error:
        logger.error("batch: rolling back %d files due to reparse failure",
                      len(old_contents))
        _rollback_files(old_contents, files_to_reparse, ctx)
        return ToolResponse(
            success=False,
            error=reparse_error,
            hint="All files rolled back and graph re-synced.",
        )

    # Phase 5 (optional): Test verification — rollback if regressions
    if verify_tests and test_baseline is not None:
        test_current = _run_test_baseline(ctx.project_root, timeout)
        baseline_failed = {f["test"] for f in test_baseline.get("failures", [])}
        current_failed = {f["test"] for f in test_current.get("failures", [])}
        regressions = current_failed - baseline_failed

        if regressions:
            logger.warning("batch: %d test regressions, rolling back",
                           len(regressions))
            _rollback_files(old_contents, files_to_reparse, ctx)
            return ToolResponse(
                success=False,
                error=f"Test regressions: {len(regressions)} new failure(s).",
                data={"regressions": sorted(regressions)},
                hint="All files rolled back. Fix regressions and retry.",
            )

    # Phase 6: Record history (only after everything succeeds)
    from lenspr.tracker import record_change

    results: list[dict[str, str]] = []
    for node, new_source in nodes_to_update:
        new_hash = hashlib.sha256(new_source.encode()).hexdigest()
        record_change(
            node_id=node.id,
            action="modified",
            old_source=node.source_code,
            new_source=new_source,
            old_hash=node.hash,
            new_hash=new_hash,
            affected_nodes=[],
            description=f"Batch update: {node.name}",
            db_path=ctx.history_db,
        )
        results.append({"node_id": node.id, "new_hash": new_hash})

    data: dict = {
        "updated": results,
        "count": len(results),
        "files_reparsed": len(files_to_reparse),
    }
    if verify_tests and test_baseline is not None:
        data["tests"] = {
            "baseline_passed": test_baseline["passed"],
            "current_passed": test_current["passed"],  # type: ignore[possibly-undefined]
            "regressions": 0,
        }

    logger.info("batch: completed %d updates across %d files",
                 len(results), len(files_to_reparse))
    return ToolResponse(success=True, data=data)

def _rollback_files(
    old_contents: dict[Path, str],
    files_to_reparse: set[str],
    ctx: LensContext,
) -> None:
    """Restore all files from saved contents and re-sync graph.

    Best-effort: individual failures are logged but not raised because the
    caller is already in an error path and will report the root cause.
    """
    logger.info("rollback: restoring %d files", len(old_contents))
    for fp, old in old_contents.items():
        try:
            fp.write_text(old, encoding="utf-8")
        except OSError as e:
            logger.error("rollback: failed to restore %s: %s", fp, e)
    for rel_path in files_to_reparse:
        try:
            ctx.reparse_file(ctx.project_root / rel_path)
        except Exception as e:
            logger.error("rollback: reparse failed for %s: %s", rel_path, e)

def _run_test_baseline(project_root: Path, timeout: int = 120) -> dict:
    """Run pytest and return structured results for baseline comparison."""
    import re
    import subprocess

    cmd = ["python", "-m", "pytest", "--tb=short", "-q", "--no-header"]
    try:
        proc = subprocess.run(
            cmd, cwd=str(project_root),
            capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return {"passed": 0, "failed": 0, "errors": 0, "all_passed": False,
                "failures": [], "error": f"Tests timed out after {timeout}s"}
    except FileNotFoundError:
        return {"passed": 0, "failed": 0, "errors": 0, "all_passed": False,
                "failures": [], "error": "pytest not found"}

    stdout = proc.stdout or ""
    stderr = proc.stderr or ""
    lines = (stdout + ("\n" + stderr if stderr.strip() else "")).splitlines()

    summary_re = re.compile(
        r"(\d+) passed"
        r"(?:,\s+(\d+) failed)?"
        r"(?:,\s+(\d+) errors?)?"
        r"(?:,\s+(\d+) skipped)?"
        r"(?:,\s+\d+ warnings?)?"
        r"\s+in\s+([\d.]+)s"
    )
    passed = failed = errors = 0
    for line in reversed(lines):
        m = summary_re.search(line)
        if m:
            passed = int(m.group(1) or 0)
            failed = int(m.group(2) or 0)
            errors = int(m.group(3) or 0)
            break

    failures: list[dict] = []
    failed_re = re.compile(r"^FAILED\s+(.+?)\s+-\s+(.+)$")
    for line in lines:
        m = failed_re.match(line)
        if m:
            failures.append({"test": m.group(1).strip(), "reason": m.group(2).strip()})

    all_passed = failed == 0 and errors == 0 and proc.returncode in (0, 5)
    return {
        "passed": passed, "failed": failed, "errors": errors,
        "all_passed": all_passed, "failures": failures,
    }



def _reload_lenspr_module_if_needed(file_path: str | None) -> None:
    """Reload a lenspr module in sys.modules after its source file was patched.

    Converts a relative file path (e.g. 'lenspr/tools/safety.py') to a
    module name ('lenspr.tools.safety') and reloads it synchronously if it
    is already loaded. This eliminates the file-watcher debounce delay —
    the next tool call sees the updated handler immediately.

    Args:
        file_path: Relative path to the modified file (from project root).
    """
    import importlib
    import sys

    if not file_path or not file_path.startswith("lenspr/") or not file_path.endswith(".py"):
        return

    # Convert path to module name: "lenspr/tools/safety.py" → "lenspr.tools.safety"
    module_name = file_path[:-3].replace("/", ".")
    # Handle __init__ files: "lenspr.tools.__init__" → "lenspr.tools"
    if module_name.endswith(".__init__"):
        module_name = module_name[: -len(".__init__")]

    if module_name not in sys.modules:
        return

    try:
        importlib.reload(sys.modules[module_name])
    except Exception:
        pass  # Never fail the patch operation over a reload error

    # Cascade: reload modules that re-export from the changed module.
    # claude_tools has module-level `from lenspr.tools import X` bindings
    # that become stale after lenspr.tools.* is reloaded.
    if module_name.startswith("lenspr.tools"):
        for dependent in ("lenspr.claude_tools", "lenspr.tools"):
            if dependent != module_name and dependent in sys.modules:
                try:
                    importlib.reload(sys.modules[dependent])
                except Exception:
                    pass

def _log_modification(
    action: str,
    node_id: str,
    file_path: str | None,
    reasoning: str,
    impact_summary: str,
    ctx,
) -> None:
    """Write an action entry to the session log so lens_resume() can reconstruct history.

    Uses a unique, time-ordered key prefixed with '_log_' so lens_resume() can
    filter action entries apart from user-written session notes.

    Args:
        action: "modified", "added", or "deleted".
        node_id: The node that was changed.
        file_path: Relative file path (or None for unknown).
        reasoning: The 'reasoning' string from the tool call parameters.
        impact_summary: Human-readable impact string, e.g. "LOW — 3 nodes".
        ctx: LensContext — provides ctx.session_db.
    """
    import json
    from datetime import datetime, timezone

    try:
        ts = datetime.now(timezone.utc)
        # Key is sortable and guaranteed unique: _log_<ISO>_<short_id>
        short_id = node_id.replace(".", "_")[-40:]
        key = f"_log_{ts.strftime('%Y%m%dT%H%M%SZ')}_{short_id}"
        value = json.dumps(
            {
                "action": action,
                "node_id": node_id,
                "file_path": file_path or "",
                "reasoning": reasoning or "(no reasoning provided)",
                "impact_summary": impact_summary,
                "timestamp": ts.isoformat(),
            },
            ensure_ascii=False,
        )
        from lenspr import database

        database.write_session_note(key, value, ctx.session_db)
    except Exception:
        pass  # Logging must never crash the tool operation


