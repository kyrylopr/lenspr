"""TypeScript/JavaScript parser using tree-sitter.

Supports: .js, .jsx, .ts, .tsx (including React components)
With cross-file resolution via TypeScriptResolver.
"""

from __future__ import annotations

import logging
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any

from lenspr.models import (
    Edge,
    EdgeConfidence,
    EdgeSource,
    EdgeType,
    Node,
    NodeType,
    Resolution,
)
from lenspr.parsers.base import BaseParser, ProgressCallback

if TYPE_CHECKING:
    from lenspr.parsers.ts_resolver import TypeScriptResolver

logger = logging.getLogger(__name__)

# Try to import tree-sitter (optional dependency)
try:
    import tree_sitter_javascript as ts_js
    import tree_sitter_typescript as ts_ts
    from tree_sitter import Language, Parser

    TREE_SITTER_AVAILABLE = True
except ImportError:
    TREE_SITTER_AVAILABLE = False
    ts_js = None  # type: ignore
    ts_ts = None  # type: ignore
    Language = None  # type: ignore
    Parser = None  # type: ignore


def _edge_id() -> str:
    """Generate a unique edge ID."""
    return uuid.uuid4().hex[:12]


def _module_id_from_path(file_path: Path, root_path: Path) -> str:
    """Convert a file path to a module-style ID."""
    rel = file_path.relative_to(root_path)
    # Remove extension and convert slashes to dots
    parts = list(rel.with_suffix("").parts)
    # Handle index files (like __init__.py in Python)
    if parts and parts[-1] == "index":
        parts = parts[:-1]
    return ".".join(parts) if parts else rel.stem


