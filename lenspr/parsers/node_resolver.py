"""Node.js-based TypeScript resolver using TypeScript Compiler API.

This module provides full type inference for TypeScript/JavaScript by using
the actual TypeScript compiler via a Node.js subprocess.

Requirements:
- Node.js >= 18.0.0
- TypeScript npm package (auto-installed on first use)
"""

from __future__ import annotations

import hashlib
import json
import logging
import shutil
import sqlite3
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from lenspr.models import EdgeConfidence, Resolution

logger = logging.getLogger(__name__)

# Path to the Node.js resolver script
RESOLVER_SCRIPT = Path(__file__).parent.parent / "helpers" / "ts_resolver.js"
HELPERS_DIR = Path(__file__).parent.parent / "helpers"


@dataclass
class ResolverRequest:
    """A single resolution request."""

    id: str
    file: str
    line: int
    column: int


@dataclass
class ResolverResult:
    """Result from the Node.js resolver."""

    id: str
    node_id: str | None
    confidence: EdgeConfidence
    reason: str = ""


class NodeResolverError(Exception):
    """Error from Node.js resolver."""

    pass


class NodeResolver:
    """
    TypeScript resolver using Node.js and TypeScript Compiler API.

    Features:
    - Full type inference (understands useState, method calls, etc.)
    - Batch processing for performance
    - SQLite caching for repeated queries
    - Automatic npm install on first use
    """

    def __init__(self, project_root: Path, cache_path: Path | None = None) -> None:
        self._project_root = project_root.resolve()
        self._node_path = self._find_node()
        self._cache_path = cache_path or (project_root / ".lens" / "resolve_cache.db")
        self._cache_conn: sqlite3.Connection | None = None
        self._initialized = False

        if self._node_path is None:
            raise NodeResolverError("Node.js not found. Please install Node.js >= 18")

        self._ensure_dependencies()

    def _find_node(self) -> str | None:
        """Find Node.js executable."""
        node = shutil.which("node")
        if node:
            # Check version
            try:
                result = subprocess.run(
                    [node, "--version"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                version = result.stdout.strip()
                # v18.0.0 or higher
                if version.startswith("v"):
                    major = int(version[1:].split(".")[0])
                    if major >= 18:
                        return node
                    logger.warning("Node.js %s found but >= 18 required", version)
            except Exception:
                pass
        return None

    def _ensure_dependencies(self) -> None:
        """Ensure TypeScript npm package is installed."""
        node_modules = HELPERS_DIR / "node_modules"
        if not (node_modules / "typescript").exists():
            logger.info("Installing TypeScript npm package...")
            try:
                subprocess.run(
                    ["npm", "install"],
                    cwd=HELPERS_DIR,
                    capture_output=True,
                    timeout=120,
                    check=True,
                )
                logger.info("TypeScript installed successfully")
            except subprocess.CalledProcessError as e:
                raise NodeResolverError(f"Failed to install TypeScript: {e.stderr}")
            except FileNotFoundError:
                raise NodeResolverError("npm not found. Please install Node.js with npm")

    def _init_cache(self) -> None:
        """Initialize SQLite cache."""
        if self._cache_conn is not None:
            return

        self._cache_path.parent.mkdir(parents=True, exist_ok=True)
        self._cache_conn = sqlite3.connect(str(self._cache_path))
        self._cache_conn.execute("""
            CREATE TABLE IF NOT EXISTS resolve_cache (
                cache_key TEXT PRIMARY KEY,
                node_id TEXT,
                confidence TEXT,
                reason TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        self._cache_conn.commit()

    def _cache_key(self, file: str, line: int, column: int) -> str:
        """Generate cache key for a resolution request."""
        data = f"{file}:{line}:{column}"
        return hashlib.md5(data.encode()).hexdigest()

    def _get_cached(self, key: str) -> ResolverResult | None:
        """Get cached result."""
        if self._cache_conn is None:
            self._init_cache()

        cursor = self._cache_conn.execute(
            "SELECT node_id, confidence, reason FROM resolve_cache WHERE cache_key = ?",
            (key,),
        )
        row = cursor.fetchone()
        if row:
            node_id, confidence, reason = row
            return ResolverResult(
                id=key,
                node_id=node_id,
                confidence=EdgeConfidence(confidence),
                reason=reason or "",
            )
        return None

    def _set_cached(self, key: str, result: ResolverResult) -> None:
        """Cache a result."""
        if self._cache_conn is None:
            self._init_cache()

        self._cache_conn.execute(
            """
            INSERT OR REPLACE INTO resolve_cache (cache_key, node_id, confidence, reason)
            VALUES (?, ?, ?, ?)
            """,
            (key, result.node_id, result.confidence.value, result.reason),
        )
        self._cache_conn.commit()

    def resolve(self, file: str, line: int, column: int) -> Resolution:
        """Resolve a name at a specific position.

        Args:
            file: Relative path from project root
            line: 1-based line number
            column: 0-based column number

        Returns:
            Resolution with node_id and confidence
        """
        results = self.resolve_batch([ResolverRequest("0", file, line, column)])
        if results:
            r = results[0]
            return Resolution(
                node_id=r.node_id,
                confidence=r.confidence,
                untracked_reason=r.reason,
            )
        return Resolution(
            node_id=None,
            confidence=EdgeConfidence.UNRESOLVED,
            untracked_reason="no_result",
        )

    def resolve_batch(self, requests: list[ResolverRequest]) -> list[ResolverResult]:
        """Resolve multiple requests in a single Node.js call.

        This is much more efficient than calling resolve() multiple times.

        Args:
            requests: List of resolution requests

        Returns:
            List of resolution results
        """
        if not requests:
            return []

        # Check cache first
        results: dict[str, ResolverResult] = {}
        uncached_requests: list[ResolverRequest] = []

        for req in requests:
            cache_key = self._cache_key(req.file, req.line, req.column)
            cached = self._get_cached(cache_key)
            if cached:
                cached.id = req.id
                results[req.id] = cached
            else:
                uncached_requests.append(req)

        # Process uncached requests via Node.js
        if uncached_requests:
            node_results = self._call_node_resolver(uncached_requests)
            for result in node_results:
                results[result.id] = result
                # Cache the result
                req = next((r for r in uncached_requests if r.id == result.id), None)
                if req:
                    cache_key = self._cache_key(req.file, req.line, req.column)
                    self._set_cached(cache_key, result)

        # Return in original order
        return [results[req.id] for req in requests if req.id in results]

    def _call_node_resolver(
        self, requests: list[ResolverRequest]
    ) -> list[ResolverResult]:
        """Call the Node.js resolver subprocess."""
        input_data = {
            "requests": [
                {
                    "id": req.id,
                    "file": req.file,
                    "line": req.line,
                    "column": req.column,
                }
                for req in requests
            ]
        }

        try:
            result = subprocess.run(
                [self._node_path, str(RESOLVER_SCRIPT), str(self._project_root)],
                input=json.dumps(input_data),
                capture_output=True,
                text=True,
                timeout=60,
            )

            if result.returncode != 0:
                logger.error("Node resolver error: %s", result.stderr)
                # Return unresolved for all requests
                return [
                    ResolverResult(
                        id=req.id,
                        node_id=None,
                        confidence=EdgeConfidence.UNRESOLVED,
                        reason="node_error",
                    )
                    for req in requests
                ]

            output = json.loads(result.stdout)

            if "error" in output:
                logger.error("Node resolver error: %s", output["error"])
                return [
                    ResolverResult(
                        id=req.id,
                        node_id=None,
                        confidence=EdgeConfidence.UNRESOLVED,
                        reason=output["error"],
                    )
                    for req in requests
                ]

            # Parse results
            results = []
            for r in output.get("results", []):
                confidence = self._parse_confidence(r.get("confidence", "unresolved"))
                results.append(
                    ResolverResult(
                        id=r["id"],
                        node_id=r.get("nodeId"),
                        confidence=confidence,
                        reason=r.get("reason", ""),
                    )
                )
            return results

        except subprocess.TimeoutExpired:
            logger.error("Node resolver timed out")
            return [
                ResolverResult(
                    id=req.id,
                    node_id=None,
                    confidence=EdgeConfidence.UNRESOLVED,
                    reason="timeout",
                )
                for req in requests
            ]
        except json.JSONDecodeError as e:
            logger.error("Failed to parse Node resolver output: %s", e)
            return [
                ResolverResult(
                    id=req.id,
                    node_id=None,
                    confidence=EdgeConfidence.UNRESOLVED,
                    reason="parse_error",
                )
                for req in requests
            ]
        except Exception as e:
            logger.error("Node resolver failed: %s", e)
            return [
                ResolverResult(
                    id=req.id,
                    node_id=None,
                    confidence=EdgeConfidence.UNRESOLVED,
                    reason=str(e),
                )
                for req in requests
            ]

    def _parse_confidence(self, value: str) -> EdgeConfidence:
        """Parse confidence string to EdgeConfidence enum."""
        mapping = {
            "resolved": EdgeConfidence.RESOLVED,
            "external": EdgeConfidence.EXTERNAL,
            "inferred": EdgeConfidence.INFERRED,
            "unresolved": EdgeConfidence.UNRESOLVED,
        }
        return mapping.get(value.lower(), EdgeConfidence.UNRESOLVED)

    def get_stats(self) -> dict[str, Any]:
        """Get resolver statistics."""
        # Call Node.js for stats
        try:
            result = subprocess.run(
                [self._node_path, str(RESOLVER_SCRIPT), str(self._project_root)],
                input='{"command": "stats"}',
                capture_output=True,
                text=True,
                timeout=30,
            )
            if result.returncode == 0:
                return json.loads(result.stdout)
        except Exception:
            pass

        return {"error": "failed_to_get_stats"}

    def clear_cache(self) -> None:
        """Clear the resolution cache."""
        if self._cache_conn:
            self._cache_conn.execute("DELETE FROM resolve_cache")
            self._cache_conn.commit()

    def close(self) -> None:
        """Close cache connection."""
        if self._cache_conn:
            self._cache_conn.close()
            self._cache_conn = None


def is_node_available() -> bool:
    """Check if Node.js is available and meets version requirements."""
    node = shutil.which("node")
    if not node:
        return False

    try:
        result = subprocess.run(
            [node, "--version"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        version = result.stdout.strip()
        if version.startswith("v"):
            major = int(version[1:].split(".")[0])
            return major >= 18
    except Exception:
        pass

    return False
