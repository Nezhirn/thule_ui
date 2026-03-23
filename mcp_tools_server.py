#!/usr/bin/env python3
"""
MCP Server для Thule UI
Инструменты требующие контроля: bash, ssh, write_file, edit_file

Реализованы все инструменты, упоминаемые в system_prompt:
- read_file, write_file, edit_file
- run_bash_command, run_ssh_command
- save_memory, read_memory, delete_memory
- list_directory, glob, grep_search
- web_fetch, web_search
- todo_write, todo_read
"""

import os
import re
import signal
import sqlite3
import subprocess
import sys
import threading
import urllib.request
import urllib.error
import json
from pathlib import Path
from typing import Optional, List

from mcp.server.fastmcp import FastMCP

# Flush stdout immediately for debugging
sys.stdout = os.fdopen(sys.stdout.fileno(), 'w', buffering=1)

def log(msg):
    """Debug logging to stderr."""
    print(f"[MCP DEBUG] {msg}", flush=True, file=sys.stderr)

log("Starting MCP server...")

mcp = FastMCP("thule-ui-tools")


DB_PATH = str(Path(__file__).parent / "sessions.db")


def get_db_connection() -> sqlite3.Connection:
    """Создаёт соединение SQLite с правильными настройками."""
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


# ─── Инструменты для работы с файлами ───────────────────────────

@mcp.tool()
def read_file(path: str) -> str:
    """Читает содержимое файла.

    path: Абсолютный путь к файлу.
    """
    try:
        file_path = Path(path)
        if not file_path.exists():
            return f"Error: файл не найден: {path}"
        if not file_path.is_file():
            return f"Error: это не файл: {path}"

        # Ограничиваем размер читаемого файла (2MB)
        content = file_path.read_text(encoding="utf-8")
        if len(content) > 2 * 1024 * 1024:
            return f"Error: файл слишком большой ({len(content)} байт). Максимум 2MB."
        return content
    except PermissionError:
        return f"Error: нет прав на чтение: {path}"
    except Exception as e:
        return f"Error: {str(e)}"


@mcp.tool()
def write_file(path: str, content: str) -> str:
    """Записывает содержимое в файл.

    path: Путь к файлу.
    content: Содержимое для записи.
    """
    try:
        file_path = Path(path)
        # Создаём родительские директории
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(content, encoding="utf-8")
        return f"Файл записан: {path} ({len(content)} байт)"
    except PermissionError:
        return f"Error: нет прав на запись: {path}"
    except Exception as e:
        return f"Error: {str(e)}"


@mcp.tool()
def edit_file(path: str, old_string: str, new_string: str) -> str:
    """Редактирует файл, заменяя old_string на new_string.

    path: Путь к файлу.
    old_string: Строка для замены.
    new_string: Новая строка.
    """
    try:
        file_path = Path(path)
        if not file_path.exists():
            return f"Error: файл не найден: {path}"

        content = file_path.read_text(encoding="utf-8")
        if old_string not in content:
            return f"Error: '{old_string[:50]}...' не найдено в файле"

        new_content = content.replace(old_string, new_string, 1)
        file_path.write_text(new_content, encoding="utf-8")
        return f"Файл обновлён: {path} (заменено 1 вхождение)"
    except PermissionError:
        return f"Error: нет прав на запись: {path}"
    except Exception as e:
        return f"Error: {str(e)}"


# ─── Инструменты для работы с директориями ───────────────────────

@mcp.tool()
def list_directory(path: str) -> str:
    """Выводит список файлов и директорий в указанной директории.

    path: Путь к директории.
    """
    try:
        dir_path = Path(path)
        if not dir_path.exists():
            return f"Error: директория не найдена: {path}"
        if not dir_path.is_dir():
            return f"Error: это не директория: {path}"

        items = []
        for item in sorted(dir_path.iterdir()):
            item_type = "📁" if item.is_dir() else "📄"
            size = ""
            if item.is_file():
                try:
                    size = f" ({_format_size(item.stat().st_size)})"
                except:
                    pass
            items.append(f"{item_type} {item.name}{size}")

        if not items:
            return "(пустая директория)"
        return "\n".join(items)
    except PermissionError:
        return f"Error: нет прав на чтение: {path}"
    except Exception as e:
        return f"Error: {str(e)}"


