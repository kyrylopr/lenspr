"""LensPR parsers: language-specific code-to-graph converters."""

from lenspr.parsers.base import BaseParser
from lenspr.parsers.multi import MultiParser
from lenspr.parsers.python_parser import PythonParser

# Optional TypeScript parser (requires tree-sitter)
try:
    from lenspr.parsers.typescript_parser import TypeScriptParser

    TYPESCRIPT_AVAILABLE = True
except ImportError:
    TypeScriptParser = None  # type: ignore
    TYPESCRIPT_AVAILABLE = False

# Cached extensions for performance
_cached_extensions: tuple[str, ...] | None = None


def get_supported_extensions() -> tuple[str, ...]:
    """Get all file extensions supported by available parsers.

    Returns a tuple like ('.py', '.ts', '.tsx', '.js', '.jsx').
    Result is cached for performance.
    """
    global _cached_extensions
    if _cached_extensions is None:
        parser = MultiParser()
        _cached_extensions = tuple(parser.get_file_extensions())
    return _cached_extensions


def is_supported_file(file_path: str) -> bool:
    """Check if a file path has a supported extension.

    Args:
        file_path: Path to check (can be relative or absolute)

    Returns:
        True if file extension is supported
    """
    extensions = get_supported_extensions()
    return any(file_path.endswith(ext) for ext in extensions)


__all__ = [
    "BaseParser",
    "MultiParser",
    "PythonParser",
    "TypeScriptParser",
    "TYPESCRIPT_AVAILABLE",
    "get_supported_extensions",
    "is_supported_file",
]
