"""Parsing statistics and reporting for LensPR."""

from dataclasses import dataclass, field
from pathlib import Path

from lenspr.models import Edge, EdgeConfidence, Node, NodeType


@dataclass
class LanguageStats:
    """Statistics for a single language."""

    language: str
    extension: str
    file_count: int = 0
    node_counts: dict[str, int] = field(default_factory=dict)  # type -> count
    edge_counts: dict[str, int] = field(default_factory=dict)  # confidence -> count
    parse_errors: list[str] = field(default_factory=list)
    parse_time_ms: float = 0.0

    @property
    def total_nodes(self) -> int:
        return sum(self.node_counts.values())

    @property
    def total_edges(self) -> int:
        return sum(self.edge_counts.values())

    @property
    def resolved_edges(self) -> int:
        return self.edge_counts.get("resolved", 0)

    @property
    def external_edges(self) -> int:
        return self.edge_counts.get("external", 0)

    @property
    def unresolved_edges(self) -> int:
        return self.edge_counts.get("unresolved", 0) + self.edge_counts.get("inferred", 0)

    @property
    def resolution_pct(self) -> float:
        """Percentage of edges that are resolved (including external)."""
        total = self.total_edges
        if total == 0:
            return 100.0
        resolved = self.resolved_edges + self.external_edges
        return round(resolved / total * 100, 1)


@dataclass
class ParseStats:
    """Statistics collected during project parsing."""

    project_root: Path
    languages: dict[str, LanguageStats] = field(default_factory=dict)
    skipped_dirs: dict[str, int] = field(default_factory=dict)  # dir_name -> file_count
    warnings: list[str] = field(default_factory=list)
    total_time_ms: float = 0.0

    @property
    def total_files(self) -> int:
        return sum(lang.file_count for lang in self.languages.values())

    @property
    def total_nodes(self) -> int:
        return sum(lang.total_nodes for lang in self.languages.values())

    @property
    def total_edges(self) -> int:
        return sum(lang.total_edges for lang in self.languages.values())

    @property
    def total_skipped(self) -> int:
        return sum(self.skipped_dirs.values())

    @property
    def overall_resolution_pct(self) -> float:
        """Overall edge resolution percentage."""
        total_edges = self.total_edges
        if total_edges == 0:
            return 100.0
        resolved = sum(
            lang.resolved_edges + lang.external_edges for lang in self.languages.values()
        )
        return round(resolved / total_edges * 100, 1)

    def add_file(self, file_path: Path, language: str, extension: str) -> None:
        """Track a file being parsed."""
        if language not in self.languages:
            self.languages[language] = LanguageStats(language=language, extension=extension)
        self.languages[language].file_count += 1

    def add_nodes(self, nodes: list[Node], language: str, extension: str) -> None:
        """Track nodes created from parsing."""
        if language not in self.languages:
            self.languages[language] = LanguageStats(language=language, extension=extension)

        lang_stats = self.languages[language]
        for node in nodes:
            node_type = node.type.value
            lang_stats.node_counts[node_type] = lang_stats.node_counts.get(node_type, 0) + 1

    def add_edges(self, edges: list[Edge], language: str, extension: str) -> None:
        """Track edges created from parsing."""
        if language not in self.languages:
            self.languages[language] = LanguageStats(language=language, extension=extension)

        lang_stats = self.languages[language]
        for edge in edges:
            confidence = edge.confidence.value
            lang_stats.edge_counts[confidence] = lang_stats.edge_counts.get(confidence, 0) + 1

    def add_skipped_dir(self, dir_name: str, file_count: int = 1) -> None:
        """Track skipped directory."""
        self.skipped_dirs[dir_name] = self.skipped_dirs.get(dir_name, 0) + file_count

    def add_warning(self, warning: str) -> None:
        """Add a warning message."""
        if warning not in self.warnings:
            self.warnings.append(warning)

    def add_parse_error(self, language: str, file_path: str, error: str) -> None:
        """Track a parse error."""
        if language in self.languages:
            self.languages[language].parse_errors.append(f"{file_path}: {error}")

    def recalculate_resolution(self, edges: list[Edge]) -> None:
        """Recalculate edge statistics after resolution.

        Called after cross-file resolution to update confidence counts
        with resolved edges (which may have changed from INFERRED to RESOLVED).
        """
        # Clear existing edge counts
        for lang_stats in self.languages.values():
            lang_stats.edge_counts.clear()

        # Re-add edges with their resolved confidence
        for edge in edges:
            # Determine language from edge file path (stored in metadata)
            file_path = edge.metadata.get("file") if edge.metadata else None
            if not file_path:
                # Try to infer from source node id (e.g., src.utils.helper -> src/utils)
                file_path = edge.from_node.replace(".", "/")

            # Determine language from extension
            ext = Path(file_path).suffix.lower() if file_path else ""
            language, _ = get_language_for_extension(ext)

            if language in self.languages:
                lang_stats = self.languages[language]
                confidence = edge.confidence.value
                lang_stats.edge_counts[confidence] = lang_stats.edge_counts.get(confidence, 0) + 1


def get_language_for_extension(ext: str) -> tuple[str, str]:
    """Get language name and normalized extension for file extension.

    Returns (language_name, display_extensions).
    """
    ext = ext.lower()
    if ext == ".py":
        return ("Python", ".py")
    elif ext in (".ts", ".tsx"):
        return ("TypeScript", ".ts/.tsx")
    elif ext in (".js", ".jsx"):
        return ("JavaScript", ".js/.jsx")
    else:
        return ("Unknown", ext)


def format_stats_report(stats: ParseStats) -> str:
    """Format statistics as a human-readable report."""
    lines = []

    # Files found
    lines.append("Found source files:")
    for lang_name, lang_stats in sorted(stats.languages.items()):
        ext_display = lang_stats.extension
        lines.append(f"  {lang_name} ({ext_display}):".ljust(28) + f"{lang_stats.file_count:>6} files")
    lines.append("  " + "-" * 35)
    lines.append(f"  {'Total:'.ljust(24)}{stats.total_files:>6} files")

    if stats.total_skipped > 0:
        lines.append(f"  {'Skipped:'.ljust(24)}{stats.total_skipped:>6} files")

    lines.append("")

    # Per-language node stats
    lines.append("Nodes created:")
    for lang_name, lang_stats in sorted(stats.languages.items()):
        node_parts = []
        for node_type in ["function", "class", "method", "module"]:
            count = lang_stats.node_counts.get(node_type, 0)
            if count > 0:
                node_parts.append(f"{count} {node_type}s")
        if node_parts:
            lines.append(f"  {lang_name}: {', '.join(node_parts)}")

    lines.append(f"  Total: {stats.total_nodes} nodes")
    lines.append("")

    # Edge resolution stats
    lines.append("Edge resolution:")
    for lang_name, lang_stats in sorted(stats.languages.items()):
        total = lang_stats.total_edges
        if total == 0:
            continue
        resolved = lang_stats.resolved_edges
        external = lang_stats.external_edges
        unresolved = lang_stats.unresolved_edges
        pct = lang_stats.resolution_pct

        lines.append(f"  {lang_name}: {total} edges")
        lines.append(f"    Resolved:   {resolved:>5} ({pct:.0f}%)")
        if external > 0:
            lines.append(f"    External:   {external:>5} (stdlib/packages)")
        if unresolved > 0:
            lines.append(f"    Unresolved: {unresolved:>5} (dynamic)")

    lines.append("")

    # Warnings
    if stats.warnings:
        lines.append("Warnings:")
        for warning in stats.warnings:
            lines.append(f"  {warning}")
        lines.append("")

    return "\n".join(lines)
