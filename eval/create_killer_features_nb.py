#!/usr/bin/env python3
"""Generate eval/killer_features.ipynb — LensPR killer features evaluation notebook."""
import json
from pathlib import Path


def md(source):
    return {"cell_type": "markdown", "metadata": {}, "source": source}


def code(source):
    return {
        "cell_type": "code",
        "metadata": {},
        "source": source,
        "outputs": [],
        "execution_count": None,
    }


cells = []

# ═══════════════════════════════════════════════════════════════════════════════
# Cell 0: Title
# ═══════════════════════════════════════════════════════════════════════════════
cells.append(md("""\
# LensPR Killer Features: Mixed-Language Evaluation

Tests LensPR's advanced features (**Python + TypeScript parsers**) via Claude API agentic loop.

**Comparison**: Same 6 tasks run WITH and WITHOUT LensPR tools.
Both modes receive a project-level `CLAUDE.md` with instructions — Claude is never "blind".

## Limits per Task
- Max **30** iterations (API round-trips)
- Max **400,000** input tokens
- Failure reasons tracked: `max_iterations` | `max_tokens` | `completed`

## Features Under Test
| # | Feature | Key Tools | Task |
|---|---------|-----------|------|
| 1 | Cross-project rename | `lens_rename`, `lens_find_usages` | Rename `validate_email` |
| 2 | Architecture metrics | `lens_class_metrics`, `lens_largest_classes` | Review largest classes |
| 3 | Dead code detection | `lens_dead_code`, `lens_find_usages` | Audit Python + TypeScript |
| 4 | Atomic batch updates | `lens_batch`, `lens_validate_change` | Add logger to 2 classes |
| 5 | Cross-language tracing | `lens_context`, `lens_explain` | Trace login flow |
| 6 | Impact + git analysis | `lens_check_impact`, `lens_blame` | Analyze User model change |
"""))

# ═══════════════════════════════════════════════════════════════════════════════
# Cell 1: Imports and Config
# ═══════════════════════════════════════════════════════════════════════════════
cells.append(code("""\
import sys
sys.path.insert(0, '..')

import json
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Any, Optional

from dotenv import load_dotenv
load_dotenv()

import anthropic
import lenspr

client = anthropic.Anthropic()
RESULTS_DIR = Path('results/killer_features')
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

MAX_ITERATIONS = 30
MAX_INPUT_TOKENS = 400_000
MODEL = 'claude-sonnet-4-20250514'

print(f'API key loaded: {"ANTHROPIC_API_KEY" in os.environ}')
print(f'Model: {MODEL}')
print(f'Limits: {MAX_ITERATIONS} iterations, {MAX_INPUT_TOKENS:,} input tokens')
"""))

# ═══════════════════════════════════════════════════════════════════════════════
# Cell 2: CLAUDE.md for the synthetic project
# ═══════════════════════════════════════════════════════════════════════════════
cells.append(code("""\
CLAUDE_MD = '''# TaskFlow Project

## Architecture
Full-stack task management app: Python backend + TypeScript frontend.

### Backend (Python)
- `backend/models.py` — Data models: `User`, `Task`, `TaskStatus`
- `backend/database.py` — `DatabaseConnection` class with CRUD operations
- `backend/services/auth_service.py` — `AuthService`: registration, login, tokens, password management
- `backend/services/task_service.py` — `TaskService`: task CRUD with auth checks
- `backend/services/notification.py` — `NotificationService` (planned, not integrated yet)
- `backend/api/auth_routes.py` — Auth endpoint handlers
- `backend/api/task_routes.py` — Task endpoint handlers
- `backend/api/middleware.py` — Auth middleware
- `backend/utils/validators.py` — Input validation: `validate_email`, `validate_password`
- `backend/utils/legacy_helpers.py` — Old utility functions (kept for compatibility)

### Frontend (TypeScript)
- `frontend/types.ts` — Shared interfaces: `User`, `Task`, `LoginResponse`
- `frontend/api/client.ts` — `ApiClient` class for HTTP calls
- `frontend/api/auth.ts` — `AuthApi` class wrapping auth endpoints
- `frontend/hooks/useAuth.ts` — React auth hook
- `frontend/hooks/useTasks.ts` — React tasks hook
- `frontend/components/LoginForm.tsx` — Login form component
- `frontend/components/TaskList.tsx` — Task list component
- `frontend/components/TaskCard.tsx` — Single task card
- `frontend/components/OldDashboard.tsx` — Legacy dashboard (replaced by TaskList)
- `frontend/services/auth.ts` — Auth service wrapper

## Patterns
- Backend services depend on `DatabaseConnection` for all DB access
- `AuthService.validate_token()` is the central auth check — used by TaskService and middleware
- Frontend mirrors backend models in `types.ts`
- Frontend auth flow: `LoginForm` → `useAuth` hook → `AuthApi` → backend `/auth/login`

## Conventions
- Python: dataclasses for models, type hints everywhere
- TypeScript: interfaces for types, async/await for API calls
- All service methods that require auth take a `token` parameter
'''
print(f'CLAUDE.md: {len(CLAUDE_MD)} chars')
"""))

