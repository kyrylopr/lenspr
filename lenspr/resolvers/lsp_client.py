"""Generic LSP client over stdin/stdout JSON-RPC 2.0.

Manages a long-running language server subprocess, sending LSP requests
and receiving responses via the standard Content-Length framing protocol.
"""

from __future__ import annotations

import json
import logging
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass
class Location:
    """A source code location returned by LSP definition/references."""

    uri: str
    line: int  # 0-based
    character: int  # 0-based

    @property
    def file_path(self) -> str:
        """Extract file path from file:// URI."""
        uri = self.uri
        if uri.startswith("file://"):
            uri = uri[7:]
        return uri


@dataclass
class SymbolInfo:
    """A symbol returned by LSP documentSymbol."""

    name: str
    kind: int  # LSP SymbolKind enum value
    start_line: int
    end_line: int
    children: list[SymbolInfo]


# ---------------------------------------------------------------------------
# LSP Client
# ---------------------------------------------------------------------------


class LSPError(Exception):
    """Error from the LSP server or transport layer."""


def _parse_single_location(item: dict) -> Location | None:
    """Parse a single Location or LocationLink from LSP response."""
    if not isinstance(item, dict):
        return None

    # Handle LocationLink (has targetUri/targetRange)
    if "targetUri" in item:
        pos = item.get("targetRange", {}).get("start", {})
        return Location(
            uri=item["targetUri"],
            line=pos.get("line", 0),
            character=pos.get("character", 0),
        )

    # Handle Location (has uri/range)
    if "uri" in item:
        pos = item.get("range", {}).get("start", {})
        return Location(
            uri=item["uri"],
            line=pos.get("line", 0),
            character=pos.get("character", 0),
        )

    return None


def _parse_symbol(item: dict) -> SymbolInfo:
    """Parse a DocumentSymbol from LSP response."""
    range_info = item.get("range", item.get("location", {}).get("range", {}))
    children_raw = item.get("children", [])
    return SymbolInfo(
        name=item.get("name", ""),
        kind=item.get("kind", 0),
        start_line=range_info.get("start", {}).get("line", 0),
        end_line=range_info.get("end", {}).get("line", 0),
        children=[_parse_symbol(c) for c in children_raw],
    )


