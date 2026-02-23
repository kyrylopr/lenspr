"""SQL/Database schema mapper.

Detects database interactions in code and creates edges:
- READS_TABLE: function performs SELECT on a table
- WRITES_TABLE: function performs INSERT/UPDATE/DELETE on a table
- MIGRATES: migration creates/alters a table

Patterns recognized:

SQLAlchemy models:
  class User(Base):
      __tablename__ = "users"      → virtual node "db.table.users"

Raw SQL:
  cursor.execute("SELECT * FROM users")  → READS_TABLE to "db.table.users"
  cursor.execute("INSERT INTO orders")   → WRITES_TABLE to "db.table.orders"

Django ORM:
  User.objects.filter(...)     → READS_TABLE (when User model is known)
  User.objects.create(...)     → WRITES_TABLE

SQLAlchemy query:
  session.query(User).filter() → READS_TABLE
  session.add(user)            → WRITES_TABLE
  db.session.delete(user)      → WRITES_TABLE
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from lenspr.models import Edge, EdgeConfidence, EdgeSource, EdgeType, Node, NodeType

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass
class TableInfo:
    """A database table discovered from model definitions."""

    name: str  # Table name (e.g., "users")
    model_node_id: str  # Node ID of the model class
    file_path: str
    line: int


@dataclass
class DbOperation:
    """A database operation (read/write) discovered in code."""

    op_type: str  # "read" or "write"
    table_name: str  # Table name or model name
    caller_node_id: str  # Node ID of the calling function
    file_path: str
    line: int


# ---------------------------------------------------------------------------
# Patterns
# ---------------------------------------------------------------------------

# SQLAlchemy: __tablename__ = "users"
_TABLENAME_RE = re.compile(
    r"""__tablename__\s*=\s*['"]([^'"]+)['"]""",
)

# SQLAlchemy: class User(Base) or class User(db.Model) or class User(DeclarativeBase)
# Used as fallback when __tablename__ is absent — infer table name from class name
_SA_BASE_RE = re.compile(
    r"""class\s+(\w+)\s*\([^)]*\b(?:Base|DeclarativeBase|db\.Model)\b""",
)

# Django: class User(models.Model) or class User(Model)
_DJANGO_MODEL_RE = re.compile(
    r"""class\s+(\w+)\s*\([^)]*\bmodels?\.Model\b[^)]*\)""",
)

# Django Meta: class Meta: db_table = "users"
_DJANGO_DB_TABLE_RE = re.compile(
    r"""db_table\s*=\s*['"]([^'"]+)['"]""",
)

# Raw SQL: SELECT ... FROM table_name
_SQL_SELECT_RE = re.compile(
    r"""\bSELECT\b[^;]*\bFROM\s+[`"']?(\w+)[`"']?""",
    re.IGNORECASE,
)

# Raw SQL: INSERT INTO table_name
_SQL_INSERT_RE = re.compile(
    r"""\bINSERT\s+INTO\s+[`"']?(\w+)[`"']?""",
    re.IGNORECASE,
)

# Raw SQL: UPDATE table_name
_SQL_UPDATE_RE = re.compile(
    r"""\bUPDATE\s+[`"']?(\w+)[`"']?\s+SET\b""",
    re.IGNORECASE,
)

# Raw SQL: DELETE FROM table_name
_SQL_DELETE_RE = re.compile(
    r"""\bDELETE\s+FROM\s+[`"']?(\w+)[`"']?""",
    re.IGNORECASE,
)

# Raw SQL: CREATE TABLE table_name
_SQL_CREATE_TABLE_RE = re.compile(
    r"""\bCREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?[`"']?(\w+)[`"']?""",
    re.IGNORECASE,
)

# Raw SQL: ALTER TABLE table_name
_SQL_ALTER_TABLE_RE = re.compile(
    r"""\bALTER\s+TABLE\s+[`"']?(\w+)[`"']?""",
    re.IGNORECASE,
)

# Raw SQL: DROP TABLE table_name
_SQL_DROP_TABLE_RE = re.compile(
    r"""\bDROP\s+TABLE\s+(?:IF\s+EXISTS\s+)?[`"']?(\w+)[`"']?""",
    re.IGNORECASE,
)