class TypeScriptParser(BaseParser):
    """
    TypeScript/JavaScript parser using tree-sitter.

    Handles .js, .jsx, .ts, .tsx files including React components.
    Error-tolerant: can parse files with syntax errors (partial AST).

    Features:
    - Cross-file resolution via Node.js TypeScript Compiler API (if available)
    - Fallback to Python-based resolver
    - tsconfig.json path alias support
    - Export tracking for RESOLVED confidence
    """

    def __init__(self, use_node_resolver: bool = True) -> None:
        if not TREE_SITTER_AVAILABLE:
            raise ImportError(
                "tree-sitter not installed. Install with: pip install 'lenspr[typescript]'"
            )

        # Initialize parsers for each language
        self._js_parser = Parser(Language(ts_js.language()))
        self._ts_parser = Parser(Language(ts_ts.language_typescript()))
        self._tsx_parser = Parser(Language(ts_ts.language_tsx()))

        # Resolvers for cross-file resolution (initialized in set_project_root)
        self._python_resolver: TypeScriptResolver | None = None
        self._node_resolver: Any = None  # NodeResolver if available
        self._lsp_resolver: Any = None  # TsServerResolver if available
        self._use_node_resolver = use_node_resolver
        self._project_root: Path | None = None

        # For backward compatibility
        self._resolver: TypeScriptResolver | None = None

    def get_file_extensions(self) -> list[str]:
        return [".js", ".jsx", ".ts", ".tsx"]

    def set_project_root(self, root_path: Path) -> None:
        """Initialize resolvers with project root for cross-file resolution."""
        from lenspr.parsers.ts_resolver import TypeScriptResolver

        self._project_root = root_path

        # Always init Python resolver (for export tracking)
        self._python_resolver = TypeScriptResolver(root_path)
        self._resolver = self._python_resolver  # Backward compat

        # Try to init Node resolver for full type inference
        if self._use_node_resolver:
            try:
                from lenspr.parsers.node_resolver import NodeResolver, is_node_available

                if is_node_available():
                    self._node_resolver = NodeResolver(root_path)
                    logger.info("TypeScriptParser: Node.js resolver enabled")
                else:
                    logger.debug("Node.js not available, using Python resolver")
            except Exception as e:
                logger.warning("Failed to init Node resolver: %s", e)

        # Try LSP-based tsserver resolver (fallback when Node resolver unavailable)
        if self._node_resolver is None:
            try:
                from lenspr.resolvers.tsserver_resolver import (
                    TsServerResolver,
                    is_tsserver_available,
                )

                if is_tsserver_available():
                    self._lsp_resolver = TsServerResolver(root_path)
                    logger.info("TypeScriptParser: LSP tsserver resolver enabled")
                else:
                    logger.debug("typescript-language-server not available")
            except Exception as e:
                logger.warning("Failed to init TsServer resolver: %s", e)

        logger.debug("TypeScriptParser: resolver initialized for %s", root_path)

    def _get_parser(self, file_path: Path) -> Parser:
        """Get the appropriate parser for the file extension."""
        ext = file_path.suffix.lower()
        if ext == ".tsx":
            return self._tsx_parser
        elif ext == ".ts":
            return self._ts_parser
        else:  # .js, .jsx
            return self._js_parser

    def parse_file(
        self, file_path: Path, root_path: Path
    ) -> tuple[list[Node], list[Edge]]:
        """Parse a single JS/TS file into nodes and edges."""
        try:
            source = file_path.read_bytes()
            source_text = source.decode("utf-8", errors="replace")
        except Exception as e:
            logger.warning("Failed to read %s: %s", file_path, e)
            return [], []

        parser = self._get_parser(file_path)
        tree = parser.parse(source)

        module_id = _module_id_from_path(file_path, root_path)
        rel_path = str(file_path.relative_to(root_path))
        source_lines = source_text.splitlines()

        # Create module node
        module_node = Node(
            id=module_id,
            type=NodeType.MODULE,
            name=file_path.stem,
            qualified_name=module_id,
            file_path=rel_path,
            start_line=1,
            end_line=len(source_lines),
            source_code=source_text,
            metadata={"language": file_path.suffix[1:]},  # js, jsx, ts, tsx
        )

        # Extract nodes and edges from AST
        # Pass raw bytes so _get_text uses byte offsets correctly (avoids
        # multi-byte UTF-8 char mismatch between tree-sitter byte offsets
        # and Python str character offsets).
        visitor = _TreeSitterVisitor(source_text, source, source_lines, module_id, rel_path)
        visitor.visit(tree.root_node)

        all_nodes = [module_node] + visitor.nodes
        all_edges = visitor.edges

        # Register exports with resolver if available
        if self._resolver is not None:
            exports = visitor.get_exports()
            if exports:
                self._resolver.register_exports(rel_path, exports)

        return all_nodes, all_edges

    def resolve_name(
        self, file_path: str, line: int, column: int, project_root: str
    ) -> Resolution:
        """Resolve a name at a specific location using the Node.js resolver.

        Uses TypeScript Compiler API for full type inference when Node.js is available.
        Falls back to Python resolver otherwise.
        """
        # Try Node.js resolver first (has full type inference)
        if self._node_resolver is not None:
            try:
                return self._node_resolver.resolve(file_path, line, column)
            except Exception as e:
                logger.debug("Node resolver failed: %s", e)

        # Fall back to Python resolver
        if self._python_resolver is not None:
            return self._python_resolver.resolve(
                from_node="",  # Not needed for position-based resolution
                call_name=file_path,  # This API doesn't match well, but try
                imports={},
            )

        return Resolution(
            node_id=None,
            confidence=EdgeConfidence.UNRESOLVED,
            untracked_reason="resolver_not_initialized",
        )

    def parse_project(
        self,
        root_path: Path,
        progress_callback: ProgressCallback | None = None,
    ) -> tuple[list[Node], list[Edge]]:
        """Parse project with cross-file resolution.

        Two-pass approach:
        1. Parse all files, collect nodes/edges, register exports
        2. Resolve edges using collected exports
        """
        # Initialize resolver
        self.set_project_root(root_path)

        # First pass: parse all files
        all_nodes, all_edges = super().parse_project(root_path, progress_callback)

        # Second pass: resolve edges using Node.js resolver if available
        resolved_edges = self._resolve_edges(all_edges)

        return all_nodes, resolved_edges

    def resolve_edges(self, edges: list[Edge], root_path: Path) -> list[Edge]:
        """Post-parse edge resolution using Node.js TypeScript resolver.

        Called by MultiParser after all files are parsed.
        Uses TypeScript Compiler API for full cross-file type inference.
        """
        # Ensure resolver is initialized
        if self._project_root != root_path:
            self.set_project_root(root_path)
        return self._resolve_edges(edges)

    def _resolve_edges(self, edges: list[Edge]) -> list[Edge]:
        """Resolve edges using best available resolver.

        Priority: Node.js resolver > LSP tsserver > Python resolver.
        """
        # Try Node.js resolver first (batch mode for performance)
        if self._node_resolver is not None:
            resolved = self._resolve_edges_with_node(edges)
        elif self._lsp_resolver is not None:
            resolved = self._resolve_edges_with_lsp(edges)
        elif self._python_resolver is not None:
            resolved = self._resolve_edges_with_python(edges)
        else:
            resolved = edges

        # Post-processing: CSS module property accesses (e.g. ./Auth.module.css.formInput)
        # are not function calls — mark them EXTERNAL so they don't inflate "unresolved".
        css_exts = (".module.css.", ".module.scss.", ".module.sass.", ".module.less.")
        result = []
        for edge in resolved:
            if (
                edge.confidence == EdgeConfidence.INFERRED
                and any(ext in edge.to_node for ext in css_exts)
            ):
                result.append(Edge(
                    id=edge.id,
                    from_node=edge.from_node,
                    to_node=edge.to_node,
                    type=edge.type,
                    line_number=edge.line_number,
                    confidence=EdgeConfidence.EXTERNAL,
                    source=edge.source,
                    metadata=edge.metadata,
                ))
            else:
                result.append(edge)
        return result

    def _resolve_edges_with_node(self, edges: list[Edge]) -> list[Edge]:
        """Resolve edges using Node.js TypeScript resolver (batch mode)."""
        from lenspr.parsers.node_resolver import ResolverRequest

        # Collect edges that need resolution
        call_edges = [
            e for e in edges
            if e.type == EdgeType.CALLS and e.confidence == EdgeConfidence.INFERRED
        ]

        if not call_edges:
            return edges

        # Build batch requests
        requests = []
        edge_map = {}  # request_id -> edge

        for i, edge in enumerate(call_edges):
            # Get file and column from metadata
            file_path = edge.metadata.get("file") if edge.metadata else None
            column = edge.metadata.get("column", 0) if edge.metadata else 0

            if file_path:
                req_id = str(i)
                requests.append(ResolverRequest(
                    id=req_id,
                    file=file_path,
                    line=edge.line_number,
                    column=column,
                ))
                edge_map[req_id] = edge

        if not requests:
            return edges

        # Resolve in batch
        try:
            results = self._node_resolver.resolve_batch(requests)
        except Exception as e:
            logger.warning("Node resolver batch failed: %s", e)
            return self._resolve_edges_with_python(edges)

        # Build result map
        result_map = {r.id: r for r in results}

        # Update edges with resolved confidence
        resolved = []
        call_edge_ids = {id(e) for e in call_edges}

        for edge in edges:
            if id(edge) in call_edge_ids:
                req_id = next(
                    (k for k, v in edge_map.items() if id(v) == id(edge)),
                    None
                )
                if req_id and req_id in result_map:
                    result = result_map[req_id]
                    if result.confidence in (EdgeConfidence.RESOLVED, EdgeConfidence.EXTERNAL):
                        resolved.append(Edge(
                            id=edge.id,
                            from_node=edge.from_node,
                            to_node=result.node_id or edge.to_node,
                            type=edge.type,
                            line_number=edge.line_number,
                            confidence=result.confidence,
                            source=edge.source,
                            metadata=edge.metadata,
                        ))
                        continue

            resolved.append(edge)

        return resolved

    def _resolve_edges_with_lsp(self, edges: list[Edge]) -> list[Edge]:
        """Resolve edges using LSP tsserver (go-to-definition)."""
        try:
            edges = self._lsp_resolver.resolve_edges(edges)
        except Exception as e:
            logger.warning("TsServer LSP resolver failed: %s", e)
        return edges

    def _resolve_edges_with_python(self, edges: list[Edge]) -> list[Edge]:
        """Resolve edges using Python TypeScriptResolver (fallback)."""
        if self._python_resolver is None:
            return edges

        resolved = []
        for edge in edges:
            if edge.type == EdgeType.CALLS and edge.confidence == EdgeConfidence.INFERRED:
                resolution = self._python_resolver.resolve_call(
                    from_node=edge.from_node,
                    call_name=edge.to_node,
                    imports={},
                )
                if resolution.confidence in (EdgeConfidence.RESOLVED, EdgeConfidence.EXTERNAL):
                    resolved.append(Edge(
                        id=edge.id,
                        from_node=edge.from_node,
                        to_node=resolution.node_id or edge.to_node,
                        type=edge.type,
                        line_number=edge.line_number,
                        confidence=resolution.confidence,
                        source=edge.source,
                        metadata=edge.metadata,
                    ))
                else:
                    resolved.append(edge)
            else:
                resolved.append(edge)

        return resolved

    def get_resolver_stats(self) -> dict[str, int]:
        """Get resolver statistics."""
        if self._python_resolver is None:
            return {"tracked_files": 0, "total_exports": 0, "cache_size": 0}
        stats = self._python_resolver.get_stats()
        stats["node_resolver_enabled"] = self._node_resolver is not None
        return stats


