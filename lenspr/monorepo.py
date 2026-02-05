"""Monorepo support: auto-detect and setup JS/TS packages."""

from __future__ import annotations

import logging
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class PackageInfo:
    """Information about a JS/TS package."""

    path: Path  # Directory containing package.json
    package_json: Path  # Path to package.json
    has_node_modules: bool
    name: str | None = None  # Package name from package.json


@dataclass
class MonorepoInfo:
    """Information about monorepo structure."""

    packages: list[PackageInfo] = field(default_factory=list)
    has_root_package: bool = False
    missing_node_modules: list[Path] = field(default_factory=list)

    @property
    def is_monorepo(self) -> bool:
        """True if multiple packages found."""
        return len(self.packages) > 1

    @property
    def needs_install(self) -> bool:
        """True if any package is missing node_modules."""
        return len(self.missing_node_modules) > 0


def find_packages(root_path: Path) -> MonorepoInfo:
    """Find all JS/TS packages in a project.

    Scans for package.json files, excluding node_modules directories.

    Args:
        root_path: Project root directory.

    Returns:
        MonorepoInfo with all found packages.
    """
    info = MonorepoInfo()

    # Skip these directories
    skip_dirs = {
        "node_modules",
        ".git",
        ".lens",
        ".venv",
        "venv",
        "dist",
        "build",
        ".next",
        ".nuxt",
        "coverage",
        "__pycache__",
    }

    def should_skip(path: Path) -> bool:
        return any(part in skip_dirs for part in path.parts)

    # Find all package.json files
    for package_json in root_path.rglob("package.json"):
        if should_skip(package_json.relative_to(root_path)):
            continue

        package_dir = package_json.parent
        node_modules = package_dir / "node_modules"

        # Try to read package name
        name = None
        try:
            import json
            data = json.loads(package_json.read_text())
            name = data.get("name")
        except Exception:
            pass

        pkg = PackageInfo(
            path=package_dir,
            package_json=package_json,
            has_node_modules=node_modules.exists() and node_modules.is_dir(),
            name=name,
        )
        info.packages.append(pkg)

        if not pkg.has_node_modules:
            info.missing_node_modules.append(package_dir)

        # Check if this is the root package
        if package_dir == root_path:
            info.has_root_package = True

    # Sort by path depth (root first)
    info.packages.sort(key=lambda p: len(p.path.parts))

    return info


def install_dependencies(
    packages: list[Path],
    progress_callback: callable | None = None,
) -> dict[Path, bool]:
    """Run npm install for each package.

    Args:
        packages: List of directories containing package.json.
        progress_callback: Optional callback(current, total, path).

    Returns:
        Dict mapping package path to success status.
    """
    results: dict[Path, bool] = {}

    npm = shutil.which("npm")
    if not npm:
        logger.warning("npm not found - cannot install dependencies")
        return {p: False for p in packages}

    total = len(packages)
    for i, package_dir in enumerate(packages):
        if progress_callback:
            progress_callback(i + 1, total, str(package_dir))

        try:
            logger.info("Installing dependencies in %s", package_dir)
            result = subprocess.run(
                [npm, "install"],
                cwd=package_dir,
                capture_output=True,
                text=True,
                timeout=300,  # 5 minutes per package
            )

            if result.returncode == 0:
                results[package_dir] = True
                logger.info("Successfully installed dependencies in %s", package_dir)
            else:
                results[package_dir] = False
                logger.warning(
                    "npm install failed in %s: %s",
                    package_dir,
                    result.stderr[:500]
                )

        except subprocess.TimeoutExpired:
            results[package_dir] = False
            logger.error("npm install timed out in %s", package_dir)
        except Exception as e:
            results[package_dir] = False
            logger.error("npm install failed in %s: %s", package_dir, e)

    return results


