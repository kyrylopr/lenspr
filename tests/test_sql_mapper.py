"""Tests for lenspr/resolvers/sql_mapper.py — SQL/DB schema mapping."""

from __future__ import annotations

import pytest

from lenspr.models import Edge, EdgeConfidence, EdgeSource, EdgeType, Node, NodeType
from lenspr.resolvers.sql_mapper import DbOperation, SqlMapper, TableInfo


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_node(
    node_id: str,
    source: str,
    file_path: str = "app.py",
    node_type: str = "function",
    start_line: int = 1,
) -> Node:
    return Node(
        id=node_id,
        type=NodeType(node_type),
        name=node_id.split(".")[-1],
        qualified_name=node_id,
        file_path=file_path,
        start_line=start_line,
        end_line=start_line + source.count("\n"),
        source_code=source,
    )


# ---------------------------------------------------------------------------
# Tests — Table extraction
# ---------------------------------------------------------------------------


class TestTableExtraction:
    def test_sqlalchemy_tablename(self) -> None:
        """__tablename__ = 'users' detected."""
        mapper = SqlMapper()
        nodes = [
            _make_node(
                "models.User",
                'class User(Base):\n'
                '    __tablename__ = "users"\n'
                '    id = Column(Integer, primary_key=True)\n',
                node_type="class",
            ),
        ]
        tables = mapper.extract_tables(nodes)
        assert len(tables) == 1
        assert tables[0].name == "users"
        assert tables[0].model_node_id == "models.User"

    def test_django_model(self) -> None:
        """class User(models.Model) detected with inferred table name."""
        mapper = SqlMapper()
        nodes = [
            _make_node(
                "models.User",
                'class User(models.Model):\n'
                '    name = models.CharField(max_length=100)\n',
                node_type="class",
            ),
        ]
        tables = mapper.extract_tables(nodes)
        assert len(tables) == 1
        assert tables[0].name == "users"  # inferred: lowercase + "s"

    def test_django_db_table_override(self) -> None:
        """class Meta: db_table = 'custom_users' overrides inferred name."""
        mapper = SqlMapper()
        nodes = [
            _make_node(
                "models.User",
                'class User(models.Model):\n'
                '    class Meta:\n'
                '        db_table = "custom_users"\n',
                node_type="class",
            ),
        ]
        tables = mapper.extract_tables(nodes)
        assert len(tables) == 1
        assert tables[0].name == "custom_users"

    def test_multiple_tables(self) -> None:
        """Multiple model classes in same project."""
        mapper = SqlMapper()
        nodes = [
            _make_node(
                "models.User",
                'class User(Base):\n    __tablename__ = "users"\n',
                node_type="class",
            ),
            _make_node(
                "models.Order",
                'class Order(Base):\n    __tablename__ = "orders"\n',
                node_type="class",
            ),
        ]
        tables = mapper.extract_tables(nodes)
        assert len(tables) == 2
        names = {t.name for t in tables}
        assert names == {"users", "orders"}

    def test_no_tables(self) -> None:
        """Regular classes without table definitions."""
        mapper = SqlMapper()
        nodes = [
            _make_node(
                "utils.Helper",
                'class Helper:\n    def run(self): pass\n',
                node_type="class",
            ),
        ]
        tables = mapper.extract_tables(nodes)
        assert len(tables) == 0


# ---------------------------------------------------------------------------
# Tests — Raw SQL extraction
# ---------------------------------------------------------------------------


