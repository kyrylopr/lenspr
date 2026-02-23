"""Git integration tool handlers."""

from __future__ import annotations

import subprocess
from datetime import datetime
from typing import TYPE_CHECKING, Any

from lenspr import database
from lenspr.models import ToolResponse
from lenspr.tools.helpers import resolve_or_fail

if TYPE_CHECKING:
    from lenspr.context import LensContext


def _run_git(args: list[str], cwd: str) -> tuple[bool, str]:
    """Run a git command and return (success, output)."""
    try:
        result = subprocess.run(
            ["git"] + args,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0:
            return True, result.stdout
        return False, result.stderr
    except subprocess.TimeoutExpired:
        return False, "Git command timed out"
    except FileNotFoundError:
        return False, "Git not found"


def _is_git_repo(path: str) -> bool:
    """Check if path is inside a git repository."""
    success, _ = _run_git(["rev-parse", "--git-dir"], path)
    return success


def handle_blame(params: dict, ctx: LensContext) -> ToolResponse:
    """Get git blame information for a node's source lines.

    Returns who wrote each line and when.
    """
    node_id, err = resolve_or_fail(params["node_id"], ctx)
    if err:
        return err

    if not _is_git_repo(str(ctx.project_root)):
        return ToolResponse(
            success=False,
            error="Not a git repository",
        )

    node = database.get_node(node_id, ctx.graph_db)
    if not node:
        return ToolResponse(
            success=False,
            error=f"Node not found: {node_id}",
            hint="Use lens_search or lens_list_nodes to find valid node IDs.",
        )

    file_path = node.file_path
    start_line = node.start_line
    end_line = node.end_line

    # Run git blame for the specific line range
    success, output = _run_git(
        [
            "blame",
            "-L", f"{start_line},{end_line}",
            "--line-porcelain",
            file_path,
        ],
        str(ctx.project_root),
    )

    if not success:
        return ToolResponse(
            success=False,
            error=f"Git blame failed: {output}",
        )

    # Parse porcelain output
    lines: list[dict[str, Any]] = []
    current_line: dict[str, Any] = {}

    for line in output.split("\n"):
        if not line:
            continue

        if line.startswith("\t"):
            # This is the actual source line
            current_line["code"] = line[1:]
            lines.append(current_line)
            current_line = {}
        elif len(line) >= 40 and all(c in "0123456789abcdef" for c in line[:40]):
            # Commit hash line: "hash orig_line final_line [count]"
            parts = line.split()
            current_line = {
                "commit": parts[0][:8],
                "line_num": int(parts[2]) if len(parts) > 2 else 0,
            }
        elif line.startswith("author "):
            current_line["author"] = line[7:]
        elif line.startswith("author-time "):
            try:
                timestamp = int(line[12:])
                current_line["date"] = datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d")
            except ValueError:
                pass
        elif line.startswith("summary "):
            current_line["summary"] = line[8:]

    # Aggregate by author
    authors: dict[str, int] = {}
    for ln in lines:
        author = ln.get("author", "Unknown")
        authors[author] = authors.get(author, 0) + 1

    return ToolResponse(
        success=True,
        data={
            "node_id": node_id,
            "file_path": file_path,
            "line_range": f"{start_line}-{end_line}",
            "total_lines": len(lines),
            "authors": authors,
            "lines": lines[:50],  # Limit to 50 lines to avoid huge responses
            "truncated": len(lines) > 50,
        },
    )


def handle_node_history(params: dict, ctx: LensContext) -> ToolResponse:
    """Get commit history for a specific node.

    Shows commits that modified the lines where this node is defined.
    """
    node_id, err = resolve_or_fail(params["node_id"], ctx)
    if err:
        return err
    limit = params.get("limit", 10)

    if not _is_git_repo(str(ctx.project_root)):
        return ToolResponse(
            success=False,
            error="Not a git repository",
        )

    node = database.get_node(node_id, ctx.graph_db)
    if not node:
        return ToolResponse(
            success=False,
            error=f"Node not found: {node_id}",
            hint="Use lens_search or lens_list_nodes to find valid node IDs.",
        )

    file_path = node.file_path
    start_line = node.start_line
    end_line = node.end_line

    # Get commits that touched these lines
    success, output = _run_git(
        [
            "log",
            f"-L{start_line},{end_line}:{file_path}",
            f"-{limit}",
            "--format=%H|%an|%ae|%at|%s",
            "--no-patch",
        ],
        str(ctx.project_root),
    )

    if not success:
        # Fallback: get history for the whole file
        success, output = _run_git(
            [
                "log",
                f"-{limit}",
                "--format=%H|%an|%ae|%at|%s",
                "--",
                file_path,
            ],
            str(ctx.project_root),
        )
        if not success:
            return ToolResponse(
                success=False,
                error=f"Git log failed: {output}",
            )

    # Parse commits
    commits: list[dict[str, Any]] = []
    for line in output.strip().split("\n"):
        if not line or "|" not in line:
            continue
        parts = line.split("|", 4)
        if len(parts) >= 5:
            commits.append({
                "hash": parts[0][:8],
                "full_hash": parts[0],
                "author": parts[1],
                "email": parts[2],
                "date": datetime.fromtimestamp(int(parts[3])).strftime("%Y-%m-%d %H:%M"),
                "message": parts[4],
            })

    return ToolResponse(
        success=True,
        data={
            "node_id": node_id,
            "file_path": file_path,
            "line_range": f"{start_line}-{end_line}",
            "commits": commits,
            "count": len(commits),
        },
    )


def handle_commit_scope(params: dict, ctx: LensContext) -> ToolResponse:
    """Analyze what nodes were affected by a specific commit.

    Shows which functions/classes were modified in a commit.
    """
    from lenspr.parsers import is_supported_file

    commit_hash = params["commit"]

    if not _is_git_repo(str(ctx.project_root)):
        return ToolResponse(
            success=False,
            error="Not a git repository",
        )

    # Get list of files changed in this commit
    success, output = _run_git(
        ["diff-tree", "--no-commit-id", "--name-only", "-r", commit_hash],
        str(ctx.project_root),
    )

    if not success:
        return ToolResponse(
            success=False,
            error=f"Invalid commit: {commit_hash}",
        )

    # Filter to supported source files
    changed_files = [f for f in output.strip().split("\n") if is_supported_file(f)]

    if not changed_files:
        return ToolResponse(
            success=True,
            data={
                "commit": commit_hash,
                "message": "No source files changed",
                "affected_nodes": [],
                "count": 0,
            },
        )

    # Get commit message
    success, msg_output = _run_git(
        ["log", "-1", "--format=%s", commit_hash],
        str(ctx.project_root),
    )
    commit_message = msg_output.strip() if success else ""

    # Get line-by-line diff to find affected ranges
    success, diff_output = _run_git(
        ["diff", f"{commit_hash}^", commit_hash, "--unified=0", "--"] + changed_files,
        str(ctx.project_root),
    )

    # Parse diff to find changed line ranges per file
    file_changes: dict[str, list[tuple[int, int]]] = {}
    current_file = ""

    for line in diff_output.split("\n"):
        if line.startswith("+++ b/"):
            current_file = line[6:]
        elif line.startswith("@@ "):
            # Parse hunk header: @@ -old_start,old_count +new_start,new_count @@
            parts = line.split()
            if len(parts) >= 3:
                new_range = parts[2]  # +new_start,new_count
                if new_range.startswith("+"):
                    new_range = new_range[1:]
                    if "," in new_range:
                        start_str, count_str = new_range.split(",")
                        start, count = int(start_str), int(count_str)
                    else:
                        start, count = int(new_range), 1
                    end = start + count - 1 if count > 0 else start

                    if current_file not in file_changes:
                        file_changes[current_file] = []
                    file_changes[current_file].append((start, end))

    # Find nodes that overlap with changed ranges
    affected_nodes: list[dict[str, Any]] = []
    nx_graph = ctx.get_graph()

    for nid, data in nx_graph.nodes(data=True):
        node_file = data.get("file_path", "")
        if node_file not in file_changes:
            continue

        node_start = data.get("start_line", 0)
        node_end = data.get("end_line", 0)
        node_type = data.get("type", "")

        # Skip modules (too broad)
        if node_type == "module":
            continue

        # Check if any changed range overlaps with this node
        for change_start, change_end in file_changes[node_file]:
            if node_start <= change_end and node_end >= change_start:
                affected_nodes.append({
                    "id": nid,
                    "name": data.get("name", ""),
                    "type": node_type,
                    "file_path": node_file,
                    "changed_lines": f"{change_start}-{change_end}",
                })
                break

    # Sort by file and type
    affected_nodes.sort(key=lambda x: (x["file_path"], x["type"], x["name"]))

    return ToolResponse(
        success=True,
        data={
            "commit": commit_hash[:8],
            "message": commit_message,
            "files_changed": changed_files,
            "affected_nodes": affected_nodes,
            "count": len(affected_nodes),
        },
    )


def handle_recent_changes(params: dict, ctx: LensContext) -> ToolResponse:
    """Get recently changed nodes based on git history.

    Useful for understanding what's been modified recently.
    """
    from lenspr.parsers import is_supported_file

    limit = params.get("limit", 5)
    file_filter = params.get("file_path")

    if not _is_git_repo(str(ctx.project_root)):
        return ToolResponse(
            success=False,
            error="Not a git repository",
        )

    # Get recent commits
    git_args = [
        "log",
        f"-{limit}",
        "--format=%H|%an|%at|%s",
        "--name-only",
    ]

    if file_filter:
        git_args.extend(["--", file_filter])
    # Note: we don't filter by extension here as git doesn't support glob in pathspec
    # Instead, we filter the files in the result

    success, output = _run_git(git_args, str(ctx.project_root))

    if not success:
        return ToolResponse(
            success=False,
            error=f"Git log failed: {output}",
        )

    # Parse commits with their files
    commits: list[dict[str, Any]] = []
    current_commit: dict[str, Any] = {}

    for line in output.split("\n"):
        if "|" in line and len(line.split("|")) >= 4:
            # New commit
            if current_commit:
                commits.append(current_commit)
            parts = line.split("|", 3)
            current_commit = {
                "hash": parts[0][:8],
                "author": parts[1],
                "date": datetime.fromtimestamp(int(parts[2])).strftime("%Y-%m-%d"),
                "message": parts[3],
                "files": [],
            }
        elif line.strip() and is_supported_file(line.strip()):
            if current_commit:
                current_commit["files"].append(line.strip())

    if current_commit:
        commits.append(current_commit)

    return ToolResponse(
        success=True,
        data={
            "commits": commits,
            "count": len(commits),
        },
    )
