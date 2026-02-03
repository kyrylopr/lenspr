"""Python parser: converts Python source files into graph nodes and edges."""

from __future__ import annotations

import ast
import logging
import uuid
from pathlib import Path

import jedi

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

logger = logging.getLogger(__name__)

# AST node types that indicate dynamic/untrackable patterns
_DYNAMIC_CALL_NAMES = {"exec", "eval", "globals", "locals", "getattr", "setattr", "delattr"}

# Python stdlib modules (common subset for fast detection)
_STDLIB_MODULES = {
    "abc", "aifc", "argparse", "array", "ast", "asynchat", "asyncio", "asyncore",
    "atexit", "audioop", "base64", "bdb", "binascii", "binhex", "bisect",
    "builtins", "bz2", "calendar", "cgi", "cgitb", "chunk", "cmath", "cmd",
    "code", "codecs", "codeop", "collections", "colorsys", "compileall",
    "concurrent", "configparser", "contextlib", "contextvars", "copy", "copyreg",
    "cProfile", "crypt", "csv", "ctypes", "curses", "dataclasses", "datetime",
    "dbm", "decimal", "difflib", "dis", "distutils", "doctest", "email",
    "encodings", "enum", "errno", "faulthandler", "fcntl", "filecmp", "fileinput",
    "fnmatch", "fractions", "ftplib", "functools", "gc", "getopt", "getpass",
    "gettext", "glob", "graphlib", "grp", "gzip", "hashlib", "heapq", "hmac",
    "html", "http", "idlelib", "imaplib", "imghdr", "imp", "importlib", "inspect",
    "io", "ipaddress", "itertools", "json", "keyword", "lib2to3", "linecache",
    "locale", "logging", "lzma", "mailbox", "mailcap", "marshal", "math",
    "mimetypes", "mmap", "modulefinder", "multiprocessing", "netrc", "nis",
    "nntplib", "numbers", "operator", "optparse", "os", "ossaudiodev", "pathlib",
    "pdb", "pickle", "pickletools", "pipes", "pkgutil", "platform", "plistlib",
    "poplib", "posix", "posixpath", "pprint", "profile", "pstats", "pty", "pwd",
    "py_compile", "pyclbr", "pydoc", "queue", "quopri", "random", "re",
    "readline", "reprlib", "resource", "rlcompleter", "runpy", "sched", "secrets",
    "select", "selectors", "shelve", "shlex", "shutil", "signal", "site",
    "smtpd", "smtplib", "sndhdr", "socket", "socketserver", "spwd", "sqlite3",
    "ssl", "stat", "statistics", "string", "stringprep", "struct", "subprocess",
    "sunau", "symtable", "sys", "sysconfig", "syslog", "tabnanny", "tarfile",
    "telnetlib", "tempfile", "termios", "test", "textwrap", "threading", "time",
    "timeit", "tkinter", "token", "tokenize", "tomllib", "trace", "traceback",
    "tracemalloc", "tty", "turtle", "turtledemo", "types", "typing", "unicodedata",
    "unittest", "urllib", "uu", "uuid", "venv", "warnings", "wave", "weakref",
    "webbrowser", "winreg", "winsound", "wsgiref", "xdrlib", "xml", "xmlrpc",
    "zipapp", "zipfile", "zipimport", "zlib", "zoneinfo",
    # Also include typing-related modules
    "typing_extensions",
}