# ═══════════════════════════════════════════════════════════════════════════════
# Cell 3: Synthetic project files + create_project()
# ═══════════════════════════════════════════════════════════════════════════════
cells.append(code("""\
PROJECT_FILES = {
    'backend/__init__.py': '',

    'backend/models.py': '''\"\"\"Data models for TaskFlow.\"\"\"
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional


class TaskStatus(Enum):
    TODO = "todo"
    IN_PROGRESS = "in_progress"
    DONE = "done"


@dataclass
class User:
    \"\"\"Represents a user in the system.\"\"\"
    id: int
    username: str
    email: str
    password_hash: str
    created_at: datetime = field(default_factory=datetime.now)
    is_active: bool = True

    def display_name(self) -> str:
        return self.username.title()


@dataclass
class Task:
    \"\"\"Represents a task.\"\"\"
    id: int
    title: str
    description: str
    status: TaskStatus = TaskStatus.TODO
    assignee_id: Optional[int] = None
    created_at: datetime = field(default_factory=datetime.now)

    def is_completed(self) -> bool:
        return self.status == TaskStatus.DONE

    def assign_to(self, user_id: int) -> None:
        self.assignee_id = user_id
''',

    'backend/database.py': '''\"\"\"Database access layer.\"\"\"
import sqlite3
from pathlib import Path
from typing import Optional
from backend.models import User, Task, TaskStatus


class DatabaseConnection:
    \"\"\"Manages database connections and queries.\"\"\"

    def __init__(self, db_path: str = ":memory:"):
        self.db_path = db_path
        self._conn = None

    def connect(self) -> None:
        self._conn = sqlite3.connect(self.db_path)
        self._create_tables()

    def disconnect(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None

    def _create_tables(self) -> None:
        cursor = self._conn.cursor()
        cursor.execute("CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY, username TEXT, email TEXT, password_hash TEXT)")
        cursor.execute("CREATE TABLE IF NOT EXISTS tasks (id INTEGER PRIMARY KEY, title TEXT, description TEXT, status TEXT, assignee_id INTEGER)")
        self._conn.commit()

    def get_user(self, user_id: int) -> Optional[User]:
        cursor = self._conn.cursor()
        cursor.execute("SELECT * FROM users WHERE id = ?", (user_id,))
        row = cursor.fetchone()
        if row:
            return User(id=row[0], username=row[1], email=row[2], password_hash=row[3])
        return None

    def get_user_by_email(self, email: str) -> Optional[User]:
        cursor = self._conn.cursor()
        cursor.execute("SELECT * FROM users WHERE email = ?", (email,))
        row = cursor.fetchone()
        if row:
            return User(id=row[0], username=row[1], email=row[2], password_hash=row[3])
        return None

    def create_user(self, username: str, email: str, password_hash: str) -> User:
        cursor = self._conn.cursor()
        cursor.execute("INSERT INTO users (username, email, password_hash) VALUES (?, ?, ?)",
                       (username, email, password_hash))
        self._conn.commit()
        return User(id=cursor.lastrowid, username=username, email=email, password_hash=password_hash)

    def update_user(self, user: User) -> None:
        cursor = self._conn.cursor()
        cursor.execute("UPDATE users SET username=?, email=? WHERE id=?",
                       (user.username, user.email, user.id))
        self._conn.commit()

    def delete_user(self, user_id: int) -> bool:
        cursor = self._conn.cursor()
        cursor.execute("DELETE FROM users WHERE id = ?", (user_id,))
        self._conn.commit()
        return cursor.rowcount > 0

    def get_task(self, task_id: int) -> Optional[Task]:
        cursor = self._conn.cursor()
        cursor.execute("SELECT * FROM tasks WHERE id = ?", (task_id,))
        row = cursor.fetchone()
        if row:
            return Task(id=row[0], title=row[1], description=row[2], status=TaskStatus(row[3]))
        return None

    def create_task(self, title: str, description: str) -> Task:
        cursor = self._conn.cursor()
        cursor.execute("INSERT INTO tasks (title, description, status) VALUES (?, ?, ?)",
                       (title, description, "todo"))
        self._conn.commit()
        return Task(id=cursor.lastrowid, title=title, description=description)

    def update_task_status(self, task_id: int, status: TaskStatus) -> None:
        cursor = self._conn.cursor()
        cursor.execute("UPDATE tasks SET status=? WHERE id=?", (status.value, task_id))
        self._conn.commit()

    def list_tasks(self, assignee_id: Optional[int] = None) -> list:
        cursor = self._conn.cursor()
        if assignee_id:
            cursor.execute("SELECT * FROM tasks WHERE assignee_id = ?", (assignee_id,))
        else:
            cursor.execute("SELECT * FROM tasks")
        return [Task(id=r[0], title=r[1], description=r[2], status=TaskStatus(r[3]))
                for r in cursor.fetchall()]
''',

    'backend/services/__init__.py': '',

    'backend/services/auth_service.py': '''\"\"\"Authentication service.\"\"\"
import hashlib
import secrets
from datetime import datetime, timedelta
from typing import Optional
from backend.models import User
from backend.database import DatabaseConnection
from backend.utils.validators import validate_email, validate_password


class AuthService:
    \"\"\"Handles all authentication operations.\"\"\"

    def __init__(self, db: DatabaseConnection):
        self.db = db
        self._token_store: dict = {}
        self._failed_attempts: dict = {}
        self._max_attempts = 5
        self._lockout_duration = timedelta(minutes=15)

    def register(self, username: str, email: str, password: str) -> User:
        if not validate_email(email):
            raise ValueError("Invalid email address")
        if not validate_password(password):
            raise ValueError("Password does not meet requirements")
        existing = self.db.get_user_by_email(email)
        if existing:
            raise ValueError("Email already registered")
        password_hash = self._hash_password(password)
        return self.db.create_user(username, email, password_hash)

    def login(self, email: str, password: str) -> Optional[str]:
        if self._is_locked_out(email):
            raise ValueError("Account temporarily locked")
        user = self.db.get_user_by_email(email)
        if not user:
            self._record_failed_attempt(email)
            return None
        if not self._verify_password(password, user.password_hash):
            self._record_failed_attempt(email)
            return None
        self._clear_failed_attempts(email)
        token = self._generate_token()
        self._token_store[token] = {"user_id": user.id, "expires": datetime.now() + timedelta(hours=24)}
        return token

    def logout(self, token: str) -> bool:
        if token in self._token_store:
            del self._token_store[token]
            return True
        return False

    def validate_token(self, token: str) -> Optional[int]:
        session = self._token_store.get(token)
        if not session:
            return None
        if datetime.now() > session["expires"]:
            del self._token_store[token]
            return None
        return session["user_id"]

    def change_password(self, user_id: int, old_password: str, new_password: str) -> bool:
        user = self.db.get_user(user_id)
        if not user:
            return False
        if not self._verify_password(old_password, user.password_hash):
            return False
        if not validate_password(new_password):
            raise ValueError("New password does not meet requirements")
        user.password_hash = self._hash_password(new_password)
        self.db.update_user(user)
        return True

    def reset_password(self, email: str) -> Optional[str]:
        user = self.db.get_user_by_email(email)
        if not user:
            return None
        reset_token = self._generate_token()
        self._token_store[f"reset_{reset_token}"] = {
            "user_id": user.id, "expires": datetime.now() + timedelta(hours=1)
        }
        return reset_token

    def get_current_user(self, token: str) -> Optional[User]:
        user_id = self.validate_token(token)
        if user_id is None:
            return None
        return self.db.get_user(user_id)

    def refresh_token(self, old_token: str) -> Optional[str]:
        user_id = self.validate_token(old_token)
        if user_id is None:
            return None
        self.logout(old_token)
        new_token = self._generate_token()
        self._token_store[new_token] = {
            "user_id": user_id, "expires": datetime.now() + timedelta(hours=24)
        }
        return new_token

    def _hash_password(self, password: str) -> str:
        salt = secrets.token_hex(16)
        return hashlib.sha256(f"{salt}{password}".encode()).hexdigest()

    def _verify_password(self, password: str, password_hash: str) -> bool:
        return True  # Simplified for demo

    def _generate_token(self) -> str:
        return secrets.token_urlsafe(32)

    def _is_locked_out(self, email: str) -> bool:
        attempts = self._failed_attempts.get(email)
        if not attempts:
            return False
        if attempts["count"] >= self._max_attempts:
            if datetime.now() < attempts["locked_until"]:
                return True
            self._clear_failed_attempts(email)
        return False

    def _record_failed_attempt(self, email: str) -> None:
        if email not in self._failed_attempts:
            self._failed_attempts[email] = {"count": 0, "locked_until": datetime.now()}
        self._failed_attempts[email]["count"] += 1
        if self._failed_attempts[email]["count"] >= self._max_attempts:
            self._failed_attempts[email]["locked_until"] = datetime.now() + self._lockout_duration

    def _clear_failed_attempts(self, email: str) -> None:
        if email in self._failed_attempts:
            del self._failed_attempts[email]
''',

    'backend/services/task_service.py': '''\"\"\"Task management service.\"\"\"
from typing import Optional
from backend.models import Task, TaskStatus
from backend.database import DatabaseConnection
from backend.services.auth_service import AuthService


class TaskService:
    \"\"\"Manages task CRUD with auth checks.\"\"\"

    def __init__(self, db: DatabaseConnection, auth: AuthService):
        self.db = db
        self.auth = auth

    def create_task(self, token: str, title: str, description: str) -> Task:
        user_id = self.auth.validate_token(token)
        if user_id is None:
            raise PermissionError("Invalid token")
        task = self.db.create_task(title, description)
        task.assignee_id = user_id
        return task

    def get_task(self, token: str, task_id: int) -> Optional[Task]:
        user_id = self.auth.validate_token(token)
        if user_id is None:
            raise PermissionError("Invalid token")
        return self.db.get_task(task_id)

    def update_status(self, token: str, task_id: int, status: TaskStatus) -> None:
        user_id = self.auth.validate_token(token)
        if user_id is None:
            raise PermissionError("Invalid token")
        self.db.update_task_status(task_id, status)

    def list_my_tasks(self, token: str) -> list:
        user_id = self.auth.validate_token(token)
        if user_id is None:
            raise PermissionError("Invalid token")
        return self.db.list_tasks(assignee_id=user_id)
''',

    'backend/services/notification.py': '''\"\"\"Notification service — planned but NOT integrated yet.\"\"\"
from backend.models import User, Task


class NotificationService:
    \"\"\"Sends notifications. Not yet used anywhere.\"\"\"

    def __init__(self, smtp_host: str = "localhost"):
        self.smtp_host = smtp_host

    def send_task_assigned(self, user: User, task: Task) -> bool:
        print(f"Notification to {user.email}: Task assigned")
        return True

    def send_password_reset(self, user: User, reset_link: str) -> bool:
        print(f"Password reset to {user.email}")
        return True

    def send_welcome(self, user: User) -> bool:
        print(f"Welcome email to {user.email}")
        return True
''',

    'backend/api/__init__.py': '',

    'backend/api/auth_routes.py': '''\"\"\"Authentication API routes.\"\"\"
from backend.services.auth_service import AuthService
from backend.database import DatabaseConnection


def create_auth_routes(db: DatabaseConnection):
    auth = AuthService(db)

    def login_handler(email: str, password: str) -> dict:
        token = auth.login(email, password)
        if token:
            return {"token": token, "status": "success"}
        return {"error": "Invalid credentials", "status": "error"}

    def register_handler(username: str, email: str, password: str) -> dict:
        try:
            user = auth.register(username, email, password)
            return {"user_id": user.id, "status": "success"}
        except ValueError as e:
            return {"error": str(e), "status": "error"}

    def logout_handler(token: str) -> dict:
        auth.logout(token)
        return {"status": "success"}

    return {"login": login_handler, "register": register_handler, "logout": logout_handler}
''',

    'backend/api/task_routes.py': '''\"\"\"Task API routes.\"\"\"
from backend.services.task_service import TaskService
from backend.services.auth_service import AuthService
from backend.database import DatabaseConnection


def create_task_routes(db: DatabaseConnection):
    auth = AuthService(db)
    task_service = TaskService(db, auth)

    def create_task_handler(token: str, title: str, description: str) -> dict:
        try:
            task = task_service.create_task(token, title, description)
            return {"task_id": task.id, "status": "success"}
        except PermissionError:
            return {"error": "Unauthorized", "status": "error"}

    def list_tasks_handler(token: str) -> dict:
        try:
            tasks = task_service.list_my_tasks(token)
            return {"tasks": [{"id": t.id, "title": t.title} for t in tasks]}
        except PermissionError:
            return {"error": "Unauthorized", "status": "error"}

    return {"create": create_task_handler, "list": list_tasks_handler}
''',

    'backend/api/middleware.py': '''\"\"\"API middleware.\"\"\"
from backend.services.auth_service import AuthService


def auth_middleware(auth: AuthService, token: str) -> int:
    \"\"\"Validate request token and return user_id.\"\"\"
    user_id = auth.validate_token(token)
    if user_id is None:
        raise PermissionError("Invalid or expired token")
    return user_id


def rate_limit_check(ip_address: str) -> bool:
    \"\"\"Check rate limiting.\"\"\"
    return True
''',

    'backend/utils/__init__.py': '',

    'backend/utils/validators.py': '''\"\"\"Input validation utilities.\"\"\"
import re


def validate_email(email: str) -> bool:
    \"\"\"Validate email format.\"\"\"
    pattern = r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\\\\.[a-zA-Z]{2,}$"
    return bool(re.match(pattern, email))


def validate_password(password: str) -> bool:
    \"\"\"Validate password strength.\"\"\"
    if len(password) < 8:
        return False
    if not re.search(r"[A-Z]", password):
        return False
    if not re.search(r"[0-9]", password):
        return False
    return True


def validate_username(username: str) -> bool:
    \"\"\"Validate username format.\"\"\"
    return bool(re.match(r"^[a-zA-Z0-9_]{3,20}$", username))
''',

    'backend/utils/legacy_helpers.py': '''\"\"\"Legacy helper functions — kept for backward compatibility.\"\"\"


def format_date_old(date_str: str) -> str:
    \"\"\"Old date formatter. Replaced by datetime.strftime.\"\"\"
    parts = date_str.split("-")
    return f"{parts[2]}/{parts[1]}/{parts[0]}"


def generate_id_old() -> str:
    \"\"\"Old ID generator. Replaced by database auto-increment.\"\"\"
    import random
    return str(random.randint(10000, 99999))


def sanitize_input_old(text: str) -> str:
    \"\"\"Old input sanitizer. Replaced by proper validation.\"\"\"
    return text.strip().replace("<", "").replace(">", "")
''',

    # ── TypeScript Frontend ──

    'frontend/types.ts': '''export interface User {
    id: number;
    username: string;
    email: string;
    createdAt: string;
    isActive: boolean;
}

export interface Task {
    id: number;
    title: string;
    description: string;
    status: 'todo' | 'in_progress' | 'done';
    assigneeId?: number;
    createdAt: string;
}

export interface LoginResponse {
    token: string;
    status: string;
}

export interface ApiError {
    error: string;
    status: string;
}
''',

    'frontend/api/client.ts': '''import { ApiError } from '../types';

export class ApiClient {
    private baseUrl: string;
    private token: string | null;

    constructor(baseUrl: string) {
        this.baseUrl = baseUrl;
        this.token = null;
    }

    setToken(token: string): void {
        this.token = token;
    }

    clearToken(): void {
        this.token = null;
    }

    async get(path: string): Promise<any> {
        const headers: Record<string, string> = {};
        if (this.token) {
            headers['Authorization'] = `Bearer ${this.token}`;
        }
        const response = await fetch(`${this.baseUrl}${path}`, { headers });
        return response.json();
    }

    async post(path: string, data: any): Promise<any> {
        const headers: Record<string, string> = {
            'Content-Type': 'application/json',
        };
        if (this.token) {
            headers['Authorization'] = `Bearer ${this.token}`;
        }
        const response = await fetch(`${this.baseUrl}${path}`, {
            method: 'POST',
            headers,
            body: JSON.stringify(data),
        });
        return response.json();
    }

    async delete(path: string): Promise<any> {
        const headers: Record<string, string> = {};
        if (this.token) {
            headers['Authorization'] = `Bearer ${this.token}`;
        }
        const response = await fetch(`${this.baseUrl}${path}`, {
            method: 'DELETE',
            headers,
        });
        return response.json();
    }
}
''',

    'frontend/api/auth.ts': '''import { ApiClient } from './client';
import { LoginResponse, User } from '../types';

export class AuthApi {
    private client: ApiClient;

    constructor(client: ApiClient) {
        this.client = client;
    }

    async login(email: string, password: string): Promise<LoginResponse> {
        const response = await this.client.post('/auth/login', { email, password });
        if (response.token) {
            this.client.setToken(response.token);
        }
        return response;
    }

    async register(username: string, email: string, password: string): Promise<any> {
        return this.client.post('/auth/register', { username, email, password });
    }

    async logout(): Promise<void> {
        await this.client.post('/auth/logout', {});
        this.client.clearToken();
    }

    async getCurrentUser(): Promise<User> {
        return this.client.get('/auth/me');
    }
}
''',

    'frontend/hooks/useAuth.ts': '''import { AuthApi } from '../api/auth';
import { ApiClient } from '../api/client';
import { User } from '../types';

const client = new ApiClient('http://localhost:8000');
const authApi = new AuthApi(client);

export function useAuth() {
    let currentUser: User | null = null;

    async function login(email: string, password: string): Promise<boolean> {
        const response = await authApi.login(email, password);
        if (response.token) {
            currentUser = await authApi.getCurrentUser();
            return true;
        }
        return false;
    }

    async function logout(): Promise<void> {
        await authApi.logout();
        currentUser = null;
    }

    function getUser(): User | null {
        return currentUser;
    }

    return { login, logout, getUser, currentUser };
}
''',

    'frontend/hooks/useTasks.ts': '''import { ApiClient } from '../api/client';
import { Task } from '../types';

const client = new ApiClient('http://localhost:8000');

export function useTasks() {
    let tasks: Task[] = [];

    async function fetchTasks(): Promise<Task[]> {
        const response = await client.get('/tasks');
        tasks = response.tasks || [];
        return tasks;
    }

    async function createTask(title: string, description: string): Promise<Task> {
        return client.post('/tasks', { title, description });
    }

    return { tasks, fetchTasks, createTask };
}
''',

    'frontend/components/LoginForm.tsx': '''import { useAuth } from '../hooks/useAuth';

export function LoginForm() {
    const { login } = useAuth();

    async function handleSubmit(email: string, password: string) {
        const success = await login(email, password);
        if (!success) {
            console.error('Login failed');
        }
    }

    return { handleSubmit };
}
''',

    'frontend/components/TaskList.tsx': '''import { useTasks } from '../hooks/useTasks';
import { TaskCard } from './TaskCard';
import { Task } from '../types';

export function TaskList() {
    const { tasks, fetchTasks } = useTasks();

    function renderTasks(taskList: Task[]) {
        return taskList.map(task => TaskCard({ task }));
    }

    return { renderTasks, fetchTasks };
}
''',

    'frontend/components/TaskCard.tsx': '''import { Task } from '../types';

export function TaskCard({ task }: { task: Task }) {
    function getStatusColor(status: string): string {
        switch (status) {
            case 'done': return 'green';
            case 'in_progress': return 'yellow';
            default: return 'gray';
        }
    }

    return { title: task.title, color: getStatusColor(task.status) };
}
''',

    'frontend/components/OldDashboard.tsx': '''import { Task } from '../types';

/** @deprecated Replaced by TaskList — not imported anywhere. */
export function OldDashboard() {
    function calculateStats(tasks: Task[]) {
        const total = tasks.length;
        const completed = tasks.filter(t => t.status === 'done').length;
        return { total, completed, percentage: (completed / total) * 100 };
    }

    function formatDate(dateStr: string): string {
        return new Date(dateStr).toLocaleDateString();
    }

    return { calculateStats, formatDate };
}
''',

    'frontend/services/auth.ts': '''import { AuthApi } from '../api/auth';
import { ApiClient } from '../api/client';

export class AuthService {
    private authApi: AuthApi;

    constructor(baseUrl: string) {
        const client = new ApiClient(baseUrl);
        this.authApi = new AuthApi(client);
    }

    async login(email: string, password: string): Promise<boolean> {
        const response = await this.authApi.login(email, password);
        return !!response.token;
    }

    async logout(): Promise<void> {
        await this.authApi.logout();
    }
}
''',
}

TSCONFIG = {
    "compilerOptions": {
        "target": "ES2020",
        "module": "ESNext",
        "moduleResolution": "node",
        "jsx": "react-jsx",
        "strict": True,
        "baseUrl": ".",
        "paths": {"@/*": ["frontend/*"]}
    },
    "include": ["frontend/**/*"]
}


def create_project(base_dir: Path) -> Path:
    \"\"\"Create the synthetic TaskFlow project and init git + LensPR.\"\"\"
    project_dir = base_dir / 'taskflow'
    project_dir.mkdir(parents=True, exist_ok=True)

    # Write all source files
    for rel_path, content in PROJECT_FILES.items():
        fp = project_dir / rel_path
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_text(content)

    # tsconfig.json
    (project_dir / 'tsconfig.json').write_text(json.dumps(TSCONFIG, indent=2))

    # CLAUDE.md
    (project_dir / 'CLAUDE.md').write_text(CLAUDE_MD)

    # Git init (needed for lens_blame / lens_node_history)
    env = {
        **os.environ,
        'GIT_AUTHOR_NAME': 'dev',
        'GIT_AUTHOR_EMAIL': 'dev@taskflow.io',
        'GIT_COMMITTER_NAME': 'dev',
        'GIT_COMMITTER_EMAIL': 'dev@taskflow.io',
    }
    subprocess.run(['git', 'init'], cwd=project_dir, capture_output=True)
    subprocess.run(['git', 'add', '.'], cwd=project_dir, capture_output=True)
    subprocess.run(['git', 'commit', '-m', 'Initial commit: TaskFlow full-stack app'],
                   cwd=project_dir, capture_output=True, env=env)

    return project_dir


print(f'Project files defined: {len(PROJECT_FILES)} files')
print(f'Backend: {sum(1 for k in PROJECT_FILES if k.startswith("backend/"))} files')
print(f'Frontend: {sum(1 for k in PROJECT_FILES if k.startswith("frontend/"))} files')
"""))