# ---------------------------------------------------------------------------
# _TreeSitterVisitor — split into core + 2 mixins for maintainability
# ---------------------------------------------------------------------------


class _ImportExportMixin:
    """Handles import/export statement processing.

    Mixin for _TreeSitterVisitor — relies on self.module_id, self._imports,
    self._in_export, self.edges, self._get_text(), self._register_export(),
    self.visit(), self._extract_import_names() being available.
    """

    def visit_import_statement(self, node: Any) -> None:  # type: ignore
        """Handle: import x from 'module'"""
        source = None
        for child in node.children:
            if child.type == "string":
                source = self._get_text(child).strip("'\"")

        if source:
            self.edges.append(
                Edge(
                    id=_edge_id(),
                    from_node=self.module_id,
                    to_node=source,
                    type=EdgeType.IMPORTS,
                    line_number=node.start_point[0] + 1,
                    confidence=EdgeConfidence.RESOLVED,
                    source=EdgeSource.STATIC,
                )
            )

            # Track imported names
            for child in node.children:
                if child.type == "import_clause":
                    self._extract_import_names(child, source)

        for child in node.children:
            self.visit(child)

    def visit_export_statement(self, node: Any) -> None:  # type: ignore
        """Handle: export { x } from 'module' or export default ..."""
        # Check for re-exports
        source = None
        for child in node.children:
            if child.type == "string":
                source = self._get_text(child).strip("'\"")

        if source:
            self.edges.append(
                Edge(
                    id=_edge_id(),
                    from_node=self.module_id,
                    to_node=source,
                    type=EdgeType.IMPORTS,
                    line_number=node.start_point[0] + 1,
                    confidence=EdgeConfidence.RESOLVED,
                    source=EdgeSource.STATIC,
                )
            )

        # Check for default export
        is_default = any(
            child.type == "default" or self._get_text(child) == "default"
            for child in node.children
        )

        # Set export context for child declarations
        self._in_export = True

        # Visit children for exported declarations
        for child in node.children:
            if child.type in (
                "function_declaration",
                "class_declaration",
                "lexical_declaration",
            ):
                self.visit(child)
            elif child.type == "export_clause":
                # Handle: export { foo, bar as baz }
                self._extract_export_names(child)
            elif child.type == "identifier":
                # Handle: export default foo
                name = self._get_text(child)
                self._register_export(name, f"{self.module_id}.{name}", is_default)

        self._in_export = False

    def _extract_export_names(self, node: Any) -> None:  # type: ignore
        """Extract names from export clause: export { foo, bar as baz }"""
        for child in node.children:
            if child.type == "export_specifier":
                name = None
                for part in child.children:
                    if part.type == "identifier":
                        if name is None:
                            name = self._get_text(part)
                        # If there's an alias, the second identifier is the export name
                if name:
                    self._register_export(name, f"{self.module_id}.{name}")

    def _extract_import_names(
        self, node: Any, source: str  # type: ignore
    ) -> None:
        """Extract imported names from import clause."""
        for child in node.children:
            if child.type == "identifier":
                # Default import: import X from '...'
                name = self._get_text(child)
                self._imports[name] = source
            elif child.type == "named_imports":
                # Named imports: import { X, Y as Z } from '...'
                for spec in child.children:
                    if spec.type == "import_specifier":
                        local_name = None
                        imported_name = None
                        for part in spec.children:
                            if part.type == "identifier":
                                if imported_name is None:
                                    imported_name = self._get_text(part)
                                else:
                                    local_name = self._get_text(part)
                        if imported_name:
                            self._imports[local_name or imported_name] = (
                                f"{source}.{imported_name}"
                            )
            elif child.type == "namespace_import":
                # Namespace import: import * as X from '...'
                for part in child.children:
                    if part.type == "identifier":
                        name = self._get_text(part)
                        self._imports[name] = source


