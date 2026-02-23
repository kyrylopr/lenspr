"""Tests for FFI bridge mapper."""

from __future__ import annotations

import pytest

from lenspr.models import EdgeConfidence, EdgeType, Node, NodeType
from lenspr.resolvers.ffi_mapper import (
    FfiMapper,
    NativeBinding,
    _module_name_from_path,
)


def _make_node(
    node_id: str,
    source: str,
    file_path: str = "src/lib/ffi.ts",
    node_type: NodeType = NodeType.FUNCTION,
    start_line: int = 1,
) -> Node:
    return Node(
        id=node_id,
        type=node_type,
        name=node_id.split(".")[-1],
        qualified_name=node_id,
        file_path=file_path,
        start_line=start_line,
        end_line=start_line + source.count("\n"),
        source_code=source,
    )


# ---------------------------------------------------------------------------
# Module name extraction
# ---------------------------------------------------------------------------
class TestModuleNameFromPath:
    def test_node_file(self):
        assert _module_name_from_path("../native/index.node") == "native"

    def test_shared_library(self):
        assert _module_name_from_path("./libcrypto.so") == "crypto"

    def test_natives_directory(self):
        assert _module_name_from_path("../natives") == "natives"

    def test_wasm_module(self):
        assert _module_name_from_path("./pkg/module.wasm") == "module"

    def test_dylib(self):
        assert _module_name_from_path("./libhtml.dylib") == "html"

    def test_named_node_file(self):
        assert _module_name_from_path("./binding.node") == "binding"

    def test_plain_addon(self):
        assert _module_name_from_path("../addon") == "addon"


# ---------------------------------------------------------------------------
# NAPI detection
# ---------------------------------------------------------------------------
class TestNapiDetection:
    def test_require_dot_node(self):
        mapper = FfiMapper()
        nodes = [
            _make_node(
                "src.ffi.loadNative",
                'const native = require("../native/index.node");',
            )
        ]
        bindings = mapper.extract_bindings(nodes)
        assert len(bindings) == 1
        assert bindings[0].bridge_type == "napi"
        assert bindings[0].native_module == "../native/index.node"

    def test_import_from_dot_node(self):
        mapper = FfiMapper()
        nodes = [
            _make_node(
                "src.ffi.importNative",
                'import { hash } from "./binding.node";',
            )
        ]
        bindings = mapper.extract_bindings(nodes)
        assert len(bindings) == 1
        assert bindings[0].bridge_type == "napi"

    def test_import_from_natives_directory(self):
        mapper = FfiMapper()
        nodes = [
            _make_node(
                "src.scraper.extract",
                'import { extractLinks, extractMetadata } from "../natives";',
            )
        ]
        bindings = mapper.extract_bindings(nodes)
        assert len(bindings) == 1
        assert bindings[0].bridge_type == "napi"
        assert bindings[0].native_module == "../natives"

    def test_import_from_binding(self):
        mapper = FfiMapper()
        nodes = [
            _make_node(
                "src.worker.process",
                'import { filterLinks } from "../binding";',
            )
        ]
        bindings = mapper.extract_bindings(nodes)
        assert len(bindings) == 1
        assert bindings[0].bridge_type == "napi"

    def test_import_from_addon_index(self):
        mapper = FfiMapper()
        nodes = [
            _make_node(
                "src.lib.addon",
                'import { compute } from "../addon/index";',
            )
        ]
        bindings = mapper.extract_bindings(nodes)
        assert len(bindings) == 1
        assert bindings[0].native_module == "../addon/index"


