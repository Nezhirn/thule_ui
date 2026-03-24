#!/usr/bin/env python3
"""
Qwen Agent — FastAPI + WebSocket + Qwen CLI + MCP
Веб-интерфейс для qwen-cli с поддержкой MCP инструментов.

Фичи:
  - Стриминг ответов с отображением thinking
  - Множественные сессии чатов (SQLite)
  - MCP инструменты (bash, ssh, web, memory)
  - Tool calling loop
  - Подтверждение bash/ssh команд перед выполнением
  - Остановка генерации по запросу пользователя
"""

from dotenv import load_dotenv
load_dotenv(override=True)
from system_prompt import SYSTEM_PROMPT, get_system_prompt

import asyncio
import json
import logging
import os
import select
import shlex
import shutil
import signal
import sqlite3
import subprocess
import sys
import uuid
from contextlib import asynccontextmanager, closing
from datetime import datetime
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.exceptions import RequestValidationError
from fastapi.encoders import jsonable_encoder
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


# ─── Конфигурация ───────────────────────────────────────────────

# Настройка логирования ДО всех импортов которые используют logger
log_handler = logging.FileHandler(Path(__file__).parent / "server.log")
log_handler.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))
log_handler.setLevel(logging.INFO)

logger = logging.getLogger(__name__)
logger.addHandler(log_handler)
logger.setLevel(logging.INFO)

# ─── Конфигурация ───────────────────────────────────────────────

DB_PATH = str(Path(__file__).parent / "sessions.db")
MCP_SERVER_SCRIPT = str(Path(__file__).parent / "mcp_tools_server.py")




# Активные stream-задачи по session_id
session_tasks: dict = {}
session_tasks_lock = asyncio.Lock()

# Отдельное хранилище фоновых job-ов инструментов по task_id
background_jobs: dict = {}
background_jobs_lock = asyncio.Lock()
BACKGROUND_JOB_TTL_SECONDS = 3600

# Инструменты требующие подтверждения
TOOLS_REQUIRING_CONFIRMATION = {
    # Bash/Shell инструменты
    "Bash", "bash", "run_bash_command", "execute_command",
    "run_command", "shell", "run_shell_command",
    # SSH инструменты
    "ssh", "SSH", "run_ssh_command", "remote_command",
    # Файловые инструменты (опасные)
    "Write", "write_file", "create_file", "WriteFile",
    "Edit", "edit_file", "replace_in_file", "EditFile",
    "delete_file", "remove_file", "rm_file",
    # Операционные системы
    "system_reboot", "system_shutdown", "reboot", "shutdown",
}

# Лимиты безопасности
MAX_REQUEST_SIZE = 50 * 1024 * 1024  # 50 MB


class CreateSessionPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(..., min_length=1, max_length=200)
    provider: str = Field(default="qwen")
    model: Optional[str] = None

    @field_validator("title")
    @classmethod
    def validate_title(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Title must not be empty")
        return value


class RenameSessionPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(..., min_length=1, max_length=200)

    @field_validator("title")
    @classmethod
    def validate_title(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Title must not be empty")
        return value


class SessionPromptPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    system_prompt: Optional[str] = None


class SessionSettingsPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str
    model: Optional[str] = None


class RequestSizeLimitMiddleware:
    """ASGI middleware для ограничения размера тела запроса. Совместим с WebSocket."""
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            headers = dict(scope.get("headers", []))
            content_length = headers.get(b"content-length")
            if content_length and int(content_length) > MAX_REQUEST_SIZE:
                response = JSONResponse({"error": "Запрос слишком большой"}, status_code=413)
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)


class SecurityHeadersMiddleware:
    """ASGI middleware для добавления security headers. Совместим с WebSocket."""
    HEADERS = [
        (b"x-content-type-options", b"nosniff"),
        (b"x-frame-options", b"DENY"),
        (b"x-xss-protection", b"1; mode=block"),
        (b"referrer-policy", b"strict-origin-when-cross-origin"),
    ]

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message):
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers.extend(self.HEADERS)
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_with_headers)



# ─── База данных ────────────────────────────────────────────────

def init_db():
    with closing(get_db()) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY,
                user_id TEXT,
                title TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                system_prompt TEXT DEFAULT NULL
            )
        """)
        for statement in (
            "ALTER TABLE sessions ADD COLUMN user_id TEXT",
            "ALTER TABLE sessions ADD COLUMN system_prompt TEXT DEFAULT NULL",
            "ALTER TABLE sessions ADD COLUMN provider TEXT DEFAULT 'qwen'",
            "ALTER TABLE sessions ADD COLUMN model TEXT DEFAULT NULL",
        ):
            try:
                conn.execute(statement)
            except sqlite3.OperationalError:
                pass
        conn.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                thinking TEXT,
                tool_calls TEXT,
                tool_name TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS memory (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                key TEXT NOT NULL,
                value TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(session_id, key)
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_sessions_user_id ON sessions(user_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_messages_session_id ON messages(session_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_memory_session_id ON memory(session_id)")
        conn.commit()


def get_db() -> sqlite3.Connection:
    """Создаёт соединение SQLite с правильными настройками."""
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def get_sessions(user_id: Optional[str] = None):
    conn = get_db()
    conn.row_factory = sqlite3.Row
    if user_id:
        rows = conn.execute(
            "SELECT * FROM sessions WHERE user_id = ? OR user_id IS NULL ORDER BY updated_at DESC",
            (user_id,),
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM sessions ORDER BY updated_at DESC").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def create_session(title="Новый чат", user_id: Optional[str] = None, provider: str = "qwen", model: Optional[str] = None):
    sid = str(uuid.uuid4())
    now = datetime.now().isoformat()

    # Validate provider
    if provider not in ["qwen", "claude"]:
        provider = "qwen"

    # Validate model for claude
    if provider == "claude" and model not in ["opus", "sonnet", "haiku"]:
        model = "sonnet"  # Default to sonnet

    conn = get_db()
    conn.execute(
        "INSERT INTO sessions (id, user_id, title, created_at, updated_at, provider, model) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (sid, user_id, title, now, now, provider, model),
    )
    conn.commit()
    conn.close()
    return {
        "id": sid,
        "sid": sid,
        "title": title,
        "created_at": now,
        "updated_at": now,
        "user_id": user_id,
        "provider": provider,
        "model": model,
    }


def rename_session(sid: str, title: str):
    conn = get_db()
    cursor = conn.execute("UPDATE sessions SET title = ?, updated_at = ? WHERE id = ?", (title, datetime.now().isoformat(), sid))
    conn.commit()
    conn.close()
    return cursor.rowcount > 0


def delete_session(sid: str):
    conn = get_db()
    conn.execute("DELETE FROM memory WHERE session_id = ?", (sid,))
    conn.execute("DELETE FROM messages WHERE session_id = ?", (sid,))
    cursor = conn.execute("DELETE FROM sessions WHERE id = ?", (sid,))
    conn.commit()
    conn.close()
    return cursor.rowcount > 0


def get_messages(session_id: str):
    conn = get_db()
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM messages WHERE session_id = ? ORDER BY id ASC",
        (session_id,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def save_message(session_id, role, content, thinking=None, tool_calls=None, tool_name=None):
    now = datetime.now().isoformat()
    conn = get_db()
    conn.execute(
        """INSERT INTO messages (session_id, role, content, thinking, tool_calls, tool_name, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (session_id, role, content, thinking,
         json.dumps(tool_calls) if tool_calls else None,
         tool_name, now),
    )
    conn.execute("UPDATE sessions SET updated_at = ? WHERE id = ?", (now, session_id))
    conn.commit()
    conn.close()


def get_session_prompt(session_id: str) -> Optional[str]:
    conn = get_db()
    row = conn.execute("SELECT system_prompt FROM sessions WHERE id = ?", (session_id,)).fetchone()
    conn.close()
    return row[0] if row and row[0] else None


def set_session_prompt(session_id: str, prompt: Optional[str]):
    conn = get_db()
    cursor = conn.execute(
        "UPDATE sessions SET system_prompt = ? WHERE id = ?",
        (prompt if prompt and prompt.strip() else None, session_id),
    )
    conn.commit()
    conn.close()
    return cursor.rowcount > 0


def read_memory_for_session(session_id: str) -> list:
    conn = get_db()
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT key, value FROM memory WHERE session_id = ? ORDER BY id ASC",
        (session_id,),
    ).fetchall()
    conn.close()
    return [{"key": r["key"], "value": r["value"]} for r in rows]


def save_memory_for_session(session_id: str, key: str, value: str):
    now = datetime.now().isoformat()
    conn = get_db()
    conn.execute(
        """INSERT INTO memory (session_id, key, value, created_at)
           VALUES (?, ?, ?, ?)
           ON CONFLICT(session_id, key) DO UPDATE SET value = excluded.value, created_at = excluded.created_at""",
        (session_id, key, value, now),
    )
    conn.commit()
    conn.close()


def auto_title(session_id: str, user_msg: str):
    title = user_msg[:50].strip()
    if len(user_msg) > 50:
        title += "..."
    conn = get_db()
    count = conn.execute(
        "SELECT COUNT(*) FROM messages WHERE session_id = ?", (session_id,)
    ).fetchone()[0]
    if count == 0:
        conn.execute("UPDATE sessions SET title = ? WHERE id = ?", (title, session_id))
        conn.commit()
    conn.close()
    return title if count == 0 else None


def normalize_title(title: str, default: str = "Новый чат") -> str:
    cleaned = (title or "").strip()
    return cleaned[:200] if cleaned else default