# Django ORM: Model.objects.filter/get/all/values/...
_DJANGO_READ_RE = re.compile(
    r"""\b(\w+)\.objects\.(?:filter|exclude|get|all|values|values_list|"""
    r"""count|exists|aggregate|annotate|order_by|first|last)\b""",
)

# Django ORM: Model.objects.create/update/bulk_create/...
_DJANGO_WRITE_RE = re.compile(
    r"""\b(\w+)\.objects\.(?:create|update|bulk_create|bulk_update|"""
    r"""get_or_create|update_or_create|delete)\b""",
)

# Django ORM: instance.save() / instance.delete()
_DJANGO_INSTANCE_WRITE_RE = re.compile(
    r"""\b\w+\.(?:save|delete)\s*\(""",
)

# SQLAlchemy: session.query(Model), db.query(Model), async_session.query(Model)
_SA_QUERY_RE = re.compile(
    r"""\b\w+\.query\s*\(\s*(\w+)""",
)

# SQLAlchemy: session.add/delete/merge — capture receiver, method, and argument
_SA_SESSION_WRITE_METHOD_RE = re.compile(
    r"""\b(\w+)\.(add|delete|merge)\s*\(\s*(\w+)""",
)
# SQLAlchemy: session.add_all([...]) — list argument, hard to resolve
_SA_SESSION_ADD_ALL_RE = re.compile(
    r"""\b\w+\.add_all\s*\(""",
)
# Note: commit(), flush(), refresh() are intentionally excluded — they are
# session lifecycle methods that don't target specific tables.

# Receiver names that indicate a DB session (used to distinguish db.add()
# from set.add(), list.delete(), etc.)
_DB_SESSION_RECEIVER_NAMES = frozenset({
    "db", "session", "sess", "async_session", "tx", "conn", "connection",
    "s", "db_session",
})

# Variable assignment to model constructor: var = Model(...)
# Used to resolve session.add(var) → Model → table
_VAR_MODEL_ASSIGN_RE = re.compile(
    r"""\b(\w+)\s*(?::\s*[\w.]+\s*)?=\s*(\w+)\s*\(""",
)

# Query-result assignment: var = db.query(Model).first()  / var = await db.get(Model, id)
# Used to resolve db.delete(var) where var was loaded from a query.
# Single-line version (line-by-line scan):
_QUERY_RESULT_RE = re.compile(
    r"""(\w+)\s*=\s*(?:await\s+)?\w+\.(?:query|get)\s*\(\s*(\w+)""",
)
# Multi-line version: var = (\n    db.query(Model)...
_QUERY_RESULT_MULTI_RE = re.compile(
    r"""(\w+)\s*=\s*\(?[\s\n]*(?:await\s+)?\w+\.(?:query|get)\s*\(\s*(\w+)""",
)

# SQLAlchemy 2.0 statement-style queries
_SA2_SELECT_RE = re.compile(
    r"""\bselect\s*\(\s*(\w+)""",
)
_SA2_WRITE_RE = re.compile(
    r"""\b(?:insert|update|delete)\s*\(\s*(\w+)""",
)

# Supabase / PostgREST client: .table("table_name").select/insert/update/delete/upsert
_SUPABASE_TABLE_RE = re.compile(
    r"""\.table\s*\(\s*['"]([^'"]+)['"]\s*\)""",
)
_SUPABASE_READ_METHODS = {"select", "rpc"}
_SUPABASE_WRITE_METHODS = {"insert", "update", "upsert", "delete"}

# General: .execute("SQL...")
_EXECUTE_RE = re.compile(
    r"""\.execute\s*\(\s*(?:f?['"`]{1,3})([^'"`]*(?:['"`]{1,3})?[^)]{0,500})""",
    re.DOTALL,
)