# ═══════════════════════════════════════════════════════════════════════════════
# Cell 4: Initialize project and verify
# ═══════════════════════════════════════════════════════════════════════════════
cells.append(code("""\
# Create project in temp dir
TEMP_DIR = Path(tempfile.mkdtemp(prefix='lenspr_killer_'))
PROJECT_DIR = create_project(TEMP_DIR)
print(f'Project created: {PROJECT_DIR}')

# Initialize LensPR
ctx, stats = lenspr.init(str(PROJECT_DIR), force=True, collect_stats=True)
print(f'LensPR initialized: {ctx is not None}')
if stats:
    print(f'Stats: {stats}')

# Verify both languages parsed
result = lenspr.handle_tool('lens_health', {})
if result['success']:
    h = result['data']
    print(f"Nodes: {h['total_nodes']}, Edges: {h['total_edges']}")

# List classes
result = lenspr.handle_tool('lens_list_nodes', {'type': 'class'})
if result['success']:
    nodes = result['data']['nodes']
    print(f"\\nClasses ({len(nodes)}):")
    for n in nodes:
        print(f"  {n['id']}")

# Check TypeScript nodes
result = lenspr.handle_tool('lens_list_nodes', {'file_path': 'frontend/'})
if result['success']:
    nodes = result['data']['nodes']
    print(f"\\nFrontend nodes: {len(nodes)}")
    for n in nodes[:10]:
        print(f"  {n['id']} ({n['type']})")
"""))

