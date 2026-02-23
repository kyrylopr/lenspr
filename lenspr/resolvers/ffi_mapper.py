"""FFI bridge mapper.

Detects code calling native modules via:
  TS/JS:
  - NAPI (Rust/C++): import { hash } from "../native/index.node"
  - NAPI re-export: import { extractLinks } from "../natives"
  - koffi (Go/C): koffi.load("./lib.so"), lib.func("int", "add", [...])
  - ffi-napi / node-ffi: new ffi.Library("libname", {...})
  - WASM: import init from "./module.wasm", WebAssembly.instantiate(...)
  - child_process: spawn("binary"), execFile("binary"), exec("command")
  - bindings(): require("bindings")("addon"), bindings("addon")
  Python:
  - ctypes: ctypes.CDLL("lib.so"), ctypes.cdll.LoadLibrary("lib")
  - cffi: ffi.dlopen("lib.so")

Creates CALLS_NATIVE edges from caller nodes to virtual native module nodes.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import PurePosixPath

from lenspr.models import (
    Edge,
    EdgeConfidence,
    EdgeSource,
    EdgeType,
    Node,
    NodeType,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# FFI detection patterns
# ---------------------------------------------------------------------------

# NAPI: require("./index.node") or import from "*.node"
_NAPI_IMPORT_RE = re.compile(
    r"""(?:require\s*\(\s*|from\s+)[`"']([^`"']*\.node)[`"']""",
)

# NAPI re-export: import from "../native" or "../binding" or "../addon"
# Common convention: a directory that re-exports .node bindings
_NAPI_REEXPORT_RE = re.compile(
    r"""(?:require\s*\(\s*|from\s+)[`"'](\.\.?/[^`"']*(?:native|binding|addon)s?(?:/index)?)[`"']""",
)

# koffi: koffi.load("./path/to/lib.so|dll|dylib")
_KOFFI_LOAD_RE = re.compile(
    r"""koffi\.load\s*\(\s*[`"']([^`"']+)[`"']\s*\)""",
)

# koffi function binding: lib.func("return_type", "func_name", [...])
_KOFFI_FUNC_RE = re.compile(
    r"""\.func\s*\(\s*[`"'][^`"']*[`"']\s*,\s*[`"']([^`"']+)[`"']""",
)

# koffi define: lib.define("funcName", ...) or koffi.define(...)
_KOFFI_DEFINE_RE = re.compile(
    r"""\.define\s*\(\s*[`"']([^`"']+)[`"']""",
)

# ffi-napi / node-ffi: new ffi.Library("libname", { func: [...] })
_FFI_LIBRARY_RE = re.compile(
    r"""ffi\.Library\s*\(\s*[`"']([^`"']+)[`"']""",
)

# WASM: import from "*.wasm" or WebAssembly.instantiate/compile
_WASM_IMPORT_RE = re.compile(
    r"""(?:from\s+[`"']([^`"']*\.wasm)[`"']|WebAssembly\.(?:instantiate|instantiateStreaming|compile)\s*\()""",
)

# child_process: spawn("binary"), execFile("binary"), exec("cmd"), fork("script")
# Matches when child_process is imported in the same source
_CHILD_PROCESS_IMPORT_RE = re.compile(
    r"""(?:from\s+[`"'](?:node:)?child_process[`"']|require\s*\(\s*[`"'](?:node:)?child_process[`"']\s*\))""",
)
_CHILD_PROCESS_CALL_RE = re.compile(
    r"""(?:spawn|spawnSync|execFile|execFileSync|exec|execSync|fork)\s*\(\s*[`"']([^`"']+)[`"']""",
)

# bindings(): require("bindings")("addon") or bindings("addon")
_BINDINGS_RE = re.compile(
    r"""(?:require\s*\(\s*[`"']bindings[`"']\s*\)\s*\(\s*[`"']([^`"']+)[`"']|bindings\s*\(\s*[`"']([^`"']+)[`"'])\s*\)""",
)

# Python ctypes: ctypes.CDLL("lib.so"), ctypes.cdll.LoadLibrary("lib")
_CTYPES_RE = re.compile(
    r"""ctypes\.(?:CDLL|WinDLL|OleDLL|PyDLL|cdll\.LoadLibrary|windll\.LoadLibrary)\s*\(\s*["']([^"']+)["']""",
)

# Python cffi: ffi.dlopen("lib.so")
_CFFI_DLOPEN_RE = re.compile(
    r"""\.dlopen\s*\(\s*["']([^"']+)["']""",
)

# TS/JS file extensions
_TS_JS_EXTENSIONS = {".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"}


@dataclass
class NativeBinding:
    """A native FFI binding detected in code."""

    bridge_type: str  # "napi", "koffi", "ffi-napi", "wasm", "child_process", "bindings", "ctypes", "cffi"
    native_module: str  # Import source or library path
    caller_node_id: str  # Node ID that imports/uses the binding
    file_path: str
    line: int
    bound_functions: list[str] = field(default_factory=list)


def _is_ts_js_file(file_path: str) -> bool:
    """Check if file is TypeScript or JavaScript."""
    return PurePosixPath(file_path).suffix.lower() in _TS_JS_EXTENSIONS


def _is_python_file(file_path: str) -> bool:
    """Check if file is Python."""
    return PurePosixPath(file_path).suffix.lower() == ".py"


def _is_test_file(file_path: str) -> bool:
    """Skip test files to avoid false positives."""
    import os

    basename = os.path.basename(file_path)
    parts = file_path.replace("\\", "/").split("/")
    return (
        basename.startswith("test_")
        or basename.startswith("test.")
        or basename.endswith(".test.ts")
        or basename.endswith(".test.js")
        or basename.endswith(".spec.ts")
        or basename.endswith(".spec.js")
        or "__tests__" in parts
    )


def _module_name_from_path(native_module: str) -> str:
    """Extract a clean module name from a native module path.

    Examples:
        "../native/index.node" → "native"
        "./libcrypto.so"       → "libcrypto"
        "../natives"           → "natives"
        "./pkg/module.wasm"    → "module"
    """
    p = PurePosixPath(native_module)
    name = p.stem  # filename without extension
    if name == "index":
        # Use parent directory name
        name = p.parent.name or "native"
    # Strip "lib" prefix for shared libraries
    if name.startswith("lib") and len(name) > 3:
        name = name[3:]
    return name


class FfiMapper:
    """Detect FFI bridges (NAPI, koffi, ffi-napi, WASM) and create CALLS_NATIVE edges."""

    def __init__(self) -> None:
        self._bindings: list[NativeBinding] = []
        self._edge_counter = 0

    def extract_bindings(self, nodes: list[Node]) -> list[NativeBinding]:
        """Scan nodes for FFI import and call patterns.

        Supports TS/JS (NAPI, koffi, ffi-napi, WASM, child_process, bindings)
        and Python (ctypes, cffi).

        Returns the list of detected bindings (also stored internally for match()).
        """
        self._bindings = []

        for node in nodes:
            if not node.file_path:
                continue
            if _is_test_file(node.file_path):
                continue
            if not node.source_code:
                continue

            is_ts_js = _is_ts_js_file(node.file_path)
            is_py = _is_python_file(node.file_path)
            if not is_ts_js and not is_py:
                continue

            source = node.source_code
            base_line = (node.start_line or 1) - 1

            if is_ts_js:
                self._extract_ts_js_bindings(node, source, base_line)
            elif is_py:
                self._extract_python_bindings(node, source, base_line)

        logger.info(
            "FFI mapper: found %d native bindings (%s)",
            len(self._bindings),
            ", ".join(
                f"{bt}={sum(1 for b in self._bindings if b.bridge_type == bt)}"
                for bt in sorted({b.bridge_type for b in self._bindings})
            )
            if self._bindings
            else "none",
        )

        return self._bindings

    def _extract_ts_js_bindings(
        self, node: Node, source: str, base_line: int
    ) -> None:
        """Extract TS/JS FFI bindings from a single node."""
        # --- NAPI .node imports ---
        for m in _NAPI_IMPORT_RE.finditer(source):
            line_num = source[: m.start()].count("\n") + 1
            self._bindings.append(
                NativeBinding(
                    bridge_type="napi",
                    native_module=m.group(1),
                    caller_node_id=node.id,
                    file_path=node.file_path or "",
                    line=line_num + base_line,
                )
            )

        # --- NAPI re-export (../natives, ../binding, ../addon) ---
        for m in _NAPI_REEXPORT_RE.finditer(source):
            module_path = m.group(1)
            if module_path.endswith(".node"):
                continue
            line_num = source[: m.start()].count("\n") + 1
            self._bindings.append(
                NativeBinding(
                    bridge_type="napi",
                    native_module=module_path,
                    caller_node_id=node.id,
                    file_path=node.file_path or "",
                    line=line_num + base_line,
                )
            )

        # --- koffi.load() ---
        for m in _KOFFI_LOAD_RE.finditer(source):
            lib_path = m.group(1)
            line_num = source[: m.start()].count("\n") + 1
            bound_funcs = [
                fm.group(1) for fm in _KOFFI_FUNC_RE.finditer(source)
            ]
            bound_funcs.extend(
                dm.group(1) for dm in _KOFFI_DEFINE_RE.finditer(source)
            )
            self._bindings.append(
                NativeBinding(
                    bridge_type="koffi",
                    native_module=lib_path,
                    caller_node_id=node.id,
                    file_path=node.file_path or "",
                    line=line_num + base_line,
                    bound_functions=sorted(set(bound_funcs)),
                )
            )

        # --- ffi-napi / node-ffi Library ---
        for m in _FFI_LIBRARY_RE.finditer(source):
            lib_name = m.group(1)
            line_num = source[: m.start()].count("\n") + 1
            self._bindings.append(
                NativeBinding(
                    bridge_type="ffi-napi",
                    native_module=lib_name,
                    caller_node_id=node.id,
                    file_path=node.file_path or "",
                    line=line_num + base_line,
                )
            )

        # --- WASM ---
        for m in _WASM_IMPORT_RE.finditer(source):
            wasm_module = m.group(1) or "WebAssembly"
            line_num = source[: m.start()].count("\n") + 1
            self._bindings.append(
                NativeBinding(
                    bridge_type="wasm",
                    native_module=wasm_module,
                    caller_node_id=node.id,
                    file_path=node.file_path or "",
                    line=line_num + base_line,
                )
            )

        # --- child_process: spawn/exec/execFile/fork ---
        if _CHILD_PROCESS_IMPORT_RE.search(source):
            for m in _CHILD_PROCESS_CALL_RE.finditer(source):
                binary = m.group(1)
                line_num = source[: m.start()].count("\n") + 1
                self._bindings.append(
                    NativeBinding(
                        bridge_type="child_process",
                        native_module=binary,
                        caller_node_id=node.id,
                        file_path=node.file_path or "",
                        line=line_num + base_line,
                    )
                )

        # --- bindings(): require("bindings")("addon") ---
        for m in _BINDINGS_RE.finditer(source):
            addon_name = m.group(1) or m.group(2)
            if not addon_name:
                continue
            line_num = source[: m.start()].count("\n") + 1
            self._bindings.append(
                NativeBinding(
                    bridge_type="bindings",
                    native_module=addon_name,
                    caller_node_id=node.id,
                    file_path=node.file_path or "",
                    line=line_num + base_line,
                )
            )

    def _extract_python_bindings(
        self, node: Node, source: str, base_line: int
    ) -> None:
        """Extract Python FFI bindings (ctypes, cffi) from a single node."""
        # --- ctypes: CDLL, WinDLL, cdll.LoadLibrary ---
        for m in _CTYPES_RE.finditer(source):
            lib_path = m.group(1)
            line_num = source[: m.start()].count("\n") + 1
            self._bindings.append(
                NativeBinding(
                    bridge_type="ctypes",
                    native_module=lib_path,
                    caller_node_id=node.id,
                    file_path=node.file_path or "",
                    line=line_num + base_line,
                )
            )

        # --- cffi: ffi.dlopen("lib.so") ---
        for m in _CFFI_DLOPEN_RE.finditer(source):
            lib_path = m.group(1)
            line_num = source[: m.start()].count("\n") + 1
            self._bindings.append(
                NativeBinding(
                    bridge_type="cffi",
                    native_module=lib_path,
                    caller_node_id=node.id,
                    file_path=node.file_path or "",
                    line=line_num + base_line,
                )
            )

    def match(self) -> list[Edge]:
        """Create CALLS_NATIVE edges from detected bindings."""
        edges: list[Edge] = []
        seen: set[tuple[str, str]] = set()

        for binding in self._bindings:
            module_name = _module_name_from_path(binding.native_module)
            target_id = f"native.{binding.bridge_type}.{module_name}"

            key = (binding.caller_node_id, target_id)
            if key in seen:
                continue
            seen.add(key)

            self._edge_counter += 1
            metadata: dict = {
                "bridge_type": binding.bridge_type,
                "native_module": binding.native_module,
            }
            if binding.bound_functions:
                metadata["functions"] = binding.bound_functions

            edges.append(
                Edge(
                    id=f"ffi_edge_{self._edge_counter}",
                    from_node=binding.caller_node_id,
                    to_node=target_id,
                    type=EdgeType.CALLS_NATIVE,
                    line_number=binding.line,
                    confidence=EdgeConfidence.INFERRED,
                    source=EdgeSource.STATIC,
                    metadata=metadata,
                )
            )

        return edges

    def get_native_nodes(self) -> list[Node]:
        """Create virtual nodes for detected native modules."""
        nodes: list[Node] = []
        seen: set[str] = set()

        for binding in self._bindings:
            module_name = _module_name_from_path(binding.native_module)
            node_id = f"native.{binding.bridge_type}.{module_name}"

            if node_id in seen:
                continue
            seen.add(node_id)

            source_parts = [
                f"# Native module: {module_name} ({binding.bridge_type})",
                f"# Source: {binding.native_module}",
            ]
            if binding.bound_functions:
                source_parts.append(
                    f"# Functions: {', '.join(binding.bound_functions)}"
                )

            metadata = {
                "bridge_type": binding.bridge_type,
                "native_module": binding.native_module,
                "is_virtual": True,
            }

            nodes.append(
                Node(
                    id=node_id,
                    type=NodeType.BLOCK,
                    name=module_name,
                    qualified_name=node_id,
                    file_path=binding.file_path,
                    start_line=binding.line,
                    end_line=binding.line,
                    source_code="\n".join(source_parts),
                    docstring=f"Native {binding.bridge_type} module: {module_name}",
                    metadata=metadata,
                )
            )

        return nodes
