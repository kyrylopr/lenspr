"""Tests for _reload_lenspr_module_if_needed in lenspr.tools.modification.

This function synchronously reloads a lenspr module after its source file is
patched on disk, eliminating the file-watcher debounce delay so the next
tool call sees the updated handler immediately.
"""

import sys
import types
from unittest.mock import patch

import pytest

from lenspr.tools.modification import _reload_lenspr_module_if_needed


class TestReloadLensprModuleIfNeeded:
    """Tests for the synchronous hot-reload helper added in the vibecoding-safety session."""

    # ------------------------------------------------------------------
    # Early-return guards — these paths must NOT call importlib.reload
    # ------------------------------------------------------------------

    def test_none_input_is_skipped(self):
        """None file_path → function returns without reloading anything."""
        with patch("importlib.reload") as mock_reload:
            _reload_lenspr_module_if_needed(None)
        mock_reload.assert_not_called()

    def test_empty_string_is_skipped(self):
        """Empty string file_path → function returns without reloading."""
        with patch("importlib.reload") as mock_reload:
            _reload_lenspr_module_if_needed("")
        mock_reload.assert_not_called()

    def test_non_lenspr_file_is_skipped(self):
        """Path not under lenspr/ → function returns without reloading.

        e.g. tests/foo.py should be ignored — we only hot-reload lenspr's own modules.
        """
        with patch("importlib.reload") as mock_reload:
            _reload_lenspr_module_if_needed("tests/foo.py")
        mock_reload.assert_not_called()

    def test_non_python_extension_is_skipped(self):
        """Path not ending in .py → function returns without reloading."""
        with patch("importlib.reload") as mock_reload:
            _reload_lenspr_module_if_needed("lenspr/tools/safety.txt")
        mock_reload.assert_not_called()

    def test_module_not_in_sys_modules_does_not_crash(self):
        """Module not yet imported → reload is NOT called and no exception raised.

        This can happen if a lenspr tool was never invoked in the current process.
        """
        synthetic_name = "lenspr._test_guard_not_a_real_module"
        # Guarantee the key is absent from sys.modules
        sys.modules.pop(synthetic_name, None)

        with patch("importlib.reload") as mock_reload:
            _reload_lenspr_module_if_needed("lenspr/_test_guard_not_a_real_module.py")

        mock_reload.assert_not_called()

    # ------------------------------------------------------------------
    # __init__ normalisation
    # ------------------------------------------------------------------

    def test_init_py_normalised_to_package_name(self):
        """lenspr/tools/__init__.py must map to 'lenspr.tools', not 'lenspr.tools.__init__'.

        If the module name is not normalised, sys.modules lookup fails and the
        real lenspr.tools package is never reloaded.
        """
        fake_tools = types.ModuleType("lenspr.tools")

        with patch.dict(sys.modules, {"lenspr.tools": fake_tools}):
            with patch("importlib.reload") as mock_reload:
                _reload_lenspr_module_if_needed("lenspr/tools/__init__.py")

        # Primary target must be reloaded (cascade may add dependent modules)
        mock_reload.assert_any_call(fake_tools)

    # ------------------------------------------------------------------
    # Reload success and failure paths
    # ------------------------------------------------------------------

    def test_successful_reload_calls_importlib_reload(self):
        """When the module is loaded, importlib.reload is called with the live module object."""
        fake_module = types.ModuleType("lenspr.tools.safety")

        with patch.dict(sys.modules, {"lenspr.tools.safety": fake_module}):
            with patch("importlib.reload") as mock_reload:
                _reload_lenspr_module_if_needed("lenspr/tools/safety.py")

        # Primary target must be reloaded (cascade may add dependent modules)
        mock_reload.assert_any_call(fake_module)

    def test_reload_failure_is_swallowed(self):
        """ImportError during reload must not propagate.

        The caller (handle_update_node) has already applied the patch and returned
        a success response.  Crashing here would mislead the user into thinking
        the patch failed.
        """
        fake_module = types.ModuleType("lenspr.tools.safety")

        with patch.dict(sys.modules, {"lenspr.tools.safety": fake_module}):
            with patch("importlib.reload", side_effect=ImportError("circular import")):
                # Must not raise — the patch was already applied successfully
                _reload_lenspr_module_if_needed("lenspr/tools/safety.py")

    def test_cascade_reloads_claude_tools_after_tools_submodule(self):
        """After reloading a lenspr.tools.* module, claude_tools must also be reloaded.

        claude_tools has module-level `from lenspr.tools import X` bindings that
        become stale after lenspr.tools.* is reloaded. Without cascade, the MCP
        server runs old handler code for the entire session.
        """
        fake_safety = types.ModuleType("lenspr.tools.safety")
        fake_claude = types.ModuleType("lenspr.claude_tools")

        modules = {"lenspr.tools.safety": fake_safety, "lenspr.claude_tools": fake_claude}
        with patch.dict(sys.modules, modules):
            with patch("importlib.reload") as mock_reload:
                _reload_lenspr_module_if_needed("lenspr/tools/safety.py")

        mock_reload.assert_any_call(fake_safety)
        mock_reload.assert_any_call(fake_claude)

    def test_no_cascade_for_non_tools_modules(self):
        """Modules outside lenspr.tools.* should NOT trigger cascade reload."""
        fake_ctx = types.ModuleType("lenspr.context")

        with patch.dict(sys.modules, {"lenspr.context": fake_ctx}):
            with patch("importlib.reload") as mock_reload:
                _reload_lenspr_module_if_needed("lenspr/context.py")

        mock_reload.assert_called_once_with(fake_ctx)

    def test_arbitrary_exception_in_reload_is_swallowed(self):
        """Any exception (not just ImportError) during reload must be silently ignored."""
        fake_module = types.ModuleType("lenspr.tools.modification")

        with patch.dict(sys.modules, {"lenspr.tools.modification": fake_module}):
            with patch("importlib.reload", side_effect=RuntimeError("unexpected")):
                _reload_lenspr_module_if_needed("lenspr/tools/modification.py")
