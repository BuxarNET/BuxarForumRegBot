# Промт 7: MainOrchestrator — главный управляющий модуль

## 1. Цель
Создать класс `MainOrchestrator`, который координирует все компоненты системы для массовой регистрации аккаунтов на форумах:
- Управляет очередью форумов из входного файла
- Распределяет задачи между пользователями (каждый пользователь проходит все форумы)
- Координирует работу `BrowserController`, `RegistrationController`, `ProxyManager`, `CaptchaExtensionHelper`
- Обеспечивает отказоустойчивость (resume после сбоя)
- Ведёт учёт результатов в файлах
- Формирует итоговый отчёт

## 2. Общие требования
- Код на Python 3.12+ с асинхронным синтаксисом (`async/await`).
- В начале каждого файла: `from __future__ import annotations`.
- Использовать библиотеки: `aiohttp`, `loguru`, `pathlib`, `python-dotenv`, `json`, `asyncio`, `re`, `datetime`.
- Модуль располагается в `src/orchestrator/main_orchestrator.py`.
- Добавить docstrings для всех классов и публичных методов (Google style).
- Строгая типизация: использовать `| None`, `list[str]`, `TypedDict`, `Callable`.
- Логирование через `loguru.logger`.
- Покрыть ключевую логику тестами (pytest) с моками.

## 3. Структура проекта (дополнения)

```
forum_reg_bot/
├── config/
│   └── settings.py             # Все настройки (таймауты, retry, пути)
├── data/
│   ├── accounts.json           # Пользователи (username, email, password, proxy_id)
│   ├── proxies.txt             # Прокси (одна строка — один прокси)
│   ├── results_new.txt         # Вход: список форумов (URL, из парсера)
│   ├── results_ok_user1.txt    # User1: успешные регистрации
│   ├── results_bad_user1.txt   # User1: неудачи
│   ├── results_ok_user2.txt    # User2: успешные регистрации
│   ├── results_bad_user2.txt   # User2: неудачи
│   └── profiles/               # Профили браузеров (per user)
│       ├── user1_20260224_153022/
│       ├── user2_20260224_154510/
│       └── ...
├── src/
│   ├── orchestrator/
│   │   └── main_orchestrator.py    # Главный модуль
│   ├── controllers/
│   │   ├── browser_controller.py   # Промт 2
│   │   └── registration_controller.py # Промт 4
│   └── utils/
│       ├── proxy_manager.py      # Промт 5
│       ├── captcha_helper.py     # Промт 6
│       └── ...
├── .env                        # API-ключи (капча, прочее)
└── main.py                     # Точка входа
```

## 4. Конфигурация (`config/settings.py`)