def session_exists(sid: str) -> bool:
    conn = get_db()
    row = conn.execute("SELECT 1 FROM sessions WHERE id = ?", (sid,)).fetchone()
    conn.close()
    return row is not None


async def cleanup_background_jobs() -> None:
    cutoff = datetime.now().timestamp() - BACKGROUND_JOB_TTL_SECONDS
    async with background_jobs_lock:
        stale_ids = [
            task_id
            for task_id, job in background_jobs.items()
            if job.get("finished_at_ts") and job["finished_at_ts"] < cutoff
        ]
        for task_id in stale_ids:
            background_jobs.pop(task_id, None)


# ─── MCP ────────────────────────────────────────────────────────

MCP_PYTHON = os.getenv("MCP_PYTHON", sys.executable)


class MCPSessionManager:
    """Менеджер единственной MCP-сессии. Не привязан к session_id чата."""
    def __init__(self):
        self._session = None
        self._cm_stdio = None
        self._cm_client = None
        self._lock = asyncio.Lock()
        self._connected = False

    async def _create_session(self):
        """Создаёт новую MCP-сессию. Вызывать ТОЛЬКО под self._lock."""
        await self._close_internal()

        env = {**os.environ, "PYTHONUNBUFFERED": "1"}
        params = StdioServerParameters(
            command=MCP_PYTHON,
            args=["-u", MCP_SERVER_SCRIPT],  # -u для unbuffered stdout
            cwd=str(Path(__file__).parent),
            env=env,
        )

        # Таймаут на создание MCP сессии (30 секунд)
        try:
            self._cm_stdio = stdio_client(params)
            read, write = await asyncio.wait_for(self._cm_stdio.__aenter__(), timeout=30.0)

            self._cm_client = ClientSession(read, write)
            self._session = await asyncio.wait_for(self._cm_client.__aenter__(), timeout=30.0)
            await asyncio.wait_for(self._session.initialize(), timeout=30.0)
            self._connected = True
        except asyncio.TimeoutError:
            logger.error("MCP session creation timeout (30s)")
            await self._close_internal()
            raise TimeoutError("Не удалось создать MCP сессию: таймаут 30 секунд")

    async def _close_internal(self):
        """Закрывает ресурсы. Вызывать ТОЛЬКО под self._lock."""
        if self._cm_client:
            try:
                await self._cm_client.__aexit__(None, None, None)
            except Exception:
                pass
            self._cm_client = None
        if self._cm_stdio:
            try:
                await self._cm_stdio.__aexit__(None, None, None)
            except Exception:
                pass
            self._cm_stdio = None
        self._session = None
        self._connected = False

    async def _ensure_session(self):
        """Гарантирует наличие живой сессии. Вызывать ТОЛЬКО под self._lock."""
        if self._session and self._connected:
            return self._session
        await self._create_session()
        return self._session

    async def call_tool(self, name: str, arguments: dict, timeout: float = 180.0):
        async with self._lock:
            try:
                logger.info(f"[MCP] Calling tool: {name} with args: {arguments}")
                session = await self._ensure_session()
                logger.info(f"[MCP] Session ready, calling tool...")
                # Добавляем таймаут на вызов MCP инструмента
                result = await asyncio.wait_for(
                    session.call_tool(name, arguments=arguments),
                    timeout=timeout
                )
                logger.info(f"[MCP] Tool {name} returned: {result}")
                return result
            except asyncio.TimeoutError:
                # Таймаут MCP вызова - пересоздаём сессию
                logger.error(f"MCP tool {name} timeout after {timeout}s")
                self._connected = False
                self._session = None
                raise TimeoutError(f"MCP tool {name} превысил таймаут {timeout} секунд")
            except asyncio.CancelledError:
                # CancelledError — НЕ маркируем сессию как сломанную
                raise
            except Exception as e:
                logger.error(f"MCP tool {name} error: {e}", exc_info=True)
                # Реальная ошибка MCP — пересоздадим сессию при следующем вызове
                self._connected = False
                self._session = None
                raise

    async def list_tools(self):
        async with self._lock:
            session = await self._ensure_session()
            # Таймаут на получение списка инструментов
            tools_list = await asyncio.wait_for(session.list_tools(), timeout=10.0)
            return [
                {
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description,
                        "parameters": t.inputSchema,
                    },
                }
                for t in tools_list.tools
            ]

    async def close(self):
        async with self._lock:
            await self._close_internal()


mcp_manager = MCPSessionManager()


async def run_mcp_tool(tool_name: str, arguments: dict, session_id: str = "", ws=None, original_tool_id: str = None) -> str:
    # Маппинг имён инструментов Claude/Qwen CLI → MCP
    # Расширенный маппинг для всех поддерживаемых инструментов
    TOOL_NAME_MAPPING = {
        # Claude CLI инструменты
        "Write": "write_file",
        "Bash": "run_bash_command",
        "Edit": "edit_file",
        "Read": "read_file",
        "SSH": "run_ssh_command",
        "Agent": "__unsupported__",
        "Task": "__unsupported__",
        "Subagent": "__unsupported__",
        "subagent": "__unsupported__",
        # Qwen CLI инструменты
        "run_shell_command": "run_bash_command",
        "execute_command": "run_bash_command",
        "run_command": "run_bash_command",
        "shell": "run_bash_command",
        # Memory инструменты
        "save_memory": "save_memory",
        "read_memory": "read_memory",
        "delete_memory": "delete_memory",
        # Todo инструменты
        "todo_write": "todo_write",
        "todo_read": "todo_read",
        # Файловые инструменты
        "list_directory": "list_directory",
        "glob": "glob",
        "grep_search": "grep_search",
        # Веб инструменты
        "web_fetch": "web_fetch",
        "fetch_webpage": "web_fetch",
        "web_search": "web_search",
        "search_web": "web_search",
    }

    # Инструменты которые не поддерживаются через MCP
    UNSUPPORTED_TOOLS = {"__unsupported__"}

    # Маппинг параметров Claude CLI → MCP
    PARAM_MAPPING = {
        "Write": {"file_path": "path"},
        "Read": {"file_path": "path"},
        "Edit": {"file_path": "path"},
    }

    # Проверяем флаг run_in_background и сохраняем его
    run_in_background = arguments.pop("run_in_background", False) if isinstance(arguments, dict) else False

    # Преобразуем имя инструмента если нужно
    mcp_tool_name = TOOL_NAME_MAPPING.get(tool_name, tool_name)

    # Проверяем поддерживается ли инструмент
    if mcp_tool_name in UNSUPPORTED_TOOLS:
        return f"[ОШИБКА] Инструмент '{tool_name}' не поддерживается в веб-интерфейсе. Суб-агенты и Task-система требуют прямого доступа к CLI."

    # Преобразуем параметры если нужно
    if tool_name in PARAM_MAPPING:
        param_map = PARAM_MAPPING[tool_name]
        transformed_args = {}
        for key, value in arguments.items():
            new_key = param_map.get(key, key)
            transformed_args[new_key] = value
        arguments = transformed_args

    # Для memory и todo инструментов передаём session_id как аргумент
    if mcp_tool_name in ("save_memory", "read_memory", "delete_memory", "todo_write", "todo_read") and session_id:
        arguments = {**arguments, "session_id": session_id}

    # Если фоновое выполнение - запускаем асинхронно
    if run_in_background and mcp_tool_name in ("run_bash_command", "run_ssh_command"):
        task_id = str(uuid.uuid4())[:8]

        async def background_runner():
            try:
                result = await mcp_manager.call_tool(mcp_tool_name, arguments, timeout=180.0)
                result_text = getattr(result, "content", [{"text": str(result)}])[0].text if getattr(result, "content", None) else "(пустой результат)"

                await cleanup_background_jobs()
                async with background_jobs_lock:
                    background_jobs[task_id] = {
                        "status": "completed",
                        "result": result_text,
                        "session_id": session_id,
                        "finished_at_ts": datetime.now().timestamp(),
                    }

                # Отправляем уведомление через WebSocket если доступен
                if ws:
                    try:
                        await ws.send_json({
                            "type": "background_task_completed",
                            "task_id": task_id,
                            "result": result_text[:1000]
                        })
                    except Exception:
                        pass

            except Exception as e:
                error_msg = f"Error: {str(e)}"
                await cleanup_background_jobs()
                async with background_jobs_lock:
                    background_jobs[task_id] = {
                        "status": "failed",
                        "result": error_msg,
                        "session_id": session_id,
                        "finished_at_ts": datetime.now().timestamp(),
                    }

                if ws:
                    try:
                        await ws.send_json({
                            "type": "background_task_failed",
                            "task_id": task_id,
                            "error": error_msg
                        })
                    except Exception:
                        pass

        # Сохраняем задачу как running
        await cleanup_background_jobs()
        async with background_jobs_lock:
            background_jobs[task_id] = {
                "status": "running",
                "result": None,
                "session_id": session_id,
                "finished_at_ts": None,
            }

        # Запускаем в фоне
        asyncio.create_task(background_runner())

        return f"Команда запущена в фоне (task_id: {task_id}). Вы получите уведомление по завершении."

    # Обычное синхронное выполнение
    try:
        result = await mcp_manager.call_tool(mcp_tool_name, arguments, timeout=180.0)
        return getattr(result, "content", [{"text": str(result)}])[0].text if getattr(result, "content", None) else "(пустой результат)"
    except TimeoutError as e:
        return f"Error: {str(e)}"
    except Exception as e:
        logger.error(f"MCP tool {mcp_tool_name} error: {e}", exc_info=True)
        return f"Error: {str(e)}"


