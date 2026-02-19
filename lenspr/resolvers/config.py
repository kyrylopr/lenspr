"""Language server configuration — maps language IDs to server commands."""

from __future__ import annotations

import shutil

# Maps language identifier to (command, language_id_for_LSP).
# command is a list of args for subprocess.Popen.
LSP_SERVERS: dict[str, dict] = {
    "python": {
        "cmd": ["pyright-langserver", "--stdio"],
        "language_id": "python",
        "extensions": [".py"],
    },
    "typescript": {
        "cmd": ["typescript-language-server", "--stdio"],
        "language_id": "typescript",
        "extensions": [".ts", ".tsx"],
    },
    "javascript": {
        "cmd": ["typescript-language-server", "--stdio"],
        "language_id": "javascript",
        "extensions": [".js", ".jsx"],
    },
    "go": {
        "cmd": ["gopls", "serve"],
        "language_id": "go",
        "extensions": [".go"],
    },
    "rust": {
        "cmd": ["rust-analyzer"],
        "language_id": "rust",
        "extensions": [".rs"],
    },
    "java": {
        "cmd": ["jdtls"],
        "language_id": "java",
        "extensions": [".java"],
    },
}


def get_server_config(language: str) -> dict | None:
    """Get server config for a language, or None if not configured."""
    return LSP_SERVERS.get(language)


def is_server_available(language: str) -> bool:
    """Check if the language server binary is installed and on PATH."""
    config = LSP_SERVERS.get(language)
    if not config:
        return False
    return shutil.which(config["cmd"][0]) is not None


def get_language_for_extension(ext: str) -> str | None:
    """Map a file extension to its language identifier."""
    for lang, config in LSP_SERVERS.items():
        if ext in config["extensions"]:
            return lang
    return None