### 4.1. Формат файла
```python
# config/settings.py

# === ПУТИ К ФАЙЛАМ ===
ACCOUNTS_FILE = "data/accounts.json"
PROXIES_FILE = "data/proxies.txt"
FORUMS_SOURCE_FILE = "data/results_new.txt"
PROFILES_DIR = "data/profiles"
RESULTS_FLUSH_IMMEDIATELY = True

# === ФОРМАТ ФАЙЛОВ ===
INPUT_COMMENT_PREFIX = "#"
INPUT_FIELD_SEPARATOR = " "  # Единый разделитель: пробел
INPUT_URL_COLUMN = 0  # URL — первое поле
OUTPUT_TIMESTAMP_FORMAT = "%Y-%m-%dT%H:%M:%S"

# === ПАРАЛЛЕЛИЗМ ===
MAX_CONCURRENT_USERS = 5  # Макс. пользователей одновременно (5 браузеров)
EACH_USER_ALL_FORUMS = True  # Каждый пользователь проходит все форумы

# === ТАЙМАУТЫ И RETRY ===
MAX_REGISTRATION_RETRIES = 3  # Попыток регистрации на одном форуме
MANUAL_CAPTCHA_TIMEOUT = 300  # 5 мин на ручную капчу
MANUAL_FIELD_FILL_TIMEOUT = 120  # 2 мин на ручное заполнение полей
TAB_TIMEOUT_SECONDS = 300  # Общий таймаут на регистрацию
FIND_REGISTRATION_PAGE_TIMEOUT = 60  # Поиск страницы /register

# === ПРОКСИ ===
REQUIRE_PROXY_PER_USER = True  # Требовать proxy_id у пользователя
ALLOW_GLOBAL_FALLBACK_PROXY = False  # Не использовать fallback
PROXY_RETRY_ON_FAILURE = True  # Попытка переподключения при ошибке прокси

# === БРАУЗЕР И ПРОФИЛИ ===
BROWSER_MODE = "one_per_user"  # Один браузер на пользователя
PROFILE_PER_USER = True  # Один профиль на пользователя
PROFILE_PERSISTENCE = True  # Сохранять профиль навсегда
SAVE_PROFILE_METADATA = True  # Сохранять meta.json (proxy, forums)
CLOSE_BROWSER_AFTER_ALL_REGISTRATIONS = True  # Закрывать после завершения
SET_WINDOW_TITLE = True  # Устанавливать заголовок окна браузера

# === ТАБЫ ===
TAB_PER_REGISTRATION = True  # Один таб на регистрацию
CLOSE_TAB_AFTER_REGISTRATION = True  # Закрывать таб после обработки
CLEAN_TAB_BEFORE_OPEN = False  # НЕ очищать cookies (реалистичность)

# === МОНИТОРИНГ ===
SHOW_BROWSER_WINDOWS = True  # Не скрывать окна (headless=False)
WINDOW_TITLE_FORMAT = "{username} | {forum} | {status}"

# === ОТЧЁТНОСТЬ ===
SHOW_FINAL_REPORT = True
SHOW_FAILED_DETAILS = True

# === ОШИБКИ (РУССКИЙ ТЕКСТ) ===
ERROR_MESSAGES_RU = {
    "captcha_timeout": "Не удалось решить капчу: истекло время ожидания",
    "captcha_failed": "Не удалось решить капчу: все провайдеры исчерпаны",
    "captcha_manual_timeout": "Ручной ввод капчи не выполнен за отведённое время",
    "fields_not_found": "Не найдены поля формы регистрации",
    "registration_page_not_found": "Страница регистрации не найдена на форуме",
    "registration_failed": "Регистрация не удалась: форум вернул ошибку",
    "account_exists": "Аккаунт с таким именем или email уже существует",
    "proxy_connection": "Ошибка подключения через прокси",
    "proxy_failed": "Прокси не доступен или заблокирован",
    "proxy_blocked": "Прокси заблокирован форумом",
    "timeout": "Превышено время ожидания ответа от форума",
    "network_error": "Ошибка сети: нет соединения",
    "browser_crash": "Браузер аварийно завершил работу",
    "manual_fill_timeout": "Ручное заполнение полей не выполнено за отведённое время",
    "unknown": "Неизвестная ошибка"
}
```

### 4.2. Формат `data/accounts.json`
```json
[
    {
        "username": "testuser1",
        "email": "test1@example.com",
        "password": "StrongPass123",
        "proxy_id": 0,
        "custom_fields": {
            "referral": "REF123",
            "city": "Moscow"
        },
        "status": "pending",
        "attempts": 0,
        "last_attempt": null
    },
    {
        "username": "testuser2",
        "email": "test2@example.com",
        "password": "AnotherPass456",
        "proxy_id": 1,
        "custom_fields": {},
        "status": "pending",
        "attempts": 0,
        "last_attempt": null
    }
]
```

### 4.3. Формат входного файла (`results_new.txt`)
```
# Список форумов для регистрации (из парсера)
# Формат: URL [комментарий] — разделитель пробел
https://forum1.com/
https://forum2.com/showthread.php?t=12345  # ссылка на тему
https://forum3.com/board/general
# https://forum4.com/  # закомментировано — пропускаем
https://forum5.com/
```

### 4.4. Формат выходных файлов (per user)
```
# results_ok_user1.txt (успех)
# Формат: URL timestamp
https://forum1.com/ 2026-02-24T15:30:22
https://forum2.com/ 2026-02-24T15:35:10

# results_bad_user1.txt (неудачи)
# Формат: URL error_code timestamp
https://forum3.com/ captcha_timeout 2026-02-24T15:40:12
https://forum4.com/ fields_not_found 2026-02-24T15:45:33
```

## 5. Класс `MainOrchestrator`

### 5.1. Инициализация
```python
class MainOrchestrator:
    def __init__(self, config: dict | None = None):
        """
        Args:
            config: словарь конфигурации (загружается из settings.py, если не передан).
        """
```
- Загружает конфигурацию из `config/settings.py`.
- Инициализирует компоненты:
  - `ProxyManager` (загрузка прокси из `proxies.txt`)
  - `CaptchaExtensionHelper` (фасад для решения капч)
  - `BrowserController` (будет создан per user)
  - `RegistrationController` (будет создан per user)