# Кэш для списка инструментов (не меняется в runtime)
_tools_cache: list = []

async def get_mcp_tools() -> list:
    global _tools_cache
    if _tools_cache:
        return _tools_cache
    _tools_cache = await mcp_manager.list_tools()
    return _tools_cache


# ─── CLI Provider Abstraction ───────────────────────────────────

class CLIProvider:
    """Base class for AI CLI providers."""

    def get_command(self, session_id: str = None, resume_id: str = None) -> list:
        """Returns command array for subprocess.Popen."""
        raise NotImplementedError

    def get_cli_path(self) -> str:
        """Returns path to CLI executable."""
        raise NotImplementedError

    def validate_model(self, model: Optional[str]) -> bool:
        """Validates model name for this provider."""
        raise NotImplementedError

    def get_provider_name(self) -> str:
        """Returns provider name for logging."""
        raise NotImplementedError


def resolve_cli_path(env_var: str, binary_name: str, fallback_paths: list[str]) -> str:
    """Resolve CLI path from env, PATH, or known local installs."""
    configured_path = os.getenv(env_var)
    candidates = [configured_path] if configured_path else []
    path_candidate = shutil.which(binary_name)
    if path_candidate:
        candidates.append(path_candidate)
    candidates.extend(fallback_paths)

    for candidate in candidates:
        if candidate and Path(candidate).exists() and os.access(candidate, os.X_OK):
            return candidate

    search_targets = ", ".join(filter(None, [configured_path, binary_name, *fallback_paths]))
    raise FileNotFoundError(
        f"Не найден исполняемый файл {binary_name}. "
        f"Проверьте переменную {env_var} или установку CLI. Проверенные пути: {search_targets}"
    )


class QwenCLIProvider(CLIProvider):
    def get_cli_path(self) -> str:
        return resolve_cli_path(
            "QWEN_PATH",
            "qwen",
            [
                "/home/andrew/.nvm/versions/node/v22.12.0/bin/qwen",
                "/usr/local/bin/qwen",
                "/usr/bin/qwen",
            ],
        )

    def get_command(self, session_id: str = None, resume_id: str = None) -> list:
        cmd = [
            self.get_cli_path(),
            "--input-format", "stream-json",
            "--output-format", "stream-json",
            "--approval-mode", "default",
        ]

        # NOTE: MCP config for Qwen временно отключен — нужно проверить формат
        # mcp_config_path = Path(__file__).parent / "mcp_config.json"
        # if mcp_config_path.exists():
        #     cmd.extend(["--mcp-config", str(mcp_config_path)])

        if resume_id:
            cmd.extend(["--resume", resume_id])
        elif session_id:
            cmd.extend(["--session-id", session_id])
        return cmd

    def validate_model(self, model: Optional[str]) -> bool:
        return True  # Qwen doesn't use model parameter

    def get_provider_name(self) -> str:
        return "qwen"


class ClaudeCLIProvider(CLIProvider):
    def __init__(self, model: str = "sonnet"):
        self.model = model

    def get_cli_path(self) -> str:
        return resolve_cli_path(
            "CLAUDE_PATH",
            "claude",
            [
                "/home/andrew/.npm-global/bin/claude",
                "/usr/local/bin/claude",
                "/usr/bin/claude",
            ],
        )

    def get_command(self, session_id: str = None, resume_id: str = None) -> list:
        cmd = [
            self.get_cli_path(),
            "--print",
            "--input-format", "stream-json",
            "--output-format", "stream-json",
            "--verbose",
            "--permission-mode", "bypassPermissions",
            "--strict-mcp-config",  # Игнорировать глобальную MCP конфигурацию
            "--model", self.model,
        ]
        if resume_id:
            cmd.extend(["--resume", resume_id])
        elif session_id:
            cmd.extend(["--session-id", session_id])
        return cmd

    def validate_model(self, model: Optional[str]) -> bool:
        return model in ["opus", "sonnet", "haiku"]

    def get_provider_name(self) -> str:
        return f"claude-{self.model}"


# ─── CLI SDK mode ───────────────────────────────────────────────

def run_cli_sdk(provider: CLIProvider, session_id: str = None, resume_id: str = None):
    """
    Spawns AI CLI in SDK mode using the provided provider.
    Supports both qwen and claude with identical protocol.
    """
    cmd = provider.get_command(session_id=session_id, resume_id=resume_id)
    logger.info(f"Starting CLI: {' '.join(cmd)}")

    # Prepare environment - remove CLAUDECODE to allow nested Claude sessions
    env = os.environ.copy()
    if 'CLAUDECODE' in env:
        del env['CLAUDECODE']
        logger.info("Removed CLAUDECODE env var to allow nested Claude CLI")

    return subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        stdin=subprocess.PIPE,
        text=True,
        bufsize=1,
        preexec_fn=os.setsid,
        env=env,
    )


def run_qwen_cli_sdk(session_id: str = None, resume_id: str = None):
    """
    Legacy wrapper for backward compatibility.
    Запускает qwen cli в SDK mode.
    """
    provider = QwenCLIProvider()
    return run_cli_sdk(provider, session_id=session_id, resume_id=resume_id)


# ─── Утилиты ────────────────────────────────────────────────────


def _auto_save_digest(session_id: str, last_user_msg: str):
    try:
        DIGEST_KEY = "_auto_conversation_topics"
        MAX_TOPICS = 20
        mem_entries = read_memory_for_session(session_id)
        existing = ""
        for e in mem_entries:
            if e["key"] == DIGEST_KEY:
                existing = e["value"]
                break
        topics = [t.strip() for t in existing.split("|") if t.strip()] if existing else []
        new_topic = last_user_msg[:100].strip()
        if new_topic:
            topics.append(new_topic)
        if len(topics) > MAX_TOPICS:
            topics = topics[-MAX_TOPICS:]
        save_memory_for_session(session_id, DIGEST_KEY, " | ".join(topics))
    except Exception:
        pass


async def _safe_send(ws: WebSocket, data: dict):
    aliases = []
    message_type = data.get("type")

    if message_type == "content" and data.get("content"):
        aliases.append({
            "type": "assistant.token",
            "token": data.get("content"),
            "content": data.get("content"),
        })
    elif message_type == "tool_call":
        aliases.append({
            "type": "tool.call",
            "name": data.get("name"),
            "args": data.get("args", {}),
        })
    elif message_type == "tool_result":
        aliases.append({
            "type": "tool.result",
            "name": data.get("name"),
            "content": data.get("content"),
        })
    elif message_type == "response_end":
        aliases.append({"type": "assistant.done"})
    elif message_type == "stopped":
        aliases.append({"type": "assistant.stopped"})

    try:
        await ws.send_json(data)
        for alias in aliases:
            await ws.send_json(alias)
    except Exception:
        pass


async def _wait_for_confirmation(
    confirm_queue: asyncio.Queue,
    stop_event: asyncio.Event,
    timeout: float = 300,
) -> str:
    """Ждёт подтверждения от пользователя через confirm_queue.
    Возвращает 'stop' если:
    - stop_event установлен
    - истёк таймаут
    - получено None (соединение закрыто)
    """
    if stop_event.is_set():
        return "stop"

    # Создаём задачи для ожидания
    queue_task = asyncio.create_task(confirm_queue.get())
    stop_task = asyncio.create_task(stop_event.wait())

    try:
        done, pending = await asyncio.wait(
            {queue_task, stop_task},
            timeout=timeout,
            return_when=asyncio.FIRST_COMPLETED,
        )
    except asyncio.CancelledError:
        queue_task.cancel()
        stop_task.cancel()
        return "stop"
    finally:
        # Гарантированно отменяем все pending задачи
        for p in pending:
            p.cancel()

    # Проверяем что произошло
    if stop_task in done or stop_event.is_set():
        return "stop"

    if queue_task in done:
        data = queue_task.result()
        # None означает что соединение закрыто
        if data is None:
            return "stop"
        # Возвращаем данные как есть - для question это dict, для confirm это строка или dict с action
        return data

    # Таймаут - возвращаем stop чтобы не ждать вечно
    return "stop"


async def _wait_for_init_response(proc, timeout=30):
    """Ждёт control_response на initialize от qwen."""
    import time
    start = time.time()
    while time.time() - start < timeout:
        try:
            line = await asyncio.wait_for(_async_readline(proc), timeout=5)
        except asyncio.TimeoutError:
            continue
        if not line:
            continue
        try:
            data = json.loads(line.strip())
            if data.get("type") == "control_response":
                return data
        except Exception:
            continue
    logger.warning(f"Таймаут ожидания init response от qwen ({timeout}s)")
    return None


def build_history(messages: list, session_id: str = "", custom_prompt: str = None, provider: str = "qwen") -> list:
    effective_prompt = custom_prompt or get_system_prompt(provider)
    system_msg = {"role": "system", "content": effective_prompt}

    all_msgs = []
    for m in messages:
        if m["role"] in ("user", "assistant"):
            all_msgs.append({"role": m["role"], "content": m["content"]})
        elif m["role"] == "tool":
            all_msgs.append({"role": "tool", "content": m["content"]})
        elif m["role"] == "assistant_tool_call":
            tc = json.loads(m["tool_calls"]) if m["tool_calls"] else []
            entry = {"role": "assistant", "content": m["content"] or ""}
            if tc:
                entry["tool_calls"] = tc
            all_msgs.append(entry)

    memory_injection = ""
    if session_id:
        mem_entries = read_memory_for_session(session_id)
        user_entries = [e for e in mem_entries if not e["key"].startswith("_auto_")]
        if user_entries:
            mem_lines = [f"  • {e['key']}: {e['value']}" for e in user_entries]
            memory_injection = "Сохранённые факты (долгосрочная память):\n" + "\n".join(mem_lines)

    history = [system_msg]
    if memory_injection:
        history.append({"role": "system", "content": memory_injection})
    history += all_msgs
    return history


