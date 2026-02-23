"""Tests for lenspr/tool_groups.py — group registry, config persistence, resolution, CLI."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from lenspr.tool_groups import (
    ALL_GROUPS,
    ALWAYS_ON,
    TOOL_GROUPS,
    get_all_tool_names,
    load_tool_config,
    resolve_enabled_tools,
    save_tool_config,
)

# ---------------------------------------------------------------------------
# Group Registry
# ---------------------------------------------------------------------------


class TestGroupRegistry:
    def test_all_groups_populated(self) -> None:
        """Every group has at least one tool."""
        for name, info in TOOL_GROUPS.items():
            assert len(info["tools"]) > 0, f"Group '{name}' has no tools"

    def test_all_groups_have_descriptions(self) -> None:
        """Every group has a non-empty description."""
        for name, info in TOOL_GROUPS.items():
            assert info.get("description"), f"Group '{name}' missing description"

    def test_core_always_on(self) -> None:
        """'core' must be in ALWAYS_ON."""
        assert "core" in ALWAYS_ON

    def test_no_duplicate_tools_across_groups(self) -> None:
        """Each tool name appears in exactly one group."""
        seen: dict[str, str] = {}
        for group_name, info in TOOL_GROUPS.items():
            for tool in info["tools"]:
                assert tool not in seen, (
                    f"Tool '{tool}' appears in both '{seen[tool]}' and '{group_name}'"
                )
                seen[tool] = group_name

    def test_all_groups_list_matches_keys(self) -> None:
        """ALL_GROUPS contains exactly the keys of TOOL_GROUPS."""
        assert set(ALL_GROUPS) == set(TOOL_GROUPS.keys())


# ---------------------------------------------------------------------------
# resolve_enabled_tools
# ---------------------------------------------------------------------------


class TestResolveEnabledTools:
    def test_none_returns_all(self) -> None:
        """None → all tools enabled (backward compat)."""
        result = resolve_enabled_tools(None)
        assert result == get_all_tool_names()

    def test_empty_list_still_has_core(self) -> None:
        """Empty enabled_groups → core still included."""
        result = resolve_enabled_tools([])
        core_tools = set(TOOL_GROUPS["core"]["tools"])
        assert core_tools.issubset(result)

    def test_specific_groups(self) -> None:
        """Only listed groups (plus core) are enabled."""
        result = resolve_enabled_tools(["modification"])
        expected = set(TOOL_GROUPS["core"]["tools"]) | set(TOOL_GROUPS["modification"]["tools"])
        assert result == expected

    def test_unknown_group_ignored(self) -> None:
        """Unknown group names are silently ignored."""
        result = resolve_enabled_tools(["nonexistent_group"])
        core_tools = set(TOOL_GROUPS["core"]["tools"])
        assert result == core_tools

    def test_all_groups_equals_all_tools(self) -> None:
        """Passing all group names → same as None."""
        result = resolve_enabled_tools(list(TOOL_GROUPS.keys()))
        assert result == get_all_tool_names()


# ---------------------------------------------------------------------------
# Config persistence
# ---------------------------------------------------------------------------


class TestConfigPersistence:
    def test_load_missing_file(self, tmp_path: Path) -> None:
        """Missing config file → None."""
        assert load_tool_config(tmp_path / "nope.json") is None

    def test_load_empty_json(self, tmp_path: Path) -> None:
        """JSON without tool_groups key → None."""
        config_path = tmp_path / "config.json"
        config_path.write_text("{}")
        assert load_tool_config(config_path) is None

    def test_load_invalid_json(self, tmp_path: Path) -> None:
        """Corrupt JSON → None."""
        config_path = tmp_path / "config.json"
        config_path.write_text("not json")
        assert load_tool_config(config_path) is None

    def test_save_and_load_roundtrip(self, tmp_path: Path) -> None:
        """Save then load → same groups."""
        config_path = tmp_path / "config.json"
        groups = ["core", "modification", "analysis"]
        save_tool_config(config_path, groups)

        loaded = load_tool_config(config_path)
        assert set(loaded) == set(groups)

    def test_save_preserves_other_keys(self, tmp_path: Path) -> None:
        """Saving tool_groups doesn't clobber other config keys."""
        config_path = tmp_path / "config.json"
        config_path.write_text(json.dumps({"parser_version": "1.0"}))

        save_tool_config(config_path, ["core"])

        config = json.loads(config_path.read_text())
        assert config["parser_version"] == "1.0"
        assert "tool_groups" in config

    def test_save_includes_disabled(self, tmp_path: Path) -> None:
        """Saved config includes both enabled and disabled lists."""
        config_path = tmp_path / "config.json"
        save_tool_config(config_path, ["core", "modification"])

        config = json.loads(config_path.read_text())
        assert "enabled" in config["tool_groups"]
        assert "disabled" in config["tool_groups"]
        assert "modification" in config["tool_groups"]["enabled"]
        # Everything not enabled should be in disabled
        assert len(config["tool_groups"]["disabled"]) > 0

    def test_load_with_enabled_list(self, tmp_path: Path) -> None:
        """Config with explicit enabled list → returns that list."""
        config_path = tmp_path / "config.json"
        config_path.write_text(json.dumps({
            "tool_groups": {"enabled": ["core", "git"]}
        }))

        loaded = load_tool_config(config_path)
        assert set(loaded) == {"core", "git"}