# ═══════════════════════════════════════════════════════════════════════════════
# Cell 5: Diagnose + fix TypeScript parser
# ═══════════════════════════════════════════════════════════════════════════════
cells.append(code("""\
# Check if TypeScript parser loaded
from lenspr.parsers.multi import MultiParser
mp = MultiParser()
ts_ok = any(ext in mp._extension_map for ext in ['.ts', '.tsx'])
print(f'TypeScript parser loaded: {ts_ok}')
print(f'Supported extensions: {mp.get_file_extensions()}')

if not ts_ok:
    print('\\nDiagnosing...')
    try:
        import tree_sitter
        print(f'  tree_sitter: {tree_sitter.__version__}')
    except ImportError:
        print('  tree_sitter: NOT INSTALLED')

    try:
        import tree_sitter_javascript as ts_js
        print(f'  tree_sitter_javascript: OK')
    except ImportError:
        print('  tree_sitter_javascript: NOT INSTALLED')

    try:
        import tree_sitter_typescript as ts_ts
        print(f'  tree_sitter_typescript: OK')
    except ImportError:
        print('  tree_sitter_typescript: NOT INSTALLED')

    print('\\nInstalling missing packages...')
    import subprocess, sys
    subprocess.check_call([
        sys.executable, '-m', 'pip', 'install', '-q',
        'tree-sitter', 'tree-sitter-javascript', 'tree-sitter-typescript'
    ])
    print('Installed. Re-initializing LensPR...')

    # Force reimport of the parser module
    import importlib
    import lenspr.parsers.typescript_parser
    importlib.reload(lenspr.parsers.typescript_parser)
    import lenspr.parsers.multi
    importlib.reload(lenspr.parsers.multi)

    # Re-init LensPR
    ctx, stats = lenspr.init(str(PROJECT_DIR), force=True, collect_stats=True)
    print(f'\\nRe-initialized. Languages: {list(stats.languages.keys())}')
    for lang, s in stats.languages.items():
        print(f'  {lang}: {s.file_count} files, nodes={s.node_counts}')
else:
    # TS parser is fine, check if files were parsed
    result = lenspr.handle_tool('lens_health', {})
    h = result['data']
    print(f'Nodes: {h["total_nodes"]}, Edges: {h["total_edges"]}')

    result = lenspr.handle_tool('lens_list_nodes', {'file_path': 'frontend/'})
    nodes = result['data']['nodes']
    if len(nodes) == 0:
        print('\\nNo frontend nodes found. Re-initializing...')
        ctx, stats = lenspr.init(str(PROJECT_DIR), force=True, collect_stats=True)
        print(f'Languages: {list(stats.languages.keys())}')
        for lang, s in stats.languages.items():
            print(f'  {lang}: {s.file_count} files, nodes={s.node_counts}')
    else:
        print(f'Frontend nodes: {len(nodes)} — all good!')
"""))