class TestRawSqlExtraction:
    def test_select_from(self) -> None:
        """SELECT * FROM users detected as read."""
        mapper = SqlMapper()
        mapper.extract_tables([])
        ops = mapper.extract_operations([
            _make_node(
                "repo.get_users",
                'def get_users(cursor):\n'
                '    cursor.execute("SELECT * FROM users")\n'
                '    return cursor.fetchall()\n',
            ),
        ])
        reads = [o for o in ops if o.op_type == "read"]
        assert len(reads) >= 1
        assert any(o.table_name == "users" for o in reads)

    def test_insert_into(self) -> None:
        """INSERT INTO orders detected as write."""
        mapper = SqlMapper()
        mapper.extract_tables([])
        ops = mapper.extract_operations([
            _make_node(
                "repo.create_order",
                'def create_order(cursor, data):\n'
                '    cursor.execute("INSERT INTO orders VALUES (?)", (data,))\n',
            ),
        ])
        writes = [o for o in ops if o.op_type == "write"]
        assert len(writes) >= 1
        assert any(o.table_name == "orders" for o in writes)

    def test_update_set(self) -> None:
        """UPDATE users SET ... detected as write."""
        mapper = SqlMapper()
        mapper.extract_tables([])
        ops = mapper.extract_operations([
            _make_node(
                "repo.update_user",
                'def update_user(cursor, uid, name):\n'
                '    cursor.execute("UPDATE users SET name=? WHERE id=?", (name, uid))\n',
            ),
        ])
        writes = [o for o in ops if o.op_type == "write"]
        assert len(writes) >= 1
        assert any(o.table_name == "users" for o in writes)

    def test_delete_from(self) -> None:
        """DELETE FROM sessions detected as write."""
        mapper = SqlMapper()
        mapper.extract_tables([])
        ops = mapper.extract_operations([
            _make_node(
                "repo.clear_sessions",
                'def clear_sessions(cursor):\n'
                '    cursor.execute("DELETE FROM sessions WHERE expired=1")\n',
            ),
        ])
        writes = [o for o in ops if o.op_type == "write"]
        assert len(writes) >= 1
        assert any(o.table_name == "sessions" for o in writes)

    def test_create_table(self) -> None:
        """CREATE TABLE detected as migrate."""
        mapper = SqlMapper()
        mapper.extract_tables([])
        ops = mapper.extract_operations([
            _make_node(
                "migrations.create_users",
                'def create_users(cursor):\n'
                '    cursor.execute("CREATE TABLE IF NOT EXISTS users (id INTEGER)")\n',
            ),
        ])
        migrates = [o for o in ops if o.op_type == "migrate"]
        assert len(migrates) >= 1
        assert any(o.table_name == "users" for o in migrates)

    def test_alter_table(self) -> None:
        """ALTER TABLE detected as migrate."""
        mapper = SqlMapper()
        mapper.extract_tables([])
        ops = mapper.extract_operations([
            _make_node(
                "migrations.add_column",
                'def add_column(cursor):\n'
                '    cursor.execute("ALTER TABLE users ADD COLUMN email TEXT")\n',
            ),
        ])
        migrates = [o for o in ops if o.op_type == "migrate"]
        assert len(migrates) >= 1
        assert any(o.table_name == "users" for o in migrates)

    def test_sql_noise_words_filtered(self) -> None:
        """SQL keywords like 'select', 'from' are not treated as table names."""
        mapper = SqlMapper()
        mapper.extract_tables([])
        ops = mapper.extract_operations([
            _make_node(
                "repo.get_data",
                'def get_data(cursor):\n'
                '    cursor.execute("SELECT id FROM orders WHERE total > 0")\n',
            ),
        ])
        tables = {o.table_name for o in ops if o.op_type == "read"}
        assert "orders" in tables
        assert "select" not in tables
        assert "where" not in tables


# ---------------------------------------------------------------------------
# Tests — Django ORM
# ---------------------------------------------------------------------------


class TestDjangoOrm:
    def test_objects_filter_is_read(self) -> None:
        """User.objects.filter() is a read operation."""
        mapper = SqlMapper()
        mapper._model_to_table = {"User": "users"}
        ops = mapper.extract_operations([
            _make_node(
                "views.list_users",
                'def list_users(request):\n'
                '    return User.objects.filter(active=True)\n',
            ),
        ])
        reads = [o for o in ops if o.op_type == "read"]
        assert len(reads) == 1
        assert reads[0].table_name == "users"

    def test_objects_create_is_write(self) -> None:
        """User.objects.create() is a write operation."""
        mapper = SqlMapper()
        mapper._model_to_table = {"User": "users"}
        ops = mapper.extract_operations([
            _make_node(
                "views.create_user",
                'def create_user(request):\n'
                '    return User.objects.create(name="test")\n',
            ),
        ])
        writes = [o for o in ops if o.op_type == "write"]
        assert len(writes) == 1
        assert writes[0].table_name == "users"

    def test_objects_get_is_read(self) -> None:
        """User.objects.get() is a read."""
        mapper = SqlMapper()
        mapper._model_to_table = {"User": "users"}
        ops = mapper.extract_operations([
            _make_node(
                "views.get_user",
                'def get_user(uid):\n'
                '    return User.objects.get(pk=uid)\n',
            ),
        ])
        reads = [o for o in ops if o.op_type == "read"]
        assert len(reads) == 1

    def test_unknown_model_infers_table(self) -> None:
        """Unknown model name → inferred table (lowercase + s)."""
        mapper = SqlMapper()
        mapper.extract_tables([])
        ops = mapper.extract_operations([
            _make_node(
                "views.list_items",
                'def list_items():\n'
                '    return Item.objects.all()\n',
            ),
        ])
        reads = [o for o in ops if o.op_type == "read"]
        assert len(reads) == 1
        assert reads[0].table_name == "items"