# SQL keywords that indicate this is not a real table name
_SQL_NOISE = {
    "select", "from", "where", "join", "inner", "left", "right", "outer",
    "on", "and", "or", "not", "null", "true", "false", "as", "in",
    "set", "values", "into", "table", "index", "create", "drop", "alter",
    "primary", "key", "foreign", "references", "constraint", "default",
    "autoincrement", "integer", "text", "real", "blob", "varchar", "char",
    "boolean", "timestamp", "date", "exists", "if",
    # Common English words matched in text/JSX content
    "the", "a", "an", "this", "that", "it", "is", "are", "was", "were",
    # Filesystem operations falsely matched as table names
    "disk", "file", "path", "directory", "folder", "tmp", "temp",
    # System/catalog tables (not user tables)
    "information_schema", "pg_catalog", "pg_stat", "pg_class",
    "sqlite_master", "sqlite_sequence", "sqlite_temp_master",
    "sys", "dual",
    # Service/platform names matched in comments/docstrings
    "supabase", "firebase", "dynamo", "redis", "mongo", "postgres",
    "database", "storage", "server", "cloud",
}

def _is_test_file(file_path: str) -> bool:
    """Check if a file path belongs to a test file."""
    import os
    basename = os.path.basename(file_path)
    return basename.startswith("test_") or basename == "conftest.py"



# ---------------------------------------------------------------------------
# SQL Mapper
# ---------------------------------------------------------------------------