# ═══════════════════════════════════════════════════════════════════════════════
# Cell 6: BenchmarkResult dataclass
# ═══════════════════════════════════════════════════════════════════════════════
cells.append(code("""\
@dataclass
class BenchmarkResult:
    \"\"\"Stores results from a single benchmark run.\"\"\"
    task_id: str
    mode: str  # 'with_lenspr' or 'without_lenspr'

    # Token metrics
    total_input_tokens: int = 0
    total_output_tokens: int = 0

    # Iteration metrics
    iterations: int = 0

    # Tool usage
    tool_calls: list = field(default_factory=list)
    tool_call_count: int = 0

    # Success
    completed: bool = False
    failure_reason: str = ''  # 'max_iterations', 'max_tokens', '', or error msg

    # Timing
    started_at: str = ''
    finished_at: str = ''
    duration_seconds: float = 0.0

    # Conversation log (truncated)
    messages: list = field(default_factory=list)

    def save(self, path: Path):
        with open(path, 'w') as f:
            json.dump(asdict(self), f, indent=2, default=str)

    @classmethod
    def load(cls, path: Path) -> 'BenchmarkResult':
        with open(path) as f:
            data = json.load(f)
        return cls(**data)

print('BenchmarkResult defined')
"""))

# ═══════════════════════════════════════════════════════════════════════════════
# Cell 6: Tool definitions + handlers
# ═══════════════════════════════════════════════════════════════════════════════
cells.append(code("""\
# ── Standard tools (available in both modes) ──
STANDARD_TOOLS = [
    {
        'name': 'read_file',
        'description': 'Read a file. Returns file content.',
        'input_schema': {
            'type': 'object',
            'properties': {'path': {'type': 'string', 'description': 'Relative path to file'}},
            'required': ['path']
        }
    },
    {
        'name': 'write_file',
        'description': 'Write content to a file (create or overwrite).',
        'input_schema': {
            'type': 'object',
            'properties': {
                'path': {'type': 'string', 'description': 'Relative path'},
                'content': {'type': 'string', 'description': 'File content'}
            },
            'required': ['path', 'content']
        }
    },
    {
        'name': 'search_files',
        'description': 'Search for a regex pattern across project files. Returns matching lines with paths.',
        'input_schema': {
            'type': 'object',
            'properties': {
                'pattern': {'type': 'string', 'description': 'Regex pattern'},
                'path': {'type': 'string', 'description': 'Directory to search', 'default': '.'}
            },
            'required': ['pattern']
        }
    },
    {
        'name': 'list_files',
        'description': 'List all files in a directory recursively.',
        'input_schema': {
            'type': 'object',
            'properties': {
                'path': {'type': 'string', 'description': 'Directory path', 'default': '.'}
            }
        }
    },
    {
        'name': 'task_complete',
        'description': 'Call this when the task is complete. Provide a detailed summary.',
        'input_schema': {
            'type': 'object',
            'properties': {
                'summary': {'type': 'string', 'description': 'Detailed summary of completed work'}
            },
            'required': ['summary']
        }
    }
]

# ── LensPR tools (WITH mode only) ──
LENSPR_TOOLS = lenspr.get_claude_tools() + STANDARD_TOOLS

print(f'Standard tools: {len(STANDARD_TOOLS)}')
print(f'LensPR tools: {len(LENSPR_TOOLS)} ({len(LENSPR_TOOLS) - len(STANDARD_TOOLS)} lens_* + {len(STANDARD_TOOLS)} standard)')

# ── Tool handlers ──
def handle_standard_tool(name: str, inputs: dict) -> str:
    try:
        if name == 'read_file':
            path = PROJECT_DIR / inputs['path']
            if not path.exists():
                return f'Error: File not found: {inputs["path"]}'
            return path.read_text()

        elif name == 'write_file':
            path = PROJECT_DIR / inputs['path']
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(inputs['content'])
            return f'Wrote {len(inputs["content"])} chars to {inputs["path"]}'

        elif name == 'search_files':
            search_path = PROJECT_DIR / inputs.get('path', '.')
            result = subprocess.run(
                ['grep', '-rn', inputs['pattern'], str(search_path)],
                capture_output=True, text=True, timeout=30
            )
            output = result.stdout or 'No matches found'
            if len(output) > 10000:
                output = output[:10000] + f'\\n... (truncated, {len(output)} total chars)'
            return output

        elif name == 'list_files':
            path = PROJECT_DIR / inputs.get('path', '.')
            if not path.exists():
                return f'Error: Directory not found: {inputs.get("path", ".")}'
            files = []
            for p in sorted(path.rglob('*')):
                if p.is_file() and not any(part.startswith('.') for part in p.parts):
                    files.append(str(p.relative_to(PROJECT_DIR)))
            return '\\n'.join(files[:200]) if files else 'No files found'

        elif name == 'task_complete':
            return f'TASK_COMPLETE: {inputs.get("summary", "No summary")}'

        else:
            return f'Unknown tool: {name}'
    except Exception as e:
        return f'Error in {name}: {e}'


def handle_tool_call(name: str, inputs: dict) -> str:
    if name.startswith('lens_'):
        result = lenspr.handle_tool(name, inputs)
        return json.dumps(result, indent=2, default=str)
    return handle_standard_tool(name, inputs)

print('Tool handlers defined')
"""))

# ═══════════════════════════════════════════════════════════════════════════════
# Cell 7: System prompts
# ═══════════════════════════════════════════════════════════════════════════════
cells.append(code("""\
SYSTEM_PROMPT_WITHOUT = f'''You are a code assistant working on TaskFlow, a full-stack Python + TypeScript project.

{CLAUDE_MD}

Available tools:
- read_file: Read file contents
- write_file: Write/create files
- search_files: Search with regex (grep)
- list_files: List directory contents
- task_complete: Call when done with a detailed summary

Rules:
1. Complete the task fully and accurately
2. When modifying files, update ALL affected locations
3. Search thoroughly before concluding something doesn't exist
4. Call task_complete with a detailed summary when done
'''

SYSTEM_PROMPT_WITH = f'''You are a code assistant working on TaskFlow, a full-stack Python + TypeScript project.
LensPR code graph tools are available for both Python and TypeScript.

{CLAUDE_MD}

## LensPR Tools (use these for code analysis!)

NAVIGATION:
- lens_context: Get function source + callers + callees + tests (ONE call for everything — use this first!)
- lens_search: Find nodes by name
- lens_grep: Regex search with graph context (shows containing function)
- lens_get_structure: Project overview
- lens_list_nodes: List functions/classes/modules
- lens_explain: Human-readable explanation of a function

ANALYSIS:
- lens_check_impact: ALWAYS check before modifying code (shows severity CRITICAL/HIGH/MEDIUM/LOW)
- lens_find_usages: Find ALL references (callers, importers, inheritors)
- lens_validate_change: Dry-run validation before applying
- lens_dead_code: Find unreachable code
- lens_health: Project health report
- lens_components: Component cohesion analysis

MODIFICATION:
- lens_rename: Rename across entire project
- lens_update_node: Update a single function/class
- lens_batch: Atomic multi-node updates (all succeed or all fail)
- lens_add_node: Add new function/class

ARCHITECTURE:
- lens_class_metrics: Method count, lines, dependencies for a class
- lens_project_metrics: Project-wide class statistics
- lens_largest_classes: Find biggest/most complex classes
- lens_compare_classes: Side-by-side class comparison

GIT:
- lens_blame: Who wrote each line of a function
- lens_node_history: Commits that modified a function

Standard tools also available: read_file, write_file, search_files, list_files

Rules:
1. Use lens_check_impact BEFORE any code changes
2. Use lens_context to understand functions (one call instead of many reads)
3. Use lens_find_usages for precise dependency tracking
4. Call task_complete with a detailed summary when done
'''

print(f'System prompts defined')
print(f'  WITHOUT: {len(SYSTEM_PROMPT_WITHOUT)} chars')
print(f'  WITH:    {len(SYSTEM_PROMPT_WITH)} chars')
"""))

