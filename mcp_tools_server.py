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
import urllib.parse
import json
from pathlib import Path
from typing import Optional, List, Dict, Any
from html import unescape

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


# ─── Веб-поиск: расширенная версия ─────────────────────────────────────

def _search_searxng(query: str, num_results: int = 10) -> List[Dict[str, str]]:
    """Поиск через публичный SearxNG (мета-поисковик)."""
    results = []
    try:
        encoded_query = urllib.parse.quote(query)
        # Используем публичный инстанс SearxNG
        instances = [
            "https://searx.be",
            "https://search.ononoki.org",
            "https://searx.org",
        ]

        for base_url in instances:
            try:
                url = f"{base_url}/search?q={encoded_query}&format=json&categories=general"

                req = urllib.request.Request(
                    url,
                    headers={
                        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                        'Accept': 'application/json',
                    }
                )

                with urllib.request.urlopen(req, timeout=15) as response:
                    data = json.loads(response.read().decode('utf-8'))

                for item in data.get('results', [])[:num_results]:
                    results.append({
                        'title': item.get('title', ''),
                        'url': item.get('url', ''),
                        'snippet': item.get('content', ''),
                        'source': 'SearxNG'
                    })

                if results:
                    break  # Успешно получили результаты
            except Exception:
                continue  # Пробуем следующий инстанс

    except Exception as e:
        log(f"SearxNG search error: {e}")

    return results


def _search_duckduckgo_lite(query: str, num_results: int = 10) -> List[Dict[str, str]]:
    """Поиск через DuckDuckGo Lite (более надежный)."""
    results = []
    try:
        encoded_query = urllib.parse.quote(query)
        # Используем lite версию - она более стабильна
        url = f"https://lite.duckduckgo.com/lite/?q={encoded_query}"

        req = urllib.request.Request(
            url,
            headers={
                'User-Agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                'Accept-Language': 'en-US,en;q=0.5',
                'DNT': '1',
            }
        )

        with urllib.request.urlopen(req, timeout=30) as response:
            html = response.read().decode('utf-8', errors='ignore')

        log(f"DuckDuckGo Lite response length: {len(html)}")

        # Парсим результаты из lite версии
        # Лайт версия использует таблицу с классом result
        # Ищем все ссылки которые не начинаются с /lite/
        link_pattern = re.compile(r'<tr class="result"><td.*?><a href="([^"]+)" class="result-link"[^>]*>(.*?)</a>', re.DOTALL)

        matches = link_pattern.findall(html)

        # Альтернативный парсинг - ищем все внешние ссылки
        if not matches:
            alt_pattern = re.compile(r'<a href="(https?://[^"]+)"[^>]*class="result-link"[^>]*>(.*?)</a>', re.DOTALL)
            matches = alt_pattern.findall(html)

        # Еще одна попытка - просто ищем заголовки
        if not matches:
            simple_pattern = re.compile(r'<a href="([^"]+)"[^>]*>([^<]+)</a>')
            all_links = simple_pattern.findall(html)
            # Фильтруем внутренние ссылки
            matches = [(url, title) for url, title in all_links
                      if url.startswith('http') and 'duckduckgo' not in url.lower()]

        for href, title in matches[:num_results]:
            clean_title = re.sub(r'<[^>]+>', '', title).strip()
            clean_href = href.split('&')[0] if '&' in href else href

            if clean_href and clean_title:
                results.append({
                    'title': clean_title,
                    'url': clean_href,
                    'snippet': '',
                    'source': 'DuckDuckGo Lite'
                })

        log(f"DuckDuckGo Lite found {len(results)} results")
    except Exception as e:
        log(f"DuckDuckGo Lite search error: {e}")

    return results


def _search_duckduckgo(query: str, num_results: int = 10) -> List[Dict[str, str]]:
    """Поиск через DuckDuckGo HTML."""
    results = []
    try:
        encoded_query = urllib.parse.quote(query)
        url = f"https://html.duckduckgo.com/html/?q={encoded_query}"

        req = urllib.request.Request(
            url,
            headers={
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                'Accept-Language': 'en-US,en;q=0.5',
            }
        )

        with urllib.request.urlopen(req, timeout=30) as response:
            html = response.read().decode('utf-8', errors='ignore')

        # Парсим заголовки и ссылки
        title_pattern = re.compile(r'<a class="result__a"[^>]*>(.*?)</a>', re.DOTALL)
        url_pattern = re.compile(r'<a class="result__a" href="([^"]*)"', re.DOTALL)
        snippet_pattern = re.compile(r'<a class="result__snippet"[^>]*>(.*?)</a>', re.DOTALL)

        titles = title_pattern.findall(html)
        urls = url_pattern.findall(html)
        snippets = snippet_pattern.findall(html)

        for i in range(min(len(titles), len(urls), num_results)):
            title = re.sub(r'<[^>]+>', '', titles[i]).strip()
            url = urls[i]

            # Извлекаем оригинальный URL из редиректа DuckDuckGo
            if url.startswith('/l/?uddg='):
                url = urllib.parse.unquote(url.replace('/l/?uddg=', '').split('&rutime=')[0])

            snippet = ""
            if i < len(snippets):
                snippet = re.sub(r'<[^>]+>', '', snippets[i]).strip()

            results.append({
                'title': title,
                'url': url,
                'snippet': snippet,
                'source': 'DuckDuckGo'
            })
    except Exception as e:
        log(f"DuckDuckGo search error: {e}")

    return results