# ---------------------------------------------------------------------------
# koffi detection
# ---------------------------------------------------------------------------
class TestKoffiDetection:
    def test_koffi_load(self):
        mapper = FfiMapper()
        source = """
const koffi = require("koffi");
const lib = koffi.load("./libconverter.so");
"""
        nodes = [_make_node("src.converter.init", source)]
        bindings = mapper.extract_bindings(nodes)
        assert len(bindings) == 1
        assert bindings[0].bridge_type == "koffi"
        assert bindings[0].native_module == "./libconverter.so"

    def test_koffi_with_func_bindings(self):
        mapper = FfiMapper()
        source = """
const koffi = require("koffi");
const lib = koffi.load("./libhtml.dylib");
const convert = lib.func("char*", "ConvertHTMLToMarkdown", ["char*"]);
const free = lib.func("void", "FreeCString", ["char*"]);
"""
        nodes = [_make_node("src.html.converter", source)]
        bindings = mapper.extract_bindings(nodes)
        assert len(bindings) == 1
        assert bindings[0].bridge_type == "koffi"
        assert sorted(bindings[0].bound_functions) == [
            "ConvertHTMLToMarkdown",
            "FreeCString",
        ]

    def test_koffi_define(self):
        mapper = FfiMapper()
        source = """
const koffi = require("koffi");
const lib = koffi.load("./libcrypto.dll");
const hash = lib.define("sha256Hash", "char*", ["char*", "int"]);
"""
        nodes = [_make_node("src.crypto.init", source)]
        bindings = mapper.extract_bindings(nodes)
        assert len(bindings) == 1
        assert "sha256Hash" in bindings[0].bound_functions


# ---------------------------------------------------------------------------
# ffi-napi detection
# ---------------------------------------------------------------------------
class TestFfiNapiDetection:
    def test_ffi_library(self):
        mapper = FfiMapper()
        source = """
const ffi = require("ffi-napi");
const libm = ffi.Library("libm", {
    ceil: ["double", ["double"]],
    floor: ["double", ["double"]],
});
"""
        nodes = [_make_node("src.math.init", source)]
        bindings = mapper.extract_bindings(nodes)
        assert len(bindings) == 1
        assert bindings[0].bridge_type == "ffi-napi"
        assert bindings[0].native_module == "libm"


# ---------------------------------------------------------------------------
# WASM detection
# ---------------------------------------------------------------------------
class TestWasmDetection:
    def test_wasm_import(self):
        mapper = FfiMapper()
        source = 'import init from "./module.wasm";'
        nodes = [_make_node("src.wasm.loader", source)]
        bindings = mapper.extract_bindings(nodes)
        assert len(bindings) == 1
        assert bindings[0].bridge_type == "wasm"
        assert bindings[0].native_module == "./module.wasm"

    def test_webassembly_instantiate(self):
        mapper = FfiMapper()
        source = """
const response = await fetch("module.wasm");
const { instance } = await WebAssembly.instantiate(await response.arrayBuffer());
"""
        nodes = [_make_node("src.wasm.loader", source)]
        bindings = mapper.extract_bindings(nodes)
        assert len(bindings) == 1
        assert bindings[0].bridge_type == "wasm"
        assert bindings[0].native_module == "WebAssembly"

    def test_webassembly_instantiate_streaming(self):
        mapper = FfiMapper()
        source = """
const { instance } = await WebAssembly.instantiateStreaming(fetch("module.wasm"));
"""
        nodes = [_make_node("src.wasm.stream", source)]
        bindings = mapper.extract_bindings(nodes)
        assert len(bindings) == 1
        assert bindings[0].bridge_type == "wasm"