# ═══════════════════════════════════════════════════════════════════════════════
# Cell 8: Agentic loop
# ═══════════════════════════════════════════════════════════════════════════════
cells.append(code("""\
def run_agent(
    task: str,
    tools: list,
    system_prompt: str,
    max_iterations: int = MAX_ITERATIONS,
    max_input_tokens: int = MAX_INPUT_TOKENS,
    model: str = MODEL,
) -> BenchmarkResult:
    \"\"\"Run Claude agent until task_complete, max iterations, or max tokens.\"\"\"
    mode = 'with_lenspr' if any(t['name'].startswith('lens_') for t in tools) else 'without_lenspr'
    result = BenchmarkResult(
        task_id='',
        mode=mode,
        started_at=datetime.now().isoformat(),
    )

    messages = [{'role': 'user', 'content': task}]

    for iteration in range(max_iterations):
        result.iterations += 1
        print(f'\\n--- Iteration {result.iterations} ---')

        # Check token limit BEFORE calling API
        if result.total_input_tokens >= max_input_tokens:
            result.failure_reason = 'max_tokens'
            result.finished_at = datetime.now().isoformat()
            result.duration_seconds = (
                datetime.fromisoformat(result.finished_at) -
                datetime.fromisoformat(result.started_at)
            ).total_seconds()
            result.messages = [
                {'role': m['role'], 'content': str(m['content'])[:500]}
                for m in messages
            ]
            print(f'\\n Token limit reached ({result.total_input_tokens:,} >= {max_input_tokens:,})')
            return result

        # Call Claude (with rate-limit retry)
        for attempt in range(5):
            try:
                response = client.messages.create(
                    model=model,
                    max_tokens=4096,
                    system=system_prompt,
                    tools=tools,
                    messages=messages,
                )
                break
            except Exception as e:
                if '429' in str(e) or 'rate' in str(e).lower() or 'overloaded' in str(e).lower():
                    wait = 2 ** attempt * 10  # 10s, 20s, 40s, 80s, 160s
                    print(f'  Rate limited, waiting {wait}s (attempt {attempt+1}/5)...')
                    time.sleep(wait)
                else:
                    raise
        else:
            result.failure_reason = 'rate_limited'
            result.finished_at = datetime.now().isoformat()
            result.duration_seconds = (
                datetime.fromisoformat(result.finished_at) -
                datetime.fromisoformat(result.started_at)
            ).total_seconds()
            result.messages = [
                {'role': m['role'], 'content': str(m['content'])[:500]}
                for m in messages
            ]
            print(f'\\n Rate limit exhausted after 5 retries')
            return result

        # Track tokens
        result.total_input_tokens += response.usage.input_tokens
        result.total_output_tokens += response.usage.output_tokens
        print(f'Tokens: +{response.usage.input_tokens:,} in, +{response.usage.output_tokens:,} out'
              f'  (cumulative: {result.total_input_tokens:,} in)')

        # Process response
        assistant_content = response.content
        messages.append({'role': 'assistant', 'content': assistant_content})

        # Print text output
        for block in assistant_content:
            if hasattr(block, 'text'):
                text = block.text[:200] + '...' if len(block.text) > 200 else block.text
                print(f'Claude: {text}')

        # Handle tool use
        if response.stop_reason == 'tool_use':
            tool_results = []

            for block in assistant_content:
                if block.type == 'tool_use':
                    tool_name = block.name
                    tool_input = block.input

                    result.tool_calls.append({'name': tool_name, 'input': tool_input})
                    result.tool_call_count += 1

                    input_preview = json.dumps(tool_input)[:100]
                    print(f'Tool: {tool_name}({input_preview}...)')

                    # Task completion
                    if tool_name == 'task_complete':
                        result.completed = True
                        result.failure_reason = ''
                        result.finished_at = datetime.now().isoformat()
                        result.duration_seconds = (
                            datetime.fromisoformat(result.finished_at) -
                            datetime.fromisoformat(result.started_at)
                        ).total_seconds()
                        result.messages = [
                            {'role': m['role'], 'content': str(m['content'])[:500]}
                            for m in messages
                        ]
                        print(f'\\n Task completed in {result.duration_seconds:.1f}s')
                        return result

                    # Execute tool
                    tool_result = handle_tool_call(tool_name, tool_input)
                    tool_results.append({
                        'type': 'tool_result',
                        'tool_use_id': block.id,
                        'content': tool_result[:5000],
                    })

            messages.append({'role': 'user', 'content': tool_results})

        elif response.stop_reason == 'end_turn':
            print('Claude stopped without completing. Prompting to continue...')
            messages.append({
                'role': 'user',
                'content': 'Please continue with the task. Call task_complete when done.'
            })

    # Max iterations reached
    result.failure_reason = 'max_iterations'
    result.finished_at = datetime.now().isoformat()
    result.duration_seconds = (
        datetime.fromisoformat(result.finished_at) -
        datetime.fromisoformat(result.started_at)
    ).total_seconds()
    result.messages = [
        {'role': m['role'], 'content': str(m['content'])[:500]}
        for m in messages
    ]
    print(f'\\n Max iterations reached ({max_iterations})')
    return result

print('run_agent() defined')
"""))

# ═══════════════════════════════════════════════════════════════════════════════
# Cell 9: Task definitions
# ═══════════════════════════════════════════════════════════════════════════════
cells.append(code("""\
TASKS = {
    'task1_rename': {
        'name': 'Cross-project Rename',
        'prompt': '''Rename the function `validate_email` in backend/utils/validators.py to `is_valid_email_format`.

Steps:
1. Find all places where validate_email is used
2. Analyze the impact of this rename
3. Perform the rename (update all references)
4. Verify the rename worked — no remaining references to the old name

Call task_complete with: how many files were modified, which functions were updated, and any issues found.
''',
        'expected_tools': ['lens_find_usages', 'lens_check_impact', 'lens_rename'],
    },

    'task2_architecture': {
        'name': 'Architecture Review',
        'prompt': '''Perform an architecture review of this project:

1. Find the largest / most complex classes in the project
2. Get detailed metrics for the top 2 classes (method count, lines, dependencies)
3. Compare them side by side
4. Analyze component cohesion for the backend/services directory
5. Provide overall architecture assessment — any concerns?

Call task_complete with a detailed architecture report including specific numbers.
''',
        'expected_tools': ['lens_largest_classes', 'lens_class_metrics', 'lens_compare_classes', 'lens_components'],
    },

    'task3_dead_code': {
        'name': 'Dead Code Audit',
        'prompt': '''Perform a comprehensive dead code audit covering BOTH Python and TypeScript files.

1. Use dead code detection to find potentially unreachable code
2. For each candidate, verify it is truly dead by checking for usages
3. Check for false positives — some code may be used dynamically
4. List ALL confirmed dead code with file paths and function/class names

Call task_complete with the full dead code report for both languages.
''',
        'expected_tools': ['lens_dead_code', 'lens_find_usages', 'lens_grep'],
    },

    'task4_batch_update': {
        'name': 'Atomic Batch Refactoring',
        'prompt': '''Add a `logger` parameter to BOTH the TaskService and AuthService __init__ methods.

Requirements:
- Add parameter: `logger = None` (with default None)
- Add `self.logger = logger` in each __init__ body
- Both updates must be applied atomically (either both succeed or neither does)
- Validate changes before applying

Steps:
1. Check the impact of modifying both classes
2. Get the current source of both __init__ methods
3. Validate the proposed changes
4. Apply both updates atomically
5. Verify the changes were applied

Call task_complete with what was changed and whether the atomic update succeeded.
''',
        'expected_tools': ['lens_check_impact', 'lens_validate_change', 'lens_batch'],
    },

    'task5_cross_language': {
        'name': 'Cross-Language Flow Analysis',
        'prompt': '''Trace the complete login flow from the frontend UI to the backend database.

Map the FULL call chain across both languages:
1. Start from the LoginForm component in frontend/components/
2. Follow through hooks and API client in TypeScript
3. Cross the HTTP boundary to the Python backend
4. Trace through the backend service layer to the database query

For each step show: function name, file, what it does, and what it calls next.

Call task_complete with the complete flow diagram showing both TypeScript and Python sides.
''',
        'expected_tools': ['lens_context', 'lens_explain', 'lens_grep', 'lens_get_connections'],
    },

    'task6_impact_git': {
        'name': 'Impact Analysis + Git',
        'prompt': '''I want to add a new required field `role: str` to the User dataclass in backend/models.py.

Analyze the FULL impact WITHOUT making the change:
1. What classes/functions directly reference the User class?
2. What is the transitive impact (depth 3)?
3. Which backend services and API routes would be affected?
4. Are there TypeScript types that mirror User and would also need updating?
5. What is the overall severity/risk of this change?
6. Check who wrote the User class and when it was last modified

Call task_complete with a comprehensive impact report.
''',
        'expected_tools': ['lens_check_impact', 'lens_find_usages', 'lens_context', 'lens_blame'],
    },
}

print(f'Defined {len(TASKS)} tasks:')
for tid, t in TASKS.items():
    print(f'  {tid}: {t["name"]}')
"""))