# ---------------------------------------------------------------------------
# get_all_tool_names
# ---------------------------------------------------------------------------


class TestGetAllToolNames:
    def test_returns_set(self) -> None:
        """Returns a set."""
        result = get_all_tool_names()
        assert isinstance(result, set)

    def test_count_matches_groups(self) -> None:
        """Total tool count matches sum of all group tool lists."""
        total = sum(len(info["tools"]) for info in TOOL_GROUPS.values())
        assert len(get_all_tool_names()) == total

    def test_known_tools_present(self) -> None:
        """Key tools are present in the result."""
        all_tools = get_all_tool_names()
        assert "lens_get_node" in all_tools
        assert "lens_check_impact" in all_tools
        assert "lens_vibecheck" in all_tools


# ---------------------------------------------------------------------------
# _generate_tool_listing
# ---------------------------------------------------------------------------


class TestGenerateToolListing:
    def test_all_tools_listed(self) -> None:
        """With None (all enabled), every group appears."""
        from lenspr import _generate_tool_listing

        result = _generate_tool_listing(None)
        assert "## Available Tools" in result
        for group_name in TOOL_GROUPS:
            assert group_name in result

    def test_filtered_groups(self) -> None:
        """Only enabled groups appear in listing."""
        from lenspr import _generate_tool_listing

        # Only core + modification tools
        enabled = set(TOOL_GROUPS["core"]["tools"]) | set(TOOL_GROUPS["modification"]["tools"])
        result = _generate_tool_listing(enabled)
        assert "core" in result
        assert "modification" in result
        # Groups with no enabled tools should not appear
        assert "infrastructure" not in result
        assert "tracing" not in result

    def test_core_marked_always_on(self) -> None:
        """Core group shows (always on) suffix."""
        from lenspr import _generate_tool_listing

        result = _generate_tool_listing(None)
        assert "(always on)" in result

    def test_empty_enabled_shows_only_core(self) -> None:
        """Empty set → no tools listed (not even core since no tools in set)."""
        from lenspr import _generate_tool_listing

        result = _generate_tool_listing(set())
        # No groups should appear since no tool names are enabled
        assert "### " not in result


# ---------------------------------------------------------------------------
# CLI: lenspr tools
# ---------------------------------------------------------------------------


def _run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "lenspr.cli", *args],
        capture_output=True,
        text=True,
        timeout=30,
    )


class TestToolsCli:
    def test_tools_list(self, tmp_path: Path) -> None:
        """tools list shows all groups."""
        result = _run_cli("tools", "--path", str(tmp_path), "list")
        assert result.returncode == 0
        assert "core" in result.stdout
        assert "modification" in result.stdout
        assert "60/60 tools enabled" in result.stdout

    def test_tools_disable(self, tmp_path: Path) -> None:
        """tools disable removes groups from config."""
        (tmp_path / ".lens").mkdir()
        result = _run_cli("tools", "--path", str(tmp_path), "disable", "infrastructure", "tracing")
        assert result.returncode == 0
        assert "Disabled" in result.stdout

        # Verify config
        config = json.loads((tmp_path / ".lens" / "config.json").read_text())
        enabled = config["tool_groups"]["enabled"]
        assert "infrastructure" not in enabled
        assert "tracing" not in enabled
        assert "core" in enabled

    def test_tools_enable(self, tmp_path: Path) -> None:
        """tools enable adds groups back to config."""
        (tmp_path / ".lens").mkdir()
        # First disable
        _run_cli("tools", "--path", str(tmp_path), "disable", "tracing")
        # Then re-enable
        result = _run_cli("tools", "--path", str(tmp_path), "enable", "tracing")
        assert result.returncode == 0
        assert "Enabled: tracing" in result.stdout

    def test_tools_reset(self, tmp_path: Path) -> None:
        """tools reset re-enables all groups."""
        (tmp_path / ".lens").mkdir()
        _run_cli("tools", "--path", str(tmp_path), "disable", "infrastructure")
        result = _run_cli("tools", "--path", str(tmp_path), "reset")
        assert result.returncode == 0
        assert "re-enabled" in result.stdout

    def test_tools_disable_core_rejected(self, tmp_path: Path) -> None:
        """Cannot disable the core group."""
        (tmp_path / ".lens").mkdir()
        result = _run_cli("tools", "--path", str(tmp_path), "disable", "core")
        assert result.returncode == 0
        assert "always on" in result.stdout

    def test_tools_unknown_group(self, tmp_path: Path) -> None:
        """Unknown group name → error."""
        (tmp_path / ".lens").mkdir()
        result = _run_cli("tools", "--path", str(tmp_path), "disable", "nonexistent")
        assert result.returncode != 0

    def test_tools_list_after_disable(self, tmp_path: Path) -> None:
        """Disabled groups show [OFF] in listing."""
        (tmp_path / ".lens").mkdir()
        _run_cli("tools", "--path", str(tmp_path), "disable", "tracing")
        result = _run_cli("tools", "--path", str(tmp_path), "list")
        assert result.returncode == 0
        assert "[OFF] tracing" in result.stdout
        assert "58/60 tools enabled" in result.stdout
