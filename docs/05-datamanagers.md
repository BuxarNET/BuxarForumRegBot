# Промт 5: DataManagers — ProxyManager и AccountManager (JSON-версия)

## 1. Цель

Создать два модуля для управления вспомогательными данными:
- `ProxyManager` — загрузка, проверка и ротация прокси-серверов.
- `AccountManager` — работа с единым JSON-файлом аккаунтов и логирование результатов регистрации.

**Важно:** Не использовать SQLite — все данные хранятся в JSON-файлах.
Единый файл `data/accounts.json` является и источником аккаунтов для регистрации,
и хранилищем их статусов. Пользователь редактирует этот файл вручную когда скрипт остановлен.

Эти модули будут использоваться в главном оркестраторе (`MainOrchestrator`).

---

## 2. Общие требования

- Код на Python 3.12+ с асинхронным синтаксисом (`async/await`).
- В начале каждого файла: `from __future__ import annotations`.
- Использовать библиотеки: `aiofiles`, `aiohttp`, `loguru`, `pathlib`, `json`.
- Модули располагаются в `src/utils/proxy_manager.py` и `src/utils/account_manager.py`.
- Добавить docstrings для всех классов и публичных методов (Google style).
- Строгая типизация: использовать `| None`, `list[str]`, `TypedDict`.
- Логирование через `loguru.logger`.
- Покрыть ключевую логику тестами (pytest) с использованием моков и временных файлов.

---

## 3. Модуль proxy_manager.py

### 3.1. Класс `ProxyManager`

**Расположение:** `src/utils/proxy_manager.py`

#### `__init__(self, proxy_file: str | Path, check_timeout: int = 5)`
- Сохраняет путь к файлу с прокси в `self.proxy_file`.
- `self.check_timeout` — таймаут проверки прокси (сек).
- Инициализирует `self.proxies: list[str] = []` и `self.current_index: int = 0`.
- Не выполняет загрузку при инициализации.

#### `async load_proxies(self) -> None`
- Асинхронно читает файл `self.proxy_file` через `aiofiles`.
- Поддерживаемые форматы строк:
  - `protocol://user:pass@host:port` (например `http://user:pass@127.0.0.1:8080`)
  - `protocol://host:port` (например `socks5://127.0.0.1:1080`)
  - `host:port` — если протокол не указан, автоматически добавляется `http://` с логированием `warning`
- Пустые строки и комментарии (начинающиеся с `#`) игнорировать.
- Сохраняет список прокси в `self.proxies`.
- Если файл не найден — логировать `warning` и установить `self.proxies = []`.
- Логировать количество загруженных прокси.

#### `async check_proxy(self, proxy: str) -> bool`
- Проверяет работоспособность прокси через подключение к `http://httpbin.org/ip`.
- Использовать `aiohttp.ClientSession` с параметром `proxy=proxy`.
- Таймаут — `self.check_timeout` через `aiohttp.ClientTimeout`.
- Для SOCKS-прокси использовать `aiohttp-socks` (обработать `ImportError` с `warning`).
- Возвращает `True` если статус 200 и получен корректный JSON.
- При любой ошибке возвращает `False` и логирует `warning`.

#### `async get_next_proxy(self, check: bool = True) -> str | None`
- Возвращает следующий прокси по кругу (round-robin).
- Если `check=True` — вызывает `check_proxy` для каждого прокси, пропускает нерабочие.
- Если после полного оборота не найден ни один рабочий прокси — возвращает `None`.
- Ограничить количество попыток числом прокси в списке (не зацикливаться).
- Логирует используемый прокси.

#### `async refresh_proxies(self) -> None`
- Перезагружает список прокси из файла (вызывает `load_proxies`).
- Сбрасывает `self.current_index = 0`.

### 3.2. Пример использования

````python
proxy_manager = ProxyManager("data/proxies.txt")
await proxy_manager.load_proxies()
proxy = await proxy_manager.get_next_proxy()
if proxy:
    async with BrowserController(proxy=proxy) as browser:
        ...
````

---

## 4. Модуль account_manager.py

### 4.1. Импорты и TypedDict

````python
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import TypedDict, NotRequired

import aiofiles
from loguru import logger

# Импортируем общие типы из registration_controller
from controllers.registration_controller import RegistrationResult, AccountData
````

**Примечание:** `RegistrationResult` и `AccountData` определены в `registration_controller.py`
(Промт 4) и импортируются отсюда. Не дублировать определения.