- Загружает пользователей из `data/accounts.json`.
- Загружает список форумов из `data/results_new.txt`.

### 5.2. Основной метод: `async run() -> dict`
**Алгоритм:**

1. **Загрузка данных:**
   - Прочитать `results_new.txt` → `all_forums: list[str]`
     - Пропускать строки, начинающиеся с `#`
     - Пропускать пустые строки
     - Разделять по первому пробелу: `url = line.split(" ", 1)[0]`
   - Прочитать `data/accounts.json` → `users: list[dict]`
   - Для каждого пользователя загрузить прокси по `proxy_id` из `ProxyManager`.

2. **Resume logic (после сбоя):**
   - Для каждого пользователя:
     - Сформировать имя файла: `ok_file = f"results_ok_{sanitize_filename(username)}.txt"`
     - Если файл существует И не пуст (размер > 0):
       - Прочитать последнюю строку файла
       - Извлечь URL: `last_url = line.split(" ", 1)[0]`
       - Найти индекс `last_url` в `all_forums`
       - Начать обработку с `index + 1`
     - Иначе: начать с индекса 0
   - Логировать: `"User {username} resuming from forum {index}"`

3. **Распределение задач:**
   - Если `EACH_USER_ALL_FORUMS == True`:
     - Каждый пользователь получает **полный список форумов** (с учётом resume)
     - Пользователи работают параллельно, независимо
   - Создать очередь задач per user: `user_queues: dict[str, list[str]]`

4. **Параллельная обработка:**
   - Запустить `MAX_CONCURRENT_USERS` асинхронных задач
   - Каждая задача: `async _process_user(username, user_data, forum_queue)`
   - Использовать `asyncio.Semaphore(MAX_CONCURRENT_USERS)` для ограничения

5. **Завершение:**
   - Дождаться завершения всех пользователей
   - Сформировать итоговую статистику
   - Вывести финальный отчёт (если `SHOW_FINAL_REPORT == True`)
   - Вернуть словарь со статистикой

### 5.3. Метод: `async _process_user(username, user_data, forum_queue) -> dict`
**Алгоритм для одного пользователя:**

1. **Инициализация браузера:**
   - Сформировать безопасное имя: `safe_username = sanitize_filename(username)`
   - Создать профиль: `profile_name = "{safe_username}_{timestamp}"`
   - Запустить браузер с профилем и прокси пользователя
   - Установить заголовок окна (если `SET_WINDOW_TITLE == True`):
     - Через `browser.set_window_title(title)` или `page.evaluate("document.title = '...'")`
     - Формат: `WINDOW_TITLE_FORMAT.format(username=username, forum="init", status="running")`

2. **Подготовка конфигурации для RegistrationController:**
   - Передать таймауты из настроек:
     ```python
     reg_config = {
         "manual_captcha_timeout": settings.MANUAL_CAPTCHA_TIMEOUT,
         "manual_field_fill_timeout": settings.MANUAL_FIELD_FILL_TIMEOUT,
         "find_registration_page_timeout": settings.FIND_REGISTRATION_PAGE_TIMEOUT,
         "max_retries": settings.MAX_REGISTRATION_RETRIES,
     }
     ```
   - Создать `RegistrationController` с этой конфигурацией

3. **Обработка каждого форума в очереди:**
   - Для каждого `forum_url` в `forum_queue`:
     - Открыть новый таб с `forum_url`
     - Обновить заголовок окна: `status = forum_url`
     - Вызвать `RegistrationController.register(...)` с retry-логикой
     - Сформировать timestamp: `datetime.now().strftime(settings.OUTPUT_TIMESTAMP_FORMAT)`
     - **При успехе:**
       - Записать в `results_ok_{safe_username}.txt`: `{url} {timestamp}`
       - Flush сразу (если `RESULTS_FLUSH_IMMEDIATELY == True`)
       - Логировать: `"User {username} registered on {forum}"`
     - **При неудаче:**
       - Определить error_code (из результата или исключения)
       - Записать в `results_bad_{safe_username}.txt`: `{url} {error_code} {timestamp}`
       - Flush сразу
       - Логировать: `"User {username} failed on {forum}: {error_code}"`
     - Закрыть таб (профиль остаётся, cookies сохраняются)

