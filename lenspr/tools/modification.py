"""Code modification tool handlers."""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING

from lenspr import database, graph
from lenspr.models import PatchError, ToolResponse
from lenspr.patcher import insert_code, remove_lines
from lenspr.tools.helpers import get_proactive_warnings
from lenspr.validator import validate_full, validate_syntax

if TYPE_CHECKING:
    from lenspr.context import LensContext


def handle_update_node(params: dict, ctx: LensContext) -> ToolResponse:
    """Update the source code of a node."""
    node_id = params["node_id"]
    new_source = params["new_source"]

    node = database.get_node(node_id, ctx.graph_db)
    if not node:
        return ToolResponse(
            success=False,
            error=f"Node not found: {node_id}",
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

    # Apply immediately
    try:
        ctx.patch_buffer.flush()
    except PatchError as e:
        ctx.patch_buffer.discard()
        return ToolResponse(
            success=False, error=str(e), warnings=all_warnings, data={"impact": impact}
        )

    # Record history
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
    )

    # Reparse file
    ctx.reparse_file(file_path)

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

    new_content = insert_code(file_path, source_code, after_line)
    file_path.write_text(new_content, encoding="utf-8")

    ctx.reparse_file(file_path)

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
    new_content = remove_lines(file_path, node.start_line, node.end_line)
    file_path.write_text(new_content, encoding="utf-8")

    # Record deletion
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
    )

    database.delete_node(node_id, ctx.graph_db)
    ctx.reparse_file(file_path)

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
    """Apply multiple node updates atomically."""
    from typing import Any

    updates = params["updates"]
    if not updates:
        return ToolResponse(success=False, error="No updates provided.")

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

    # Phase 2: Buffer all patches
    files_to_reparse: set[str] = set()
    for node, new_source in nodes_to_update:
        file_path = ctx.project_root / node.file_path
        ctx.patch_buffer.add(file_path, node, new_source)
        files_to_reparse.add(node.file_path)

    # Phase 3: Apply all patches at once
    try:
        ctx.patch_buffer.flush()
    except PatchError as e:
        ctx.patch_buffer.discard()
        return ToolResponse(
            success=False,
            error=f"Patch failed: {e}",
            hint="All updates rolled back.",
        )

    # Phase 4: Record history and reparse
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

    for rel_path in files_to_reparse:
        ctx.reparse_file(ctx.project_root / rel_path)

    return ToolResponse(
        success=True,
        data={
            "updated": results,
            "count": len(results),
            "files_reparsed": len(files_to_reparse),
        },
    )