Дополнительный тип для аккаунтов с служебными полями:

````python
class StoredAccount(TypedDict):
    username: str
    email: str
    password: str
    proxy_id: int
    custom_fields: dict[str, str]
    status: str
    attempts: int
    last_attempt: str | None
````

### 4.2. Класс `AccountManager`

**Расположение:** `src/utils/account_manager.py`

#### `__init__(self, accounts_file: str | Path = "data/accounts.json", log_file: str | Path = "data/registration_log.json")`
- Сохраняет пути к JSON-файлам через `pathlib.Path`.
- Инициализирует кэш: `self._accounts_cache: list[StoredAccount] | None = None`.

#### `async _load_json(self, filepath: Path) -> list`
- Внутренний метод для асинхронной загрузки JSON.
- Всегда возвращает `list` (при отсутствии файла — пустой список `[]`).
- Обрабатывает `FileNotFoundError` и `JSONDecodeError` с логированием `warning`.
- Не выбрасывает исключения наружу.

#### `async _save_json(self, filepath: Path, data: list) -> None`
- Внутренний метод для асинхронного сохранения JSON через `aiofiles`.
- Создаёт родительские директории при необходимости (`mkdir(parents=True, exist_ok=True)`).
- Использует `indent=2` для читаемости.

#### `async log_registration(self, result: RegistrationResult, account_data: AccountData | None = None) -> None`
- Сохраняет результат регистрации в `registration_log.json`.
- Если `account_data` передан — использует его, иначе извлекает данные из `result["form_data"]`.
- Добавляет запись в конец списка и сохраняет файл.
- Формат записи:

````json
{
    "timestamp": "2026-02-24T15:30:22",
    "username": "user1",
    "email": "user1@example.com",
    "success": true,
    "reason": null,
    "template_used": "XenForo",
    "screenshot_path": "data/screenshots/success_user1_20260224.png",
    "form_data": {"username": "user1", "email": "..."}
}
````

#### `async get_registration_log(self, username: str | None = None, success: bool | None = None) -> list[dict]`
- Возвращает список записей из лога с опциональной фильтрацией.
- `username` — фильтровать по имени пользователя.
- `success` — фильтровать по статусу (`True`/`False`).
- Возвращает отсортированный список по `timestamp` (новые первыми).

#### `async get_pending_accounts(self, limit: int = 100) -> list[StoredAccount]`
- Возвращает список аккаунтов со статусом `pending` из `accounts.json`.
- Параметр `limit` ограничивает количество возвращаемых записей.
- Сортировка: сначала аккаунты с `attempts == 0`, затем по возрастанию `last_attempt`.
- Перед возвратом проверяет наличие обязательных полей (`username`, `email`, `password`, `proxy_id`).
- Аккаунты с отсутствующими обязательными полями пропускать с логированием `warning`.

#### `async update_account_status(self, username: str, status: str, reason: str | None = None, proxy: str | None = None) -> None`
- Обновляет статус аккаунта в `accounts.json`.
- Увеличивает `attempts` на 1.
- Обновляет `last_attempt = datetime.now().isoformat()`.
- Опционально сохраняет `reason` и `proxy_used` в поле `last_error`.
- Сохраняет изменения в файл.
- Инвалидирует кэш (`self._accounts_cache = None`).

#### `async export_failed_accounts(self, output_path: str | Path) -> int`
- Экспортирует аккаунты со статусом `failed` или `banned` в JSON-файл.
- Возвращает количество экспортированных записей.

### 4.3. Пример использования

````python
account_manager = AccountManager()

# Получение очереди аккаунтов
pending = await account_manager.get_pending_accounts(limit=50)

# Логирование результата регистрации
await account_manager.log_registration(result, account_data)