async def _async_readline(proc) -> str:
    """Читает строку из stdout процесса без блокировки event loop.
    Корректно обрабатывает таймауты — не оставляет заблокированные threads."""
    loop = asyncio.get_event_loop()
    
    def _readline_with_poll():
        """Читает строку, но сначала проверяет что данные доступны."""
        fd = proc.stdout.fileno()
        # Ждём данные до 1 секунды за раз
        ready, _, _ = select.select([fd], [], [], 1.0)
        if ready:
            return proc.stdout.readline()
        return ""  # Нет данных — вернём пустую строку, вызывающий повторит
    
    return await loop.run_in_executor(None, _readline_with_poll)


def _read_stderr_tail(proc, max_bytes: int = 4000) -> str:
    if proc is None or proc.stderr is None:
        return ""
    try:
        fd = proc.stderr.fileno()
        ready, _, _ = select.select([fd], [], [], 0)
        if not ready:
            return ""
        chunk = os.read(fd, max_bytes)
        return chunk.decode(errors="ignore").strip()
    except Exception:
        return ""


def _kill_proc(proc):
    """Безопасно завершает процесс через process group."""
    if proc is None:
        return

    try:
        proc.stdin.close()
    except Exception:
        pass

    try:
        # Проверяем что процесс ещё жив
        if proc.poll() is None:
            try:
                # Пытаемся убить через process group
                os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
                proc.wait(timeout=5)
            except ProcessLookupError:
                # Процесс уже завершён
                pass
            except Exception as e:
                logger.warning(f"SIGTERM failed: {e}, trying SIGKILL")
                try:
                    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                    proc.wait(timeout=2)
                except Exception as e2:
                    logger.error(f"SIGKILL failed: {e2}")
    except Exception as e:
        logger.error(f"Failed to kill process: {e}")


async def stream_chat_background(
    session_id: str,
    user_message: str,
    connection_state: dict,
    stop_event: asyncio.Event,
    confirm_queue: asyncio.Queue,
    ws: WebSocket,
):
    """
    Обрабатывает запрос пользователя.
    qwen-code работает в --approval-mode default.
    Для опасных инструментов (bash/ssh/write/edit) показываем confirm_request.
    """
    logger.info(f"Начало обработки: {session_id}, сообщение: {user_message[:50]}...")

    new_title = auto_title(session_id, user_message)
    if new_title:
        await _safe_send(ws, {"type": "session_renamed", "id": session_id, "title": new_title})

    save_message(session_id, "user", user_message)
    _auto_save_digest(session_id, user_message)

    await _safe_send(ws, {"type": "response_start"})
    await _safe_send(ws, {"type": "stream_start"})

    # Load provider and model from session FIRST
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT provider, model FROM sessions WHERE id = ?",
        (session_id,)
    )
    row = cursor.fetchone()
    conn.close()

    provider_name = row[0] if row and row[0] else 'qwen'
    model = row[1] if row and row[1] else None

    db_messages = get_messages(session_id)
    custom_prompt = get_session_prompt(session_id)
    history = build_history(db_messages, session_id=session_id, custom_prompt=custom_prompt, provider=provider_name)

    thinking_buffer = ""
    content_buffer = ""
    tool_calls_log = []
    tool_results_log = []  # Копим tool результаты для сохранения в правильном порядке
    pending_tool_calls = {}  # tool_use_id -> tool_info
    last_tool_call_time = None  # Время последнего вызова BASH инструмента
    TOOL_EXECUTION_TIMEOUT = 180.0  # Максимальное время выполнения BASH команды (секунды)

    # Инструменты, для которых применяется таймаут (только bash/shell команды)
    BASH_TOOLS = {"run_shell_command", "run_bash_command", "execute_command", "run_command", "shell", "bash"}

    proc = None
    try:

        # Create provider instance
        if provider_name == 'claude':
            if not model:
                model = 'sonnet'  # Default to sonnet
            provider = ClaudeCLIProvider(model=model)
            logger.info(f"Using Claude CLI with model: {model}")
        else:
            provider = QwenCLIProvider()
            logger.info(f"Using Qwen CLI")

        # Если в БД есть предыдущие сообщения (не только текущее) — resume
        has_history = len(db_messages) > 1

        # Проверяем что session_id — полный UUID (36 символов)
        is_valid_uuid = len(session_id) == 36 and session_id.count('-') == 4

        # Если есть кастомный промпт — НЕ используем session management
        use_full_history = custom_prompt is not None

        # SDK mode: инициализация и отправка через stdin
        if use_full_history:
            # Кастомный промпт: запускаем БЕЗ --session-id/--resume
            # Управляем историей сами через SQLite
            proc = run_cli_sdk(provider)
        elif has_history and is_valid_uuid:
            # Нет кастомного промпта + есть история: используем --resume
            proc = run_cli_sdk(provider, resume_id=session_id)
        elif is_valid_uuid:
            # Нет кастомного промпта + первое сообщение: создаём сессию
            proc = run_cli_sdk(provider, session_id=session_id)
        else:
            # Старая сессия с коротким ID — без контекста
            proc = run_cli_sdk(provider)

        # 1. Инициализируем SDK mode
        init_request = {"subtype": "initialize"}

        # Если есть кастомный промпт, пробуем передать его в инициализации
        if use_full_history and custom_prompt:
            init_request["system_prompt"] = custom_prompt

        init_msg = json.dumps({
            "type": "control_request",
            "request_id": "init-001",
            "request": init_request
        })
        proc.stdin.write(init_msg + "\n")
        proc.stdin.flush()

        # Ждём control_response для initialize
        init_resp = await _wait_for_init_response(proc)

        # Fallback: если процесс умер (например --resume на несуществующей сессии)
        if init_resp is None and proc.poll() is not None:
            logger.warning(f"{provider.get_provider_name()} процесс завершился, пробуем без --resume (session_id={session_id})")
            stderr_tail = _read_stderr_tail(proc)
            proc = run_cli_sdk(provider)
            init_msg_fallback = json.dumps({
                "type": "control_request",
                "request_id": "init-002",
                "request": {"subtype": "initialize"}
            })
            proc.stdin.write(init_msg_fallback + "\n")
            proc.stdin.flush()
            init_resp = await _wait_for_init_response(proc)
            use_full_history = True  # После fallback отправляем полную историю
            if init_resp is None:
                fallback_stderr = _read_stderr_tail(proc)
                stderr_tail = fallback_stderr or stderr_tail
                details = f" STDERR: {stderr_tail[:1000]}" if stderr_tail else ""
                raise RuntimeError(f"{provider.get_provider_name()} не ответил на initialize после fallback.{details}")
        elif init_resp is None:
            stderr_tail = _read_stderr_tail(proc)
            details = f" STDERR: {stderr_tail[:1000]}" if stderr_tail else ""
            raise RuntimeError(f"{provider.get_provider_name()} не ответил на initialize.{details}")

        # 2. Отправляем полную историю если нужно (кастомный промпт или fallback)
        if use_full_history:
            # Если system_prompt не был принят в init, отправляем как первое сообщение
            effective_prompt = custom_prompt if custom_prompt else get_system_prompt(provider_name)

            # Пробуем отправить как user message с system content
            system_as_user = json.dumps({
                "type": "user",
                "message": {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": f"[SYSTEM INSTRUCTION]\n{effective_prompt}\n[END SYSTEM INSTRUCTION]"
                        }
                    ]
                }
            })
            proc.stdin.write(system_as_user + "\n")
            proc.stdin.flush()

            # Отправляем фейковый assistant ack
            ack_msg = json.dumps({
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "Understood. I will follow these instructions."}]
                }
            })
            proc.stdin.write(ack_msg + "\n")
            proc.stdin.flush()
            logger.info(f"Sent system prompt as conversation for session {session_id}")

            # Отправляем все предыдущие сообщения (кроме последнего user message)
            for msg in db_messages[:-1]:  # Исключаем последнее (текущее) сообщение
                if msg["role"] == "user":
                    msg_data = json.dumps({
                        "type": "user",
                        "message": {"role": "user", "content": msg["content"]}
                    })
                    proc.stdin.write(msg_data + "\n")
                    proc.stdin.flush()
                elif msg["role"] == "assistant":
                    msg_data = json.dumps({
                        "type": "assistant",
                        "message": {"role": "assistant", "content": [{"type": "text", "text": msg["content"]}]}
                    })
                    proc.stdin.write(msg_data + "\n")
                    proc.stdin.flush()
                elif msg["role"] == "assistant_tool_call":
                    tool_calls = json.loads(msg["tool_calls"]) if msg["tool_calls"] else []
                    content_parts = []
                    if msg["content"]:
                        content_parts.append({"type": "text", "text": msg["content"]})
                    for idx, tc in enumerate(tool_calls):
                        func = tc.get("function", {})
                        # Используем реальный ID если есть, иначе генерируем UUID
                        tool_id = tc.get("id") or f"tool_{uuid.uuid4().hex[:8]}_{idx}"
                        content_parts.append({
                            "type": "tool_use",
                            "id": tool_id,
                            "name": func.get("name", ""),
                            "input": func.get("arguments", {})
                        })
                    msg_data = json.dumps({
                        "type": "assistant",
                        "message": {"role": "assistant", "content": content_parts}
                    })
                    proc.stdin.write(msg_data + "\n")
                    proc.stdin.flush()
                elif msg["role"] == "tool":
                    # Tool results нужно отправлять как user message с tool_result
                    # Ищем соответствующий tool_use_id из предыдущего assistant_tool_call сообщения
                    tool_use_id = msg.get("tool_use_id") or f"tool_{uuid.uuid4().hex[:8]}"
                    msg_data = json.dumps({
                        "type": "user",
                        "message": {
                            "role": "user",
                            "content": [{
                                "type": "tool_result",
                                "tool_use_id": tool_use_id,
                                "content": msg["content"]
                            }]
                        }
                    })
                    proc.stdin.write(msg_data + "\n")
                    proc.stdin.flush()

        # 3. Отправляем текущее сообщение пользователя
        user_msg = json.dumps({
            "type": "user",
            "message": {"role": "user", "content": user_message}
        })
        proc.stdin.write(user_msg + "\n")
        proc.stdin.flush()

        # 3. Читаем поток
        done = False
        while not done:
            # Проверяем таймаут выполнения BASH инструмента
            if last_tool_call_time is not None:
                elapsed = asyncio.get_event_loop().time() - last_tool_call_time
                if elapsed > TOOL_EXECUTION_TIMEOUT:
                    logger.error(f"Bash command timeout ({TOOL_EXECUTION_TIMEOUT}s exceeded) for session {session_id}")

                    # СНАЧАЛА отправляем tool_result для всех pending инструментов
                    timeout_message = f"⏱️ Таймаут выполнения ({TOOL_EXECUTION_TIMEOUT:.0f} секунд). Операция прервана."
                    for tool_id, tool_info in list(pending_tool_calls.items()):
                        tool_name = tool_info.get("name", "")
                        await _safe_send(ws, {
                            "type": "tool_result",
                            "name": tool_name,
                            "content": timeout_message
                        })
                        # Добавляем в лог результатов для сохранения
                        tool_results_log.append({
                            "content": timeout_message,
                            "tool_name": tool_name
                        })
                    pending_tool_calls.clear()

                    # Затем отправляем сообщения о завершении
                    await _safe_send(ws, {
                        "type": "error",
                        "content": timeout_message
                    })
                    await _safe_send(ws, {"type": "stream_end"})
                    await _safe_send(ws, {"type": "response_end"})

                    # ПОТОМ убиваем процесс
                    _kill_proc(proc)

                    # Сохраняем накопленный контент
                    if content_buffer or thinking_buffer or tool_calls_log:
                        if tool_calls_log:
                            save_message(session_id, "assistant_tool_call",
                                        content_buffer, thinking=thinking_buffer,
                                        tool_calls=tool_calls_log)
                            for tr in tool_results_log:
                                save_message(session_id, "tool", tr["content"], tool_name=tr["tool_name"])
                        else:
                            save_message(session_id, "assistant", content_buffer, thinking=thinking_buffer)

                    return

            if stop_event.is_set():
                _kill_proc(proc)
                # Сохраняем накопленный контент ДО отправки stopped
                if content_buffer or thinking_buffer or tool_calls_log:
                    if tool_calls_log:
                        save_message(session_id, "assistant_tool_call",
                                    content_buffer, thinking=thinking_buffer,
                                    tool_calls=tool_calls_log)
                        for tr in tool_results_log:
                            save_message(session_id, "tool", tr["content"], tool_name=tr["tool_name"])
                    else:
                        save_message(session_id, "assistant", content_buffer, thinking=thinking_buffer)
                    # Очищаем буферы чтобы не сохранить дважды
                    content_buffer = ""
                    thinking_buffer = ""
                    tool_calls_log = []
                    tool_results_log = []
                await _safe_send(ws, {"type": "stopped"})
                break

            # Проверяем завершение процесса
            if proc.poll() is not None:
                # Читаем остаток stdout с таймаутом
                try:
                    remaining = await asyncio.wait_for(
                        asyncio.get_event_loop().run_in_executor(None, proc.stdout.read),
                        timeout=5.0
                    )
                    if remaining:
                        for line in remaining.splitlines():
                            thinking_buffer, content_buffer, done, last_tool_call_time = await _process_line(
                                ws, line, proc, thinking_buffer, content_buffer, tool_calls_log,
                                pending_tool_calls, connection_state, confirm_queue,
                                stop_event, session_id, tool_results_log, last_tool_call_time, BASH_TOOLS
                            )
                            if done:
                                break
                except asyncio.TimeoutError:
                    logger.warning(f"Таймаут чтения остатка stdout (session_id={session_id})")
                break

            try:
                # Таймаут 300 секунд (5 минут) для чтения ответа от qwen
                line = await asyncio.wait_for(_async_readline(proc), timeout=300)
            except asyncio.TimeoutError:
                logger.error(f"Таймаут чтения от qwen процесса (session_id={session_id})")
                _kill_proc(proc)
                await _safe_send(ws, {"type": "error", "content": "Таймаут ожидания ответа (5 минут). Процесс остановлен."})
                await _safe_send(ws, {"type": "response_end"})
                return  # Полностью завершаем функцию

            if not line:
                if proc.poll() is not None:
                    break
                continue

            thinking_buffer, content_buffer, done, last_tool_call_time = await _process_line(
                ws, line, proc, thinking_buffer, content_buffer, tool_calls_log,
                pending_tool_calls, connection_state, confirm_queue,
                stop_event, session_id, tool_results_log, last_tool_call_time, BASH_TOOLS
            )
            if done:
                break
    except asyncio.CancelledError:
        # Задача отменена - корректно завершаем процесс и отправляем сигнал
        logger.info(f"Задача отменена для сессии {session_id}")
        _kill_proc(proc)
        # Сохраняем накопленный контент
        if content_buffer or thinking_buffer or tool_calls_log:
            if tool_calls_log:
                save_message(session_id, "assistant_tool_call",
                            content_buffer, thinking=thinking_buffer,
                            tool_calls=tool_calls_log)
                for tr in tool_results_log:
                    save_message(session_id, "tool", tr["content"], tool_name=tr["tool_name"])
            else:
                save_message(session_id, "assistant", content_buffer, thinking=thinking_buffer)
        try:
            await _safe_send(ws, {"type": "stopped"})
        except Exception:
            pass
        # Пробрасываем CancelledError дальше
        raise
    except Exception as e:
        logger.error(f"Ошибка в stream_chat_background (session_id={session_id}): {e}", exc_info=True)
        await _safe_send(ws, {"type": "error", "content": f"Внутренняя ошибка: {str(e)}"})
    finally:
        if proc:
            if proc.poll() is None:
                logger.info(f"Завершаем qwen процесс (session_id={session_id})")
                _kill_proc(proc)
            else:
                logger.debug(f"qwen процесс уже завершён (session_id={session_id}, exit_code={proc.poll()})")

    # Сохраняем в БД в правильном порядке: assistant_tool_call → tool × N
    if content_buffer or thinking_buffer or tool_calls_log:
        if tool_calls_log:
            # Сначала assistant_tool_call
            save_message(session_id, "assistant_tool_call",
                        content_buffer, thinking=thinking_buffer,
                        tool_calls=tool_calls_log)
            # Затем tool результаты
            for tr in tool_results_log:
                save_message(session_id, "tool", tr["content"], tool_name=tr["tool_name"])
        else:
            save_message(session_id, "assistant", content_buffer, thinking=thinking_buffer)

    await _safe_send(ws, {"type": "stream_end"})
    await _safe_send(ws, {"type": "response_end"})