def _format_size(size: int) -> str:
    """Форматирует размер файла."""
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size < 1024:
            return f"{size:.1f}{unit}"
        size /= 1024
    return f"{size:.1f}TB"


@mcp.tool()
def glob(pattern: str, path: str = ".") -> str:
    """Ищет файлы по шаблону.

    pattern: Шаблон поиска (например, *.py, **/*.js).
    path: Базовая директория для поиска.
    """
    try:
        base_path = Path(path)
        if not base_path.exists():
            return f"Error: директория не найдена: {path}"

        # Используем glob с рекурсивным поиском
        matches = list(base_path.glob(pattern))

        if not matches:
            return f"По шаблону '{pattern}' ничего не найдено в {path}"

        results = []
        for match in matches[:100]:  # Ограничиваем вывод
            rel_path = match.relative_to(base_path)
            item_type = "📁" if match.is_dir() else "📄"
            results.append(f"{item_type} {rel_path}")

        if len(matches) > 100:
            results.append(f"\n... и ещё {len(matches) - 100} файлов")

        return "\n".join(results)
    except PermissionError:
        return f"Error: нет прав на чтение: {path}"
    except Exception as e:
        return f"Error: {str(e)}"


@mcp.tool()
def grep_search(pattern: str, path: str = ".", case_sensitive: bool = False) -> str:
    """Ищет по содержимому файлов.

    pattern: Регулярное выражение или текст для поиска.
    path: Директория или файл для поиска.
    case_sensitive: Учитывать регистр.
    """
    try:
        search_path = Path(path)
        if not search_path.exists():
            return f"Error: путь не найден: {path}"

        results = []
        flags = 0 if case_sensitive else re.IGNORECASE

        if search_path.is_file():
            files_to_search = [search_path]
        else:
            # Рекурсивный поиск в директории (только текстовые файлы)
            files_to_search = []
            for ext in ['.txt', '.md', '.py', '.js', '.ts', '.jsx', '.tsx', '.json', '.yaml', '.yml', '.xml', '.html', '.css', '.sh', '.bash', '.zsh', '.conf', '.cfg', '.ini', '.toml']:
                files_to_search.extend(search_path.rglob(f'*{ext}'))

        max_files = 50  # Ограничиваем количество файлов
        max_lines_per_file = 20

        for file_path in files_to_search[:max_files]:
            try:
                matches_in_file = []
                with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                    for line_num, line in enumerate(f, 1):
                        if re.search(pattern, line, flags):
                            matches_in_file.append((line_num, line.rstrip()))
                        if len(matches_in_file) >= max_lines_per_file:
                            matches_in_file.append((None, "... (слишком много совпадений)"))
                            break

                if matches_in_file:
                    results.append(f"\n📄 {file_path}:")
                    for line_num, line in matches_in_file:
                        if line_num:
                            results.append(f"  {line_num}: {line[:200]}")
                        else:
                            results.append(f"  {line}")

            except (PermissionError, UnicodeDecodeError):
                continue

        if not results:
            return f"Поиск '{pattern}' не дал результатов"

        output = "".join(results)
        # Ограничиваем общий вывод
        if len(output) > 10000:
            output = output[:10000] + "\n\n... (вывод обрезан)"
        return output

    except Exception as e:
        return f"Error: {str(e)}"


# ─── Bash и SSH ──────────────────────────────────────────────────

@mcp.tool()
def run_bash_command(command: str) -> str:
    """Исполняет bash команду на локальном сервере.

    command: Bash команда для выполнения.
    Требует подтверждения пользователя перед выполнением.
    Таймаут: 120 секунд.
    """
    try:
        # Используем Popen с явной группой процессов для надёжного таймаута
        process = subprocess.Popen(
            command,
            shell=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.PIPE,
            text=True,
            preexec_fn=os.setsid
        )

        def kill_process():
            try:
                os.killpg(os.getpgid(process.pid), signal.SIGTERM)
                process.wait(timeout=5)
            except Exception:
                try:
                    os.killpg(os.getpgid(process.pid), signal.SIGKILL)
                except Exception:
                    pass

        timer = threading.Timer(120.0, kill_process)
        timer.start()

        try:
            stdout, stderr = process.communicate(timeout=120)
            timer.cancel()

            output = stdout
            if stderr:
                output += "\n--- STDERR ---\n" + stderr

            return output[:8000] if output else "(пустой вывод)"
        except subprocess.TimeoutExpired:
            return "Error: команда превысила таймаут в 120 секунд"
        except Exception as e:
            timer.cancel()
            return f"Error: {str(e)}"
    except Exception as e:
        return f"Error: {str(e)}"