class _DeclarationMixin:
    """Handles function, variable, class, and JSX declarations.

    Mixin for _TreeSitterVisitor — relies on self attributes and core
    methods being available from _TreeSitterVisitor.
    """

    # === Function handling ===

    def visit_function_declaration(self, node: Any) -> None:  # type: ignore
        """Handle: function name() { ... }"""
        self._handle_function(node, is_async=False)

    def visit_generator_function_declaration(
        self, node: Any  # type: ignore
    ) -> None:
        """Handle: function* name() { ... }"""
        self._handle_function(node, is_async=False, is_generator=True)

    def _handle_function(
        self,
        node: Any,  # type: ignore
        is_async: bool = False,
        is_generator: bool = False,
        name_override: str | None = None,
    ) -> None:
        """Extract function node and its call edges."""
        # Get function name
        name = name_override
        if not name:
            for child in node.children:
                if child.type == "identifier":
                    name = self._get_text(child)
                    break

        if not name:
            return  # Anonymous function at module level, skip

        node_id = self._push_scope(name)
        start_line = node.start_point[0] + 1
        end_line = node.end_point[0] + 1
        source = self._get_source_segment(start_line, end_line)

        # Detect React component (PascalCase function returning JSX)
        is_react_component = name[0].isupper() and self._returns_jsx(node)

        self.nodes.append(
            Node(
                id=node_id,
                type=NodeType.FUNCTION,
                name=name,
                qualified_name=node_id,
                file_path=self.file_path,
                start_line=start_line,
                end_line=end_line,
                source_code=source,
                signature=self._get_function_signature(node, name),
                metadata={
                    "is_async": is_async or self._is_async(node),
                    "is_generator": is_generator,
                    "is_react_component": is_react_component,
                    "is_exported": self._in_export,
                },
            )
        )

        # Register export if in export context
        if self._in_export:
            self._register_export(name, node_id)

        # Extract calls from function body
        self._extract_calls(node, node_id)

        # Visit children (for nested functions)
        for child in node.children:
            if child.type == "statement_block":
                self.visit(child)

        self._pop_scope()

    def _is_async(self, node: Any) -> bool:  # type: ignore
        """Check if function is async."""
        for child in node.children:
            if child.type == "async":
                return True
        return False

    def _returns_jsx(self, node: Any) -> bool:  # type: ignore
        """Check if function returns JSX (React component)."""
        text = self._get_text(node)
        # Simple heuristic: contains JSX-like syntax
        return "<" in text and "/>" in text or "</" in text

    def _get_function_signature(
        self, node: Any, name: str  # type: ignore
    ) -> str:
        """Build function signature string."""
        params = []
        for child in node.children:
            if child.type == "formal_parameters":
                for param in child.children:
                    if param.type in (
                        "identifier",
                        "required_parameter",
                        "optional_parameter",
                        "rest_parameter",
                    ):
                        params.append(self._get_text(param))
                break

        async_prefix = "async " if self._is_async(node) else ""
        return f"{async_prefix}function {name}({', '.join(params)})"

    # === Arrow functions / variables ===

    def visit_lexical_declaration(self, node: Any) -> None:  # type: ignore
        """Handle: const name = () => { ... }"""
        for child in node.children:
            if child.type == "variable_declarator":
                self._handle_variable_declarator(child)

    def visit_variable_declaration(self, node: Any) -> None:  # type: ignore
        """Handle: var/let name = () => { ... }"""
        for child in node.children:
            if child.type == "variable_declarator":
                self._handle_variable_declarator(child)

    def _handle_variable_declarator(
        self, node: Any  # type: ignore
    ) -> None:
        """Handle variable declarator (const x = ...).

        Detects arrow functions, function expressions, and generator functions
        assigned to variables and creates FUNCTION nodes for them.  Also
        unwraps TypeScript wrapper expressions (``as``, ``satisfies``,
        parenthesised) so that patterns like
        ``const fn = (async () => {}) as MyType`` are recognised.
        """
        _FUNC_TYPES = frozenset((
            "arrow_function",
            "function_expression",
            "generator_function",
        ))

        name = None
        value = None

        for child in node.children:
            if child.type == "identifier":
                name = self._get_text(child)
            elif child.type in _FUNC_TYPES:
                value = child
            elif value is None and child.type in (
                "as_expression",
                "satisfies_expression",
                "parenthesized_expression",
            ):
                # Unwrap TS type wrappers to find the underlying function
                value = self._unwrap_to_function(child, _FUNC_TYPES)

        if name and value:
            is_generator = value.type == "generator_function"
            self._handle_function(value, name_override=name, is_generator=is_generator)
        elif name:
            # Check for object literal: const api = { method: () => ... }
            has_object = any(child.type == "object" for child in node.children)
            if has_object:
                self._push_scope(name)
                if self._in_export:
                    self._register_export(name, self._current_scope)
                for child in node.children:
                    self.visit(child)
                self._pop_scope()
            else:
                for child in node.children:
                    self.visit(child)

    def _unwrap_to_function(
        self, node: Any, func_types: frozenset[str]  # type: ignore
    ) -> Any | None:
        """Unwrap TS type wrappers to find an underlying function node.

        Handles ``as_expression``, ``satisfies_expression``, and
        ``parenthesized_expression`` which can wrap arrow functions or
        function expressions in TypeScript.  Recurses at most 5 levels
        deep to avoid pathological nesting.
        """
        for _ in range(5):  # bounded recursion
            if node.type in func_types:
                return node
            if node.type in (
                "as_expression",
                "satisfies_expression",
                "parenthesized_expression",
            ):
                # The function is typically the first meaningful child
                for child in node.children:
                    if child.type in func_types:
                        return child
                    if child.type in (
                        "as_expression",
                        "satisfies_expression",
                        "parenthesized_expression",
                    ):
                        node = child
                        break
                else:
                    return None  # No wrapper child found
            else:
                return None
        return None

    # === Object literal pair handling ===

    def visit_pair(self, node: Any) -> None:  # type: ignore
        """Handle property: value pairs in object literals.

        Detects arrow functions / function expressions assigned to object
        keys like ``{ createItem: (data) => api.post(...) }`` and creates
        FUNCTION nodes for them.
        """
        _FUNC_TYPES = frozenset((
            "arrow_function", "function_expression", "generator_function",
        ))
        name = value = None
        for child in node.children:
            if child.type in ("property_identifier", "string") and name is None:
                name = self._get_text(child).strip("'\"")
            elif child.type in _FUNC_TYPES:
                value = child
        if name and value:
            self._handle_function(
                value,
                name_override=name,
                is_generator=value.type == "generator_function",
            )
        else:
            for child in node.children:
                self.visit(child)

    # === Class handling ===

    def visit_class_declaration(self, node: Any) -> None:  # type: ignore
        """Handle: class Name { ... }"""
        self._handle_class(node)

    def visit_class(self, node: Any) -> None:  # type: ignore
        """Handle class expression."""
        self._handle_class(node)

    def _handle_class(self, node: Any) -> None:  # type: ignore
        """Extract class node and its methods."""
        name = None
        extends = None

        for child in node.children:
            # TypeScript uses type_identifier for class names
            if child.type in ("identifier", "type_identifier") and name is None:
                name = self._get_text(child)
            elif child.type == "class_heritage":
                # extends SomeClass
                for part in child.children:
                    if part.type == "extends_clause":
                        for subpart in part.children:
                            if subpart.type in ("identifier", "type_identifier"):
                                extends = self._get_text(subpart)
                            elif subpart.type == "member_expression":
                                extends = self._get_member_expression(subpart)

        if not name:
            return

        node_id = self._push_scope(name)
        start_line = node.start_point[0] + 1
        end_line = node.end_point[0] + 1
        source = self._get_source_segment(start_line, end_line)

        # Detect React class component
        is_react_component = extends in ("Component", "React.Component", "PureComponent")

        self.nodes.append(
            Node(
                id=node_id,
                type=NodeType.CLASS,
                name=name,
                qualified_name=node_id,
                file_path=self.file_path,
                start_line=start_line,
                end_line=end_line,
                source_code=source,
                metadata={
                    "extends": extends,
                    "is_react_component": is_react_component,
                    "is_exported": self._in_export,
                },
            )
        )

        # Register export if in export context
        if self._in_export:
            self._register_export(name, node_id)

        # Add inheritance edge
        if extends:
            target = self._imports.get(extends, extends)
            self.edges.append(
                Edge(
                    id=_edge_id(),
                    from_node=node_id,
                    to_node=target,
                    type=EdgeType.INHERITS,
                    line_number=start_line,
                    confidence=EdgeConfidence.INFERRED,
                    source=EdgeSource.STATIC,
                )
            )

        # Visit class body for methods
        for child in node.children:
            if child.type == "class_body":
                for member in child.children:
                    if member.type == "method_definition":
                        self._handle_method(member)
                    elif member.type == "field_definition":
                        # Class field with arrow function
                        self._handle_class_field(member)

        self._pop_scope()

    def _handle_method(self, node: Any) -> None:  # type: ignore
        """Extract class method."""
        name = None
        is_static = False
        is_async = False
        is_getter = False
        is_setter = False

        for child in node.children:
            if child.type == "property_identifier":
                name = self._get_text(child)
            elif child.type == "static":
                is_static = True
            elif child.type == "async":
                is_async = True
            elif child.type == "get":
                is_getter = True
            elif child.type == "set":
                is_setter = True

        if not name:
            return

        node_id = self._push_scope(name)
        start_line = node.start_point[0] + 1
        end_line = node.end_point[0] + 1
        source = self._get_source_segment(start_line, end_line)

        self.nodes.append(
            Node(
                id=node_id,
                type=NodeType.METHOD,
                name=name,
                qualified_name=node_id,
                file_path=self.file_path,
                start_line=start_line,
                end_line=end_line,
                source_code=source,
                metadata={
                    "is_static": is_static,
                    "is_async": is_async,
                    "is_getter": is_getter,
                    "is_setter": is_setter,
                },
            )
        )

        # Extract calls
        self._extract_calls(node, node_id)

        self._pop_scope()

    def _handle_class_field(self, node: Any) -> None:  # type: ignore
        """Handle class field (especially arrow function methods)."""
        name = None
        value = None

        for child in node.children:
            if child.type == "property_identifier":
                name = self._get_text(child)
            elif child.type == "arrow_function":
                value = child

        if name and value:
            node_id = self._push_scope(name)
            start_line = node.start_point[0] + 1
            end_line = node.end_point[0] + 1
            source = self._get_source_segment(start_line, end_line)

            self.nodes.append(
                Node(
                    id=node_id,
                    type=NodeType.METHOD,
                    name=name,
                    qualified_name=node_id,
                    file_path=self.file_path,
                    start_line=start_line,
                    end_line=end_line,
                    source_code=source,
                    metadata={"is_arrow": True},
                )
            )

            self._extract_calls(value, node_id)
            self._pop_scope()

    # === JSX handling ===

    def _extract_jsx_prop_refs(self, element_node: Any) -> None:  # type: ignore
        """Extract USES edges from JSX attribute prop references.

        Handles two patterns inside JSX attributes:
        - Bare identifier:      onSave={handleSave}   -> USES edge
        - Member expression:    handler={obj.method}   -> USES edge
        - Arrow/call in prop:   onClick={() => save()} -> already handled by
          _extract_calls in _handle_function, so we skip those here.
        """
        for child in element_node.children:
            if child.type != "jsx_attribute":
                continue

            # Find the jsx_expression value of this attribute
            for attr_child in child.children:
                if attr_child.type != "jsx_expression":
                    continue

                # Look for bare identifier or member_expression references
                for expr_child in attr_child.children:
                    if expr_child.type == "identifier":
                        ref_name = self._get_text(expr_child)
                        if not ref_name:
                            continue

                        # Resolve through imports
                        target = self._imports.get(ref_name, ref_name)

                        self.edges.append(
                            Edge(
                                id=_edge_id(),
                                from_node=self._current_scope,
                                to_node=target,
                                type=EdgeType.USES,
                                line_number=expr_child.start_point[0] + 1,
                                confidence=EdgeConfidence.INFERRED,
                                source=EdgeSource.STATIC,
                                metadata={
                                    "jsx_prop": True,
                                    "column": expr_child.start_point[1],
                                    "file": self.file_path,
                                },
                            )
                        )
                    elif expr_child.type == "member_expression":
                        # Handle dotted references: obj.method
                        ref_name = self._get_member_expression(expr_child)
                        if not ref_name:
                            continue

                        # Resolve first part through imports
                        parts = ref_name.split(".", 1)
                        target = self._imports.get(parts[0], parts[0])
                        if len(parts) > 1:
                            target = f"{target}.{parts[1]}"

                        self.edges.append(
                            Edge(
                                id=_edge_id(),
                                from_node=self._current_scope,
                                to_node=target,
                                type=EdgeType.USES,
                                line_number=expr_child.start_point[0] + 1,
                                confidence=EdgeConfidence.INFERRED,
                                source=EdgeSource.STATIC,
                                metadata={
                                    "jsx_prop": True,
                                    "column": expr_child.start_point[1],
                                    "file": self.file_path,
                                },
                            )
                        )

    def visit_jsx_element(self, node: Any) -> None:  # type: ignore
        """Handle JSX elements as component calls."""
        for child in node.children:
            if child.type in ("jsx_opening_element", "jsx_self_closing_element"):
                for part in child.children:
                    if part.type == "identifier":
                        component_name = self._get_text(part)
                        # PascalCase = custom component, lowercase = HTML
                        if component_name and component_name[0].isupper():
                            target = self._imports.get(component_name, component_name)
                            self.edges.append(
                                Edge(
                                    id=_edge_id(),
                                    from_node=self._current_scope,
                                    to_node=target,
                                    type=EdgeType.CALLS,
                                    line_number=child.start_point[0] + 1,
                                    confidence=EdgeConfidence.INFERRED,
                                    source=EdgeSource.STATIC,
                                    metadata={
                                        "jsx": True,
                                        "column": part.start_point[1],
                                        "file": self.file_path,
                                    },
                                )
                            )
                # Extract prop function references from JSX attributes
                self._extract_jsx_prop_refs(child)
            self.visit(child)

    def visit_jsx_self_closing_element(
        self, node: Any  # type: ignore
    ) -> None:
        """Handle: <Component />"""
        for child in node.children:
            if child.type == "identifier":
                component_name = self._get_text(child)
                if component_name and component_name[0].isupper():
                    target = self._imports.get(component_name, component_name)
                    self.edges.append(
                        Edge(
                            id=_edge_id(),
                            from_node=self._current_scope,
                            to_node=target,
                            type=EdgeType.CALLS,
                            line_number=node.start_point[0] + 1,
                            confidence=EdgeConfidence.INFERRED,
                            source=EdgeSource.STATIC,
                            metadata={
                                "jsx": True,
                                "column": child.start_point[1],
                                "file": self.file_path,
                            },
                        )
                    )
        # Extract prop function references from JSX attributes
        self._extract_jsx_prop_refs(node)


