# Manual Claude Code Test — TaskFlow Project

## Setup

```bash
# 1. Create TWO copies of the project
python eval/setup_test_project.py eval/test_projects/with_lenspr
python eval/setup_test_project.py eval/test_projects/without_lenspr

# 2. Init LensPR on the WITH copy
cd eval/test_projects/with_lenspr/taskflow
lenspr init
cd -

# 3. Open TWO Claude Code windows
# Window 1 (WITH LensPR):
cd eval/test_projects/with_lenspr/taskflow && claude

# Window 2 (WITHOUT LensPR — disable MCP):
cd eval/test_projects/without_lenspr/taskflow && claude --no-mcp
# Or remove .claude/ config from that copy if --no-mcp isn't available
```

## Important: Fair Comparison Rules
- Same prompt in both windows
- Let each finish completely before judging
- Note: iterations, time, and whether result is correct
- Both have CLAUDE.md with project description (neither is "blind")

---

## Task 1: Cross-Project Rename

```
Rename the function `validate_email` in backend/utils/validators.py to `is_valid_email_format`.

Find all usages, analyze impact, perform the rename (update ALL references), and verify no old references remain.
```

**Check**: `grep -r "validate_email" .` should return nothing. `grep -r "is_valid_email_format" .` should show all updated references.

---

## Task 2: Architecture Review

```
Perform an architecture review of this project:
1. Find the largest / most complex classes
2. Get detailed metrics for the top 2 classes (method count, lines, dependencies)
3. Compare them side by side
4. Analyze component cohesion for backend/services/
5. Provide overall architecture assessment — any concerns?
```

**Check**: Should mention AuthService (14 methods, largest), DatabaseConnection (11 methods), method counts, cohesion.

---

## Task 3: Dead Code Audit

```
Perform a comprehensive dead code audit covering BOTH Python and TypeScript files.
For each candidate, verify it is truly dead by checking for usages.
List ALL confirmed dead code with file paths and function/class names.
```

**Check**: Should find:
- `backend/utils/legacy_helpers.py` — `generate_id_old`, `format_date_old`, `sanitize_input_old`
- `backend/services/notification.py` — `NotificationService`
- `frontend/components/OldDashboard.tsx` — `OldDashboard`

---

## Task 4: Atomic Batch Refactoring

```
Add a `logger` parameter to BOTH TaskService.__init__ and AuthService.__init__:
- Add parameter: logger=None (with default None)
- Add self.logger = logger in each __init__ body
- Both updates must be applied together — if one fails, revert both

Verify the changes were applied correctly.
```

**Check**: Both `backend/services/auth_service.py` and `backend/services/task_service.py` should have `logger=None` parameter and `self.logger = logger`.

---

## Task 5: Cross-Language Flow Analysis

```
Trace the complete login flow from the frontend UI to the backend database.
Map the FULL call chain across both languages:
1. Start from LoginForm in frontend/components/
2. Follow through hooks and API client in TypeScript
3. Cross the HTTP boundary to Python backend
4. Trace through the backend to the database query

For each step: function name, file, what it does, what it calls next.
```

**Check**: Should map: LoginForm → useAuth → AuthApi → (HTTP) → auth_routes → AuthService → DatabaseConnection.

---

## Task 6: Impact Analysis + Git

```
I want to add a new required field `role: str` to the User dataclass in backend/models.py.
Analyze the FULL impact WITHOUT making the change:
1. What directly references User?
2. Transitive impact (depth 3)?
3. Which backend services and API routes are affected?
4. Are there TypeScript types that mirror User?
5. Overall severity/risk?
6. Who wrote the User class and when?
```

**Check**: Should mention User, AuthService, API routes, frontend/types.ts User interface, severity assessment, git blame info.

---

## Scoring Guide

For each task, note:
| Metric | WITH LensPR | WITHOUT LensPR |
|--------|-------------|----------------|
| Correct? (Y/N) | | |
| Iterations (tool calls) | | |
| Time (approx) | | |
| Completeness (1-5) | | |
| Notes | | |
