"""Tests for lenspr/resolvers/lsp_client.py — LSPClient."""

from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from lenspr.resolvers.lsp_client import (
    LSPClient,
    LSPError,
    Location,
    SymbolInfo,
)
from lenspr.resolvers.config import (
    get_language_for_extension,
    get_server_config,
    is_server_available,
)


# ---------------------------------------------------------------------------
# Mock LSP server script — echoes back predictable responses
# ---------------------------------------------------------------------------

MOCK_SERVER_SCRIPT = '''
import json
import sys

def read_message():
    """Read a JSON-RPC message from stdin."""
    content_length = None
    while True:
        line = sys.stdin.buffer.readline()
        if not line:
            return None
        line_str = line.decode("ascii").strip()
        if not line_str:
            break
        if line_str.lower().startswith("content-length:"):
            content_length = int(line_str.split(":", 1)[1].strip())
    if content_length is None:
        return None
    body = sys.stdin.buffer.read(content_length)
    return json.loads(body)

def write_message(msg):
    """Write a JSON-RPC message to stdout."""
    body = json.dumps(msg).encode("utf-8")
    header = f"Content-Length: {len(body)}\\r\\n\\r\\n".encode("ascii")
    sys.stdout.buffer.write(header + body)
    sys.stdout.buffer.flush()

def main():
    while True:
        msg = read_message()
        if msg is None:
            break

        method = msg.get("method", "")
        msg_id = msg.get("id")

        # Notifications (no id) — just consume
        if msg_id is None:
            if method == "exit":
                break
            continue

        # Requests — respond based on method
        if method == "initialize":
            write_message({
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "capabilities": {
                        "definitionProvider": True,
                        "referencesProvider": True,
                        "documentSymbolProvider": True,
                    }
                },
            })
        elif method == "textDocument/definition":
            write_message({
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "uri": "file:///project/target.py",
                    "range": {
                        "start": {"line": 10, "character": 4},
                        "end": {"line": 10, "character": 20},
                    },
                },
            })
        elif method == "textDocument/references":
            write_message({
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": [
                    {
                        "uri": "file:///project/a.py",
                        "range": {
                            "start": {"line": 5, "character": 0},
                            "end": {"line": 5, "character": 10},
                        },
                    },
                    {
                        "uri": "file:///project/b.py",
                        "range": {
                            "start": {"line": 20, "character": 8},
                            "end": {"line": 20, "character": 18},
                        },
                    },
                ],
            })
        elif method == "textDocument/documentSymbol":
            write_message({
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": [
                    {
                        "name": "MyClass",
                        "kind": 5,
                        "range": {
                            "start": {"line": 0, "character": 0},
                            "end": {"line": 20, "character": 0},
                        },
                        "children": [
                            {
                                "name": "my_method",
                                "kind": 6,
                                "range": {
                                    "start": {"line": 5, "character": 4},
                                    "end": {"line": 10, "character": 0},
                                },
                                "children": [],
                            },
                        ],
                    },
                ],
            })
        elif method == "shutdown":
            write_message({
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": None,
            })
        else:
            write_message({
                "jsonrpc": "2.0",
                "id": msg_id,
                "error": {"code": -32601, "message": f"Unknown method: {method}"},
            })

if __name__ == "__main__":
    main()
'''


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_server_path(tmp_path: Path) -> Path:
    """Write the mock LSP server script to a temp file."""
    script = tmp_path / "mock_lsp_server.py"
    script.write_text(MOCK_SERVER_SCRIPT)
    return script


@pytest.fixture
def client(mock_server_path: Path, tmp_path: Path) -> LSPClient:
    """An LSPClient connected to the mock server."""
    c = LSPClient(timeout=10.0)
    c.start([sys.executable, str(mock_server_path)], tmp_path)
    yield c
    c.shutdown()


# ---------------------------------------------------------------------------
# Tests — JSON-RPC Transport
# ---------------------------------------------------------------------------


class TestJsonRpcTransport:
    def test_start_and_shutdown(
        self, mock_server_path: Path, tmp_path: Path
    ) -> None:
        """Client can start and gracefully shut down a server."""
        c = LSPClient(timeout=5.0)
        c.start([sys.executable, str(mock_server_path)], tmp_path)
        assert c._process is not None
        assert c._process.poll() is None  # still running
        c.shutdown()
        assert c._process is None

    def test_start_nonexistent_binary_raises(self, tmp_path: Path) -> None:
        """Starting with a non-existent binary raises LSPError."""
        c = LSPClient()
        with pytest.raises(LSPError, match="not found"):
            c.start(["nonexistent-lsp-server-xyz"], tmp_path)

    def test_double_start_raises(
        self, mock_server_path: Path, tmp_path: Path
    ) -> None:
        """Starting an already-started client raises LSPError."""
        c = LSPClient(timeout=5.0)
        c.start([sys.executable, str(mock_server_path)], tmp_path)
        try:
            with pytest.raises(LSPError, match="already started"):
                c.start([sys.executable, str(mock_server_path)], tmp_path)
        finally:
            c.shutdown()