4. **Завершение пользователя:**
   - Если `CLOSE_BROWSER_AFTER_ALL_REGISTRATIONS == True`:
     - Закрыть браузер
   - Сохранить метаданные профиля в `meta.json`:
     ```json
     {
         "username": "user1",
         "proxy_id": 0,
         "proxy_value": "http://user:pass@ip:port",
         "created_at": "2026-02-24T15:30:22",
         "forums_registered": ["forum1.com", "forum2.com"]
     }
     ```
   - Вернуть статистику пользователя

### 5.4. Метод: `_sanitize_filename(username: str) -> str`
**Назначение:** Создание безопасного имени файла из username.

**Алгоритм:**
1. Заменить недопустимые символы на `_`: `< > : " / \\ | ? *`
2. Обрезать пробелы в начале и конце
3. Вернуть результат

**Пример:**
```python
sanitize_filename("user<1>")  # → "user_1_"
sanitize_filename("test@user")  # → "test@user" (@ допустим)
```

### 5.5. Метод: `_parse_file_line(line: str) -> str | None`
**Назначение:** Универсальный парсер строк для всех файлов результатов.

**Алгоритм:**
1. Если строка начинается с `INPUT_COMMENT_PREFIX` → вернуть `None`
2. Если строка пустая → вернуть `None`
3. Разделить по первому пробелу: `parts = line.split(" ", 1)`
4. Вернуть `parts[0]` (URL)

**Применение:**
- Чтение `results_new.txt`
- Чтение `results_ok_*.txt` (для resume)
- Чтение `results_bad_*.txt` (для отчёта)

### 5.6. Метод: `_write_result(filepath: str, url: str, extra_data: list[str]) -> None`
**Назначение:** Запись результата в файл.

**Алгоритм:**
1. Сформировать строку: `line = f"{url} {' '.join(extra_data)}\n"`
2. Открыть файл в режиме append
3. Записать строку
4. Если `RESULTS_FLUSH_IMMEDIATELY == True` → сделать `flush()`

**Примеры:**
- Успех: `_write_result("results_ok_user1.txt", url, [timestamp])`
- Неудача: `_write_result("results_bad_user1.txt", url, [error_code, timestamp])`

### 5.7. Метод: `_generate_final_report() -> str`
**Назначение:** Формирование итогового отчёта для вывода на экран.

**Структура отчёта:**
```
═══════════════════════════════════════════════════════════
                    REGISTRATION REPORT
═══════════════════════════════════════════════════════════

Всего форумов в очереди:     100
Пользователей:               2
Всего регистраций:           200 (100 форумов × 2 пользователя)
Обработано:                  200
Успешно:                     174
Неудачи:                     26

Капчи:
  Решено автоматически:      150
  Решено вручную:            24
  Не решено:                 26

Профили создано:             2

───────────────────────────────────────────────────────────
                    FAILED REGISTRATIONS
───────────────────────────────────────────────────────────

Пользователь: user1
| Форум                    | Причина                          |
|--------------------------|----------------------------------|
| forum3.com/              | Не удалось решить капчу: таймаут |
| forum4.com/              | Не найдены поля формы            |

Пользователь: user2
| Форум                    | Причина                          |
|--------------------------|----------------------------------|
| forum5.com/              | Ошибка подключения через прокси  |

═══════════════════════════════════════════════════════════
```

**Алгоритм:**
1. Прочитать все `results_ok_*.txt` → подсчитать успехи
2. Прочитать все `results_bad_*.txt` → подсчитать неудачи, сгруппировать по пользователям
3. Для каждого error_code в неудачах:
   - Взять русский текст из `ERROR_MESSAGES_RU`
4. Сформировать строку отчёта с таблицами
5. Вернуть строку

### 5.8. Метод: `async shutdown() -> None`
**Назначение:** Корректное завершение работы.

**Алгоритм:**
1. Сигнализировать всем задачам пользователей о остановке
2. Дождаться завершения текущих регистраций (таймаут: `GRACEFUL_SHUTDOWN_TIMEOUT`)
3. Закрыть все браузеры
4. Сохранить метаданные профилей
5. Сформировать и вывести отчёт (если не был выведен)
6. Логировать: `"Shutdown complete"`