def _search_google(query: str, num_results: int = 10) -> List[Dict[str, str]]:
    """Поиск через Google (через HTML версию)."""
    results = []
    try:
        encoded_query = urllib.parse.quote(query)
        url = f"https://www.google.com/search?q={encoded_query}&num={num_results}"

        req = urllib.request.Request(
            url,
            headers={
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                'Accept-Language': 'en-US,en;q=0.5',
            }
        )

        with urllib.request.urlopen(req, timeout=30) as response:
            html = response.read().decode('utf-8', errors='ignore')

        # Парсим результаты Google
        title_pattern = re.compile(r'<h3 class="[^"]*">([^<]*)</h3>')
        url_pattern = re.compile(r'<a href="([^"]+)"[^>]*>.*?<h3')

        urls = re.findall(r'/url\?q=([^&]+)&', html)
        titles = title_pattern.findall(html)

        # Фильтруем служебные URL
        clean_urls = [u for u in urls if not u.startswith(('http://webcache.google', 'https://www.google.com'))]

        for i in range(min(len(titles), len(clean_urls), num_results)):
            results.append({
                'title': unescape(re.sub(r'<[^>]+>', '', titles[i])).strip(),
                'url': urllib.parse.unquote(clean_urls[i]),
                'snippet': '',
                'source': 'Google'
            })
    except Exception as e:
        log(f"Google search error: {e}")

    return results


def _search_brave(query: str, num_results: int = 10) -> List[Dict[str, str]]:
    """Поиск через Brave Search (если доступен API)."""
    results = []
    try:
        # Пытаемся использовать Brave Search API (если есть ключ)
        api_key = os.environ.get('BRAVE_SEARCH_API_KEY')
        if not api_key:
            return results

        encoded_query = urllib.parse.quote(query)
        url = f"https://api.search.brave.com/res/v1/web/search?q={encoded_query}&count={num_results}"

        req = urllib.request.Request(
            url,
            headers={
                'User-Agent': 'Mozilla/5.0 (compatible; ThuleUI/1.0)',
                'Accept': 'application/json',
                'X-Subscription-Token': api_key,
            }
        )

        with urllib.request.urlopen(req, timeout=30) as response:
            data = json.loads(response.read().decode('utf-8'))

        web_results = data.get('web', {}).get('results', [])
        for item in web_results[:num_results]:
            results.append({
                'title': item.get('title', ''),
                'url': item.get('url', ''),
                'snippet': item.get('description', ''),
                'source': 'Brave'
            })
    except Exception as e:
        log(f"Brave search error: {e}")

    return results


def _search_wikipedia(query: str) -> List[Dict[str, str]]:
    """Поиск через Wikipedia API."""
    results = []
    try:
        encoded_query = urllib.parse.quote(query)
        # Используем Wikipedia API для поиска
        url = f"https://ru.wikipedia.org/w/api.php?action=query&list=search&srsearch={encoded_query}&format=json&srlimit=10"

        req = urllib.request.Request(
            url,
            headers={
                'User-Agent': 'Mozilla/5.0 (compatible; ThuleUI/1.0)',
                'Accept': 'application/json',
            }
        )

        with urllib.request.urlopen(req, timeout=30) as response:
            data = json.loads(response.read().decode('utf-8'))

        search_results = data.get('query', {}).get('search', [])
        for item in search_results[:10]:
            title = item.get('title', '')
            snippet = item.get('snippet', '')
            # Убираем HTML теги из сниппета
            clean_snippet = re.sub(r'<[^>]+>', '', snippet).strip()
            results.append({
                'title': title,
                'url': f"https://ru.wikipedia.org/wiki/{urllib.parse.quote(title.replace(' ', '_'))}",
                'snippet': clean_snippet,
                'source': 'Wikipedia'
            })
    except Exception as e:
        log(f"Wikipedia search error: {e}")

    return results


