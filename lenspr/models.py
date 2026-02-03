"""Data models for LensPR code graph."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class NodeType(Enum):
    """Types of code graph nodes."""

    MODULE = "module"
    CLASS = "class"
    FUNCTION = "function"
    METHOD = "method"
    BLOCK = "block"  # Module-level statements (constants, type aliases, guards, etc.)


class EdgeType(Enum):
    """Types of relationships between nodes."""

    CALLS = "calls"
    IMPORTS = "imports"
    INHERITS = "inherits"
    USES = "uses"
    DECORATES = "decorates"


class EdgeConfidence(Enum):
    """Confidence level for edge resolution."""

    RESOLVED = "resolved"  # Direct import + call, high certainty
    INFERRED = "inferred"  # Type hint or star import, medium certainty
    UNRESOLVED = "unresolved"  # Cannot determine target statically


class EdgeSource(Enum):
    """How the edge was discovered."""

    STATIC = "static"  # Found by parser / AST analysis
    RUNTIME = "runtime"  # Observed during test/production trace
    BOTH = "both"  # Confirmed by both static and runtime


@dataclass
class Node:
    """A unit of code in the graph (function, class, module, or block)."""

    id: str  # Unique identifier, e.g. "payments.processor.process_payment"
    type: NodeType
    name: str  # Short name, e.g. "process_payment"
    qualified_name: str  # Full dotted path
    file_path: str  # Relative path from project root
    start_line: int
    end_line: int
    source_code: str
    docstring: Optional[str] = None
    signature: Optional[str] = None  # For functions/methods
    hash: str = ""  # SHA256 of source_code, computed automatically
    metadata: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.hash:
            self.hash = self.compute_hash()

    def compute_hash(self) -> str:
        """Compute SHA256 hash of source code for change detection."""
        return hashlib.sha256(self.source_code.encode("utf-8")).hexdigest()

    def to_dict(self) -> dict:
        """Serialize to dictionary for database storage."""
        return {
            "id": self.id,
            "type": self.type.value,
            "name": self.name,
            "qualified_name": self.qualified_name,
            "file_path": self.file_path,
            "start_line": self.start_line,
            "end_line": self.end_line,
            "source_code": self.source_code,
            "docstring": self.docstring,
            "signature": self.signature,
            "hash": self.hash,
            "metadata": json.dumps(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict) -> Node:
        """Deserialize from dictionary."""
        return cls(
            id=data["id"],
            type=NodeType(data["type"]),
            name=data["name"],
            qualified_name=data["qualified_name"],
            file_path=data["file_path"],
            start_line=data["start_line"],
            end_line=data["end_line"],
            source_code=data["source_code"],
            docstring=data.get("docstring"),
            signature=data.get("signature"),
            hash=data.get("hash", ""),
            metadata=json.loads(data["metadata"]) if isinstance(data.get("metadata"), str) else data.get("metadata", {}),
        )


@dataclass
class Edge:
    """A relationship between two nodes in the code graph."""

    id: str
    from_node: str  # Source node ID
    to_node: str  # Target node ID
    type: EdgeType
    line_number: Optional[int] = None  # Where the relationship occurs in source
    confidence: EdgeConfidence = EdgeConfidence.RESOLVED
    source: EdgeSource = EdgeSource.STATIC
    untracked_reason: str = ""  # Why confidence is UNRESOLVED (e.g. "dynamic_call")
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        """Serialize to dictionary for database storage."""
        return {
            "id": self.id,
            "from_node": self.from_node,
            "to_node": self.to_node,
            "type": self.type.value,
            "line_number": self.line_number,
            "confidence": self.confidence.value,
            "source": self.source.value,
            "untracked_reason": self.untracked_reason,
            "metadata": json.dumps(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict) -> Edge:
        """Deserialize from dictionary."""
        return cls(
            id=data["id"],
            from_node=data["from_node"],
            to_node=data["to_node"],
            type=EdgeType(data["type"]),
            line_number=data.get("line_number"),
            confidence=EdgeConfidence(data.get("confidence", "resolved")),
            source=EdgeSource(data.get("source", "static")),
            untracked_reason=data.get("untracked_reason", ""),
            metadata=json.loads(data["metadata"]) if isinstance(data.get("metadata"), str) else data.get("metadata", {}),
        )


@dataclass
class Change:
    """A recorded change to the graph."""

    id: int
    timestamp: str  # ISO 8601
    node_id: str
    action: str  # "created" | "modified" | "deleted"
    old_source: Optional[str] = None
    new_source: Optional[str] = None
    old_hash: str = ""
    new_hash: str = ""
    affected_nodes: list[str] = field(default_factory=list)
    description: str = ""


@dataclass
class Patch:
    """A pending code change to apply to a file."""

    start_line: int
    end_line: int
    new_source: str
    node_id: str = ""


@dataclass
class Resolution:
    """Result of name resolution."""

    node_id: Optional[str]
    confidence: EdgeConfidence
    untracked_reason: str = ""


@dataclass
class FileAnalysis:
    """Analysis result for a single file."""

    file_path: str
    total_calls: int
    resolved_calls: int
    untracked_calls: int
    confidence: float  # resolved / total (0.0 - 1.0)
    issues: list[str] = field(default_factory=list)


@dataclass
class ProjectHealth:
    """Overall health report for a project's code graph."""

    total_nodes: int
    total_edges: int
    resolved_edges: int
    untracked_edges: int
    overall_confidence: float
    dirty_files: list[FileAnalysis] = field(default_factory=list)
    clean_files: list[FileAnalysis] = field(default_factory=list)
    has_exec: list[str] = field(default_factory=list)
    has_monkey_patching: list[str] = field(default_factory=list)
    has_circular_imports: list[str] = field(default_factory=list)
    has_star_imports: list[str] = field(default_factory=list)


@dataclass
class SyncResult:
    """Result of syncing graph with filesystem."""

    added: list[Node] = field(default_factory=list)
    modified: list[Node] = field(default_factory=list)
    deleted: list[Node] = field(default_factory=list)


@dataclass
class RenameResult:
    """Result of a cross-file rename operation."""

    success: bool
    files_modified: int = 0
    references_updated: int = 0
    needs_review: list[dict] = field(default_factory=list)  # String matches not auto-renamed
    error: Optional[str] = None


@dataclass
class ToolResponse:
    """Structured response from a Claude tool call."""

    success: bool
    data: Optional[dict] = None
    error: Optional[str] = None
    hint: Optional[str] = None
    warnings: list[str] = field(default_factory=list)
    affected_nodes: list[str] = field(default_factory=list)
    diff: Optional[str] = None


# Custom exceptions


class LensError(Exception):
    """Base exception for LensPR errors."""


class NotInitializedError(LensError):
    """Raised when operating on a project without .lens/ directory."""


class NodeNotFoundError(LensError):
    """Raised when a node ID does not exist in the graph."""


class SyntaxValidationError(LensError):
    """Raised when generated/patched code is not valid Python."""


class PatchError(LensError):
    """Raised when a patch cannot be applied."""