## 6. Обработка ошибок и retry

### 6.1. Retry логика на уровне регистрации
```
Для каждого форума:
    attempt = 0
    while attempt < MAX_REGISTRATION_RETRIES:
        result = await RegistrationController.register(...)
        if result.success:
            Записать в results_ok
            break
        else:
            attempt += 1
            if attempt >= MAX_REGISTRATION_RETRIES:
                Записать в results_bad с reason
            else:
                Логировать: "Retry {attempt}/{MAX_REGISTRATION_RETRIES}"
                Подождать экспоненциальную задержку (2^attempt сек)
```

### 6.2. Типы ошибок и действия
| Тип ошибки | Действие |
|------------|----------|
| `captcha_timeout` | Retry с другим провайдером капчи |
| `captcha_failed` | Retry или записать в failed (если все провайдеры исчерпаны) |
| `fields_not_found` | Записать в failed (проблема шаблона, retry не поможет) |
| `registration_page_not_found` | Записать в failed |
| `account_exists` | Записать в failed (не retry) |
| `proxy_connection` | Retry с тем же прокси (временная ошибка) |
| `proxy_failed` | Записать в failed (прокси не доступен) |
| `proxy_blocked` | Записать в failed (сменить прокси нельзя — он привязан к профилю) |
| `timeout` / `network_error` | Retry с экспоненциальной задержкой |
| `browser_crash` | Перезапустить браузер с тем же профилем, retry |
| `manual_fill_timeout` | Записать в failed (пользователь не заполнил поля за таймаут) |

## 7. Интеграция с другими модулями

### 7.1. С `RegistrationController` (Промт #4)
- `MainOrchestrator` создаёт экземпляр per user
- Передаёт: `browser_controller`, `template_manager`, `selector_finder`, `page`, `config`
- **В config передаются таймауты из settings.py:**
  - `manual_captcha_timeout`
  - `manual_field_fill_timeout`
  - `find_registration_page_timeout`
  - `max_retries`
- Вызывает: `await registration_controller.register(account_data)`
- Получает: `RegistrationResult` (success, reason, screenshot, ...)

### 7.2. С `CaptchaExtensionHelper` (Промт #6)
- `MainOrchestrator` создаёт один экземпляр (shared)
- Передаёт: `page`, `stats_callback`
- Вызывается внутри `RegistrationController._handle_captcha()`
- Статистика капч агрегируется в финальном отчёте

### 7.3. С `ProxyManager` (Промт #5)
- `MainOrchestrator` создаёт один экземпляр
- Загружает прокси из `proxies.txt`
- Для каждого пользователя: `proxy = proxy_manager.proxies[proxy_id]`
- При ошибке прокси: error_code = `"proxy_failed"`

### 7.4. С `BrowserController` (Промт #2)
- `MainOrchestrator` создаёт экземпляр per user
- Передаёт: `proxy`, `profile_path`, `config`
- **Требование:** BrowserController должен поддерживать установку заголовка окна:
  - Через `page.evaluate("document.title = '...'")`
- Управляет жизненным циклом: создать → использовать → закрыть

## 8. Тестирование

### 8.1. Unit-тесты (с моками)
- Мок `RegistrationController.register()` → эмуляция успеха/неудачи
- Мок `BrowserController` → эмуляция открытия/закрытия табов
- Проверить resume logic (последняя строка в results_ok)
- **Проверить обработку пустого файла results_ok** (начинать с индекса 0)
- Проверить парсинг файлов (`_parse_file_line`)
- Проверить запись результатов (`_write_result`)
- Проверить формирование отчёта (`_generate_final_report`)
- Проверить санитизацию имён файлов (`_sanitize_filename`)

### 8.2. Интеграционные тесты (опционально)
- Пометить `@pytest.mark.integration`
- Использовать 1-2 реальных форума (демо)
- Требуют реальные аккаунты, прокси, API-ключи капчи
- Запускать вручную

### 8.3. Тестирование resume после сбоя
- Создать `results_new.txt` с 10 форумами
- Запустить оркестратор, прервать после 5 форумов
- Перезапустить → убедиться, что продолжил с 6-го форума
- **Проверить, что форумы 1-5 не обработаны повторно**
- Проверить корректность при пустом файле results_ok

## 9. Критерии готовности