# Обновление статуса после регистрации
await account_manager.update_account_status(
    username="user1",
    status="registered" if result["success"] else "failed",
    reason=result["reason"]
)
```

---

## 5. Формат файлов

### 5.1. `data/accounts.json`

Единый рабочий файл. Пользователь редактирует его вручную когда скрипт остановлен.
Скрипт читает и обновляет его в процессе работы.

Обязательные поля: `username`, `email`, `password`, `proxy_id`.
При отсутствии любого обязательного поля — аккаунт пропускается с логированием `warning`.

````json
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
````

**Возможные значения `status`:**
- `pending` — ожидает регистрации
- `registered` — успешно зарегистрирован
- `failed` — попытка не удалась
- `banned` — забанен на форуме

### 5.2. `data/proxies.txt`

````
# Комментарии игнорируются
http://user:pass@127.0.0.1:8080
socks5://127.0.0.1:1080
192.168.1.1:3128
````

### 5.3. `data/registration_log.json`

Лог всех попыток регистрации. Только для чтения и анализа, скрипт только дописывает в конец.

---

## 6. Тестирование

### 6.1. Unit-тесты для ProxyManager

- Создать временный файл с прокси (использовать `tmp_path` fixture).
- Проверить загрузку и парсинг всех форматов (с протоколом, без, с авторизацией).
- Проверить игнорирование комментариев (`#`) и пустых строк.
- Проверить `get_next_proxy` без проверки (`check=False`).
- Проверить `get_next_proxy` с проверкой: мок неудачных проверок, убедиться что нерабочие пропускаются.
- Проверить `refresh_proxies`: изменение файла → перезагрузка списка.

### 6.2. Unit-тесты для AccountManager

- Использовать временные JSON-файлы (`tmp_path`).
- Проверить `log_registration`: запись результата, корректная сериализация.
- Проверить `get_pending_accounts`: фильтрация по статусу, сортировка, пропуск невалидных.
- Проверить `update_account_status`: увеличение `attempts`, обновление `last_attempt`.
- Проверить `export_failed_accounts`: фильтрация и экспорт.

### 6.3. Интеграционные тесты (опционально)

- Пометить маркером `@pytest.mark.integration`.
- Проверить совместную работу `ProxyManager` и `BrowserController` на реальном прокси.
- Проверить `AccountManager` с реальными JSON-файлами: запись → чтение → обновление.

---

## 7. Критерии готовности

- [ ] `ProxyManager` загружает прокси из файла, игнорируя комментарии и пустые строки.
- [ ] `ProxyManager` корректно парсит форматы: с протоколом, без, с авторизацией.
- [ ] `ProxyManager.get_next_proxy` возвращает прокси по кругу, пропуская нерабочие.
- [ ] `ProxyManager.check_proxy` использует `aiohttp` с таймаутом и обработкой ошибок.
- [ ] `AccountManager` работает с единым `accounts.json` асинхронно через `aiofiles`.
- [ ] `AccountManager._load_json` всегда возвращает `list`, никогда не выбрасывает исключения.
- [ ] `AccountManager.log_registration` корректно сохраняет `RegistrationResult`.
- [ ] `AccountManager.get_pending_accounts` возвращает `list[StoredAccount]` с фильтрацией и сортировкой.
- [ ] Аккаунты без обязательных полей пропускаются с логированием `warning`.
- [ ] `RegistrationResult` и `AccountData` импортируются из `registration_controller.py`, не дублируются.
- [ ] Все публичные методы имеют docstrings и корректные аннотации типов.
- [ ] Unit-тесты покрывают основную логику и проходят успешно.
- [ ] Не используется SQLite — только JSON-файлы.

---

## 8. Согласованность с предыдущими промтами

### 8.1. Типы данных
- `RegistrationResult` и `AccountData` определены в Промте 4 (`registration_controller.py`).
- `AccountManager` импортирует их оттуда — единый источник истины.
- `StoredAccount` расширяет `AccountData` служебными полями и определяется в `account_manager.py`.

### 8.2. Интеграция с RegistrationController
- `AccountManager.log_registration()` принимает результат `RegistrationController.register()` напрямую.
- `ProxyManager.get_next_proxy()` возвращает строку совместимую с параметром `proxy` в `BrowserController`.

### 8.3. Привязка прокси к аккаунту
- Каждый аккаунт в `accounts.json` содержит `proxy_id` — индекс прокси из списка `ProxyManager`.
- `MainOrchestrator` при запуске регистрации берёт `proxy_id` из аккаунта и передаёт в `BrowserController`.
- Привязка гарантирует что каждый аккаунт всегда регистрируется через один и тот же IP.

### 8.4. Пути и конфигурация
- `data/accounts.json` — единый файл аккаунтов (чтение и запись).
- `data/proxies.txt` — список прокси.
- `data/registration_log.json` — лог регистраций (только дозапись).
- Все пути обрабатываются через `pathlib.Path`.

После выполнения этого промта можно переходить к `CaptchaExtensionHelper` (Промт 6) и `MainOrchestrator` (Промт 7).