class LSPClient:
    """Generic LSP client communicating via stdin/stdout JSON-RPC 2.0.

    Usage::

        client = LSPClient()
        client.start(["pyright-langserver", "--stdio"], project_root)
        client.initialize()
        client.did_open("src/main.py", "python", source_text)
        loc = client.definition("src/main.py", line=10, col=5)
        client.shutdown()
    """

    def __init__(self, timeout: float = 30.0) -> None:
        self._timeout = timeout
        self._process: subprocess.Popen | None = None
        self._request_id = 0
        self._pending: dict[int, threading.Event] = {}
        self._responses: dict[int, dict] = {}
        self._reader_thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._root_uri: str = ""
        self._running = False

    # -- Lifecycle -----------------------------------------------------------

    def start(self, cmd: list[str], root: Path) -> None:
        """Start the language server subprocess."""
        if self._process is not None:
            raise LSPError("LSP client already started")

        root = root.resolve()
        self._root_uri = root.as_uri()

        try:
            self._process = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=str(root),
            )
        except FileNotFoundError:
            raise LSPError(
                f"Language server not found: {cmd[0]}. "
                f"Install it and ensure it's on PATH."
            )

        self._running = True
        self._reader_thread = threading.Thread(
            target=self._read_loop, daemon=True, name="lsp-reader"
        )
        self._reader_thread.start()
        logger.info("LSP server started: %s (pid=%d)", cmd[0], self._process.pid)

    def shutdown(self) -> None:
        """Gracefully shut down the language server."""
        if not self._process:
            return

        try:
            # Send shutdown request
            self._send_request("shutdown")
            # Send exit notification (no response expected)
            self._send_notification("exit")
        except (BrokenPipeError, OSError, LSPError):
            pass

        self._running = False

        try:
            self._process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            logger.warning("LSP server did not exit, sending SIGTERM")
            self._process.terminate()
            try:
                self._process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                logger.warning("LSP server did not terminate, killing")
                self._process.kill()

        self._process = None
        logger.info("LSP server shut down")

    # -- LSP Protocol Methods -----------------------------------------------

    def initialize(self) -> dict:
        """Send initialize request and initialized notification."""
        result = self._send_request(
            "initialize",
            {
                "processId": None,
                "rootUri": self._root_uri,
                "capabilities": {
                    "textDocument": {
                        "definition": {"dynamicRegistration": False},
                        "references": {"dynamicRegistration": False},
                        "documentSymbol": {"dynamicRegistration": False},
                    },
                },
                "workspaceFolders": [
                    {"uri": self._root_uri, "name": "root"}
                ],
            },
        )
        # Send initialized notification
        self._send_notification("initialized", {})
        return result

    def did_open(self, file_path: str, language_id: str, text: str) -> None:
        """Notify server that a file was opened."""
        uri = self._to_uri(file_path)
        self._send_notification(
            "textDocument/didOpen",
            {
                "textDocument": {
                    "uri": uri,
                    "languageId": language_id,
                    "version": 1,
                    "text": text,
                },
            },
        )

    def did_close(self, file_path: str) -> None:
        """Notify server that a file was closed."""
        uri = self._to_uri(file_path)
        self._send_notification(
            "textDocument/didClose",
            {"textDocument": {"uri": uri}},
        )

    def definition(
        self, file_path: str, line: int, col: int
    ) -> Location | None:
        """Get the definition location for a symbol at the given position.

        Args:
            file_path: Absolute path to the file.
            line: 0-based line number.
            col: 0-based column number.

        Returns:
            Location of the definition, or None if not found.
        """
        uri = self._to_uri(file_path)
        result = self._send_request(
            "textDocument/definition",
            {
                "textDocument": {"uri": uri},
                "position": {"line": line, "character": col},
            },
        )
        return self._parse_location(result)

    def references(
        self, file_path: str, line: int, col: int
    ) -> list[Location]:
        """Get all references to a symbol at the given position.

        Args:
            file_path: Absolute path to the file.
            line: 0-based line number.
            col: 0-based column number.

        Returns:
            List of locations referencing the symbol.
        """
        uri = self._to_uri(file_path)
        result = self._send_request(
            "textDocument/references",
            {
                "textDocument": {"uri": uri},
                "position": {"line": line, "character": col},
                "context": {"includeDeclaration": False},
            },
        )
        if not result or not isinstance(result, list):
            return []
        return [
            loc
            for item in result
            if (loc := _parse_single_location(item)) is not None
        ]

    def document_symbols(self, file_path: str) -> list[SymbolInfo]:
        """Get all symbols defined in a file.

        Args:
            file_path: Absolute path to the file.

        Returns:
            List of symbols with their positions.
        """
        uri = self._to_uri(file_path)
        result = self._send_request(
            "textDocument/documentSymbol",
            {"textDocument": {"uri": uri}},
        )
        if not result or not isinstance(result, list):
            return []
        return [_parse_symbol(item) for item in result]

    # -- Helpers ------------------------------------------------------------

    @staticmethod
    def _to_uri(file_path: str) -> str:
        """Convert a file path to a file:// URI, resolving relative paths."""
        if file_path.startswith("file://"):
            return file_path
        return Path(file_path).resolve().as_uri()

    # -- JSON-RPC Transport -------------------------------------------------

    def _next_id(self) -> int:
        with self._lock:
            self._request_id += 1
            return self._request_id

    def _send_request(self, method: str, params: dict | None = None) -> Any:
        """Send a JSON-RPC request and wait for the response."""
        if not self._process or not self._process.stdin:
            raise LSPError("LSP client not started")

        req_id = self._next_id()
        msg: dict[str, Any] = {
            "jsonrpc": "2.0",
            "id": req_id,
            "method": method,
        }
        if params is not None:
            msg["params"] = params

        event = threading.Event()
        with self._lock:
            self._pending[req_id] = event

        self._write_message(msg)

        if not event.wait(timeout=self._timeout):
            with self._lock:
                self._pending.pop(req_id, None)
            raise LSPError(
                f"LSP request timed out after {self._timeout}s: {method}"
            )

        with self._lock:
            response = self._responses.pop(req_id, {})

        if "error" in response:
            err = response["error"]
            raise LSPError(
                f"LSP error ({err.get('code', '?')}): {err.get('message', '?')}"
            )

        return response.get("result")

    def _send_notification(
        self, method: str, params: dict | None = None
    ) -> None:
        """Send a JSON-RPC notification (no response expected)."""
        if not self._process or not self._process.stdin:
            raise LSPError("LSP client not started")

        msg: dict[str, Any] = {
            "jsonrpc": "2.0",
            "method": method,
        }
        if params is not None:
            msg["params"] = params

        self._write_message(msg)

    def _write_message(self, msg: dict) -> None:
        """Encode and write a JSON-RPC message with Content-Length header."""
        body = json.dumps(msg).encode("utf-8")
        header = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
        try:
            self._process.stdin.write(header + body)
            self._process.stdin.flush()
        except (BrokenPipeError, OSError) as e:
            raise LSPError(f"Failed to write to LSP server: {e}") from e

    def _read_loop(self) -> None:
        """Background thread: read JSON-RPC messages from stdout."""
        stdout = self._process.stdout
        while self._running and stdout:
            try:
                content_length = self._read_header(stdout)
                if content_length is None:
                    break
                body = stdout.read(content_length)
                if not body:
                    break
                msg = json.loads(body)
                self._dispatch_message(msg)
            except (json.JSONDecodeError, ValueError) as e:
                logger.debug("LSP read error: %s", e)
                continue
            except OSError:
                break

    def _read_header(self, stream) -> int | None:
        """Read Content-Length from LSP message header."""
        content_length = None
        while True:
            line = stream.readline()
            if not line:
                return None
            line_str = line.decode("ascii", errors="replace").strip()
            if not line_str:
                # Empty line = end of headers
                break
            if line_str.lower().startswith("content-length:"):
                content_length = int(line_str.split(":", 1)[1].strip())
        return content_length

    def _dispatch_message(self, msg: dict) -> None:
        """Route an incoming JSON-RPC message to the right handler."""
        if "id" in msg and "method" not in msg:
            # This is a response to our request
            req_id = msg["id"]
            with self._lock:
                event = self._pending.pop(req_id, None)
                if event:
                    self._responses[req_id] = msg
                    event.set()
        elif "method" in msg and "id" in msg:
            # Server-to-client request (e.g. window/workDoneProgress/create)
            # Auto-respond with null result
            self._write_message({
                "jsonrpc": "2.0",
                "id": msg["id"],
                "result": None,
            })
        # Notifications from server (no id, has method) — ignore silently

    # -- Response Parsing ---------------------------------------------------

    def _parse_location(self, result: Any) -> Location | None:
        """Parse a definition result (can be Location, list, or null)."""
        if not result:
            return None
        if isinstance(result, list):
            if not result:
                return None
            result = result[0]
        return _parse_single_location(result)

    # -- Context Manager ----------------------------------------------------

    def __enter__(self) -> LSPClient:
        return self

    def __exit__(self, *args) -> None:
        self.shutdown()
