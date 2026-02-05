#!/usr/bin/env python3
"""Create the TaskFlow test project for manual Claude Code testing.

Usage:
    python eval/setup_test_project.py [target_dir]

Creates taskflow/ project with Python backend + TypeScript frontend.
Default target: eval/test_projects/
"""
import json
import subprocess
import sys
import os
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent
NOTEBOOK = SCRIPT_DIR / 'killer_features.ipynb'


def _load_project_data():
    """Extract PROJECT_FILES, TSCONFIG, CLAUDE_MD from the notebook."""
    nb = json.loads(NOTEBOOK.read_text())

    # Cell 2 defines CLAUDE_MD, Cell 3 defines PROJECT_FILES + TSCONFIG + create_project
    ns = {}
    # We need: json, os, subprocess, Path, tempfile, re
    ns['json'] = json
    ns['os'] = os
    ns['subprocess'] = subprocess
    ns['Path'] = Path
    ns['re'] = __import__('re')
    ns['tempfile'] = __import__('tempfile')

    # Execute cell 2 (CLAUDE_MD)
    exec(nb['cells'][2]['source'], ns)
    # Execute cell 3 (PROJECT_FILES, TSCONFIG, create_project)
    exec(nb['cells'][3]['source'], ns)

    return ns['PROJECT_FILES'], ns['TSCONFIG'], ns['CLAUDE_MD'], ns['create_project']


def setup_project(target_dir: Path) -> Path:
    project_dir = target_dir / 'taskflow'
    if project_dir.exists():
        print(f'Already exists: {project_dir}')
        print('Delete it first to recreate: rm -rf', project_dir)
        return project_dir

    PROJECT_FILES, TSCONFIG, CLAUDE_MD, create_project = _load_project_data()

    # create_project expects a base_dir and creates taskflow/ inside it
    result = create_project(target_dir)
    print(f'\nCreated: {result}')
    return result


if __name__ == '__main__':
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else SCRIPT_DIR / 'test_projects'
    target.mkdir(parents=True, exist_ok=True)
    setup_project(target)