async def _process_line(
    ws: WebSocket,
    line: str,
    proc,
    thinking_buffer: str,
    content_buffer: str,
    tool_calls_log: list,
    pending_tool_calls: dict,
    connection_state: dict,
    confirm_queue: asyncio.Queue,
    stop_event: asyncio.Event,
    session_id: str,
    tool_results_log: list,
    last_tool_call_time: float = None,
    BASH_TOOLS: set = None,
) -> tuple:
    """Обрабатывает одну строку вывода qwen в SDK mode.
    Возвращает (thinking_buffer, content_buffer, done, last_tool_call_time).
    """
    line = line.strip()
    if not line:
        return thinking_buffer, content_buffer, False, last_tool_call_time

    try:
        data = json.loads(line)
    except json.JSONDecodeError:
        return thinking_buffer, content_buffer, False, last_tool_call_time

    tp = data.get("type", "")

    # --- control_request: qwen просит подтверждения ---
    if tp == "control_request":
        req = data.get("request", {})
        rid = data.get("request_id", "")
        sub = req.get("subtype", "")

        logger.info(f"control_request: subtype={sub}, request_id={rid}")

        if sub == "can_use_tool":
            tool_name = req.get("tool_name", "")
            tool_input = req.get("input", {})

            logger.info(f"can_use_tool: tool_name={tool_name}")

            # Специальная обработка ask_user_question (Qwen и Claude)
            if tool_name in ("ask_user_question", "AskUserQuestion"):
                questions = tool_input.get("questions", [])

                # Форматируем вопросы в красивый текст
                formatted_text = ""
                for i, q in enumerate(questions, 1):
                    if i > 1:
                        formatted_text += "\n"

                    header = q.get("header", f"Question {i}")
                    question = q.get("question", "")
                    options = q.get("options", [])

                    formatted_text += f"**{header}**: {question}\n"

                    for j, opt in enumerate(options, 1):
                        label = opt.get("label", "")
                        description = opt.get("description", "")
                        formatted_text += f"{j}. **{label}** — {description}\n"

                # Отправляем вопросы как обычный content
                await _safe_send(ws, {
                    "type": "content",
                    "content": formatted_text
                })

                # Отправляем системное сообщение что сессия остановлена
                await _safe_send(ws, {
                    "type": "content",
                    "content": "\n\n📌 CLI остановлен для отображения вопросов. Отправьте ответ чтобы продолжить."
                })

                # Завершаем поток
                await _safe_send(ws, {"type": "stream_end"})
                await _safe_send(ws, {"type": "stopped"})

                # Убиваем процесс qwen ПОСЛЕ отправки всех сообщений
                _kill_proc(proc)

                # Возвращаем formatted_text в content_buffer и done=True
                return thinking_buffer, content_buffer + formatted_text, True, last_tool_call_time

            # Авто-одобрение если allow_all
            if connection_state.get("allow_all"):
                allow_resp = json.dumps({
                    "type": "control_response",
                    "response": {
                        "subtype": "success",
                        "request_id": rid,
                        "response": {"behavior": "allow"}
                    }
                })
                try:
                    proc.stdin.write(allow_resp + "\n")
                    proc.stdin.flush()
                except Exception:
                    pass
                return thinking_buffer, content_buffer, False, last_tool_call_time

            # Показываем confirm UI во фронте
            await _safe_send(ws, {
                "type": "confirm_request",
                "name": tool_name,
                "args": tool_input
            })

            # Ждём ответа от пользователя
            action = await _wait_for_confirmation(confirm_queue, stop_event)

            # Обрабатываем ответ (может быть строка или dict с action)
            if isinstance(action, dict):
                action = action.get("action", "deny")

            if action == "stop":
                stop_event.set()
                deny_resp = json.dumps({
                    "type": "control_response",
                    "response": {
                        "subtype": "success",
                        "request_id": rid,
                        "response": {"behavior": "deny", "message": "Остановлено"}
                    }
                })
                try:
                    proc.stdin.write(deny_resp + "\n")
                    proc.stdin.flush()
                except Exception:
                    pass
                _kill_proc(proc)
                return thinking_buffer, content_buffer, True, last_tool_call_time

            elif action == "deny":
                stop_event.set()
                deny_resp = json.dumps({
                    "type": "control_response",
                    "response": {
                        "subtype": "success",
                        "request_id": rid,
                        "response": {"behavior": "deny", "message": "Отклонено"}
                    }
                })
                try:
                    proc.stdin.write(deny_resp + "\n")
                    proc.stdin.flush()
                except Exception:
                    pass
                await _safe_send(ws, {"type": "tool_denied", "name": tool_name})
                _kill_proc(proc)
                return thinking_buffer, content_buffer, True, last_tool_call_time

            else:
                # allow или allow_all
                if action == "allow_all":
                    connection_state["allow_all"] = True
                    await _safe_send(ws, {"type": "allow_all_enabled"})

                allow_resp = json.dumps({
                    "type": "control_response",
                    "response": {
                        "subtype": "success",
                        "request_id": rid,
                        "response": {"behavior": "allow"}
                    }
                })
                try:
                    proc.stdin.write(allow_resp + "\n")
                    proc.stdin.flush()
                except Exception:
                    pass

        return thinking_buffer, content_buffer, False, last_tool_call_time

    # --- control_response: ответ от qwen (init и т.д.) ---
    if tp == "control_response":
        return thinking_buffer, content_buffer, False, last_tool_call_time

    # --- system: пропускаем ---
    if tp == "system":
        return thinking_buffer, content_buffer, False, last_tool_call_time

    # --- assistant: thinking, text, tool_use ---
    if tp == "assistant":
        content_list = data.get("message", {}).get("content", [])
        
        # Защита: content может быть строкой вместо списка
        if isinstance(content_list, str):
            if content_list:
                content_buffer += content_list
                await _safe_send(ws, {"type": "content", "content": content_list})
            return thinking_buffer, content_buffer, False, last_tool_call_time
        
        for item in content_list:
            it = item.get("type", "")

            if it == "thinking":
                t = item.get("thinking", "")
                if t:
                    thinking_buffer += t
                    await _safe_send(ws, {"type": "thinking", "content": t})

            elif it == "text":
                c = item.get("text", "")
                if c:
                    content_buffer += c
                    await _safe_send(ws, {"type": "content", "content": c})

            elif it == "tool_use":
                tool_name = item.get("name", "")
                tool_args = item.get("input", {})
                tool_id = item.get("id", "")

                # Специальная обработка ask_user_question (Qwen и Claude)
                if tool_name in ("ask_user_question", "AskUserQuestion"):
                    questions = tool_args.get("questions", [])

                    # Форматируем вопросы в красивый текст
                    formatted_text = ""
                    for i, q in enumerate(questions, 1):
                        if i > 1:
                            formatted_text += "\n"

                        header = q.get("header", f"Question {i}")
                        question = q.get("question", "")
                        options = q.get("options", [])

                        formatted_text += f"**{header}**: {question}\n"

                        for j, opt in enumerate(options, 1):
                            label = opt.get("label", "")
                            description = opt.get("description", "")
                            formatted_text += f"{j}. **{label}** — {description}\n"

                    # Отправляем вопросы как обычный content
                    await _safe_send(ws, {
                        "type": "content",
                        "content": formatted_text
                    })

                    # Отправляем системное сообщение что сессия остановлена
                    await _safe_send(ws, {
                        "type": "content",
                        "content": "\n\n📌 CLI остановлен для отображения вопросов. Отправьте ответ чтобы продолжить."
                    })

                    # Завершаем поток
                    await _safe_send(ws, {"type": "stream_end"})
                    await _safe_send(ws, {"type": "stopped"})

                    # Убиваем процесс CLI ПОСЛЕ отправки всех сообщений
                    _kill_proc(proc)

                    # Возвращаем formatted_text в content_buffer и done=True
                    return thinking_buffer, content_buffer + formatted_text, True, last_tool_call_time

                # Запоминаем время вызова ТОЛЬКО для bash команд
                if tool_name in BASH_TOOLS:
                    last_tool_call_time = asyncio.get_event_loop().time()

                # Конвертируем в формат фронта
                tool_calls_log.append({
                    "id": tool_id,
                    "function": {
                        "name": tool_name,
                        "arguments": tool_args
                    }
                })
                pending_tool_calls[tool_id] = {"name": tool_name, "args": tool_args}

                await _safe_send(ws, {
                    "type": "tool_call",
                    "name": tool_name,
                    "args": tool_args
                })

                # Проверяем нужно ли подтверждение (для Claude CLI)
                if tool_name in TOOLS_REQUIRING_CONFIRMATION:
                    # Авто-одобрение если allow_all
                    if connection_state.get("allow_all"):
                        # Выполняем инструмент через MCP
                        try:
                            result = await run_mcp_tool(tool_name, tool_args, session_id, ws, original_tool_id=tool_id)
                        except Exception as e:
                            result = f"Error: {str(e)}"

                        # Отправляем результат обратно в Claude CLI
                        tool_result_msg = json.dumps({
                            "type": "user",
                            "message": {
                                "role": "user",
                                "content": [{
                                    "type": "tool_result",
                                    "tool_use_id": tool_id,
                                    "content": result
                                }]
                            }
                        })
                        try:
                            proc.stdin.write(tool_result_msg + "\n")
                            proc.stdin.flush()
                        except Exception:
                            pass

                        # Отправляем результат во фронт
                        await _safe_send(ws, {
                            "type": "tool_result",
                            "name": tool_name,
                            "content": result[:3000]
                        })

                        # Копим в лог
                        tool_results_log.append({"content": result, "tool_name": tool_name})

                        # Удаляем из pending
                        if tool_id in pending_tool_calls:
                            del pending_tool_calls[tool_id]

                        # Сбрасываем таймер
                        last_tool_call_time = None
                    else:
                        # Показываем confirm UI во фронте
                        await _safe_send(ws, {
                            "type": "confirm_request",
                            "name": tool_name,
                            "args": tool_args
                        })

                        # Ждём ответа от пользователя
                        action = await _wait_for_confirmation(confirm_queue, stop_event)

                        # Обрабатываем ответ
                        if isinstance(action, dict):
                            action = action.get("action", "deny")

                        if action == "stop":
                            stop_event.set()
                            # Отправляем error result в Claude
                            error_msg = json.dumps({
                                "type": "user",
                                "message": {
                                    "role": "user",
                                    "content": [{
                                        "type": "tool_result",
                                        "tool_use_id": tool_id,
                                        "content": "Остановлено пользователем",
                                        "is_error": True
                                    }]
                                }
                            })
                            try:
                                proc.stdin.write(error_msg + "\n")
                                proc.stdin.flush()
                            except Exception:
                                pass

                            if tool_id in pending_tool_calls:
                                del pending_tool_calls[tool_id]
                            last_tool_call_time = None
                            _kill_proc(proc)
                            return thinking_buffer, content_buffer, True, last_tool_call_time

                        elif action == "deny":
                            # Отправляем error result в Claude
                            error_msg = json.dumps({
                                "type": "user",
                                "message": {
                                    "role": "user",
                                    "content": [{
                                        "type": "tool_result",
                                        "tool_use_id": tool_id,
                                        "content": "Отклонено пользователем",
                                        "is_error": True
                                    }]
                                }
                            })
                            try:
                                proc.stdin.write(error_msg + "\n")
                                proc.stdin.flush()
                            except Exception:
                                pass

                            await _safe_send(ws, {"type": "tool_denied", "name": tool_name})

                            if tool_id in pending_tool_calls:
                                del pending_tool_calls[tool_id]
                            last_tool_call_time = None
                            _kill_proc(proc)
                            return thinking_buffer, content_buffer, True, last_tool_call_time

                        else:
                            # allow или allow_all
                            if action == "allow_all":
                                connection_state["allow_all"] = True
                                await _safe_send(ws, {"type": "allow_all_enabled"})

                            # Выполняем инструмент через MCP
                            try:
                                result = await run_mcp_tool(tool_name, tool_args, session_id, ws, original_tool_id=tool_id)
                            except Exception as e:
                                result = f"Error: {str(e)}"

                            # Отправляем результат обратно в Claude CLI
                            tool_result_msg = json.dumps({
                                "type": "user",
                                "message": {
                                    "role": "user",
                                    "content": [{
                                        "type": "tool_result",
                                        "tool_use_id": tool_id,
                                        "content": result
                                    }]
                                }
                            })
                            try:
                                proc.stdin.write(tool_result_msg + "\n")
                                proc.stdin.flush()
                            except Exception:
                                pass

                            # Отправляем результат во фронт
                            await _safe_send(ws, {
                                "type": "tool_result",
                                "name": tool_name,
                                "content": result[:3000]
                            })

                            # Копим в лог
                            tool_results_log.append({"content": result, "tool_name": tool_name})

                            # Удаляем из pending
                            if tool_id in pending_tool_calls:
                                del pending_tool_calls[tool_id]

                            # Сбрасываем таймер
                            last_tool_call_time = None

        return thinking_buffer, content_buffer, False, last_tool_call_time

    # --- user (tool_result): qwen исполнил инструмент ---
    if tp == "user":
        content_list = data.get("message", {}).get("content", [])
        for item in content_list:
            if item.get("type") == "tool_result":
                tool_use_id = item.get("tool_use_id", "")

                # Нормализуем content (может быть list или str)
                raw_content = item.get("content", "")
                if isinstance(raw_content, list):
                    parts = []
                    for part in raw_content:
                        if isinstance(part, dict) and part.get("type") == "text":
                            parts.append(part.get("text", ""))
                        elif isinstance(part, str):
                            parts.append(part)
                        else:
                            parts.append(str(part))
                    tool_content = "\n".join(parts)
                else:
                    tool_content = str(raw_content)

                # Матчим по tool_use_id → tool_name
                # Если tool_use_id НЕ в pending_tool_calls, значит мы уже обработали этот инструмент
                # через MCP и отправили результат во фронт. Пропускаем дубликат.
                if tool_use_id in pending_tool_calls:
                    result_name = pending_tool_calls[tool_use_id]["name"]
                    del pending_tool_calls[tool_use_id]

                    await _safe_send(ws, {
                        "type": "tool_result",
                        "name": result_name or tool_use_id,
                        "content": tool_content[:3000]
                    })

                    # Копим в лог (сохраним позже в правильном порядке)
                    tool_results_log.append({"content": tool_content, "tool_name": result_name})

                    # Сбрасываем таймер после получения результата
                    last_tool_call_time = None
                # else: это эхо от Claude CLI, пропускаем

        return thinking_buffer, content_buffer, False, last_tool_call_time

    # --- result: конец ---
    if tp == "result":
        return thinking_buffer, content_buffer, True, last_tool_call_time

    return thinking_buffer, content_buffer, False, last_tool_call_time