# ---------------------------------------------------------------------------
# Tests — SQLAlchemy session
# ---------------------------------------------------------------------------


class TestSqlAlchemySession:
    def test_session_query_is_read(self) -> None:
        """session.query(User) is a read."""
        mapper = SqlMapper()
        mapper._model_to_table = {"User": "users"}
        ops = mapper.extract_operations([
            _make_node(
                "repo.get_users",
                'def get_users(session):\n'
                '    return session.query(User).all()\n',
            ),
        ])
        reads = [o for o in ops if o.op_type == "read"]
        assert len(reads) == 1
        assert reads[0].table_name == "users"

    def test_session_add_is_write(self) -> None:
        """session.add() is a write."""
        mapper = SqlMapper()
        mapper.extract_tables([])
        ops = mapper.extract_operations([
            _make_node(
                "repo.save_user",
                'def save_user(session, user):\n'
                '    session.add(user)\n'
                '    session.commit()\n',
            ),
        ])
        writes = [o for o in ops if o.op_type == "write"]
        assert len(writes) >= 1


# ---------------------------------------------------------------------------
# Tests — End-to-end matching
# ---------------------------------------------------------------------------


class TestEndToEndMatching:
    def test_read_creates_reads_table_edge(self) -> None:
        """SELECT FROM users → READS_TABLE edge to model node."""
        mapper = SqlMapper()

        mapper.extract_tables([
            _make_node(
                "models.User",
                'class User(Base):\n    __tablename__ = "users"\n',
                node_type="class",
                file_path="models.py",
            ),
        ])
        mapper.extract_operations([
            _make_node(
                "repo.get_users",
                'def get_users(cursor):\n'
                '    cursor.execute("SELECT * FROM users")\n',
                file_path="repo.py",
            ),
        ])
        edges = mapper.match()

        assert len(edges) >= 1
        read_edges = [e for e in edges if e.type == EdgeType.READS_TABLE]
        assert len(read_edges) >= 1
        assert read_edges[0].from_node == "repo.get_users"
        assert read_edges[0].to_node == "models.User"
        assert read_edges[0].metadata["table"] == "users"

    def test_write_creates_writes_table_edge(self) -> None:
        """INSERT INTO orders → WRITES_TABLE edge."""
        mapper = SqlMapper()

        mapper.extract_tables([
            _make_node(
                "models.Order",
                'class Order(Base):\n    __tablename__ = "orders"\n',
                node_type="class",
                file_path="models.py",
            ),
        ])
        mapper.extract_operations([
            _make_node(
                "repo.create_order",
                'def create_order(cursor):\n'
                '    cursor.execute("INSERT INTO orders VALUES (?)")\n',
                file_path="repo.py",
            ),
        ])
        edges = mapper.match()

        write_edges = [e for e in edges if e.type == EdgeType.WRITES_TABLE]
        assert len(write_edges) >= 1
        assert write_edges[0].from_node == "repo.create_order"
        assert write_edges[0].to_node == "models.Order"

    def test_migrate_creates_migrates_edge(self) -> None:
        """CREATE TABLE → MIGRATES edge."""
        mapper = SqlMapper()
        mapper.extract_tables([])
        mapper.extract_operations([
            _make_node(
                "migrations.init",
                'def init(cursor):\n'
                '    cursor.execute("CREATE TABLE users (id INTEGER)")\n',
                file_path="migrations.py",
            ),
        ])
        edges = mapper.match()

        migrate_edges = [e for e in edges if e.type == EdgeType.MIGRATES]
        assert len(migrate_edges) >= 1
        assert migrate_edges[0].to_node == "db.table.users"

    def test_unknown_table_uses_virtual_node(self) -> None:
        """Table not in models → target is db.table.<name>."""
        mapper = SqlMapper()
        mapper.extract_tables([])
        mapper.extract_operations([
            _make_node(
                "repo.get_logs",
                'def get_logs(cursor):\n'
                '    cursor.execute("SELECT * FROM audit_logs")\n',
            ),
        ])
        edges = mapper.match()

        assert len(edges) >= 1
        assert edges[0].to_node == "db.table.audit_logs"

    def test_dedup_same_table_same_function(self) -> None:
        """Multiple reads from same table in same function → one edge."""
        mapper = SqlMapper()
        mapper.extract_tables([])
        mapper.extract_operations([
            _make_node(
                "repo.complex_query",
                'def complex_query(cursor):\n'
                '    cursor.execute("SELECT * FROM users WHERE active=1")\n'
                '    cursor.execute("SELECT count(*) FROM users")\n',
            ),
        ])
        edges = mapper.match()

        read_edges = [e for e in edges if e.type == EdgeType.READS_TABLE]
        # Should be deduplicated
        assert len(read_edges) == 1

    def test_no_operations_no_edges(self) -> None:
        """No DB operations → no edges."""
        mapper = SqlMapper()
        mapper.extract_tables([])
        mapper.extract_operations([
            _make_node("utils.helper", "def helper(x):\n    return x * 2\n"),
        ])
        edges = mapper.match()
        assert len(edges) == 0

    def test_edge_metadata(self) -> None:
        """Edges have table and operation in metadata."""
        mapper = SqlMapper()
        mapper.extract_tables([])
        mapper.extract_operations([
            _make_node(
                "repo.get_data",
                'def get_data(cursor):\n'
                '    cursor.execute("SELECT * FROM products")\n',
            ),
        ])
        edges = mapper.match()

        assert len(edges) >= 1
        assert edges[0].metadata["table"] == "products"
        assert edges[0].metadata["operation"] == "read"

    def test_edge_ids_unique(self) -> None:
        """Each edge gets a unique ID."""
        mapper = SqlMapper()
        mapper.extract_tables([])
        mapper.extract_operations([
            _make_node(
                "repo.mixed",
                'def mixed(cursor):\n'
                '    cursor.execute("SELECT * FROM users")\n'
                '    cursor.execute("INSERT INTO logs VALUES (?)")\n',
            ),
        ])
        edges = mapper.match()

        assert len(edges) >= 2
        ids = {e.id for e in edges}
        assert len(ids) == len(edges)

    def test_django_full_pipeline(self) -> None:
        """Django model + ORM query → edge to model node."""
        mapper = SqlMapper()

        mapper.extract_tables([
            _make_node(
                "models.Article",
                'class Article(models.Model):\n'
                '    title = models.CharField(max_length=200)\n',
                node_type="class",
                file_path="models.py",
            ),
        ])
        mapper.extract_operations([
            _make_node(
                "views.list_articles",
                'def list_articles(request):\n'
                '    return Article.objects.filter(published=True)\n',
                file_path="views.py",
            ),
        ])
        edges = mapper.match()

        assert len(edges) >= 1
        assert edges[0].type == EdgeType.READS_TABLE
        assert edges[0].from_node == "views.list_articles"
        assert edges[0].to_node == "models.Article"