class SqlMapper:
    """Extract database tables and operations, then create edges."""

    def __init__(self) -> None:
        self._tables: list[TableInfo] = []
        self._operations: list[DbOperation] = []
        self._model_to_table: dict[str, str] = {}  # ModelName → table_name
        self._edge_counter = 0

    def _resolve_write_target(
        self, arg: str, var_to_model: dict[str, str],
    ) -> str:
        """Resolve the argument of session.add/delete/merge to a table name.

        Tries three strategies:
        1. Direct model class: session.add(User(...)) — arg is the model name
        2. Variable tracking: user = User(...); session.add(user)
        3. Capitalized-name fallback (same heuristic as reads)

        Returns the resolved table name or "<unknown>".
        """
        # 1. Direct model class reference: session.add(ModelName(...))
        if arg in self._model_to_table:
            return self._model_to_table[arg]

        # 2. Variable tracking: var was assigned from a model constructor
        model = var_to_model.get(arg)
        if model is not None:
            if model in self._model_to_table:
                return self._model_to_table[model]
            # Capitalized model not in known tables — infer table name
            if model[0].isupper():
                return model.lower() + "s"

        # 3. Arg itself is capitalized (e.g., session.add(SomeModel))
        if arg[0:1].isupper() and arg.lower() not in _SQL_NOISE:
            return arg.lower() + "s"

        return "<unknown>"

    @staticmethod
    def _build_var_to_model(
        lines: list[str], model_to_table: dict[str, str],
    ) -> dict[str, str]:
        """Build a mapping of variable names to model class names.

        Scans function body for assignments like ``var = ModelName(...)``
        where ``ModelName`` is a known model or starts with an uppercase
        letter.
        """
        var_to_model: dict[str, str] = {}
        for line_text in lines:
            # Pattern 1: var = Model(...) — constructor assignment
            for m in _VAR_MODEL_ASSIGN_RE.finditer(line_text):
                var_name = m.group(1)
                model_name = m.group(2)
                # Accept if it's a known model OR looks like a class name
                if model_name in model_to_table or (
                    model_name[0:1].isupper()
                    and model_name.lower() not in _SQL_NOISE
                ):
                    var_to_model[var_name] = model_name

            # Pattern 2: var = db.query(Model).first() — query result (single-line)
            for m in _QUERY_RESULT_RE.finditer(line_text):
                var_name = m.group(1)
                model_name = m.group(2)
                if model_name in model_to_table or (
                    model_name[0:1].isupper()
                    and model_name.lower() not in _SQL_NOISE
                ):
                    var_to_model[var_name] = model_name

        # Pattern 3: Multi-line query results — var = (\n   db.query(Model)...\n)
        full_source = "\n".join(lines)
        for m in _QUERY_RESULT_MULTI_RE.finditer(full_source):
            var_name = m.group(1)
            model_name = m.group(2)
            if var_name not in var_to_model and (
                model_name in model_to_table or (
                    model_name[0:1].isupper()
                    and model_name.lower() not in _SQL_NOISE
                )
            ):
                var_to_model[var_name] = model_name

        return var_to_model

    def extract_tables(self, nodes: list[Node]) -> list[TableInfo]:
        """Extract table definitions from SQLAlchemy/Django model classes."""
        tables: list[TableInfo] = []

        # Skip test files and non-class nodes. Table definitions (tablename
        # assignments, Django Model subclasses) only appear in class bodies.
        # Scanning modules/functions/blocks causes false positives from
        # docstrings and comments that happen to contain matching patterns.
        nodes = [
            n for n in nodes
            if not _is_test_file(n.file_path) and n.type.value == "class"
        ]

        for node in nodes:
            if not node.source_code:
                continue

            # SQLAlchemy: look for tablename assignment in class body.
            # Only accept if match is in the first 200 chars (class-level attribute,
            # not buried in a method comment).
            match = _TABLENAME_RE.search(node.source_code)
            if match and match.start() < 200:
                table_name = match.group(1)
                tables.append(TableInfo(
                    name=table_name,
                    model_node_id=node.id,
                    file_path=node.file_path,
                    line=node.start_line,
                ))
                class_name = node.name
                self._model_to_table[class_name] = table_name

            # Django models.Model: verify the detected class name matches
            # this node's actual name to avoid matching patterns in comments
            for match in _DJANGO_MODEL_RE.finditer(node.source_code):
                class_name = match.group(1)
                if class_name != node.name:
                    continue
                db_match = _DJANGO_DB_TABLE_RE.search(node.source_code)
                table_name = db_match.group(1) if db_match else class_name.lower() + "s"
                tables.append(TableInfo(
                    name=table_name,
                    model_node_id=node.id,
                    file_path=node.file_path,
                    line=node.start_line,
                ))
                self._model_to_table[class_name] = table_name

            # SQLAlchemy auto-naming fallback: class User(Base) without
            # explicit __tablename__. SQLAlchemy infers table name as
            # class_name.lower(). Only apply if this class wasn't already
            # matched by _TABLENAME_RE or _DJANGO_MODEL_RE above.
            if node.name not in self._model_to_table:
                sa_match = _SA_BASE_RE.search(node.source_code)
                if sa_match and sa_match.group(1) == node.name:
                    table_name = node.name.lower()
                    tables.append(TableInfo(
                        name=table_name,
                        model_node_id=node.id,
                        file_path=node.file_path,
                        line=node.start_line,
                    ))
                    self._model_to_table[node.name] = table_name

        self._tables = tables
        return tables

    def extract_operations(self, nodes: list[Node]) -> list[DbOperation]:
        """Extract database read/write operations from function nodes."""
        ops: list[DbOperation] = []

        # Skip test files — they contain SQL string literals that produce false edges
        nodes = [n for n in nodes if not _is_test_file(n.file_path)]

        for node in nodes:
            if not node.source_code:
                continue
            if node.type.value not in ("function", "method"):
                continue

            source = node.source_code
            lines = source.splitlines()
            is_python = node.file_path.endswith(".py")

            # Build variable → model mapping for this function body
            # so we can resolve session.add(var) → Model → table
            var_to_model: dict[str, str] = (
                self._build_var_to_model(lines, self._model_to_table)
                if is_python else {}
            )

            for i, line in enumerate(lines):
                line_num = node.start_line + i

                # Raw SQL in strings
                self._extract_raw_sql(line, node, line_num, ops)

                # Supabase / PostgREST: .table("name").select/insert/update/delete
                # Check first — if found, skip ORM patterns (they false-match
                # on chained methods like .insert(data))
                supabase_matches = list(_SUPABASE_TABLE_RE.finditer(line))
                if supabase_matches:
                    for match in supabase_matches:
                        table_name = match.group(1)
                        if table_name.lower() in _SQL_NOISE:
                            continue
                        rest = line[match.end():]
                        method_match = re.search(r'\.(\w+)\s*\(', rest)
                        if method_match:
                            method = method_match.group(1)
                            if method in _SUPABASE_WRITE_METHODS:
                                op_type = "write"
                            else:
                                op_type = "read"
                        else:
                            op_type = "read"
                        ops.append(DbOperation(
                            op_type=op_type,
                            table_name=table_name,
                            caller_node_id=node.id,
                            file_path=node.file_path,
                            line=line_num,
                        ))
                    continue  # skip ORM patterns for this line

                # Python ORM patterns — only apply to .py files
                if is_python:
                    # Django ORM reads
                    for match in _DJANGO_READ_RE.finditer(line):
                        model = match.group(1)
                        table = self._model_to_table.get(model)
                        if table is None:
                            if not model[0].isupper():
                                continue
                            table = model.lower() + "s"
                        ops.append(DbOperation(
                            op_type="read",
                            table_name=table,
                            caller_node_id=node.id,
                            file_path=node.file_path,
                            line=line_num,
                        ))

                    # Django ORM writes
                    for match in _DJANGO_WRITE_RE.finditer(line):
                        model = match.group(1)
                        table = self._model_to_table.get(model)
                        if table is None:
                            if not model[0].isupper():
                                continue
                            table = model.lower() + "s"
                        ops.append(DbOperation(
                            op_type="write",
                            table_name=table,
                            caller_node_id=node.id,
                            file_path=node.file_path,
                            line=line_num,
                        ))

                    # SQLAlchemy: session.query(Model)
                    for match in _SA_QUERY_RE.finditer(line):
                        model = match.group(1)
                        table = self._model_to_table.get(model)
                        if table is None:
                            if not model[0].isupper():
                                continue
                            table = model.lower() + "s"
                        ops.append(DbOperation(
                            op_type="read",
                            table_name=table,
                            caller_node_id=node.id,
                            file_path=node.file_path,
                            line=line_num,
                        ))

                    # SQLAlchemy: session.add/delete/merge — resolve to model
                    sa_write_match = _SA_SESSION_WRITE_METHOD_RE.search(line)
                    if sa_write_match:
                        receiver = sa_write_match.group(1)
                        arg = sa_write_match.group(3)
                        table = self._resolve_write_target(arg, var_to_model)
                        if table != "<unknown>":
                            # Resolved to a real model — always create edge
                            ops.append(DbOperation(
                                op_type="write",
                                table_name=table,
                                caller_node_id=node.id,
                                file_path=node.file_path,
                                line=line_num,
                            ))
                        elif receiver.lower() in _DB_SESSION_RECEIVER_NAMES:
                            # Unresolved but receiver is a DB session
                            ops.append(DbOperation(
                                op_type="write",
                                table_name="<unknown>",
                                caller_node_id=node.id,
                                file_path=node.file_path,
                                line=line_num,
                            ))
                        # else: skip — likely set.add(), list.delete(), etc.
                    elif _SA_SESSION_ADD_ALL_RE.search(line):
                        ops.append(DbOperation(
                            op_type="write",
                            table_name="<unknown>",
                            caller_node_id=node.id,
                            file_path=node.file_path,
                            line=line_num,
                        ))

                    # SQLAlchemy 2.0: select(Model).where(...)
                    for match in _SA2_SELECT_RE.finditer(line):
                        model = match.group(1)
                        if model.lower() in _SQL_NOISE:
                            continue
                        if not model[0].isupper():
                            continue
                        table = self._model_to_table.get(model, model.lower() + "s")
                        ops.append(DbOperation(
                            op_type="read",
                            table_name=table,
                            caller_node_id=node.id,
                            file_path=node.file_path,
                            line=line_num,
                        ))

                    # SQLAlchemy 2.0: insert(Model), update(Model), delete(Model)
                    for match in _SA2_WRITE_RE.finditer(line):
                        model = match.group(1)
                        if model.lower() in _SQL_NOISE:
                            continue
                        if not model[0].isupper():
                            continue
                        table = self._model_to_table.get(model, model.lower() + "s")
                        ops.append(DbOperation(
                            op_type="write",
                            table_name=table,
                            caller_node_id=node.id,
                            file_path=node.file_path,
                            line=line_num,
                        ))

                    # Django instance writes: obj.save(), obj.delete()
                    # Skip if already matched by SA session write (db.delete matches both)
                    if not sa_write_match and _DJANGO_INSTANCE_WRITE_RE.search(line):
                        ops.append(DbOperation(
                            op_type="write",
                            table_name="<unknown>",
                            caller_node_id=node.id,
                            file_path=node.file_path,
                            line=line_num,
                        ))

            # Also check .execute() calls with SQL strings
            for match in _EXECUTE_RE.finditer(source):
                sql_fragment = match.group(1)
                exec_line = node.start_line + source[:match.start()].count("\n")
                self._extract_raw_sql(sql_fragment, node, exec_line, ops)

        self._operations = ops
        return ops

    def _extract_raw_sql(
        self,
        text: str,
        node: Node,
        line_num: int,
        ops: list[DbOperation],
    ) -> None:
        """Extract table references from raw SQL patterns in text."""
        for match in _SQL_SELECT_RE.finditer(text):
            table = match.group(1).lower()
            if table not in _SQL_NOISE:
                ops.append(DbOperation(
                    op_type="read",
                    table_name=table,
                    caller_node_id=node.id,
                    file_path=node.file_path,
                    line=line_num,
                ))

        for regex in (_SQL_INSERT_RE, _SQL_UPDATE_RE, _SQL_DELETE_RE):
            for match in regex.finditer(text):
                table = match.group(1).lower()
                if table not in _SQL_NOISE:
                    ops.append(DbOperation(
                        op_type="write",
                        table_name=table,
                        caller_node_id=node.id,
                        file_path=node.file_path,
                        line=line_num,
                    ))

        for regex in (_SQL_CREATE_TABLE_RE, _SQL_ALTER_TABLE_RE, _SQL_DROP_TABLE_RE):
            for match in regex.finditer(text):
                table = match.group(1).lower()
                if table not in _SQL_NOISE:
                    ops.append(DbOperation(
                        op_type="migrate",
                        table_name=table,
                        caller_node_id=node.id,
                        file_path=node.file_path,
                        line=line_num,
                    ))

    def match(self) -> list[Edge]:
        """Create edges from database operations to table virtual nodes."""
        edges: list[Edge] = []
        seen: set[tuple[str, str, str]] = set()  # (caller, op_type, table) dedup

        # Map table names to model node IDs
        table_to_model: dict[str, str] = {}
        for table in self._tables:
            table_to_model[table.name] = table.model_node_id

        for op in self._operations:
            if op.table_name == "<unknown>":
                # Still create edge for generic writes (session.add, etc.)
                # so the function is marked as DB-writing even without table resolution
                if op.op_type in ("write",):
                    key = (op.caller_node_id, op.op_type, "<unknown>")
                    if key not in seen:
                        seen.add(key)
                        self._edge_counter += 1
                        edges.append(Edge(
                            id=f"db_edge_{self._edge_counter}",
                            from_node=op.caller_node_id,
                            to_node="db.write.unknown",
                            type=EdgeType.WRITES_TABLE,
                            line_number=op.line,
                            confidence=EdgeConfidence.INFERRED,
                            source=EdgeSource.STATIC,
                            metadata={"table": "<unknown>", "operation": op.op_type},
                        ))
                continue

            key = (op.caller_node_id, op.op_type, op.table_name)
            if key in seen:
                continue
            seen.add(key)

            # Target: either the model class node or a virtual "db.table.X" ID
            target = table_to_model.get(op.table_name, f"db.table.{op.table_name}")

            if op.op_type == "read":
                edge_type = EdgeType.READS_TABLE
            elif op.op_type == "write":
                edge_type = EdgeType.WRITES_TABLE
            else:
                edge_type = EdgeType.MIGRATES

            self._edge_counter += 1
            edges.append(Edge(
                id=f"db_edge_{self._edge_counter}",
                from_node=op.caller_node_id,
                to_node=target,
                type=edge_type,
                line_number=op.line,
                confidence=EdgeConfidence.INFERRED,
                source=EdgeSource.STATIC,
                metadata={
                    "table": op.table_name,
                    "operation": op.op_type,
                },
            ))

        if edges:
            logger.info(
                "SQL mapper: %d DB edges (%d tables, %d operations)",
                len(edges), len(self._tables), len(self._operations),
            )

        return edges
