"""Data models for LensPR code graph."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from enum import Enum


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
    CONTAINS = "contains"  # Parent function/class contains nested definition


class EdgeConfidence(Enum):
    """Confidence level for edge resolution."""

    RESOLVED = "resolved"  # Jedi confirmed, target in project
    INFERRED = "inferred"  # AST-based, not confirmed by jedi
    EXTERNAL = "external"  # Stdlib/third-party (known, outside project)
    UNRESOLVED = "unresolved"  # Cannot determine target statically


class EdgeSource(Enum):
    """How the edge was discovered."""

    STATIC = "static"  # Found by parser / AST analysis
    RUNTIME = "runtime"  # Observed during test/production trace
    BOTH = "both"  # Confirmed by both static and runtime


class NodeRole(Enum):
    """Semantic role of a code node."""

    VALIDATOR = "validator"  # Validates input, returns bool/raises
    TRANSFORMER = "transformer"  # Transforms data A → B
    IO = "io"  # Reads/writes external systems (files, network, db)
    ORCHESTRATOR = "orchestrator"  # Coordinates multiple calls
    PURE = "pure"  # No side effects, deterministic
    HANDLER = "handler"  # Handles events/requests
    TEST = "test"  # Test function
    UTILITY = "utility"  # Generic helper
    FACTORY = "factory"  # Creates objects
    ACCESSOR = "accessor"  # Gets/sets properties


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
    docstring: str | None = None
    signature: str | None = None  # For functions/methods
    hash: str = ""  # SHA256 of source_code, computed automatically
    metadata: dict = field(default_factory=dict)

    # Semantic annotations (optional, populated by lens_annotate/lens_save_annotation)
    summary: str | None = None  # Short description of what this does
    role: NodeRole | None = None  # Semantic role (validator, transformer, etc.)
    side_effects: list[str] = field(default_factory=list)  # e.g. ["writes_file", "network_io"]
    semantic_inputs: list[str] = field(default_factory=list)  # e.g. ["user_input", "config"]
    semantic_outputs: list[str] = field(default_factory=list)  # e.g. ["validated_data"]
    annotation_hash: str | None = None  # Hash of source when annotation was created

    def __post_init__(self) -> None:
        if not self.hash:
            self.hash = self.compute_hash()

    def compute_hash(self) -> str:
        """Compute SHA256 hash of source code for change detection."""
        return hashlib.sha256(self.source_code.encode("utf-8")).hexdigest()

    @property
    def is_annotated(self) -> bool:
        """Check if this node has any semantic annotations."""
        return self.summary is not None or self.role is not None

    @property
    def is_annotation_stale(self) -> bool:
        """Check if annotation was made on older version of source."""
        if not self.is_annotated or not self.annotation_hash:
            return False
        return self.annotation_hash != self.hash

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
            # Annotation fields
            "summary": self.summary,
            "role": self.role.value if self.role else None,
            "side_effects": (
                json.dumps(self.side_effects) if self.side_effects else None
            ),
            "semantic_inputs": (
                json.dumps(self.semantic_inputs) if self.semantic_inputs else None
            ),
            "semantic_outputs": (
                json.dumps(self.semantic_outputs) if self.semantic_outputs else None
            ),
            "annotation_hash": self.annotation_hash,
        }

    @classmethod
    def from_dict(cls, data: dict) -> Node:
        """Deserialize from dictionary."""
        # Parse JSON lists for annotations
        side_effects = data.get("side_effects")
        if isinstance(side_effects, str):
            side_effects = json.loads(side_effects) if side_effects else []
        semantic_inputs = data.get("semantic_inputs")
        if isinstance(semantic_inputs, str):
            semantic_inputs = json.loads(semantic_inputs) if semantic_inputs else []
        semantic_outputs = data.get("semantic_outputs")
        if isinstance(semantic_outputs, str):
            semantic_outputs = json.loads(semantic_outputs) if semantic_outputs else []

        # Parse role enum
        role_value = data.get("role")
        role = NodeRole(role_value) if role_value else None

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
            metadata=(
                json.loads(data["metadata"])
                if isinstance(data.get("metadata"), str)
                else data.get("metadata", {})
            ),
            # Annotation fields
            summary=data.get("summary"),
            role=role,
            side_effects=side_effects or [],
            semantic_inputs=semantic_inputs or [],
            semantic_outputs=semantic_outputs or [],
            annotation_hash=data.get("annotation_hash"),
        )


@dataclass
class Edge:
    """A relationship between two nodes in the code graph."""

    id: str
    from_node: str  # Source node ID
    to_node: str  # Target node ID
    type: EdgeType
    line_number: int | None = None  # Where the relationship occurs in source
    column: int | None = None  # Column offset for precise jedi resolution
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
            "column": self.column,
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
            column=data.get("column"),
            confidence=EdgeConfidence(data.get("confidence", "resolved")),
            source=EdgeSource(data.get("source", "static")),
            untracked_reason=data.get("untracked_reason", ""),
            metadata=(
                json.loads(data["metadata"])
                if isinstance(data.get("metadata"), str)
                else data.get("metadata", {})
            ),
        )


@dataclass
class Change:
    """A recorded change to the graph."""

    id: int
    timestamp: str  # ISO 8601
    node_id: str
    action: str  # "created" | "modified" | "deleted"
    old_source: str | None = None
    new_source: str | None = None
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

    node_id: str | None
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
    error: str | None = None


@dataclass
class ToolResponse:
    """Structured response from a Claude tool call."""

    success: bool
    data: dict | None = None
    error: str | None = None
    hint: str | None = None
    warnings: list[str] = field(default_factory=list)
    affected_nodes: list[str] = field(default_factory=list)
    diff: str | None = None


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
