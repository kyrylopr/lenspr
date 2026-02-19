"""Pytest plugin that activates LensPR call tracing during test runs.

Activated via: pytest -p lenspr.pytest_tracer
Writes runtime edges to .lens/trace_edges.json after all tests complete.

Requires Python 3.12+ (sys.monitoring). Silently no-ops on older versions.
"""

from __future__ import annotations

import os
from pathlib import Path


def pytest_configure(config) -> None:
    """Start the call tracer before tests run."""
    from lenspr.tracer import CallTracer, is_tracing_available

    if not is_tracing_available():
        return

    # Find project root: use LENSPR_PROJECT_ROOT env var, or pytest rootdir
    project_root = os.environ.get("LENSPR_PROJECT_ROOT")
    if project_root:
        root = Path(project_root)
    else:
        root = Path(str(config.rootdir))

    tracer = CallTracer(project_root=root)
    config._lenspr_tracer = tracer
    tracer.start()


def pytest_unconfigure(config) -> None:
    """Stop tracer and write edges to .lens/trace_edges.json."""
    tracer = getattr(config, "_lenspr_tracer", None)
    if tracer is None:
        return

    project_root = tracer.project_root
    output_path = project_root / ".lens" / "trace_edges.json"
    tracer.save_edges(output_path)