# ---------------------------------------------------------------------------
# Tests — LSP Protocol
# ---------------------------------------------------------------------------


class TestLSPProtocol:
    def test_initialize(self, client: LSPClient) -> None:
        """Initialize handshake returns server capabilities."""
        result = client.initialize()
        assert result["capabilities"]["definitionProvider"] is True

    def test_definition(self, client: LSPClient) -> None:
        """textDocument/definition returns a Location."""
        client.initialize()
        loc = client.definition("/project/source.py", line=5, col=10)
        assert loc is not None
        assert isinstance(loc, Location)
        assert loc.uri == "file:///project/target.py"
        assert loc.line == 10
        assert loc.character == 4

    def test_references(self, client: LSPClient) -> None:
        """textDocument/references returns a list of Locations."""
        client.initialize()
        refs = client.references("/project/source.py", line=5, col=10)
        assert len(refs) == 2
        assert refs[0].uri == "file:///project/a.py"
        assert refs[0].line == 5
        assert refs[1].uri == "file:///project/b.py"
        assert refs[1].line == 20

    def test_document_symbols(self, client: LSPClient) -> None:
        """textDocument/documentSymbol returns hierarchical symbols."""
        client.initialize()
        symbols = client.document_symbols("/project/source.py")
        assert len(symbols) == 1
        assert symbols[0].name == "MyClass"
        assert symbols[0].kind == 5
        assert len(symbols[0].children) == 1
        assert symbols[0].children[0].name == "my_method"

    def test_did_open_and_close(self, client: LSPClient) -> None:
        """didOpen and didClose notifications don't raise."""
        client.initialize()
        client.did_open("/project/test.py", "python", "x = 1\n")
        client.did_close("/project/test.py")


# ---------------------------------------------------------------------------
# Tests — Location parsing
# ---------------------------------------------------------------------------


class TestLocationParsing:
    def test_parse_location_link(self) -> None:
        """LocationLink format (targetUri) is correctly parsed."""
        loc = LSPClient._parse_single_location({
            "targetUri": "file:///a.py",
            "targetRange": {
                "start": {"line": 3, "character": 2},
                "end": {"line": 3, "character": 10},
            },
        })
        assert loc is not None
        assert loc.uri == "file:///a.py"
        assert loc.line == 3
        assert loc.character == 2

    def test_parse_location_standard(self) -> None:
        """Standard Location format (uri + range) is correctly parsed."""
        loc = LSPClient._parse_single_location({
            "uri": "file:///b.py",
            "range": {
                "start": {"line": 7, "character": 0},
                "end": {"line": 7, "character": 5},
            },
        })
        assert loc is not None
        assert loc.uri == "file:///b.py"
        assert loc.line == 7

    def test_parse_null_returns_none(self) -> None:
        """Null/empty result returns None."""
        c = LSPClient()
        assert c._parse_location(None) is None
        assert c._parse_location([]) is None

    def test_file_path_property(self) -> None:
        """Location.file_path strips file:// prefix."""
        loc = Location(uri="file:///home/user/project/main.py", line=0, character=0)
        assert loc.file_path == "/home/user/project/main.py"


# ---------------------------------------------------------------------------
# Tests — Error handling
# ---------------------------------------------------------------------------


class TestErrorHandling:
    def test_timeout_raises(self, tmp_path: Path) -> None:
        """Request to a server that never responds times out."""
        # Write a server that reads but never responds
        script = tmp_path / "slow_server.py"
        script.write_text(
            "import sys, time\n"
            "while True:\n"
            "    line = sys.stdin.buffer.readline()\n"
            "    if not line: break\n"
        )
        c = LSPClient(timeout=1.0)
        c.start([sys.executable, str(script)], tmp_path)
        try:
            with pytest.raises(LSPError, match="timed out"):
                c._send_request("initialize", {})
        finally:
            c.shutdown()

    def test_server_error_response(
        self, client: LSPClient
    ) -> None:
        """Server returning an error response raises LSPError."""
        client.initialize()
        with pytest.raises(LSPError, match="Unknown method"):
            client._send_request("textDocument/foobar", {})

    def test_context_manager(
        self, mock_server_path: Path, tmp_path: Path
    ) -> None:
        """LSPClient works as a context manager."""
        with LSPClient(timeout=5.0) as c:
            c.start([sys.executable, str(mock_server_path)], tmp_path)
            result = c.initialize()
            assert result["capabilities"]["definitionProvider"] is True
        # After __exit__, process should be cleaned up
        assert c._process is None


# ---------------------------------------------------------------------------
# Tests — Config
# ---------------------------------------------------------------------------


class TestConfig:
    def test_get_server_config_python(self) -> None:
        config = get_server_config("python")
        assert config is not None
        assert "pyright-langserver" in config["cmd"][0]

    def test_get_server_config_unknown(self) -> None:
        assert get_server_config("brainfuck") is None

    def test_get_language_for_extension(self) -> None:
        assert get_language_for_extension(".py") == "python"
        assert get_language_for_extension(".ts") == "typescript"
        assert get_language_for_extension(".go") == "go"
        assert get_language_for_extension(".xyz") is None
