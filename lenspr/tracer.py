"""Runtime call tracer using sys.monitoring (Python 3.12+).

Collects caller→callee edges during test execution, then merges
them into the static graph as EdgeSource.RUNTIME or BOTH.
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Minimum Python version for sys.monitoring
_MIN_VERSION = (3, 12)


def is_tracing_available() -> bool:
    """Check if sys.monitoring is available (Python 3.12+)."""
    return sys.version_info >= _MIN_VERSION


class CallTracer:
    """Collect runtime call edges using sys.monitoring.

    Usage:
        tracer = CallTracer(project_root=Path("/my/project"))
        tracer.start()
        # ... run tests or code ...
        edges = tracer.stop()
        # edges is a list of (caller_id, callee_id) tuples
    """

    TOOL_ID = sys.monitoring.DEBUGGER_ID if is_tracing_available() else 0

    def __init__(self, project_root: Path):
        self.project_root = project_root.resolve()
        self._project_str = str(self.project_root) + "/"
        # Deduplicated edges: (caller_id, callee_id) → call_count
        self._edges: dict[tuple[str, str], int] = {}
        # Cache: code object id → node_id (or None if external)
        self._code_cache: dict[int, str | None] = {}
        self._active = False

    def start(self) -> None:
        """Register sys.monitoring callbacks."""
        if not is_tracing_available():
            logger.warning("sys.monitoring requires Python 3.12+, tracing disabled")
            return

        mon = sys.monitoring
        mon.use_tool_id(self.TOOL_ID, "lenspr_tracer")
        mon.set_events(self.TOOL_ID, mon.events.CALL)
        mon.register_callback(self.TOOL_ID, mon.events.CALL, self._on_call)
        self._active = True
        logger.info("CallTracer started (sys.monitoring CALL events)")

    def stop(self) -> list[tuple[str, str, int]]:
        """Unregister callbacks and return collected edges.

        Returns:
            List of (caller_node_id, callee_node_id, call_count) tuples.
        """
        if self._active:
            mon = sys.monitoring
            mon.set_events(self.TOOL_ID, 0)
            mon.register_callback(self.TOOL_ID, mon.events.CALL, None)
            mon.free_tool_id(self.TOOL_ID)
            self._active = False
            logger.info(
                "CallTracer stopped: %d unique edges from %d cached code objects",
                len(self._edges), len(self._code_cache),
            )

        return [
            (caller, callee, count)
            for (caller, callee), count in self._edges.items()
        ]

    def _on_call(self, code: Any, instruction_offset: int, callable: Any, arg0: Any) -> Any:
        """sys.monitoring CALL callback — fires on every function call."""
        try:
            # Get caller from the code object (the function being executed)
            caller_id = self._resolve_code(code)
            if caller_id is None:
                return  # External caller — skip

            # Get callee from the callable
            callee_id = self._resolve_callable(callable)
            if callee_id is None:
                return  # External callee — skip

            if caller_id == callee_id:
                return  # Self-call — not interesting

            key = (caller_id, callee_id)
            self._edges[key] = self._edges.get(key, 0) + 1
        except Exception:
            pass  # Never let tracing crash the test run

    def _resolve_code(self, code: Any) -> str | None:
        """Map a code object to a graph node ID."""
        code_id = id(code)
        if code_id in self._code_cache:
            return self._code_cache[code_id]

        filename = getattr(code, "co_filename", None)
        if not filename or not filename.startswith(self._project_str):
            self._code_cache[code_id] = None
            return None

        qualname = getattr(code, "co_qualname", None) or getattr(code, "co_name", "")
        node_id = self._build_node_id(filename, qualname)
        self._code_cache[code_id] = node_id
        return node_id

    def _resolve_callable(self, callable: Any) -> str | None:
        """Map a callable to a graph node ID."""
        # Get the underlying function from methods/descriptors
        func = callable
        if hasattr(func, "__func__"):
            func = func.__func__  # bound method → function
        if hasattr(func, "__wrapped__"):
            func = func.__wrapped__  # decorated function

        module = getattr(func, "__module__", None) or ""
        qualname = getattr(func, "__qualname__", None) or getattr(func, "__name__", "")

        if not module or not qualname:
            return None

        # Filter: must be a project module
        # Check if the module file is under project root
        try:
            mod = sys.modules.get(module)
            if mod is None:
                return None
            mod_file = getattr(mod, "__file__", None)
            if not mod_file or not mod_file.startswith(self._project_str):
                return None
        except Exception:
            return None

        # Build node_id: module.qualname (e.g. "lenspr.tools.safety.handle_vibecheck")
        return f"{module}.{qualname}"

    def _build_node_id(self, filename: str, qualname: str) -> str | None:
        """Build a graph node ID from filename + co_qualname."""
        try:
            rel = Path(filename).relative_to(self.project_root)
        except ValueError:
            return None

        # Convert path to module: lenspr/tools/safety.py → lenspr.tools.safety
        module = str(rel).replace("/", ".").replace("\\", ".")
        if module.endswith(".py"):
            module = module[:-3]

        # co_qualname uses <locals> for closures — skip those
        if "<" in qualname:
            return None

        return f"{module}.{qualname}"

    def save_edges(self, output_path: Path) -> int:
        """Write collected edges to a JSON file.

        Returns the number of edges written.
        """
        edges = self.stop() if self._active else [
            (caller, callee, count)
            for (caller, callee), count in self._edges.items()
        ]

        data = {
            "edges": [
                {"from": caller, "to": callee, "count": count}
                for caller, callee, count in edges
            ],
            "total_calls": sum(count for _, _, count in edges),
            "unique_edges": len(edges),
        }

        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        logger.info("Wrote %d runtime edges to %s", len(edges), output_path)
        return len(edges)