@mcp.tool()
def run_ssh_command(host: str, command: str, user: str = "root") -> str:
    """Подключается по SSH к удалённому серверу и исполняет команду.

    host: IP адрес или домен сервера.
    command: Команда для выполнения.
    user: Имя пользователя (по умолчанию root).
    Требует подтверждения пользователя перед выполнением.
    Таймаут: 120 секунд.
    """
    try:
        ssh_cmd = [
            "ssh",
            "-o", "StrictHostKeyChecking=accept-new",
            "-o", "ConnectTimeout=10",
            "-o", "ServerAliveInterval=30",
            f"{user}@{host}",
            command
        ]

        process = subprocess.Popen(
            ssh_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )

        def kill_process():
            try:
                process.terminate()
                process.wait(timeout=5)
            except Exception:
                try:
                    process.kill()
                except Exception:
                    pass

        timer = threading.Timer(120.0, kill_process)
        timer.start()

        try:
            stdout, stderr = process.communicate(timeout=120)
            timer.cancel()
            output = stdout
            if stderr:
                output += "\n--- STDERR ---\n" + stderr
            return output[:8000] if output else "(пустой вывод)"
        except subprocess.TimeoutExpired:
            return "Error: команда превысила таймаут в 120 секунд"
        except Exception as e:
            timer.cancel()
            return f"SSH Error: {str(e)}"
    except Exception as e:
        return f"SSH Error: {str(e)}"


# ─── Веб-инструменты ─────────────────────────────────────────────

@mcp.tool()
def web_fetch(url: str) -> str:
    """Загружает содержимое веб-страницы.

    url: URL страницы.
    """
    try:
        req = urllib.request.Request(
            url,
            headers={
                'User-Agent': 'Mozilla/5.0 (compatible; Qwen-Agent/1.0)',
                'Accept': 'text/html,application/xhtml+xml,*/*',
            }
        )
        with urllib.request.urlopen(req, timeout=30) as response:
            content = response.read().decode('utf-8', errors='ignore')

        # Ограничиваем размер
        if len(content) > 100000:
            content = content[:100000] + "\n\n... (контент обрезан)"

        return content
    except urllib.error.HTTPError as e:
        return f"Error HTTP {e.code}: {e.reason}"
    except urllib.error.URLError as e:
        return f"Error: не удалось загрузить страницу - {str(e.reason)}"
    except Exception as e:
        return f"Error: {str(e)}"


@mcp.tool()
def web_search(query: str) -> str:
    """Выполняет поиск в интернете.

    query: Поисковый запрос.
    """
    try:
        # Используем DuckDuckGo через их HTML интерфейс
        encoded_query = urllib.parse.quote(query)
        url = f"https://html.duckduckgo.com/html/?q={encoded_query}"

        req = urllib.request.Request(
            url,
            headers={
                'User-Agent': 'Mozilla/5.0 (compatible; Qwen-Agent/1.0)',
            }
        )

        with urllib.request.urlopen(req, timeout=30) as response:
            html = response.read().decode('utf-8', errors='ignore')

        # Парсим результаты из HTML
        results = []
        # Ищем ссылки в результатах
        import re
        matches = re.findall(r'<a class="result__snippet"[^>]*href="([^"]*)"[^>]*>(.*?)</a>', html, re.DOTALL)

        for href, snippet in matches[:10]:
            # Очищаем HTML теги из сниппета
            clean_snippet = re.sub(r'<[^>]+>', '', snippet).strip()
            clean_href = href.split('?')[0]  # Убираем параметры
            results.append(f"- {clean_snippet}\n  URL: {clean_href}")

        if not results:
            return "Ничего не найдено"

        return f"Результаты поиска '{query}':\n\n" + "\n".join(results)

    except Exception as e:
        return f"Error: {str(e)}"