- [ ] Класс `MainOrchestrator` реализован с указанными методами
- [ ] Чтение `results_new.txt` с пропуском `#` комментариев и парсингом по пробелу
- [ ] Пользователи загружаются из `data/accounts.json` (JSON, не БД)
- [ ] Каждый пользователь проходит все форумы (параллельно, независимо)
- [ ] Resume logic: найти последний URL в `results_ok_USER.txt`, продолжить со следующей строки
- [ ] **Обработка пустого файла results_ok** (начинать с индекса 0)
- [ ] Запись в `results_ok_USER.txt` / `results_bad_USER.txt` сразу после каждой регистрации (flush)
- [ ] Формат вывода: разделитель пробел (`URL timestamp` или `URL error_code timestamp`)
- [ ] **Безопасные имена файлов** (санитизация username)
- [ ] Ошибки: код в файле, русский текст в отчёте (словарь `ERROR_MESSAGES_RU`)
- [ ] **Добавлена ошибка `proxy_failed`**
- [ ] Retry логика с настраиваемым `MAX_REGISTRATION_RETRIES`
- [ ] **Таймауты передаются в RegistrationController через config**
- [ ] Таймауты: `MANUAL_CAPTCHA_TIMEOUT`, `MANUAL_FIELD_FILL_TIMEOUT`, `TAB_TIMEOUT_SECONDS`
- [ ] **manual_fill_timeout логика в RegistrationController**
- [ ] 1 браузер на пользователя с персистентным профилем + прокси
- [ ] Cookies сохраняются между табами (реалистичность)
- [ ] **Заголовки окон браузеров** (через set_window_title или JS)
- [ ] Визуальный мониторинг (отдельные окна, заголовки с информацией)
- [ ] Финальный отчёт: статистика + детали неудач на русском
- [ ] **Timestamp через datetime.now().strftime(OUTPUT_TIMESTAMP_FORMAT)**
- [ ] Graceful shutdown с сохранением прогресса
- [ ] Все публичные методы имеют docstrings и аннотации типов
- [ ] Логирование через `loguru` с разными уровнями

## 10. Согласованность с предыдущими промтами

### 10.1. Типы данных
- `RegistrationResult` из Промта #4 используется для получения результатов регистрации
- `CaptchaResult` из Промта #6 используется для статистики капч
- Формат файлов согласован с выходными данными парсера (Промт 1)

### 10.2. Конфигурация
- API-ключи в `.env` (как в Промте #5, #6)
- Логика в `config/settings.py` (единый источник настроек)
- **Таймауты синхронизированы между MainOrchestrator и RegistrationController**
- Пути через `pathlib.Path` для кроссплатформенности

### 10.3. Интеграция
- `MainOrchestrator` → создаёт и координирует все компоненты
- `RegistrationController` → выполняет регистрацию на одном форуме (получает таймауты из config)
- `CaptchaExtensionHelper` → решает капчи
- `ProxyManager` → предоставляет прокси
- `BrowserController` → управляет браузером (поддерживает set_window_title)

## 11. Финальные инструкции

- Не использовать SQLite — все данные в JSON и TXT файлах
- Единый разделитель для всех файлов: пробел (не `|`)
- Парсинг: `line.split(" ", 1)[0]` для извлечения URL
- Resume: по последней строке в `results_ok_USER.txt`, не проверять все URL
- **Обработка пустого файла:** если файл пуст → начинать с индекса 0
- Запись результатов: сразу после каждой регистрации (flush)
- **Timestamp:** `datetime.now().strftime(OUTPUT_TIMESTAMP_FORMAT)`
- **Безопасные имена файлов:** санитизация username перед использованием в имени файла
- Ошибки: короткий код в файле, русский текст в отчёте
- **Добавлена ошибка `proxy_failed`** для случаев недоступности прокси
- Профили браузеров: персистентные, с meta.json
- Прокси: привязан к пользователю навсегда (через proxy_id)
- Браузеры: один на пользователя, отдельные окна для мониторинга
- **Заголовки окон:** устанавливать через set_window_title или JS
- Табы: один активный, закрывается после регистрации, cookies сохраняются
- **Таймауты передаются в RegistrationController через config**
- Graceful shutdown: сохранить прогресс, закрыть браузеры, вывести отчёт

После выполнения этого промта система будет полностью готова к массовой регистрации аккаунтов на форумах с отказоустойчивостью, визуальным мониторингом и детальным отчётом.