# ---------------------------------------------------------------------------
# Edge and virtual node creation
# ---------------------------------------------------------------------------
class TestEdgeCreation:
    def test_creates_calls_native_edges(self):
        mapper = FfiMapper()
        nodes = [
            _make_node(
                "src.ffi.load",
                'import { hash } from "../native/index.node";',
            )
        ]
        mapper.extract_bindings(nodes)
        edges = mapper.match()
        assert len(edges) == 1
        assert edges[0].type == EdgeType.CALLS_NATIVE
        assert edges[0].from_node == "src.ffi.load"
        assert edges[0].to_node == "native.napi.native"
        assert edges[0].confidence == EdgeConfidence.INFERRED
        assert edges[0].metadata["bridge_type"] == "napi"

    def test_deduplicates_edges(self):
        mapper = FfiMapper()
        # Same import in two nodes but same caller
        nodes = [
            _make_node(
                "src.ffi.load",
                'import { a } from "../natives";\nimport { b } from "../natives";',
            )
        ]
        mapper.extract_bindings(nodes)
        edges = mapper.match()
        # Two bindings but same (caller, target) pair → one edge
        assert len(edges) == 1

    def test_koffi_edge_includes_functions(self):
        mapper = FfiMapper()
        source = """
const koffi = require("koffi");
const lib = koffi.load("./libhtml.so");
const convert = lib.func("char*", "ConvertHTML", ["char*"]);
"""
        nodes = [_make_node("src.html.init", source)]
        mapper.extract_bindings(nodes)
        edges = mapper.match()
        assert len(edges) == 1
        assert "ConvertHTML" in edges[0].metadata["functions"]

    def test_creates_virtual_nodes(self):
        mapper = FfiMapper()
        nodes = [
            _make_node(
                "src.ffi.load",
                'import { hash } from "../native/index.node";',
            )
        ]
        mapper.extract_bindings(nodes)
        virtual_nodes = mapper.get_native_nodes()
        assert len(virtual_nodes) == 1
        assert virtual_nodes[0].id == "native.napi.native"
        assert virtual_nodes[0].type == NodeType.BLOCK
        assert virtual_nodes[0].metadata["is_virtual"] is True

    def test_deduplicates_virtual_nodes(self):
        mapper = FfiMapper()
        nodes = [
            _make_node(
                "src.a.load",
                'import { a } from "../native/index.node";',
                file_path="src/a.ts",
            ),
            _make_node(
                "src.b.load",
                'import { b } from "../native/index.node";',
                file_path="src/b.ts",
            ),
        ]
        mapper.extract_bindings(nodes)
        virtual_nodes = mapper.get_native_nodes()
        assert len(virtual_nodes) == 1


# ---------------------------------------------------------------------------
# Filtering
# ---------------------------------------------------------------------------
class TestFiltering:
    def test_skips_python_files(self):
        mapper = FfiMapper()
        nodes = [
            _make_node(
                "app.main",
                'import native from "../natives";',
                file_path="app/main.py",
            )
        ]
        bindings = mapper.extract_bindings(nodes)
        assert len(bindings) == 0

    def test_skips_test_files(self):
        mapper = FfiMapper()
        nodes = [
            _make_node(
                "test.ffi",
                'import { hash } from "../native/index.node";',
                file_path="src/__tests__/ffi.test.ts",
            )
        ]
        bindings = mapper.extract_bindings(nodes)
        assert len(bindings) == 0

    def test_skips_nodes_without_source(self):
        mapper = FfiMapper()
        node = Node(
            id="src.empty",
            type=NodeType.FUNCTION,
            name="empty",
            qualified_name="src.empty",
            file_path="src/empty.ts",
            start_line=1,
            end_line=1,
            source_code="",
        )
        bindings = mapper.extract_bindings([node])
        assert len(bindings) == 0

    def test_ignores_regular_imports(self):
        mapper = FfiMapper()
        nodes = [
            _make_node(
                "src.utils.helper",
                'import { debounce } from "lodash";\nimport { useState } from "react";',
            )
        ]
        bindings = mapper.extract_bindings(nodes)
        assert len(bindings) == 0


# ---------------------------------------------------------------------------
# Multiple bindings in one file
# ---------------------------------------------------------------------------
class TestMultipleBindings:
    def test_multiple_bridge_types(self):
        mapper = FfiMapper()
        source = """
import { extractLinks } from "../natives";
const koffi = require("koffi");
const lib = koffi.load("./libconvert.so");
const { instance } = await WebAssembly.instantiate(buffer);
"""
        nodes = [_make_node("src.scraper.init", source)]
        bindings = mapper.extract_bindings(nodes)
        bridge_types = {b.bridge_type for b in bindings}
        assert "napi" in bridge_types
        assert "koffi" in bridge_types
        assert "wasm" in bridge_types

    def test_multiple_files(self):
        mapper = FfiMapper()
        nodes = [
            _make_node(
                "src.html.extract",
                'import { extractLinks } from "../natives";',
                file_path="src/html/extract.ts",
            ),
            _make_node(
                "src.converter.init",
                'const lib = koffi.load("./libconvert.so");',
                file_path="src/converter/init.ts",
            ),
        ]
        bindings = mapper.extract_bindings(nodes)
        assert len(bindings) == 2
        edges = mapper.match()
        assert len(edges) == 2
        virtual_nodes = mapper.get_native_nodes()
        assert len(virtual_nodes) == 2