@mcp.tool()
def web_search(
    query: str,
    num_results: int = 10,
    engine: str = "auto",
    include_snippets: bool = True
) -> str:
    """Выполняет поиск в интернете с поддержкой нескольких движков.

    query: Поисковый запрос.
    num_results: Количество результатов (по умолчанию 10, макс 20).
    engine: Поисковая система: 'duckduckgo', 'google', 'brave', 'auto' (все доступные).
    include_snippets: Включать краткое описание результатов.
    """
    # Ограничиваем количество результатов
    num_results = min(max(1, num_results), 20)

    all_results: List[Dict[str, str]] = []
    engines_used: List[str] = []

    # Определяем какие движки использовать
    if engine == "auto":
        # SearxNG как первый источник - мета-поисковик, более надежен
        searx_results = _search_searxng(query, num_results)
        if searx_results:
            all_results.extend(searx_results)
            engines_used.append("SearxNG")

        # DuckDuckGo Lite как второй источник
        if len(all_results) < num_results:
            lite_results = _search_duckduckgo_lite(query, num_results)
            if lite_results:
                all_results.extend(lite_results)
                engines_used.append("DuckDuckGo Lite")

        # Затем обычный DuckDuckGo
        if len(all_results) < num_results:
            ddg_results = _search_duckduckgo(query, num_results)
            if ddg_results:
                all_results.extend(ddg_results)
                engines_used.append("DuckDuckGo")

        # Google как дополнительный источник
        if len(all_results) < num_results:
            google_results = _search_google(query, num_results)
            if google_results:
                all_results.extend(google_results)
                engines_used.append("Google")

        # Brave API если есть ключ
        if len(all_results) < num_results:
            brave_results = _search_brave(query, num_results)
            if brave_results:
                all_results.extend(brave_results)
                engines_used.append("Brave")

        # Если ничего не найдено - пробуем Wikipedia
        if not all_results:
            wiki_results = _search_wikipedia(query)
            if wiki_results:
                all_results.extend(wiki_results)
                engines_used.append("Wikipedia")

        # Удаляем дубликаты по URL
        seen_urls = set()
        unique_results = []
        for r in all_results:
            if r['url'] not in seen_urls:
                seen_urls.add(r['url'])
                unique_results.append(r)

        all_results = unique_results[:num_results]

    elif engine == "duckduckgo":
        # Пробуем оба варианта DuckDuckGo
        all_results = _search_searxng(query, num_results)
        if not all_results:
            all_results = _search_duckduckgo_lite(query, num_results)
        if not all_results:
            all_results = _search_duckduckgo(query, num_results)
        engines_used.append("DuckDuckGo")

    elif engine == "google":
        all_results = _search_google(query, num_results)
        engines_used.append("Google")

    elif engine == "brave":
        all_results = _search_brave(query, num_results)
        engines_used.append("Brave")

    else:
        return f"Error: неизвестный поисковый движок '{engine}'. Доступные: duckduckgo, google, brave, auto"

    # Логирование для отладки
    log(f"Web search: query='{query}', engine='{engine}', found {len(all_results)} results")

    # Форматируем результат
    if not all_results:
        # Возвращаем полезное сообщение с альтернативами
        return f"""❌ Ничего не найдено по запросу: **{query}**

Возможные причины:
- Поисковые системы блокируют автоматические запросы
- Временные проблемы с сетью

Попробуйте:
1. Изменить формулировку запроса
2. Использовать WebFetch с конкретным URL
3. Посетить напрямую: https://duckduckgo.com/?q={urllib.parse.quote(query)}"""

    # Заголовок с указанием использованных движков
    output = [f"🔍 Результаты поиска: **{query}**"]
    output.append(f"_Источники: {', '.join(engines_used)}_ (найдено: {len(all_results)})\n")

    # Список результатов
    for i, result in enumerate(all_results, 1):
        title = result.get('title', 'Без названия')
        url = result.get('url', '')
        snippet = result.get('snippet', '')
        source = result.get('source', '')

        entry = f"**{i}. [{title}]({url})**"
        if source:
            entry += f" _(источник: {source})_"

        if include_snippets and snippet:
            entry += f"\n   > {snippet}"

        output.append(entry)

    # Добавляем источники для цитирования
    output.append("\n---")
    output.append("📚 **Источники для цитирования:**")
    for i, result in enumerate(all_results, 1):
        output.append(f"{i}. [{result.get('title', 'Без названия')}]({result.get('url', '')})")

    return "\n\n".join(output)


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