# ---------------------------------------------------------------------------
# Tests — Raw SQL file parsing
# ---------------------------------------------------------------------------


class TestRawSqlFileParsing:
    """Test parsing of raw .sql files."""

    def test_create_table(self, tmp_path):
        sql = "CREATE TABLE users (\n    id SERIAL PRIMARY KEY,\n    email TEXT NOT NULL\n);"
        sql_file = tmp_path / "init.sql"
        sql_file.write_text(sql)

        mapper = SqlMapper()
        mapper.parse_sql_file(sql_file, tmp_path)
        edges = mapper.match()

        assert len(edges) == 1
        assert edges[0].type == EdgeType.MIGRATES
        assert edges[0].to_node == "db.table.users"

    def test_insert(self, tmp_path):
        sql = "INSERT INTO orders (user_id, total) VALUES (1, 99.99);"
        sql_file = tmp_path / "seed.sql"
        sql_file.write_text(sql)

        mapper = SqlMapper()
        mapper.parse_sql_file(sql_file, tmp_path)
        edges = mapper.match()

        assert len(edges) == 1
        assert edges[0].type == EdgeType.WRITES_TABLE
        assert edges[0].to_node == "db.table.orders"

    def test_select(self, tmp_path):
        sql = "SELECT u.email, o.total FROM users u JOIN orders o ON u.id = o.user_id;"
        sql_file = tmp_path / "query.sql"
        sql_file.write_text(sql)

        mapper = SqlMapper()
        mapper.parse_sql_file(sql_file, tmp_path)
        edges = mapper.match()

        assert any(e.type == EdgeType.READS_TABLE for e in edges)
        table_names = {e.metadata["table"] for e in edges}
        assert "users" in table_names

    def test_multi_statement_file(self, tmp_path):
        sql = (
            "CREATE TABLE events (\n    id SERIAL PRIMARY KEY,\n    name TEXT\n);\n\n"
            "CREATE INDEX idx_events_name ON events (name);\n\n"
            "INSERT INTO events (name) VALUES ('signup');\n\n"
            "SELECT * FROM events WHERE name = 'signup';"
        )
        sql_file = tmp_path / "migrations" / "001.sql"
        (tmp_path / "migrations").mkdir()
        sql_file.write_text(sql)

        mapper = SqlMapper()
        mapper.parse_sql_file(sql_file, tmp_path)
        edges = mapper.match()

        op_types = {e.type for e in edges}
        assert EdgeType.MIGRATES in op_types
        assert EdgeType.WRITES_TABLE in op_types
        assert EdgeType.READS_TABLE in op_types

    def test_sql_comments_ignored(self, tmp_path):
        sql = (
            "-- This is a comment\n"
            "/* Block comment:\n"
            "   CREATE TABLE fake_table (id INT);\n"
            "*/\n"
            "CREATE TABLE real_table (id SERIAL PRIMARY KEY);"
        )
        sql_file = tmp_path / "with_comments.sql"
        sql_file.write_text(sql)

        mapper = SqlMapper()
        mapper.parse_sql_file(sql_file, tmp_path)
        edges = mapper.match()

        table_names = {e.metadata["table"] for e in edges}
        assert "real_table" in table_names
        assert "fake_table" not in table_names

    def test_pg_cron_scheduled_job(self, tmp_path):
        sql = (
            "SELECT cron.schedule(\n"
            "    '*/5 * * * *',\n"
            "    'DELETE FROM expired_sessions WHERE created_at < NOW() - INTERVAL ''1 day'''\n"
            ");"
        )
        sql_file = tmp_path / "cron.sql"
        sql_file.write_text(sql)

        mapper = SqlMapper()
        mapper.parse_sql_file(sql_file, tmp_path)
        edges = mapper.match()

        # Should detect DELETE FROM expired_sessions inside cron job
        table_names = {e.metadata["table"] for e in edges}
        assert "expired_sessions" in table_names

    def test_virtual_node_created(self, tmp_path):
        sql = "CREATE TABLE users (id SERIAL PRIMARY KEY);"
        sql_file = tmp_path / "init.sql"
        sql_file.write_text(sql)

        mapper = SqlMapper()
        mapper.parse_sql_file(sql_file, tmp_path)
        nodes = mapper.get_sql_file_nodes()

        assert len(nodes) == 1
        assert nodes[0].id == "sql.init"
        assert nodes[0].metadata["is_virtual"] is True
        assert "users" in nodes[0].metadata["tables"]

    def test_nested_sql_file_node_id(self, tmp_path):
        (tmp_path / "db" / "migrations").mkdir(parents=True)
        sql_file = tmp_path / "db" / "migrations" / "001_init.sql"
        sql_file.write_text("CREATE TABLE users (id SERIAL);")

        mapper = SqlMapper()
        mapper.parse_sql_file(sql_file, tmp_path)
        nodes = mapper.get_sql_file_nodes()

        assert nodes[0].id == "sql.db.migrations.001_init"

    def test_no_sql_files_returns_empty(self):
        mapper = SqlMapper()
        nodes = mapper.get_sql_file_nodes()
        assert nodes == []

    def test_create_index_on_table(self, tmp_path):
        sql = "CREATE UNIQUE INDEX idx_users_email ON users (email);"
        sql_file = tmp_path / "indexes.sql"
        sql_file.write_text(sql)

        mapper = SqlMapper()
        mapper.parse_sql_file(sql_file, tmp_path)
        edges = mapper.match()

        assert any(e.metadata["table"] == "users" for e in edges)