# ─── Память ─────────────────────────────────────────────────────

@mcp.tool()
def save_memory(session_id: str, key: str, value: str) -> str:
    """Сохраняет информацию в долгосрочную память сессии.

    session_id: ID сессии чата.
    key: Ключ для сохранения.
    value: Значение для сохранения.
    """
    try:
        conn = get_db_connection()
        now = sqlite3.datetime.now().isoformat()
        conn.execute(
            """INSERT INTO memory (session_id, key, value, created_at)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(session_id, key) DO UPDATE SET value = excluded.value, created_at = excluded.created_at""",
            (session_id, key, value, now),
        )
        conn.commit()
        conn.close()
        return f"Сохранено в память: {key}"
    except Exception as e:
        return f"Error: {str(e)}"


@mcp.tool()
def read_memory(session_id: str) -> str:
    """Читает все сохранённые факты из памяти сессии.

    session_id: ID сессии чата.
    """
    try:
        conn = get_db_connection()
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT key, value FROM memory WHERE session_id = ? ORDER BY created_at ASC",
            (session_id,),
        ).fetchall()
        conn.close()

        if not rows:
            return "(память пуста)"

        entries = []
        for row in rows:
            entries.append(f"• {row['key']}: {row['value']}")

        return "Сохранённые факты:\n" + "\n".join(entries)
    except Exception as e:
        return f"Error: {str(e)}"


@mcp.tool()
def delete_memory(session_id: str, key: str) -> str:
    """Удаляет запись из памяти сессии.

    session_id: ID сессии чата.
    key: Ключ для удаления.
    """
    try:
        conn = get_db_connection()
        conn.execute(
            "DELETE FROM memory WHERE session_id = ? AND key = ?",
            (session_id, key),
        )
        conn.commit()
        conn.close()
        return f"Удалено из памяти: {key}"
    except Exception as e:
        return f"Error: {str(e)}"


# ─── Todo ────────────────────────────────────────────────────────

@mcp.tool()
def todo_write(session_id: str, todos: List[dict]) -> str:
    """Записывает список задач (TODO) для сессии.

    session_id: ID сессии чата.
    todos: Список задач в формате [{"content": "...", "status": "pending|in_progress|completed"}].
    """
    log(f"todo_write called: session_id={session_id}, todos_count={len(todos)}")
    try:
        conn = get_db_connection()
        log("todo_write: DB connection created")
        now = sqlite3.datetime.now().isoformat()

        # Удаляем старые todos
        conn.execute(
            "DELETE FROM memory WHERE session_id = ? AND key LIKE '_todo_%'",
            (session_id,)
        )

        # Сохраняем новые todos
        for i, todo in enumerate(todos):
            key = f"_todo_{i}"
            value = json.dumps(todo)
            conn.execute(
                """INSERT INTO memory (session_id, key, value, created_at)
                   VALUES (?, ?, ?, ?)""",
                (session_id, key, value, now),
            )

        log("todo_write: committing...")
        conn.commit()
        log("todo_write: closing connection...")
        conn.close()
        log("todo_write: done")
        return f"Задачи обновлены ({len(todos)} задач)"
    except Exception as e:
        log(f"todo_write ERROR: {e}")
        return f"Error: {str(e)}"


@mcp.tool()
def todo_read(session_id: str) -> str:
    """Читает список задач (TODO) для сессии.

    session_id: ID сессии чата.
    """
    try:
        conn = get_db_connection()
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT value FROM memory WHERE session_id = ? AND key LIKE '_todo_%' ORDER BY key",
            (session_id,),
        ).fetchall()
        conn.close()

        if not rows:
            return "(список задач пуст)"

        todos = []
        for row in rows:
            try:
                todos.append(json.loads(row['value']))
            except:
                continue

        result_lines = ["Текущие задачи:"]
        status_icons = {"pending": "○", "in_progress": "◐", "completed": "●"}

        for i, todo in enumerate(todos, 1):
            status = todo.get("status", "pending")
            content = todo.get("content", "")
            icon = status_icons.get(status, "○")
            result_lines.append(f"{icon} {i}. {content}")

        return "\n".join(result_lines)
    except Exception as e:
        return f"Error: {str(e)}"


if __name__ == "__main__":
    mcp.run(transport="stdio")
