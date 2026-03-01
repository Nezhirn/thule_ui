# Qwen Agent

![Описание картинки](https://lh3.googleusercontent.com/d/1L6Ya-BCRb8AdSndyjB2mLf0GiPzCwwVK)
**Веб-интерфейс для qwen-cli с поддержкой MCP инструментов**

---

## Авторы

**Claude Opus 4.6 + Qwen Code 3.5**

---

## Оглавление

1. [Описание](#описание)
2. [Архитектура](#архитектура)
3. [Требования](#требования)
4. [Установка](#установка)
5. [Конфигурация](#конфигурация)
6. [Запуск](#запуск)
7. [Переменные окружения](#переменные-окружения)
8. [API](#api)
9. [MCP инструменты](#mcp-инструменты)
10. [Структура проекта](#структура-проекта)

---

## Описание

Qwen Agent — это веб-приложение на базе FastAPI, предоставляющее интерфейс для работы с AI-ассистентом Qwen через CLI. Приложение поддерживает:

- **Множественные сессии чатов** с сохранением истории в SQLite
- **MCP (Model Context Protocol) инструменты** для выполнения системных команд
- **Подтверждение опасных операций** (bash, ssh, запись файлов) перед выполнением
- **Стриминг ответов** с отображением процесса мышления модели
- **Долгосрочную память** через save_memory/read_memory инструменты

---

## Архитектура

```
┌─────────────────────────────────────────────────────────────────┐
│                        Браузер (Клиент)                         │
│  React + TypeScript + Vite + Tailwind CSS                       │
│  WebSocket для стриминга ответов                                │
└─────────────────────────────────────────────────────────────────┘
                              │
                              │ HTTP/WebSocket (порт 10310)
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                    FastAPI Server (server.py)                   │
│  • Управление сессиями (SQLite)                                 │
│  • WebSocket handler                                            │
│  • MCP Session Manager                                          │
│  • CORS: allow_origins=["*"]                                    │
└─────────────────────────────────────────────────────────────────┘
                    │                           │
                    │                           │
                    ▼                           ▼
┌───────────────────────────┐   ┌─────────────────────────────────┐
│     qwen CLI (SDK mode)   │   │   MCP Server (mcp_tools_server) │
│  --input-format stream-json│  │  Stdio transport                │
│  --output-format stream-json│ │  Инструменты:                   │
└───────────────────────────┘   │  • run_bash_command             │
                                │  • run_ssh_command              │
                                │  • write_file                   │
                                │  • edit_file                    │
                                └─────────────────────────────────┘
                                            │
                                            ▼
                              ┌─────────────────────────┐
                              │   SQLite (sessions.db)  │
                              │   • sessions            │
                              │   • messages            │
                              │   • memory              │
                              └─────────────────────────┘
```

---

## Требования

### Системные требования

- **ОС:** Linux (протестировано на Arch Linux)
- **Python:** 3.10+ (рекомендуется 3.14)
- **Node.js:** 18+ (для сборки фронтенда)
- **qwen CLI:** установлен и доступен

### Python зависимости

```
fastapi>=0.109.0
uvicorn[standard]>=0.27.0
websockets>=12.0
mcp>=1.0.0
httpx>=0.26.0
python-dotenv>=1.0.0
beautifulsoup4>=4.12.0
requests>=2.31.0
```

### Node.js зависимости (фронтенд)

См. `static/package.json`

---

## Установка

### 1. Клонирование репозитория

```bash
git clone git@github.com:Nezhirn/qwen-code-web-unofficial.git
cd qwen-code-web-unofficial
```

### 2. Установка Python зависимостей

```bash
pip install -r requirements.txt
```

### 3. Установка Node.js зависимостей (для сборки фронтенда)

```bash
cd static
npm install
npm run build
cd ..
```

### 4. Проверка qwen CLI

Убедитесь, что qwen CLI установлен и доступен:

```bash
qwen --version
```

Если используется кастомный путь, настройте переменную `QWEN_PATH` в `.env`.

---

## Конфигурация

### Файл `.env`

Создайте файл `.env` в корне проекта. Переменные загружаются автоматически через `python-dotenv`:

```bash
# Путь к Python для MCP (по умолчанию используется sys.executable)
export MCP_PYTHON="/path/to/python"

# Путь к qwen CLI
export QWEN_PATH="/path/to/qwen"
```

---

## Запуск

### Разработка (с auto-reload)

```bash
python server.py
```

Или через uvicorn напрямую:

```bash
uvicorn server:app --host 0.0.0.0 --port 10310 --reload
```

### Продакшн

```bash
uvicorn server:app --host 0.0.0.0 --port 10310
```

### Доступ к приложению

Откройте в браузере: `http://localhost:10310`

Сервер доступен с любого адреса (CORS: `allow_origins=["*"]`).

---

## Переменные окружения

| Переменная | Описание | По умолчанию |
|------------|----------|--------------|
| `QWEN_PATH` | Путь к исполняемому файлу qwen CLI | автоопределение |
| `MCP_PYTHON` | Путь к Python для MCP сервера | `sys.executable` (текущий Python) |

Контекст и лимиты результатов инструментов управляются qwen-cli.

---

## API

### REST API

| Метод | Эндпоинт | Описание |
|-------|----------|----------|
| `GET` | `/` | Главная страница (React SPA) |
| `GET` | `/api/health` | Health-check |
| `GET` | `/api/sessions` | Список сессий |
| `POST` | `/api/sessions` | Создать сессию |
| `DELETE` | `/api/sessions/{id}` | Удалить сессию |
| `PUT` | `/api/sessions/{id}` | Переименовать сессию |
| `GET` | `/api/sessions/{id}/messages` | Сообщения сессии |
| `GET` | `/api/sessions/{id}/system-prompt` | Системный промпт сессии |
| `PUT` | `/api/sessions/{id}/system-prompt` | Установить системный промпт |
| `GET` | `/api/default-prompt` | Системный промпт по умолчанию |
| `GET` | `/api/user` | Текущий пользователь |

### WebSocket

**Подключение:** `ws://localhost:10310/ws/{session_id}`

**Сообщения клиент → сервер:**

```json
{"type": "message", "content": "Текст сообщения"}
{"type": "stop"}
{"type": "confirm_response", "action": "allow|deny|allow_all"}
```

**Сообщения сервер → клиент:**

| Тип | Описание |
|-----|----------|
| `response_start` | Начало обработки |
| `stream_start` | Начало стриминга |
| `thinking` | Фрагмент мышления |
| `content` | Фрагмент ответа |
| `tool_call` | Вызов инструмента |
| `tool_result` | Результат инструмента |
| `tool_denied` | Инструмент запрещён |
| `confirm_request` | Запрос подтверждения |
| `allow_all_enabled` | Разрешены все инструменты |
| `response_end` | Конец ответа |
| `stopped` | Остановлено пользователем |
| `error` | Ошибка |
| `session_renamed` | Сессия переименована |
| `ping` | Heartbeat |

---

## MCP инструменты

### Встроенные инструменты

| Инструмент | Описание | Требует подтверждения |
|------------|----------|----------------------|
| `run_bash_command` | Выполнение bash команды | ✅ |
| `run_ssh_command` | SSH команда на удалённом сервере | ✅ |
| `write_file` | Запись в файл | ✅ |
| `edit_file` | Редактирование файла | ✅ |

### Инструменты qwen CLI (нативные)

- `run_shell_command` — bash команды
- `read_file` — чтение файлов
- `write_file` — запись файлов
- `edit_file` — редактирование файлов
- `list_directory` — список файлов
- `glob` — поиск файлов
- `grep_search` — поиск по содержимому
- `web_fetch` — загрузка веб-страниц
- `web_search` — поиск в интернете
- `todo_write` / `todo_read` — управление задачами
- `save_memory` / `read_memory` — долгосрочная память

---

## Структура проекта

```
qwen-code-web-unofficial/
├── server.py              # Основной FastAPI сервер
├── mcp_tools_server.py    # MCP сервер с инструментами
├── requirements.txt       # Python зависимости
├── .env                   # Переменные окружения
├── sessions.db            # SQLite база данных
├── server.log             # Лог файл
├── static/                # Фронтенд
│   ├── package.json       # Node.js зависимости
│   ├── tsconfig.json      # TypeScript конфиг
│   ├── vite.config.ts     # Vite конфиг
│   ├── index.html         # HTML шаблон
│   ├── dist/              # Сборка для продакшна
│   └── src/
│       ├── main.tsx       # Точка входа React
│       ├── App.tsx        # Главный компонент
│       ├── api.ts         # API клиент
│       ├── types.ts       # TypeScript типы
│       ├── index.css      # Стили
│       ├── components/    # React компоненты
│       │   ├── Sidebar.tsx
│       │   ├── ChatHeader.tsx
│       │   ├── ChatInput.tsx
│       │   ├── MessageBubble.tsx
│       │   ├── ConfirmBar.tsx
│       │   ├── StatusBar.tsx
│       │   ├── SettingsModal.tsx
│       │   ├── EmptyState.tsx
│       │   ├── ThinkingBlock.tsx
│       │   └── ToolBlock.tsx
│       └── utils/         # Утилиты
└── README.md              # Этот файл
```

---

## База данных

### Таблицы

**sessions**
- `id` (TEXT, PRIMARY KEY) — UUID сессии
- `user_id` (TEXT) — зарезервировано
- `title` (TEXT) — Заголовок чата
- `created_at` (TEXT) — Дата создания
- `updated_at` (TEXT) — Дата обновления
- `system_prompt` (TEXT) — Кастомный системный промпт

**messages**
- `id` (INTEGER, PRIMARY KEY)
- `session_id` (TEXT, FK)
- `role` (TEXT) — user/assistant/assistant_tool_call/tool
- `content` (TEXT)
- `thinking` (TEXT) — Содержимое thinking
- `tool_calls` (TEXT, JSON) — Вызовы инструментов
- `tool_name` (TEXT) — Название инструмента
- `created_at` (TEXT)

**memory**
- `id` (INTEGER, PRIMARY KEY)
- `session_id` (TEXT, FK)
- `key` (TEXT)
- `value` (TEXT)
- `created_at` (TEXT)

---

## Безопасность

### Middleware

1. **RequestSizeLimitMiddleware** — ограничение размера запроса (5 MB)
2. **SecurityHeadersMiddleware** — security headers:
   - `X-Content-Type-Options: nosniff`
   - `X-Frame-Options: DENY`
   - `X-XSS-Protection: 1; mode=block`
   - `Referrer-Policy: strict-origin-when-cross-origin`

### Подтверждение операций

Инструменты требующие подтверждения:
- `bash`, `shell`, `run_bash_command`, `execute_command`
- `ssh`, `run_ssh_command`, `remote_command`
- `write_file`, `create_file`
- `edit_file`, `replace_in_file`

---

## Лицензия

MIT
