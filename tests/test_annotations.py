"""Tests for semantic annotation functionality.

Tests cover:
1. Batch annotation flow (first-time annotation of entire codebase)
2. Single node annotation when nodes are added/modified
3. Pending annotations queue (MCP server integration)
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lenspr import database
from lenspr.context import LensContext
from lenspr.tools.annotation import (
    VALID_ROLES,
    handle_annotate,
    handle_annotate_batch,
    handle_annotation_stats,
    handle_batch_save_annotations,
    handle_save_annotation,
)


@pytest.fixture
def project(tmp_path: Path) -> LensContext:
    """Create a minimal project with initialized LensPR context."""
    src = tmp_path / "app.py"
    src.write_text(
        "def validate_email(email: str) -> bool:\n"
        '    """Check if email is valid."""\n'
        '    return "@" in email\n'
        "\n"
        "\n"
        "def send_notification(user_id: int, message: str) -> None:\n"
        '    """Send a notification to user."""\n'
        "    print(f'Sending to {user_id}: {message}')\n"
        "\n"
        "\n"
        "class UserService:\n"
        '    """Manages user operations."""\n'
        "\n"
        "    def get_user(self, user_id: int):\n"
        "        return {'id': user_id}\n"
        "\n"
        "    def create_user(self, name: str):\n"
        "        return {'name': name}\n"
    )

    lens_dir = tmp_path / ".lens"
    lens_dir.mkdir()
    database.init_database(lens_dir)

    ctx = LensContext(project_root=tmp_path, lens_dir=lens_dir)
    ctx.full_sync()
    return ctx


class TestAnnotateBatch:
    """Test getting nodes that need annotation (first-time batch flow)."""

    def test_returns_unannotated_nodes(self, project: LensContext) -> None:
        """On first run, all annotatable nodes should be returned."""
        result = handle_annotate_batch({"limit": 50}, project)

        assert result.success
        assert result.data["count"] > 0

        # Should include functions and methods
        node_ids = [n["id"] for n in result.data["nodes"]]
        assert "app.validate_email" in node_ids
        assert "app.send_notification" in node_ids

    def test_filters_by_type(self, project: LensContext) -> None:
        """Should filter by node type."""
        result = handle_annotate_batch(
            {"type_filter": "class", "limit": 50}, project
        )

        assert result.success
        for node in result.data["nodes"]:
            assert node["type"] == "class"

    def test_respects_limit(self, project: LensContext) -> None:
        """Should respect the limit parameter."""
        result = handle_annotate_batch({"limit": 2}, project)

        assert result.success
        assert len(result.data["nodes"]) <= 2

    def test_excludes_already_annotated(self, project: LensContext) -> None:
        """After annotating a node, it should not appear in the batch."""
        # First, annotate one node
        handle_save_annotation(
            {
                "node_id": "app.validate_email",
                "summary": "Validates email format",
                "role": "validator",
            },
            project,
        )

        # Now get batch - should not include the annotated node
        result = handle_annotate_batch({"limit": 50}, project)
        node_ids = [n["id"] for n in result.data["nodes"]]

        assert "app.validate_email" not in node_ids


class TestBatchSaveAnnotations:
    """Test saving multiple annotations at once (Claude Code batch flow)."""

    def test_saves_multiple_annotations(self, project: LensContext) -> None:
        """Should save multiple annotations in one call."""
        annotations = [
            {
                "node_id": "app.validate_email",
                "summary": "Validates email format by checking for @ symbol",
                "role": "validator",
                "side_effects": [],
                "semantic_inputs": ["email_string"],
                "semantic_outputs": ["is_valid_bool"],
            },
            {
                "node_id": "app.send_notification",
                "summary": "Sends notification message to a user",
                "role": "io",
                "side_effects": ["console_output"],
                "semantic_inputs": ["user_id", "message"],
                "semantic_outputs": [],
            },
        ]

        result = handle_batch_save_annotations({"annotations": annotations}, project)

        assert result.success
        assert result.data["saved_count"] == 2
        assert result.data["error_count"] == 0

        # Verify annotations were saved
        node1 = database.get_node("app.validate_email", project.graph_db)
        assert node1.summary == "Validates email format by checking for @ symbol"
        assert node1.role.value == "validator"

        node2 = database.get_node("app.send_notification", project.graph_db)
        assert node2.summary == "Sends notification message to a user"
        assert node2.role.value == "io"

    def test_validates_roles(self, project: LensContext) -> None:
        """Should reject invalid roles."""
        annotations = [
            {
                "node_id": "app.validate_email",
                "summary": "Test",
                "role": "invalid_role",
            },
        ]

        result = handle_batch_save_annotations({"annotations": annotations}, project)

        assert not result.success
        assert result.data["error_count"] == 1
        assert "Invalid role" in result.data["errors"][0]["error"]

    def test_handles_nonexistent_nodes(self, project: LensContext) -> None:
        """Should handle non-existent nodes gracefully."""
        annotations = [
            {
                "node_id": "app.nonexistent_function",
                "summary": "Test",
                "role": "utility",
            },
        ]

        result = handle_batch_save_annotations({"annotations": annotations}, project)

        assert not result.success
        assert result.data["error_count"] == 1
        assert "Node not found" in result.data["errors"][0]["error"]

    def test_partial_success(self, project: LensContext) -> None:
        """Should save valid annotations even if some fail."""
        annotations = [
            {
                "node_id": "app.validate_email",
                "summary": "Valid annotation",
                "role": "validator",
            },
            {
                "node_id": "app.nonexistent",
                "summary": "Invalid - node doesn't exist",
                "role": "utility",
            },
        ]

        result = handle_batch_save_annotations({"annotations": annotations}, project)

        assert not result.success  # Overall failure due to errors
        assert result.data["saved_count"] == 1
        assert result.data["error_count"] == 1

        # Valid annotation should still be saved
        node = database.get_node("app.validate_email", project.graph_db)
        assert node.summary == "Valid annotation"

    def test_empty_annotations_fails(self, project: LensContext) -> None:
        """Should fail if no annotations provided."""
        result = handle_batch_save_annotations({"annotations": []}, project)

        assert not result.success
        assert "No annotations provided" in result.error


class TestSingleAnnotation:
    """Test single node annotation (used when Claude analyzes one node)."""

    def test_get_annotation_context(self, project: LensContext) -> None:
        """lens_annotate should return context for annotation."""
        result = handle_annotate({"node_id": "app.validate_email"}, project)

        assert result.success
        assert result.data["node_id"] == "app.validate_email"
        assert result.data["source_code"] is not None
        assert result.hint is not None  # Should include hint for Claude
        # Hint should tell Claude to only provide summary
        assert "summary" in result.hint
        assert "auto-detected" in result.hint

    def test_save_single_annotation(self, project: LensContext) -> None:
        """lens_save_annotation should save a single annotation."""
        result = handle_save_annotation(
            {
                "node_id": "app.validate_email",
                "summary": "Checks email validity",
                "role": "validator",
                "side_effects": [],
            },
            project,
        )

        assert result.success
        assert result.data["saved"] is True

        # Verify
        node = database.get_node("app.validate_email", project.graph_db)
        assert node.summary == "Checks email validity"
        assert node.role.value == "validator"


class TestAnnotationStats:
    """Test annotation coverage statistics."""

    def test_initial_stats(self, project: LensContext) -> None:
        """Initially all nodes should be unannotated."""
        result = handle_annotation_stats({}, project)

        assert result.success
        assert result.data["total_annotatable"] > 0
        assert result.data["annotated"] == 0

    def test_stats_after_annotation(self, project: LensContext) -> None:
        """Stats should update after annotations."""
        # Annotate some nodes
        handle_batch_save_annotations(
            {
                "annotations": [
                    {"node_id": "app.validate_email", "summary": "Test", "role": "validator"},
                    {"node_id": "app.send_notification", "summary": "Test", "role": "io"},
                ]
            },
            project,
        )

        result = handle_annotation_stats({}, project)

        assert result.success
        assert result.data["annotated"] == 2


class TestPendingAnnotationsQueue:
    """Test the pending annotations queue for auto-annotation."""

    def test_pending_queue_functions(self) -> None:
        """Test the pending annotations queue helper functions."""
        from lenspr.mcp_server import (
            _add_pending_annotations,
            _get_and_clear_pending,
        )
        from lenspr.models import NodeType

        # Clear any existing
        _get_and_clear_pending()

        # Create mock nodes
        class MockNode:
            def __init__(self, node_id: str, name: str, node_type: str, file_path: str):
                self.id = node_id
                self.name = name
                self.type = NodeType(node_type)
                self.file_path = file_path

        nodes = [
            MockNode("app.func1", "func1", "function", "app.py"),
            MockNode("app.func2", "func2", "function", "app.py"),
            MockNode("app", "app", "module", "app.py"),  # Should be filtered out
        ]

        _add_pending_annotations(nodes)

        # Get and clear
        pending = _get_and_clear_pending()

        # Should have 2 nodes (module filtered out)
        assert len(pending) == 2
        assert pending[0]["id"] == "app.func1"
        assert pending[1]["id"] == "app.func2"

        # Queue should be empty now
        pending2 = _get_and_clear_pending()
        assert len(pending2) == 0

    def test_wrap_result_with_pending(self) -> None:
        """Test that tool results get wrapped with pending annotations."""
        import json

        from lenspr.mcp_server import (
            _add_pending_annotations,
            _get_and_clear_pending,
            _wrap_result_with_pending,
        )
        from lenspr.models import NodeType

        # Clear queue
        _get_and_clear_pending()

        # Add a pending node
        class MockNode:
            id = "app.new_func"
            name = "new_func"
            type = NodeType.FUNCTION
            file_path = "app.py"

        _add_pending_annotations([MockNode()])

        # Wrap a result
        original = json.dumps({"success": True, "data": {"test": 1}})
        wrapped = _wrap_result_with_pending(original)

        result = json.loads(wrapped)

        assert "_pending_annotations" in result
        assert result["_pending_annotations"]["count"] == 1
        assert "ACTION REQUIRED" in result["_pending_annotations"]["hint"]
        assert result["_pending_annotations"]["nodes"][0]["id"] == "app.new_func"


class TestValidRoles:
    """Test that all valid roles are properly defined."""

    def test_valid_roles_exist(self) -> None:
        """All expected roles should be in VALID_ROLES."""
        expected = [
            "validator",
            "transformer",
            "io",
            "orchestrator",
            "pure",
            "handler",
            "test",
            "utility",
            "factory",
            "accessor",
        ]
        for role in expected:
            assert role in VALID_ROLES

    def test_all_roles_saveable(self, project: LensContext) -> None:
        """Should be able to save annotations with any valid role."""
        for role in VALID_ROLES:
            result = handle_save_annotation(
                {
                    "node_id": "app.validate_email",
                    "summary": f"Test {role}",
                    "role": role,
                },
                project,
            )
            assert result.success, f"Failed to save role: {role}"


class TestPatternDetection:
    """Test automatic role and side_effects detection from patterns."""

    def test_detect_validator_role(self) -> None:
        """Functions with validate/check patterns should be validators."""
        from lenspr.tools.patterns import detect_role

        assert detect_role("validate_email", "function") == "validator"
        assert detect_role("check_permissions", "function") == "validator"
        assert detect_role("is_valid_token", "function") == "validator"

    def test_detect_io_role(self) -> None:
        """Functions with save/write/send patterns should be io."""
        from lenspr.tools.patterns import detect_role

        assert detect_role("save_user", "function") == "io"
        assert detect_role("write_data", "function") == "io"
        assert detect_role("send_notification", "function") == "io"
        assert detect_role("store_config", "function") == "io"

    def test_detect_factory_role(self) -> None:
        """Functions with create/build/__init__ patterns should be factories."""
        from lenspr.tools.patterns import detect_role

        assert detect_role("create_user", "function") == "factory"
        assert detect_role("build_config", "function") == "factory"
        assert detect_role("__init__", "method") == "factory"

    def test_detect_accessor_role(self) -> None:
        """Functions with get/fetch/load patterns should be accessors."""
        from lenspr.tools.patterns import detect_role

        assert detect_role("get_user", "function") == "accessor"
        assert detect_role("fetch_data", "function") == "accessor"
        assert detect_role("load_config", "function") == "accessor"

    def test_detect_test_role(self) -> None:
        """Functions with test_ prefix should be tests."""
        from lenspr.tools.patterns import detect_role

        assert detect_role("test_validate_email", "function") == "test"
        assert detect_role("test_user_creation", "function") == "test"

    def test_detect_handler_role(self) -> None:
        """Functions with handle/on_ patterns should be handlers."""
        from lenspr.tools.patterns import detect_role

        assert detect_role("handle_request", "function") == "handler"
        assert detect_role("on_click", "method") == "handler"

    def test_detect_class_roles(self) -> None:
        """Classes should have roles based on their names."""
        from lenspr.tools.patterns import detect_role

        assert detect_role("UserService", "class") == "orchestrator"
        assert detect_role("EmailValidator", "class") == "validator"
        assert detect_role("UserRepository", "class") == "io"

    def test_default_to_utility(self) -> None:
        """Unknown patterns should default to utility."""
        from lenspr.tools.patterns import detect_role

        assert detect_role("do_something", "function") == "utility"
        assert detect_role("helper_func", "function") == "utility"

    def test_detect_side_effects(self) -> None:
        """Side effects should be detected from patterns."""
        from lenspr.tools.patterns import detect_side_effects

        effects = detect_side_effects("save_user")
        assert "writes_database" in effects

        effects = detect_side_effects("send_email")
        assert "network_io" in effects

        effects = detect_side_effects("write_log")
        assert "writes_file" in effects or "writes_log" in effects

    def test_auto_annotate_fills_missing(self) -> None:
        """auto_annotate should fill in role and side_effects when not provided."""
        from lenspr.tools.patterns import auto_annotate

        result = auto_annotate(
            name="validate_email",
            node_type="function",
            source_code="def validate_email(email): return '@' in email",
        )

        assert result["role"] == "validator"
        assert isinstance(result["side_effects"], list)

    def test_auto_annotate_preserves_provided(self) -> None:
        """auto_annotate should not override provided values."""
        from lenspr.tools.patterns import auto_annotate

        result = auto_annotate(
            name="validate_email",
            node_type="function",
            provided_role="io",  # Override detection
            provided_side_effects=["custom_effect"],
        )

        assert result["role"] == "io"  # Preserved
        assert result["side_effects"] == ["custom_effect"]  # Preserved

    def test_annotation_auto_fills_role(self, project: LensContext) -> None:
        """When saving annotation without role, it should be auto-detected."""
        result = handle_save_annotation(
            {
                "node_id": "app.validate_email",
                "summary": "Validates email format",
                # No role provided
            },
            project,
        )

        assert result.success
        assert result.data["role"] == "validator"  # Auto-detected
        assert result.data["auto_detected"] is True