# Python builtins (functions, types, exceptions)
_BUILTINS = {
    "abs", "aiter", "all", "anext", "any", "ascii", "bin", "bool", "breakpoint",
    "bytearray", "bytes", "callable", "chr", "classmethod", "compile", "complex",
    "delattr", "dict", "dir", "divmod", "enumerate", "eval", "exec", "filter",
    "float", "format", "frozenset", "getattr", "globals", "hasattr", "hash",
    "help", "hex", "id", "input", "int", "isinstance", "issubclass", "iter",
    "len", "list", "locals", "map", "max", "memoryview", "min", "next", "object",
    "oct", "open", "ord", "pow", "print", "property", "range", "repr", "reversed",
    "round", "set", "setattr", "slice", "sorted", "staticmethod", "str", "sum",
    "super", "tuple", "type", "vars", "zip",
    # Common exceptions
    "Exception", "BaseException", "ValueError", "TypeError", "KeyError",
    "IndexError", "AttributeError", "RuntimeError", "StopIteration",
    "ImportError", "ModuleNotFoundError", "FileNotFoundError", "IOError",
    "OSError", "PermissionError", "NotImplementedError", "AssertionError",
}


def _is_external(name: str) -> bool:
    """Check if a name refers to stdlib or builtin (external to project)."""
    if not name:
        return False
    # Check if it's a builtin
    if name in _BUILTINS:
        return True
    # Check if the module part is stdlib
    root_module = name.split(".")[0]
    return root_module in _STDLIB_MODULES


def _make_id(parts: list[str]) -> str:
    """Build a dotted node ID from path components."""
    return ".".join(p for p in parts if p)


def _module_id_from_path(file_path: Path, root_path: Path) -> str:
    """Convert a file path to a module-style ID (e.g. 'app.models')."""
    rel = file_path.relative_to(root_path)
    parts = list(rel.with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts) if parts else rel.stem


