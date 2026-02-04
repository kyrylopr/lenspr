# Plan: Production Readiness for Real Projects

## Status: Phases 1-4 Complete

---

## Completed Phases

### ✅ Phase 1: Improved Init Output (DONE)
- Language breakdown (Python, TypeScript, JavaScript)
- Per-language node counts (functions, classes, methods)
- Edge resolution statistics with percentages
- Warnings for missing configs
- Parse time and database size

### ✅ Phase 2: Exclude Patterns (DONE)
Default excludes:
- `node_modules/`, `.venv/`, `venv/`, `__pycache__/`
- `dist/`, `build/`, `.next/`, `.nuxt/`, `.output/`, `out/`
- `coverage/`, `htmlcov/`, `.nyc_output/`
- `.git/`, `.lens/`, `.mypy_cache/`, `.pytest_cache/`

### ✅ Phase 4: lenspr doctor (DONE)
Checks:
- Environment: Python version, Node.js, tree-sitter, MCP
- Configuration: tsconfig.json, jsconfig.json, node_modules, path aliases
- Graph: database exists, freshness, resolution quality
- Actionable recommendations

---

## Remaining Phases

### Phase 3: tsconfig.json Support (Priority: HIGH)
**Goal:** Respect TypeScript project configuration

**Files to modify:**
- `lenspr/parsers/config_reader.py` (new)
- `lenspr/helpers/ts_resolver.js`

**Key features:**
- Read and merge tsconfig.json with extended configs
- Expand path aliases (`@/*` → `src/*`)
- Pass paths to Node.js resolver

### Phase 5: Framework Detection (Priority: MEDIUM)
**Goal:** Optimize for Next.js, React, Vue, etc.

**Detection from:**
- package.json dependencies
- File structure (app/, pages/, src/)

**Framework-specific:**
- Exclude patterns
- Entry points detection
- Component counting

### Phase 6: Monorepo Support (Priority: MEDIUM)
**Goal:** Handle packages/*, workspaces, internal dependencies

**Detect:**
- npm/yarn workspaces (package.json)
- pnpm workspaces (pnpm-workspace.yaml)
- Lerna (lerna.json)

**Features:**
- Parse each package separately
- Resolve cross-package imports
- Show package-level dependency graph

### Phase 7: Incremental Parsing (Priority: LOW)
**Goal:** Fast re-sync for large projects

**Features:**
- File hash tracking
- Only reparse changed files
- Re-resolve edges for affected files

---

## Files Created/Modified

### New Files:
- `lenspr/stats.py` - Parsing statistics
- `lenspr/doctor.py` - Health diagnostics

### Modified Files:
- `lenspr/cli.py` - New doctor command, improved init output
- `lenspr/context.py` - Stats collection support
- `lenspr/__init__.py` - Updated API
- `lenspr/parsers/multi.py` - Stats collection, better excludes
- `tests/test_cli.py` - Updated tests

---

## Test Results

- **224 tests passed**
- **5 skipped** (watchdog not installed)

---

## Next Steps

1. Test on real frontend project
2. Implement Phase 3 (tsconfig paths) if needed
3. Implement Phase 5 (framework detection) for better UX