# ═══════════════════════════════════════════════════════════════════════════════
# Cell 10: Runner
# ═══════════════════════════════════════════════════════════════════════════════
cells.append(code("""\
def run_benchmark(task_id: str, with_lenspr: bool) -> BenchmarkResult:
    \"\"\"Run a single benchmark task in the specified mode.\"\"\"
    task = TASKS[task_id]
    mode = 'with_lenspr' if with_lenspr else 'without_lenspr'

    print(f'\\n{"=" * 60}')
    print(f'Running: {task["name"]} ({mode})')
    print(f'{"=" * 60}')

    tools = LENSPR_TOOLS if with_lenspr else STANDARD_TOOLS
    system = SYSTEM_PROMPT_WITH if with_lenspr else SYSTEM_PROMPT_WITHOUT

    result = run_agent(
        task=task['prompt'],
        tools=tools,
        system_prompt=system,
    )

    result.task_id = task_id
    result.mode = mode

    # Save
    result_path = RESULTS_DIR / f'{task_id}_{mode}.json'
    result.save(result_path)
    print(f'Saved: {result_path}')

    return result


def run_all_benchmarks(task_ids=None):
    \"\"\"Run all tasks in both modes. Returns dict of results.\"\"\"
    if task_ids is None:
        task_ids = list(TASKS.keys())

    all_results = {}

    for mode_with_lenspr in [False, True]:
        mode = 'with_lenspr' if mode_with_lenspr else 'without_lenspr'
        print(f'\\n\\n{"#" * 60}')
        print(f'# MODE: {mode.upper()}')
        print(f'{"#" * 60}')

        # Re-create project for clean state each mode
        global PROJECT_DIR
        if PROJECT_DIR.exists():
            shutil.rmtree(PROJECT_DIR.parent)
        base = Path(tempfile.mkdtemp(prefix='lenspr_killer_'))
        PROJECT_DIR = create_project(base)
        ctx, _ = lenspr.init(str(PROJECT_DIR), force=True)
        print(f'Fresh project: {PROJECT_DIR}')

        for task_id in task_ids:
            result = run_benchmark(task_id, with_lenspr=mode_with_lenspr)
            all_results[f'{task_id}_{mode}'] = result

    return all_results

print('Runner defined. Execute next cell to start benchmarks.')
"""))

# ═══════════════════════════════════════════════════════════════════════════════
# Cell 11: Execute
# ═══════════════════════════════════════════════════════════════════════════════
cells.append(md("""\
## Run Benchmarks

Execute the cell below to run all 6 tasks in both modes.
This will make ~60-360 API calls and may take 10-30 minutes.
"""))

cells.append(code("""\
all_results = run_all_benchmarks()
print(f'\\nCompleted {len(all_results)} benchmark runs')
"""))

# ═══════════════════════════════════════════════════════════════════════════════
# Cell 12: Results table + timing
# ═══════════════════════════════════════════════════════════════════════════════
cells.append(md("## Results"))

cells.append(code("""\
def load_results():
    \"\"\"Load all results from saved JSON files.\"\"\"
    results = {}
    for path in RESULTS_DIR.glob('*.json'):
        r = BenchmarkResult.load(path)
        results[f'{r.task_id}_{r.mode}'] = r
    return results

def display_results(results):
    \"\"\"Display comparison table.\"\"\"
    print('\\n' + '=' * 110)
    print('RESULTS: LensPR Killer Features Evaluation')
    print('=' * 110)
    print()
    print(f'{\"Task\":<28} {\"Mode\":<12} {\"Status\":<12} {\"Iter\":>5} {\"Input Tok\":>12} {\"Output Tok\":>12} {\"Time (s)\":>10} {\"Failure\":>15}')
    print('-' * 110)

    totals = {
        'with_lenspr': {'in': 0, 'out': 0, 'iter': 0, 'time': 0, 'pass': 0},
        'without_lenspr': {'in': 0, 'out': 0, 'iter': 0, 'time': 0, 'pass': 0},
    }

    for task_id in TASKS:
        for mode in ['without_lenspr', 'with_lenspr']:
            key = f'{task_id}_{mode}'
            if key not in results:
                continue
            r = results[key]
            mode_short = 'WITHOUT' if 'without' in mode else 'WITH'
            status = 'PASS' if r.completed else 'FAIL'
            failure = r.failure_reason if not r.completed else ''
            print(f'{TASKS[task_id][\"name\"]:<28} {mode_short:<12} {status:<12} '
                  f'{r.iterations:>5} {r.total_input_tokens:>12,} {r.total_output_tokens:>12,} '
                  f'{r.duration_seconds:>10.1f} {failure:>15}')

            totals[mode]['in'] += r.total_input_tokens
            totals[mode]['out'] += r.total_output_tokens
            totals[mode]['iter'] += r.iterations
            totals[mode]['time'] += r.duration_seconds
            totals[mode]['pass'] += int(r.completed)
        print()

    print('-' * 110)
    for mode in ['without_lenspr', 'with_lenspr']:
        t = totals[mode]
        label = 'WITHOUT' if 'without' in mode else 'WITH'
        print(f'{\"TOTAL\":<28} {label:<12} {t[\"pass\"]}/{len(TASKS):<10} '
              f'{t[\"iter\"]:>5} {t[\"in\"]:>12,} {t[\"out\"]:>12,} {t[\"time\"]:>10.1f}')

    # Deltas
    print()
    w, wo = totals['with_lenspr'], totals['without_lenspr']
    if wo['iter'] > 0:
        iter_pct = (wo['iter'] - w['iter']) / wo['iter'] * 100
        print(f'Iteration savings: {iter_pct:+.1f}%')
    if wo['in'] > 0:
        tok_pct = (wo['in'] - w['in']) / wo['in'] * 100
        print(f'Input token savings: {tok_pct:+.1f}%')
    if wo['time'] > 0:
        time_pct = (wo['time'] - w['time']) / wo['time'] * 100
        print(f'Time savings: {time_pct:+.1f}%')
    print(f'Task completion: WITHOUT {wo[\"pass\"]}/{len(TASKS)} vs WITH {w[\"pass\"]}/{len(TASKS)}')

# Load and display
try:
    results = all_results
except NameError:
    results = load_results()
display_results(results)
"""))

# ═══════════════════════════════════════════════════════════════════════════════
# Cell 13: Tool usage breakdown
# ═══════════════════════════════════════════════════════════════════════════════
cells.append(code("""\
def tool_usage_report(results):
    \"\"\"Show which tools were used per task and expected vs actual.\"\"\"
    print('\\n' + '=' * 80)
    print('TOOL USAGE REPORT')
    print('=' * 80)

    for task_id in TASKS:
        task = TASKS[task_id]
        print(f'\\n--- {task[\"name\"]} ---')
        expected = set(task.get('expected_tools', []))

        for mode in ['without_lenspr', 'with_lenspr']:
            key = f'{task_id}_{mode}'
            if key not in results:
                continue
            r = results[key]
            used = [t['name'] for t in r.tool_calls]
            used_set = set(used)
            lens_used = [t for t in used if t.startswith('lens_')]
            std_used = [t for t in used if not t.startswith('lens_')]

            label = 'WITHOUT' if 'without' in mode else 'WITH'
            print(f'  {label}: {len(used)} calls ({len(lens_used)} lens_*, {len(std_used)} standard)')
            if lens_used:
                from collections import Counter
                counts = Counter(lens_used)
                print(f'    LensPR: {", ".join(f"{t}({c})" for t, c in counts.most_common(8))}')
            if 'with' in mode and expected:
                hit = expected & used_set
                miss = expected - used_set
                print(f'    Expected tools used: {len(hit)}/{len(expected)}'
                      + (f' (missing: {", ".join(miss)})' if miss else ' (all used)'))

tool_usage_report(results)
"""))