def _get_signature(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    """Extract function signature as a string."""
    args = node.args
    parts: list[str] = []

    # Positional-only args
    for arg in args.posonlyargs:
        parts.append(arg.arg)
    if args.posonlyargs:
        parts.append("/")

    # Regular positional args
    num_defaults = len(args.defaults)
    num_args = len(args.args)
    for i, arg in enumerate(args.args):
        name = arg.arg
        default_idx = i - (num_args - num_defaults)
        if default_idx >= 0:
            name += "=..."
        parts.append(name)

    # *args
    if args.vararg:
        parts.append(f"*{args.vararg.arg}")
    elif args.kwonlyargs:
        parts.append("*")

    # Keyword-only args
    for i, arg in enumerate(args.kwonlyargs):
        name = arg.arg
        if i < len(args.kw_defaults) and args.kw_defaults[i] is not None:
            name += "=..."
        parts.append(name)

    # **kwargs
    if args.kwarg:
        parts.append(f"**{args.kwarg.arg}")

    prefix = "async " if isinstance(node, ast.AsyncFunctionDef) else ""
    return f"{prefix}def {node.name}({', '.join(parts)})"


def _get_docstring(
    node: ast.AsyncFunctionDef | ast.FunctionDef | ast.ClassDef | ast.Module,
) -> str | None:
    """Extract docstring from a class or function node."""
    return ast.get_docstring(node)


def _get_source_segment(source_lines: list[str], start_line: int, end_line: int) -> str:
    """Extract source code lines (1-based, inclusive)."""
    segment = source_lines[start_line - 1 : end_line]
    return "\n".join(segment)


def _edge_id() -> str:
    """Generate a unique edge ID."""
    return uuid.uuid4().hex[:12]


class _ImportTable:
    """
    Fast resolver: maps local names to qualified module paths within a file.

    Built from import statements during AST traversal.
    Covers ~70% of name resolutions without needing jedi.
    """

    def __init__(self) -> None:
        self.names: dict[str, str] = {}  # local_name → qualified_name
        self.star_imports: list[str] = []  # modules imported with *

    def add_import(self, module: str, name: str, alias: str | None = None) -> None:
        local = alias or name
        qualified = f"{module}.{name}" if module else name
        self.names[local] = qualified

    def add_module_import(self, module: str, alias: str | None = None) -> None:
        local = alias or module
        self.names[local] = module

    def add_star_import(self, module: str) -> None:
        self.star_imports.append(module)

    def resolve(self, name: str) -> tuple[str, EdgeConfidence] | None:
        """Try to resolve a local name to a qualified name.

        Handles both simple names (e.g., "database") and dotted names
        (e.g., "database.save_annotation") by resolving the first part.
        """
        # Direct match (simple name)
        if name in self.names:
            return self.names[name], EdgeConfidence.RESOLVED

        # Try to resolve dotted names (e.g., "database.save_annotation")
        if "." in name:
            parts = name.split(".", 1)
            first, rest = parts[0], parts[1]
            if first in self.names:
                # Resolve first part and append the rest
                resolved_first = self.names[first]
                return f"{resolved_first}.{rest}", EdgeConfidence.RESOLVED

        # Check if name could come from a star import
        for module in self.star_imports:
            return f"{module}.{name}", EdgeConfidence.INFERRED

        return None


class CodeGraphVisitor(ast.NodeVisitor):
    """
    AST visitor that extracts nodes and edges from a Python file.

    Tracks current scope (module > class > function) to build
    qualified names and properly categorize nested definitions.
    """

    def __init__(
        self, source_lines: list[str], module_id: str, file_path: str
    ) -> None:
        self.source_lines = source_lines
        self.module_id = module_id
        self.file_path = file_path
        self.nodes: list[Node] = []
        self.edges: list[Edge] = []
        self.import_table = _ImportTable()
        self._scope_stack: list[str] = [module_id]
        self._class_stack: list[str] = []

        # Track which lines belong to known nodes (for BLOCK detection)
        self._claimed_lines: set[int] = set()

    @property
    def _current_scope(self) -> str:
        return self._scope_stack[-1]

    def _push_scope(self, name: str) -> str:
        node_id = _make_id([self._current_scope, name])
        self._scope_stack.append(node_id)
        return node_id

    def _pop_scope(self) -> None:
        self._scope_stack.pop()

    def _claim_lines(self, start: int, end: int) -> None:
        for line in range(start, end + 1):
            self._claimed_lines.add(line)

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self.import_table.add_module_import(alias.name, alias.asname)
            self.edges.append(
                Edge(
                    id=_edge_id(),
                    from_node=self.module_id,
                    to_node=alias.name,
                    type=EdgeType.IMPORTS,
                    line_number=node.lineno,
                    confidence=EdgeConfidence.RESOLVED,
                    source=EdgeSource.STATIC,
                )
            )
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        module = node.module or ""
        for alias in node.names:
            if alias.name == "*":
                self.import_table.add_star_import(module)
            else:
                self.import_table.add_import(module, alias.name, alias.asname)
                self.edges.append(
                    Edge(
                        id=_edge_id(),
                        from_node=self.module_id,
                        to_node=f"{module}.{alias.name}",
                        type=EdgeType.IMPORTS,
                        line_number=node.lineno,
                        confidence=EdgeConfidence.RESOLVED,
                        source=EdgeSource.STATIC,
                    )
                )
        self.generic_visit(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        # Capture parent scope BEFORE pushing new scope
        parent_id = self._current_scope

        node_id = self._push_scope(node.name)
        end_line = node.end_lineno or node.lineno
        source = _get_source_segment(self.source_lines, node.lineno, end_line)

        self.nodes.append(
            Node(
                id=node_id,
                type=NodeType.CLASS,
                name=node.name,
                qualified_name=node_id,
                file_path=self.file_path,
                start_line=node.lineno,
                end_line=end_line,
                source_code=source,
                docstring=_get_docstring(node),
                metadata={
                    "decorators": [
                        ast.dump(d) for d in node.decorator_list
                    ],
                },
            )
        )
        self._claim_lines(node.lineno, end_line)

        # Add CONTAINS edge if this class is nested inside a function
        if len(self._scope_stack) > 2 and parent_id not in self._class_stack:
            self.edges.append(
                Edge(
                    id=_edge_id(),
                    from_node=parent_id,
                    to_node=node_id,
                    type=EdgeType.CONTAINS,
                    line_number=node.lineno,
                    confidence=EdgeConfidence.RESOLVED,
                    source=EdgeSource.STATIC,
                )
            )

        # Inheritance edges
        for base in node.bases:
            base_name = self._resolve_name_from_ast(base)
            if base_name:
                resolved = self.import_table.resolve(base_name)
                target = resolved[0] if resolved else base_name
                confidence = resolved[1] if resolved else EdgeConfidence.INFERRED
                self.edges.append(
                    Edge(
                        id=_edge_id(),
                        from_node=node_id,
                        to_node=target,
                        type=EdgeType.INHERITS,
                        line_number=node.lineno,
                        confidence=confidence,
                        source=EdgeSource.STATIC,
                    )
                )

        # Decorators
        for decorator in node.decorator_list:
            dec_name = self._resolve_name_from_ast(decorator)
            if dec_name:
                resolved = self.import_table.resolve(dec_name)
                target = resolved[0] if resolved else dec_name
                self.edges.append(
                    Edge(
                        id=_edge_id(),
                        from_node=target,
                        to_node=node_id,
                        type=EdgeType.DECORATES,
                        line_number=decorator.lineno,
                        source=EdgeSource.STATIC,
                    )
                )

        self._class_stack.append(node_id)
        self.generic_visit(node)
        self._class_stack.pop()
        self._pop_scope()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function(node)

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        is_method = len(self._class_stack) > 0
        node_type = NodeType.METHOD if is_method else NodeType.FUNCTION

        # Capture parent scope BEFORE pushing new scope
        parent_id = self._current_scope

        node_id = self._push_scope(node.name)
        end_line = node.end_lineno or node.lineno
        source = _get_source_segment(self.source_lines, node.lineno, end_line)

        self.nodes.append(
            Node(
                id=node_id,
                type=node_type,
                name=node.name,
                qualified_name=node_id,
                file_path=self.file_path,
                start_line=node.lineno,
                end_line=end_line,
                source_code=source,
                docstring=_get_docstring(node),
                signature=_get_signature(node),
                metadata={
                    "is_async": isinstance(node, ast.AsyncFunctionDef),
                    "decorators": [ast.dump(d) for d in node.decorator_list],
                },
            )
        )
        self._claim_lines(node.lineno, end_line)

        # Add CONTAINS edge if this is nested inside a function (not module/class)
        # Check: scope stack >1 (not at module level) AND parent is not a class
        if len(self._scope_stack) > 2 and parent_id not in self._class_stack:
            self.edges.append(
                Edge(
                    id=_edge_id(),
                    from_node=parent_id,
                    to_node=node_id,
                    type=EdgeType.CONTAINS,
                    line_number=node.lineno,
                    confidence=EdgeConfidence.RESOLVED,
                    source=EdgeSource.STATIC,
                )
            )

        # Decorators
        for decorator in node.decorator_list:
            dec_name = self._resolve_name_from_ast(decorator)
            if dec_name:
                resolved = self.import_table.resolve(dec_name)
                target = resolved[0] if resolved else dec_name
                self.edges.append(
                    Edge(
                        id=_edge_id(),
                        from_node=target,
                        to_node=node_id,
                        type=EdgeType.DECORATES,
                        line_number=decorator.lineno,
                        source=EdgeSource.STATIC,
                    )
                )

        # Extract type annotation edges
        self._extract_type_annotations(node, node_id)

        # Extract calls from function body
        self._extract_calls(node, node_id)

        self.generic_visit(node)
        self._pop_scope()

    def _extract_calls(self, func_node: ast.AST, caller_id: str) -> None:
        """Extract function/method call edges from an AST subtree."""
        for node in ast.walk(func_node):
            if not isinstance(node, ast.Call):
                continue

            call_name = self._resolve_name_from_ast(node.func)
            if not call_name:
                continue

            # Get column offset from the call's func node for precise jedi resolution
            col_offset = getattr(node.func, "col_offset", None)

            # Detect dynamic/untrackable calls
            if call_name in _DYNAMIC_CALL_NAMES:
                self.edges.append(
                    Edge(
                        id=_edge_id(),
                        from_node=caller_id,
                        to_node=call_name,
                        type=EdgeType.CALLS,
                        line_number=node.lineno,
                        column=col_offset,
                        confidence=EdgeConfidence.UNRESOLVED,
                        source=EdgeSource.STATIC,
                        untracked_reason=f"dynamic_{call_name}",
                    )
                )
                continue

            # Try fast resolution via import table
            resolved = self.import_table.resolve(call_name)
            if resolved:
                target, confidence = resolved
            else:
                target = call_name
                confidence = EdgeConfidence.INFERRED

            self.edges.append(
                Edge(
                    id=_edge_id(),
                    from_node=caller_id,
                    to_node=target,
                    type=EdgeType.CALLS,
                    line_number=node.lineno,
                    column=col_offset,
                    confidence=confidence,
                    source=EdgeSource.STATIC,
                )
            )

    def _extract_type_annotations(
        self, func_node: ast.FunctionDef | ast.AsyncFunctionDef, node_id: str
    ) -> None:
        """Extract USES edges from type annotations in function signatures."""
        annotations: list[ast.expr] = []

        # Parameter annotations
        for arg in func_node.args.args + func_node.args.posonlyargs + func_node.args.kwonlyargs:
            if arg.annotation:
                annotations.append(arg.annotation)
        if func_node.args.vararg and func_node.args.vararg.annotation:
            annotations.append(func_node.args.vararg.annotation)
        if func_node.args.kwarg and func_node.args.kwarg.annotation:
            annotations.append(func_node.args.kwarg.annotation)

        # Return annotation
        if func_node.returns:
            annotations.append(func_node.returns)

        # Extract type names from annotations
        for ann in annotations:
            col_offset = getattr(ann, "col_offset", None)
            for name in self._extract_names_from_annotation(ann):
                resolved = self.import_table.resolve(name)
                target = resolved[0] if resolved else name
                confidence = resolved[1] if resolved else EdgeConfidence.INFERRED
                self.edges.append(
                    Edge(
                        id=_edge_id(),
                        from_node=node_id,
                        to_node=target,
                        type=EdgeType.USES,
                        line_number=ann.lineno,
                        column=col_offset,
                        confidence=confidence,
                        source=EdgeSource.STATIC,
                    )
                )

    def _extract_names_from_annotation(self, node: ast.expr) -> list[str]:
        """Recursively extract type names from an annotation AST node."""
        names: list[str] = []
        if isinstance(node, ast.Name):
            # Skip builtins
            if node.id not in {"int", "str", "float", "bool", "bytes", "None", "type", "object"}:
                names.append(node.id)
        elif isinstance(node, ast.Attribute):
            name = self._resolve_name_from_ast(node)
            if name:
                names.append(name)
        elif isinstance(node, ast.Subscript):
            # e.g. list[User], dict[str, User], Optional[User]
            names.extend(self._extract_names_from_annotation(node.value))
            names.extend(self._extract_names_from_annotation(node.slice))
        elif isinstance(node, ast.Tuple):
            # e.g. tuple[User, Admin]
            for elt in node.elts:
                names.extend(self._extract_names_from_annotation(elt))
        elif isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
            # e.g. User | None (PEP 604)
            names.extend(self._extract_names_from_annotation(node.left))
            names.extend(self._extract_names_from_annotation(node.right))
        elif isinstance(node, ast.Constant):
            pass  # string annotations, None, etc.
        return names

    def _resolve_name_from_ast(self, node: ast.AST) -> str | None:
        """Extract a dotted name string from an AST node."""
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            value = self._resolve_name_from_ast(node.value)
            if value:
                return f"{value}.{node.attr}"
            return node.attr
        if isinstance(node, ast.Call):
            return self._resolve_name_from_ast(node.func)
        return None

    def extract_blocks(self, tree: ast.Module) -> None:
        """
        Identify module-level statements that are not functions, classes, or imports.

        Groups consecutive unclaimed statements into BLOCK nodes.
        Covers: constants, type aliases, if __name__ guards, assignments, etc.
        """
        block_start: int | None = None
        block_stmts: list[ast.stmt] = []

        def _flush_block() -> None:
            nonlocal block_start, block_stmts
            if not block_stmts or block_start is None:
                block_stmts = []
                block_start = None
                return

            first = block_stmts[0]
            last = block_stmts[-1]
            start_line = first.lineno
            end_line = last.end_lineno or last.lineno
            source = _get_source_segment(self.source_lines, start_line, end_line)

            # Determine a descriptive name for the block
            name = self._block_name(block_stmts)
            node_id = _make_id([self.module_id, f"_block_{start_line}"])

            self.nodes.append(
                Node(
                    id=node_id,
                    type=NodeType.BLOCK,
                    name=name,
                    qualified_name=node_id,
                    file_path=self.file_path,
                    start_line=start_line,
                    end_line=end_line,
                    source_code=source,
                    metadata={"block_kind": self._classify_block(block_stmts)},
                )
            )
            block_stmts = []
            block_start = None

        for stmt in tree.body:
            # Skip imports, functions, classes — they are handled separately
            if isinstance(stmt, (ast.Import, ast.ImportFrom, ast.FunctionDef,
                                 ast.AsyncFunctionDef, ast.ClassDef)):
                _flush_block()
                continue

            end_line = stmt.end_lineno or stmt.lineno
            # Check if these lines are already claimed by a nested structure
            if any(ln in self._claimed_lines for ln in range(stmt.lineno, end_line + 1)):
                _flush_block()
                continue

            if block_start is None:
                block_start = stmt.lineno
            block_stmts.append(stmt)

        _flush_block()

    def _block_name(self, stmts: list[ast.stmt]) -> str:
        """Generate a descriptive name for a block of statements."""
        if len(stmts) == 1:
            stmt = stmts[0]
            if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1:
                target = stmt.targets[0]
                if isinstance(target, ast.Name):
                    return target.id
            if isinstance(stmt, ast.If):
                return "guard"
        names = []
        for stmt in stmts:
            if isinstance(stmt, ast.Assign):
                for target in stmt.targets:
                    if isinstance(target, ast.Name):
                        names.append(target.id)
        if names:
            return ", ".join(names[:3]) + ("..." if len(names) > 3 else "")
        return f"block_{stmts[0].lineno}"

    def _classify_block(self, stmts: list[ast.stmt]) -> str:
        """Classify what kind of block this is."""
        if len(stmts) == 1 and isinstance(stmts[0], ast.If):
            # Check for if __name__ == "__main__"
            test = stmts[0].test
            if (isinstance(test, ast.Compare)
                    and isinstance(test.left, ast.Name)
                    and test.left.id == "__name__"):
                return "main_guard"
            return "conditional"

        has_assign = any(isinstance(s, ast.Assign) for s in stmts)
        has_ann_assign = any(isinstance(s, ast.AnnAssign) for s in stmts)

        if has_ann_assign:
            return "type_aliases"
        if has_assign:
            return "constants"
        return "statements"


class PythonParser(BaseParser):
    """
    Python language parser using ast for structure and jedi for name resolution.
    """

    def __init__(self) -> None:
        self._jedi_project: jedi.Project | None = None

    def get_file_extensions(self) -> list[str]:
        return [".py"]

    def set_project_root(self, root_path: Path) -> None:
        """Initialize jedi project for deep name resolution."""
        self._jedi_project = jedi.Project(path=str(root_path))

    def parse_file(self, file_path: Path, root_path: Path) -> tuple[list[Node], list[Edge]]:
        """Parse a single Python file into nodes and edges."""
        try:
            source = file_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            source = file_path.read_text(encoding="utf-8", errors="replace")
        source_lines = source.splitlines()

        try:
            tree = ast.parse(source, filename=str(file_path))
        except SyntaxError as e:
            logger.warning("Syntax error in %s: %s", file_path, e)
            return [], []

        module_id = _module_id_from_path(file_path, root_path)
        rel_path = str(file_path.relative_to(root_path))

        # Create module node
        module_node = Node(
            id=module_id,
            type=NodeType.MODULE,
            name=file_path.stem,
            qualified_name=module_id,
            file_path=rel_path,
            start_line=1,
            end_line=len(source_lines),
            source_code=source,
            docstring=_get_docstring(tree),
            metadata={"encoding": "utf-8"},
        )

        # Visit AST to extract functions, classes, edges
        visitor = CodeGraphVisitor(source_lines, module_id, rel_path)
        visitor.visit(tree)

        # Extract BLOCK nodes for unclaimed module-level code
        visitor.extract_blocks(tree)

        # Combine: module node + visitor-found nodes
        all_nodes = [module_node] + visitor.nodes
        all_edges = visitor.edges

        # Jedi resolution pass: upgrade INFERRED edges to RESOLVED
        if self._jedi_project is not None:
            self._resolve_edges_with_jedi(all_edges, str(file_path))

        return all_nodes, all_edges

    def _resolve_edges_with_jedi(self, edges: list[Edge], file_path: str) -> None:
        """Use jedi to upgrade INFERRED edges to RESOLVED or EXTERNAL."""
        try:
            script = jedi.Script(path=file_path, project=self._jedi_project)
        except Exception:
            return

        for edge in edges:
            if edge.confidence != EdgeConfidence.INFERRED:
                continue
            if edge.type not in (EdgeType.CALLS, EdgeType.USES):
                continue
            if edge.line_number is None:
                continue

            # Check if target is stdlib/builtin first
            if _is_external(edge.to_node):
                edge.confidence = EdgeConfidence.EXTERNAL
                continue

            try:
                # Use actual column if available, otherwise try 0 then 4
                col = edge.column if edge.column is not None else 0
                names = script.goto(edge.line_number, col)
                if not names and edge.column is None:
                    names = script.goto(edge.line_number, 4)
                if names:
                    d = names[0]
                    module = d.module_name or ""
                    name = d.name or ""
                    resolved_id = f"{module}.{name}" if module else name
                    if resolved_id and resolved_id != ".":
                        edge.to_node = resolved_id
                        # Check if resolved target is external
                        if _is_external(resolved_id):
                            edge.confidence = EdgeConfidence.EXTERNAL
                        else:
                            edge.confidence = EdgeConfidence.RESOLVED
            except Exception:
                continue

    def resolve_name(
        self, file_path: str, line: int, column: int, project_root: str
    ) -> Resolution:
        """
        Deep name resolution using jedi.

        Falls back gracefully if jedi cannot resolve.
        """
        try:
            if self._jedi_project is None:
                self.set_project_root(Path(project_root))

            script = jedi.Script(path=file_path, project=self._jedi_project)
            definitions = script.goto(line, column)

            if definitions:
                d = definitions[0]
                module = d.module_name or ""
                name = d.name or ""
                node_id = f"{module}.{name}" if module else name
                return Resolution(
                    node_id=node_id,
                    confidence=EdgeConfidence.RESOLVED,
                )

            return Resolution(
                node_id=None,
                confidence=EdgeConfidence.UNRESOLVED,
                untracked_reason="jedi_no_definition",
            )
        except Exception:
            return Resolution(
                node_id=None,
                confidence=EdgeConfidence.UNRESOLVED,
                untracked_reason="jedi_error",
            )

    def parse_project(
        self,
        root_path: Path,
        progress_callback: ProgressCallback | None = None,
    ) -> tuple[list[Node], list[Edge]]:
        """Parse project, initializing jedi context first."""
        self.set_project_root(root_path)
        return super().parse_project(root_path, progress_callback)