# ─── FastAPI ────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield
    # Shutdown: закрываем MCP сессию
    await mcp_manager.close()

app = FastAPI(title="Qwen Agent", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
# Порядок: сначала RequestSizeLimitMiddleware (последний add_middleware = первый в цепочке)
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(RequestSizeLimitMiddleware)


@app.exception_handler(RequestValidationError)
async def request_validation_exception_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(status_code=400, content={"detail": jsonable_encoder(exc.errors())})

# Раздача статики React build (JS, CSS, images)
_dist_dir = Path(__file__).parent / "static" / "dist"
if _dist_dir.exists():
    _assets_dir = _dist_dir / "assets"
    if _assets_dir.exists():
        app.mount("/assets", StaticFiles(directory=str(_assets_dir)), name="static-assets")


@app.get("/")
async def index():
    # Отдаём собранный React фронтенд из dist/
    html_path = Path(__file__).parent / "static" / "dist" / "index.html"
    if not html_path.exists():
        # Fallback на исходный index.html если dist нет
        html_path = Path(__file__).parent / "static" / "index.html"
    return HTMLResponse(html_path.read_text(encoding="utf-8"))


@app.get("/api/user")
async def api_user():
    return {"id": "anonymous", "login": "Anonymous", "oauth_enabled": False}


@app.get("/api/health")
async def health_check():
    """Health-check endpoint для мониторинга."""
    try:
        conn = get_db()
        conn.execute("SELECT 1")
        conn.close()
        db_ok = True
    except Exception as exc:
        logger.error(f"Health check database failure: {exc}", exc_info=True)
        db_ok = False
    payload = {
        "status": "ok" if db_ok else "error",
        "database": db_ok,
        "db": "ok" if db_ok else "down",
        "version": "1.0.0"
    }
    if db_ok:
        return payload
    return JSONResponse(status_code=500, content=payload)


@app.get("/api/sessions")
async def api_sessions():
    return get_sessions()


@app.post("/api/sessions", status_code=201)
async def api_create_session(payload: CreateSessionPayload):
    session = create_session(
        normalize_title(payload.title),
        provider=payload.provider,
        model=payload.model,
    )
    return session


@app.delete("/api/sessions/{sid}")
async def api_delete_session(sid: str):
    # Остановить активную задачу если есть
    async with session_tasks_lock:
        task_info = session_tasks.get(sid)
    if task_info:
        task_info["stop_event"].set()
        task = task_info["task"]
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=3)
        except (asyncio.TimeoutError, Exception):
            pass
    deleted = delete_session(sid)
    if not deleted:
        raise HTTPException(404, "Session not found")
    return Response(status_code=204)