class _TreeSitterVisitor(_ImportExportMixin, _DeclarationMixin):
    """Extracts nodes and edges from a tree-sitter AST.

    Core visitor with scope management, text utilities, call extraction,
    and export tracking. Declaration and import/export handling are in
    _DeclarationMixin and _ImportExportMixin respectively.
    """

    def __init__(
        self,
        source: str,
        source_bytes: bytes,
        source_lines: list[str],
        module_id: str,
        file_path: str,
    ) -> None:
        self.source = source
        self.source_bytes = source_bytes
        self.source_lines = source_lines
        self.module_id = module_id
        self.file_path = file_path
        self.nodes: list[Node] = []
        self.edges: list[Edge] = []
        self._scope_stack: list[str] = [module_id]
        self._imports: dict[str, str] = {}  # local_name -> source_module
        self._exports: list[dict[str, Any]] = []  # Tracked exports
        self._in_export: bool = False  # Flag for export context

    def get_exports(self) -> list[dict[str, Any]]:
        """Return tracked exports for resolver registration."""
        return self._exports

    @property
    def _current_scope(self) -> str:
        return self._scope_stack[-1]

    def _push_scope(self, name: str) -> str:
        node_id = f"{self._current_scope}.{name}"
        self._scope_stack.append(node_id)
        return node_id

    def _pop_scope(self) -> None:
        if len(self._scope_stack) > 1:
            self._scope_stack.pop()

    def _get_text(self, node: Any) -> str:  # type: ignore
        """Get the text content of a node."""
        raw = self.source_bytes[node.start_byte : node.end_byte]
        return raw.decode("utf-8", errors="replace")

    def _get_source_segment(self, start_line: int, end_line: int) -> str:
        """Extract source lines (1-based)."""
        return "\n".join(self.source_lines[start_line - 1 : end_line])

    def visit(self, node: Any) -> None:  # type: ignore
        """Visit a node and its children."""
        method_name = f"visit_{node.type}"
        visitor = getattr(self, method_name, None)

        if visitor:
            visitor(node)
        else:
            # Visit children for unhandled node types
            for child in node.children:
                self.visit(child)

    def _register_export(
        self, name: str, node_id: str, is_default: bool = False
    ) -> None:
        """Register an export for resolver tracking."""
        self._exports.append({
            "name": name,
            "node_id": node_id,
            "is_default": is_default,
            "is_type": False,
        })

    # === Call extraction ===

    def _extract_calls(
        self, node: Any, caller_id: str  # type: ignore
    ) -> None:
        """Extract function call edges from a node."""
        for child in node.children:
            if child.type == "call_expression":
                self._handle_call(child, caller_id)
            else:
                self._extract_calls(child, caller_id)

    def _handle_call(
        self, node: Any, caller_id: str  # type: ignore
    ) -> None:
        """Handle a function call."""
        call_name = None
        call_column = node.start_point[1]  # 0-based column

        for child in node.children:
            if child.type == "identifier":
                call_name = self._get_text(child)
                call_column = child.start_point[1]  # Column of the identifier
            elif child.type == "member_expression":
                call_name = self._get_member_expression(child)
                # Get column from the leftmost identifier
                leftmost = child
                while leftmost.children:
                    if leftmost.children[0].type in ("identifier", "member_expression"):
                        leftmost = leftmost.children[0]
                    else:
                        break
                call_column = leftmost.start_point[1]

        if call_name:
            # Resolve through imports
            target = self._imports.get(call_name.split(".")[0], call_name)
            if target != call_name and "." in call_name:
                # Partial match: imported module, access member
                parts = call_name.split(".", 1)
                target = f"{self._imports.get(parts[0], parts[0])}.{parts[1]}"

            self.edges.append(
                Edge(
                    id=_edge_id(),
                    from_node=caller_id,
                    to_node=target,
                    type=EdgeType.CALLS,
                    line_number=node.start_point[0] + 1,
                    confidence=EdgeConfidence.INFERRED,
                    source=EdgeSource.STATIC,
                    metadata={
                        "column": call_column,
                        "file": self.file_path,
                    },
                )
            )

        # Continue extracting nested calls
        for child in node.children:
            self._extract_calls(child, caller_id)

    def _get_member_expression(self, node: Any) -> str:  # type: ignore
        """Get dotted name from member expression (a.b.c)."""
        parts: list[str] = []

        def collect(n: Any) -> None:  # type: ignore
            if n.type == "identifier":
                parts.append(self._get_text(n))
            elif n.type == "property_identifier":
                parts.append(self._get_text(n))
            elif n.type == "member_expression":
                for child in n.children:
                    collect(child)

        collect(node)
        return ".".join(parts)