# ═══════════════════════════════════════════════════════════════════════════════
# Cell 14: Charts
# ═══════════════════════════════════════════════════════════════════════════════
cells.append(code("""\
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np

def generate_charts(results):
    task_names = [TASKS[tid]['name'] for tid in TASKS]
    n = len(task_names)
    x = np.arange(n)
    width = 0.35

    # Collect data
    iters_wo, iters_w = [], []
    tokens_wo, tokens_w = [], []
    time_wo, time_w = [], []
    pass_wo, pass_w = [], []

    for task_id in TASKS:
        for mode, iters, tokens, times, passes in [
            ('without_lenspr', iters_wo, tokens_wo, time_wo, pass_wo),
            ('with_lenspr', iters_w, tokens_w, time_w, pass_w),
        ]:
            key = f'{task_id}_{mode}'
            r = results.get(key)
            if r:
                iters.append(r.iterations)
                tokens.append(r.total_input_tokens / 1000)
                times.append(r.duration_seconds)
                passes.append(int(r.completed))
            else:
                iters.append(0)
                tokens.append(0)
                times.append(0)
                passes.append(0)

    fig, axes = plt.subplots(2, 2, figsize=(16, 12))
    colors_wo = '#ff6b6b'
    colors_w = '#4ecdc4'

    # 1. Iterations
    ax = axes[0, 0]
    ax.bar(x - width/2, iters_wo, width, label='Without LensPR', color=colors_wo)
    ax.bar(x + width/2, iters_w, width, label='With LensPR', color=colors_w)
    ax.set_ylabel('Iterations')
    ax.set_title('Iterations per Task')
    ax.set_xticks(x)
    ax.set_xticklabels(task_names, rotation=30, ha='right', fontsize=8)
    ax.legend()
    ax.axhline(y=MAX_ITERATIONS, color='red', linestyle='--', alpha=0.3, label=f'Limit ({MAX_ITERATIONS})')

    # 2. Input Tokens
    ax = axes[0, 1]
    ax.bar(x - width/2, tokens_wo, width, label='Without LensPR', color=colors_wo)
    ax.bar(x + width/2, tokens_w, width, label='With LensPR', color=colors_w)
    ax.set_ylabel('Input Tokens (K)')
    ax.set_title('Token Usage per Task')
    ax.set_xticks(x)
    ax.set_xticklabels(task_names, rotation=30, ha='right', fontsize=8)
    ax.legend()
    ax.axhline(y=MAX_INPUT_TOKENS/1000, color='red', linestyle='--', alpha=0.3)

    # 3. Time
    ax = axes[1, 0]
    ax.bar(x - width/2, time_wo, width, label='Without LensPR', color=colors_wo)
    ax.bar(x + width/2, time_w, width, label='With LensPR', color=colors_w)
    ax.set_ylabel('Time (seconds)')
    ax.set_title('Time per Task')
    ax.set_xticks(x)
    ax.set_xticklabels(task_names, rotation=30, ha='right', fontsize=8)
    ax.legend()

    # 4. Success Rate
    ax = axes[1, 1]
    total_wo = sum(pass_wo)
    total_w = sum(pass_w)
    bars = ax.bar(['Without LensPR', 'With LensPR'], [total_wo, total_w],
                  color=[colors_wo, colors_w])
    ax.set_ylabel('Tasks Completed')
    ax.set_title('Task Completion Rate')
    ax.set_ylim(0, n + 0.5)
    for bar, val in zip(bars, [total_wo, total_w]):
        ax.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 0.1,
                f'{val}/{n}', ha='center', fontweight='bold', fontsize=14)

    plt.suptitle('LensPR Killer Features: WITH vs WITHOUT', fontsize=16, fontweight='bold')
    plt.tight_layout()

    chart_path = RESULTS_DIR / 'killer_features_summary.png'
    plt.savefig(chart_path, dpi=150, bbox_inches='tight')
    print(f'Chart saved: {chart_path}')
    plt.show()

generate_charts(results)
"""))

# ═══════════════════════════════════════════════════════════════════════════════
# Cell 15: Markdown summary for README/Git
# ═══════════════════════════════════════════════════════════════════════════════
cells.append(code("""\
def generate_markdown_summary(results):
    \"\"\"Generate a markdown summary table for README/Git.\"\"\"
    lines = []
    lines.append('## Killer Features Benchmark Results')
    lines.append('')
    lines.append(f'**Model**: {MODEL}  ')
    lines.append(f'**Limits**: {MAX_ITERATIONS} iterations, {MAX_INPUT_TOKENS:,} input tokens per task  ')
    lines.append('')
    lines.append('| Task | Mode | Status | Iter | Input Tokens | Time (s) |')
    lines.append('|------|------|--------|------|-------------|----------|')

    for task_id in TASKS:
        for mode in ['without_lenspr', 'with_lenspr']:
            key = f'{task_id}_{mode}'
            r = results.get(key)
            if not r:
                continue
            label = 'WITHOUT' if 'without' in mode else '**WITH**'
            status = 'PASS' if r.completed else f'FAIL ({r.failure_reason})'
            lines.append(
                f'| {TASKS[task_id][\"name\"]} | {label} | {status} | '
                f'{r.iterations} | {r.total_input_tokens:,} | {r.duration_seconds:.1f} |'
            )

    # Totals
    lines.append('')
    for mode in ['without_lenspr', 'with_lenspr']:
        total_iter = sum(results[f'{tid}_{mode}'].iterations for tid in TASKS if f'{tid}_{mode}' in results)
        total_tok = sum(results[f'{tid}_{mode}'].total_input_tokens for tid in TASKS if f'{tid}_{mode}' in results)
        total_time = sum(results[f'{tid}_{mode}'].duration_seconds for tid in TASKS if f'{tid}_{mode}' in results)
        total_pass = sum(int(results[f'{tid}_{mode}'].completed) for tid in TASKS if f'{tid}_{mode}' in results)
        label = 'WITHOUT' if 'without' in mode else '**WITH**'
        lines.append(f'| **TOTAL** | {label} | {total_pass}/{len(TASKS)} | '
                     f'{total_iter} | {total_tok:,} | {total_time:.1f} |')

    md_text = '\\n'.join(lines)

    # Save
    md_path = RESULTS_DIR / 'killer_features_results.md'
    md_path.write_text(md_text)
    print(f'Markdown saved: {md_path}')
    print()
    print(md_text)

generate_markdown_summary(results)
"""))

# ═══════════════════════════════════════════════════════════════════════════════
# Cell 16: Cleanup
# ═══════════════════════════════════════════════════════════════════════════════
cells.append(code("""\
# Cleanup temp directories
try:
    if PROJECT_DIR.exists():
        shutil.rmtree(PROJECT_DIR.parent, ignore_errors=True)
        print(f'Cleaned up: {PROJECT_DIR.parent}')
except Exception as e:
    print(f'Cleanup note: {e}')

print('Done! Results saved in results/killer_features/')
"""))

# ═══════════════════════════════════════════════════════════════════════════════
# Assemble notebook
# ═══════════════════════════════════════════════════════════════════════════════
notebook = {
    "cells": cells,
    "metadata": {
        "kernelspec": {
            "display_name": "Python 3",
            "language": "python",
            "name": "python3",
        },
        "language_info": {
            "name": "python",
            "version": "3.12.0",
        },
    },
    "nbformat": 4,
    "nbformat_minor": 5,
}

output_path = Path(__file__).parent / "killer_features.ipynb"
output_path.write_text(json.dumps(notebook, indent=1))
print(f"Created {output_path} ({len(cells)} cells)")