@app.put("/api/sessions/{sid}")
async def api_rename_session(sid: str, payload: RenameSessionPayload):
    title = normalize_title(payload.title, default="")
    renamed = rename_session(sid, title)
    if not renamed:
        raise HTTPException(404, "Session not found")
    return {"ok": True, "id": sid, "sid": sid, "title": title}


@app.get("/api/sessions/{sid}/messages")
async def api_messages(sid: str, limit: int = 50, offset: int = 0):
    """Получить сообщения сессии с пагинацией."""
    if not session_exists(sid):
        raise HTTPException(404, "Session not found")
    conn = get_db()
    conn.row_factory = sqlite3.Row
    total = conn.execute(
        "SELECT COUNT(*) FROM messages WHERE session_id = ?", (sid,)
    ).fetchone()[0]
    rows = conn.execute(
        "SELECT * FROM messages WHERE session_id = ? ORDER BY id ASC LIMIT ? OFFSET ?",
        (sid, min(limit, 200), offset),
    ).fetchall()
    conn.close()
    return {"messages": [dict(r) for r in rows], "total": total, "limit": limit, "offset": offset}


@app.get("/api/default-prompt")
async def api_default_prompt():
    return {"default_prompt": SYSTEM_PROMPT}


@app.get("/api/sessions/{sid}/system-prompt")
async def api_get_system_prompt(sid: str):
    if not session_exists(sid):
        raise HTTPException(404, "Session not found")
    custom = get_session_prompt(sid)
    return {"system_prompt": custom, "default_prompt": SYSTEM_PROMPT}


@app.put("/api/sessions/{sid}/system-prompt")
async def api_set_system_prompt(sid: str, payload: SessionPromptPayload):
    prompt = payload.system_prompt
    updated = set_session_prompt(sid, prompt)
    if not updated:
        raise HTTPException(404, "Session not found")
    return {"ok": True, "system_prompt": prompt}


@app.patch("/api/sessions/{sid}/settings")
async def api_update_session_settings(sid: str, payload: SessionSettingsPayload):
    """Update session provider and model settings."""
    provider = payload.provider
    model = payload.model

    if provider not in ["qwen", "claude"]:
        raise HTTPException(400, "Invalid provider")

    conn = get_db()
    existing = conn.execute("SELECT 1 FROM sessions WHERE id = ?", (sid,)).fetchone()
    if not existing:
        conn.close()
        raise HTTPException(404, "Session not found")

    if provider == "claude":
        if model not in ["opus", "sonnet", "haiku"]:
            model = "sonnet"
    else:
        model = None

    conn.execute(
        "UPDATE sessions SET provider = ?, model = ?, updated_at = ? WHERE id = ?",
        (provider, model, datetime.now().isoformat(), sid)
    )
    conn.commit()
    conn.close()

    return {"ok": True, "provider": provider, "model": model}


@app.get("/api/sessions/{sid}/task-status")
async def api_task_status(sid: str):
    if not session_exists(sid):
        raise HTTPException(404, "Session not found")
    async with session_tasks_lock:
        task_info = session_tasks.get(sid)
    if task_info:
        return {"has_task": True, "done": task_info["task"].done(), "cancelled": task_info["task"].cancelled()}
    return {"has_task": False}


@app.get("/api/sessions/{sid}/export")
async def api_export_session(sid: str):
    """Экспорт сессии в markdown файл."""
    from fastapi.responses import Response
    from urllib.parse import quote

    # Получаем информацию о сессии
    conn = get_db()
    conn.row_factory = sqlite3.Row
    session = conn.execute("SELECT * FROM sessions WHERE id = ?", (sid,)).fetchone()
    if not session:
        conn.close()
        raise HTTPException(status_code=404, detail="Session not found")

    # Получаем все сообщения
    messages = conn.execute(
        "SELECT * FROM messages WHERE session_id = ? ORDER BY id ASC",
        (sid,)
    ).fetchall()
    conn.close()

    # Формируем markdown
    md_lines = []
    md_lines.append(f"# {session['title']}")
    md_lines.append(f"\n**Created:** {session['created_at']}")
    md_lines.append(f"\n**Session ID:** `{sid}`\n")
    md_lines.append("\n---\n")

    for msg in messages:
        role = msg['role']
        content = msg['content'] or ""
        thinking = msg['thinking'] or ""
        tool_calls = json.loads(msg['tool_calls']) if msg['tool_calls'] else None
        tool_name = msg['tool_name']

        if role == "user":
            md_lines.append("\n## 👤 User\n")
            md_lines.append(f"{content}\n")
            md_lines.append("\n---\n")

        elif role == "assistant":
            md_lines.append("\n## 🤖 Assistant\n")
            if thinking:
                md_lines.append("\n**Thinking:**\n")
                md_lines.append(f"```\n{thinking}\n```\n")
            if content:
                md_lines.append(f"\n{content}\n")
            md_lines.append("\n---\n")

        elif role == "assistant_tool_call":
            md_lines.append("\n## 🤖 Assistant\n")
            if thinking:
                md_lines.append("\n**Thinking:**\n")
                md_lines.append(f"```\n{thinking}\n```\n")
            if content:
                md_lines.append(f"\n{content}\n")
            if tool_calls:
                md_lines.append("\n**Tools called:**\n")
                for tc in tool_calls:
                    func = tc.get('function', {})
                    name = func.get('name', 'unknown')
                    args = func.get('arguments', {})
                    md_lines.append(f"\n- `{name}`\n")
                    if args:
                        md_lines.append(f"  ```json\n  {json.dumps(args, indent=2, ensure_ascii=False)}\n  ```\n")
            md_lines.append("\n---\n")

        elif role == "tool":
            md_lines.append(f"\n**Tool result:** `{tool_name}`\n")
            md_lines.append(f"```\n{content[:1000]}\n```\n")
            if len(content) > 1000:
                md_lines.append(f"\n*(truncated, {len(content)} chars total)*\n")
            md_lines.append("\n---\n")

    markdown_content = "".join(md_lines)

    # Безопасное имя файла с поддержкой UTF-8 (RFC 5987)
    title_safe = session['title'][:30].replace(" ", "_")
    filename_ascii = f"chat_{sid[:8]}.md"
    filename_utf8 = f"chat_{title_safe}_{sid[:8]}.md"

    return Response(
        content=markdown_content,
        media_type="text/markdown",
        headers={
            "Content-Disposition": f'attachment; filename="{filename_ascii}"; filename*=UTF-8\'\'{quote(filename_utf8)}'
        }
    )



@app.websocket("/ws/{session_id}")
async def websocket_endpoint(ws: WebSocket, session_id: str):
    await ws.accept()
    if not session_exists(session_id):
        await ws.send_json({"type": "error", "content": "Session not found"})
        await ws.close(code=1008)
        return
    logger.info(f"WebSocket подключен: {session_id}")

    connection_state = {"allow_all": False}
    msg_queue: asyncio.Queue = asyncio.Queue()
    stop_event = asyncio.Event()

    async def ws_reader():
        try:
            while True:
                try:
                    data = await asyncio.wait_for(ws.receive_json(), timeout=30)  # 30 сек
                except asyncio.TimeoutError:
                    # Отправляем ping для поддержания соединения (heartbeat)
                    try:
                        await ws.send_json({"type": "ping"})
                    except Exception:
                        break  # Не можем отправить — соединение разорвано
                    continue
                logger.debug(f"Получено сообщение: {data.get('type')}")
                message_type = data.get("type")

                if message_type in ("stop", "assistant.stop"):
                    async with session_tasks_lock:
                        task_info = session_tasks.get(session_id)
                    if task_info:
                        task_info["stop_event"].set()
                elif message_type in ("confirm_response", "confirm"):
                    action = data.get("action")
                    if action is None and isinstance(data.get("allow"), bool):
                        action = "allow" if data.get("allow") else "deny"
                    async with session_tasks_lock:
                        task_info = session_tasks.get(session_id)
                    if task_info:
                        await task_info["confirm_queue"].put(action or "deny")
                elif message_type == "set_allow_all":
                    connection_state["allow_all"] = data.get("value", False)
                    await _safe_send(ws, {
                        "type": "allow_all_changed",
                        "value": connection_state["allow_all"]
                    })
                else:
                    if message_type == "input":
                        data = {"type": "message", "content": data.get("text", "")}
                    await msg_queue.put(data)
        except WebSocketDisconnect as e:
            # Нормальное закрытие соединения — не логируем как ошибку
            code = e.code if hasattr(e, 'code') else None
            # 1000 = нормальное закрытие, 1001 = клиент уходит, 1005 = нет статуса, 1006 = разрыв
            # 1012 = сервис перезагружается (uvicorn reload), 1013 = повторная попытка
            if code in (1000, 1001, 1005, 1006, 1012, 1013):
                logger.info(f"WebSocket закрыт: session_id={session_id}, code={code}")
            else:
                logger.warning(f"WebSocket закрыт с кодом {code}: session_id={session_id}")
            await msg_queue.put(None)
        except Exception as e:
            # Реальная ошибка — логируем
            logger.error(f"WebSocket reader ошибка: {e}", exc_info=True)
            await msg_queue.put(None)

    reader_task = asyncio.create_task(ws_reader())

    try:
        while True:
            item = await msg_queue.get()
            if item is None:
                # Reader завершил работу — соединение закрыто
                break
            if item.get("type") == "message":
                if not isinstance(item.get("content"), str) or not item["content"].strip():
                    await _safe_send(ws, {"type": "error", "content": "Пустое сообщение не поддерживается"})
                    continue
                logger.info(f"Обработка сообщения для сессии {session_id}: {item['content'][:50]}...")
                stop_event.clear()

                confirm_queue = asyncio.Queue()
                task_stop_event = asyncio.Event()
                background_task = asyncio.create_task(
                    stream_chat_background(
                        session_id, item["content"],
                        connection_state, task_stop_event, confirm_queue, ws
                    )
                )

                async with session_tasks_lock:
                    session_tasks[session_id] = {
                        "task": background_task,
                        "stop_event": task_stop_event,
                        "confirm_queue": confirm_queue,
                    }

                try:
                    await background_task
                    logger.info(f"Задача завершена для сессии {session_id}")
                except asyncio.CancelledError:
                    # Задача была отменена - корректно завершаем
                    logger.info(f"Задача отменена для сессии {session_id}")
                    # Отправляем сигнал остановки в background_task через confirm_queue
                    async with session_tasks_lock:
                        task_info = session_tasks.get(session_id)
                    if task_info:
                        try:
                            await task_info["confirm_queue"].put(None)
                        except Exception:
                            pass
                        # Принудительно останавливаем задачу
                        task_info["stop_event"].set()
                        try:
                            await asyncio.wait_for(background_task, timeout=2)
                        except (asyncio.TimeoutError, asyncio.CancelledError):
                            background_task.cancel()
                    break
                except Exception as e:
                    logger.error(f"Ошибка в background_task: {e}", exc_info=True)
                    try:
                        await ws.send_json({"type": "error", "content": "Произошла внутренняя ошибка. Подробности в логе сервера."})
                        await ws.send_json({"type": "response_end"})
                    except Exception:
                        break
                finally:
                    async with session_tasks_lock:
                        task_info = session_tasks.get(session_id)
                        if task_info and task_info["task"] == background_task:
                            del session_tasks[session_id]
    except asyncio.CancelledError:
        # Нормальное завершение при shutdown - отменяем все задачи
        async with session_tasks_lock:
            task_info = session_tasks.get(session_id)
        if task_info:
            task_info["stop_event"].set()
            try:
                await task_info["confirm_queue"].put(None)
            except Exception:
                pass
            try:
                await asyncio.wait_for(task_info["task"], timeout=2)
            except (asyncio.TimeoutError, asyncio.CancelledError, Exception):
                task_info["task"].cancel()
            finally:
                async with session_tasks_lock:
                    session_tasks.pop(session_id, None)
    except Exception as e:
        logger.error(f"WebSocket ошибка: {e}", exc_info=True)
    finally:
        # Отменяем reader_task при выходе
        reader_task.cancel()
        try:
            await reader_task
        except (asyncio.CancelledError, Exception):
            pass
        # Также отменяем background_task если она ещё выполняется
        async with session_tasks_lock:
            task_info = session_tasks.get(session_id)
        if task_info:
            task_info["stop_event"].set()
            try:
                await task_info["confirm_queue"].put(None)
            except Exception:
                pass
            try:
                await asyncio.wait_for(task_info["task"], timeout=2)
            except (asyncio.TimeoutError, asyncio.CancelledError, Exception):
                task_info["task"].cancel()
            finally:
                async with session_tasks_lock:
                    session_tasks.pop(session_id, None)
        logger.info(f"WebSocket сессия завершена: {session_id}")


@app.get("/{path:path}")
async def spa_fallback(request: Request, path: str):
    """SPA fallback — все неизвестные пути отдают index.html."""
    if path.startswith("api/") or path.startswith("ws/") or path.startswith("auth/"):
        raise HTTPException(status_code=404, detail="Not found")
    html_path = Path(__file__).parent / "static" / "dist" / "index.html"
    if not html_path.exists():
        html_path = Path(__file__).parent / "static" / "index.html"
    return HTMLResponse(html_path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server:app", host="0.0.0.0", port=10310, reload=False, log_level="info")
