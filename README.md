# BuxarForumRegBot

<div align="center">

[🇷🇺 Русский](README.md) | [🇬🇧 English](README_ENG.md)

[![Python](https://img.shields.io/badge/Python-3.12+-blue.svg)](https://www.python.org/)
[![License](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Tests](https://img.shields.io/badge/Tests-139%20passed-brightgreen.svg)]()
[![Pydoll](https://img.shields.io/badge/Browser-Pydoll-orange.svg)](https://github.com/autoscrape-labs/pydoll)

**Система автоматической массовой регистрации аккаунтов на форумах**

*Поддержка множества движков форумов · Автоматическое решение капч · Параллельная обработка · Отказоустойчивость*

</div>

---

## 📋 Содержание

- [Описание](#-описание)
- [Возможности](#-возможности)
- [Требования](#-требования)
- [Установка](#-установка)
- [Быстрый старт](#-быстрый-старт)
- [Структура проекта](#-структура-проекта)
- [Конфигурация](#-конфигурация)
- [Форматы файлов](#-форматы-файлов)
- [Тестирование](#-тестирование)
- [Добавление нового провайдера капч](#-добавление-нового-провайдера-капч)
- [Решение проблем](#-решение-проблем)
- [Вклад в проект](#-вклад-в-проект)
- [Пожертвования](#-пожертвования)
- [Лицензия](#-лицензия)

---

## 📖 Описание

**BuxarForumRegBot** — асинхронная система для массовой автоматической регистрации аккаунтов на форумах. Поддерживает популярные движки (phpBB, XenForo, vBulletin, SMF) через систему шаблонов, а для неизвестных форумов автоматически анализирует HTML и находит поля регистрации эвристически.

Система работает в связке с реальным браузером Chrome через библиотеку Pydoll, что обеспечивает максимальную совместимость с защитами форумов и реалистичное поведение.

---

## ✨ Возможности

### Регистрация
- 🌐 Поддержка популярных движков форумов: **phpBB, XenForo, vBulletin, SMF**
- 🔍 Эвристический анализ неизвестных форумов — автоматический поиск полей формы
- 👥 Параллельная работа нескольких пользователей одновременно (настраивается)
- 🔄 Resume после сбоя — продолжает с места остановки без повторной обработки
- 🔁 Retry с экспоненциальной задержкой при временных ошибках

### Капча
- 🤖 Автоматическое решение через цепочку провайдеров с fallback
- 🖐 Ручной режим — ожидание решения пользователем в открытом браузере
- 💰 Умный выбор провайдера по приоритету или минимальной цене
- 📊 Автоматический расчёт реальной стоимости на основе изменения баланса
- 🔌 Поддержка 7 провайдеров: AZCaptcha, CapSolver, 2Captcha, TrueCaptcha, DeathByCaptcha, SolveCaptcha, EndCaptcha

### Браузер и профили
- 🖥 Отдельный браузер с персистентным профилем для каждого пользователя
- 🍪 Сохранение cookies между вкладками (реалистичное поведение)
- 🔒 Изоляция пользователей — каждый работает в своём профиле и прокси
- 📌 Информативные заголовки окон для визуального мониторинга

### Результаты и отчёты
- 📝 Запись результатов сразу после каждой регистрации (с flush)
- 📊 Детальный финальный отчёт с таблицами неудач на русском языке
- 📸 Автоматические скриншоты при ошибках
- 🛑 Graceful shutdown — корректное завершение с сохранением прогресса

---

## 💻 Требования

- **Python** 3.12+
- **Chromium** или **Google Chrome** (устанавливается отдельно)
- **uv** (рекомендуется) или **pip**
- Операционная система: Linux, macOS, Windows

---

## 🚀 Установка

### 1. Установка uv (рекомендуется)

`uv` — современный быстрый менеджер пакетов Python, замена pip + venv.

**Linux / macOS:**
```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

**Windows (PowerShell):**
```powershell
powershell -c "irm https://astral.sh/uv/install.ps1 | iex"
```

После установки перезапустите терминал или выполните:
```bash
source $HOME/.local/bin/env   # Linux/macOS
```

### 2. Клонирование репозитория

```bash
git clone https://github.com/buxar/BuxarForumRegBot.git
cd BuxarForumRegBot
```

### 3. Создание виртуального окружения и установка зависимостей

**С uv (рекомендуется):**
```bash
uv venv .venv
source .venv/bin/activate        # Linux/macOS
.venv\Scripts\activate           # Windows

uv pip install -r requirements.txt
```

**С pip (альтернатива):**
```bash
python -m venv .venv
source .venv/bin/activate        # Linux/macOS
.venv\Scripts\activate           # Windows

pip install -r requirements.txt
```

### 4. Настройка конфигурации

```bash
cp .env.example .env
```

Откройте `.env` и добавьте API-ключи нужных капча-провайдеров:

```env
# Добавьте ключи только тех провайдеров которые используете
AZCAPTCHA_API_KEY=ваш_ключ
CAPSOLVER_API_KEY=ваш_ключ
TWOCAPTCHA_API_KEY=ваш_ключ
TRUECAPTCHA_API_KEY=ваш_ключ
DEATHBYCAPTCHA_API_KEY=ваш_ключ
SOLVECAPTCHA_API_KEY=ваш_ключ
ENDCAPTCHA_API_KEY=ваш_ключ
```

> Если API-ключи не заданы — система автоматически переключится в ручной режим решения капч.

---

## ⚡ Быстрый старт

### Подготовка данных

**`src/data/accounts.json`** — список аккаунтов:
```json
[
    {
        "username": "myuser1",
        "email": "user1@example.com",
        "password": "StrongPass123",
        "proxy_id": 0,
        "custom_fields": {},
        "status": "pending",
        "attempts": 0,
        "last_attempt": null
    }
]
```

**`src/data/proxies.txt`** — список прокси (один на строку):
```
http://user:pass@1.2.3.4:8080
http://5.6.7.8:3128
socks5://9.10.11.12:1080
```

**`src/data/results_new.txt`** — список форумов:
```
# Список форумов для регистрации
https://forum1.com/
https://forum2.com/register
https://forum3.com/
```

### Запуск

```bash
cd src

# Проверить окружение
python main.py --check

# Посмотреть план без запуска браузеров
python main.py --dry-run

# Запустить регистрацию
python main.py

# Сформировать отчёт по существующим результатам
python main.py --report
```

---

## 📁 Структура проекта

```
BuxarForumRegBot/
├── src/
│   ├── config/
│   │   ├── settings.py              # Все настройки системы
│   │   └── logging.conf             # Конфигурация логирования
│   ├── controllers/
│   │   ├── browser_controller.py    # Управление браузером Chrome (Pydoll)
│   │   └── registration_controller.py # Логика регистрации на форуме
│   ├── data/
│   │   ├── profiles/                # Персистентные профили браузеров
│   │   ├── screenshots/             # Скриншоты ошибок регистрации
│   │   ├── accounts.json            # Список аккаунтов
│   │   ├── proxies.txt              # Список прокси
│   │   └── results_new.txt          # Входной список форумов
│   ├── extensions/
│   │   └── nopecha_solver/          # Расширение NopeCHA для авто-капчи
│   ├── logs/                        # Логи работы системы
│   ├── orchestrator/
│   │   └── main_orchestrator.py     # Главный координирующий модуль
│   ├── templates/
│   │   ├── known_forums/            # Шаблоны известных движков форумов
│   │   │   ├── phpbb.json           # Шаблон phpBB
│   │   │   ├── smf.json             # Шаблон SMF
│   │   │   ├── vbulletin.json       # Шаблон vBulletin
│   │   │   └── xenforo.json         # Шаблон XenForo
│   │   ├── common_fields.json       # Общие правила полей форм
│   │   └── heuristic_rules.json     # Эвристические правила поиска полей
│   ├── utils/
│   │   ├── captcha_providers/       # Провайдеры решения капч
│   │   │   ├── implementations/
│   │   │   │   ├── az_captcha.py    # AZCaptcha
│   │   │   │   ├── cap_solver.py    # CapSolver
│   │   │   │   ├── two_captcha.py   # 2Captcha
│   │   │   │   ├── manual.py        # Ручной режим
│   │   │   │   └── _stubs.py        # Заглушки остальных провайдеров
│   │   │   ├── base.py              # Абстрактный класс провайдера
│   │   │   └── registry.py          # Реестр и цепочка провайдеров
│   │   ├── account_manager.py       # Управление аккаунтами
│   │   ├── captcha_helper.py        # Фасад для решения капч
│   │   └── proxy_manager.py         # Управление прокси
│   ├── main.py                      # Точка входа
│   ├── selector_finder.py           # Эвристический поиск полей форм
│   └── template_manager.py          # Управление шаблонами форумов
├── tests/
│   ├── integration/
│   │   └── test_registration_flow.py # Интеграционные тесты (ручной запуск)
│   ├── check_browser_controller.py   # Ручная проверка браузера
│   ├── check_setup.py                # Проверка окружения
│   ├── conftest.py                   # Общие фикстуры pytest
│   ├── test_captcha_helper.py        # Тесты капча-провайдеров
│   ├── test_datamanagers.py          # Тесты менеджеров данных
│   ├── test_integration.py           # Интеграционные тесты модулей
│   ├── test_main_orchestrator.py     # Тесты главного оркестратора
│   ├── test_registration_controller.py # Тесты контроллера регистрации
│   ├── test_selector_finder.py       # Тесты поиска полей
│   └── test_template_manager.py      # Тесты менеджера шаблонов
├── .env.example                      # Шаблон переменных окружения
├── pytest.ini                        # Конфигурация pytest
├── README.md                         # Документация (русский)
├── README_ENG.md                     # Документация (английский)
└── requirements.txt                  # Зависимости Python
```

---

## ⚙️ Конфигурация

Все настройки находятся в `src/config/settings.py`.

### 📂 Пути к файлам

| Параметр | По умолчанию | Описание |
|----------|-------------|----------|
| `ACCOUNTS_FILE` | `data/accounts.json` | Файл со списком аккаунтов |
| `PROXIES_FILE` | `data/proxies.txt` | Файл со списком прокси |
| `FORUMS_SOURCE_FILE` | `data/results_new.txt` | Входной список форумов |
| `PROFILES_DIR` | `data/profiles` | Директория профилей браузеров |
| `RESULTS_DIR` | `data` | Директория для файлов результатов |
| `RESULTS_FLUSH_IMMEDIATELY` | `True` | Записывать результаты сразу после каждой операции |

### ⚡ Параллелизм

| Параметр | По умолчанию | Описание |
|----------|-------------|----------|
| `MAX_CONCURRENT_USERS` | `5` | Максимум браузеров одновременно |
| `EACH_USER_ALL_FORUMS` | `True` | `True` — каждый пользователь проходит все форумы; `False` — форумы распределяются между пользователями |

### ⏱ Таймауты и повторы

| Параметр | По умолчанию | Описание |
|----------|-------------|----------|
| `MAX_REGISTRATION_RETRIES` | `3` | Попыток на один форум перед записью в неудачи |
| `MANUAL_CAPTCHA_TIMEOUT` | `300` | Секунд на ручное решение капчи (5 мин) |
| `MANUAL_FIELD_FILL_TIMEOUT` | `120` | Секунд на ручное заполнение полей (2 мин) |
| `TAB_TIMEOUT_SECONDS` | `300` | Общий таймаут одной регистрации (5 мин) |
| `FIND_REGISTRATION_PAGE_TIMEOUT` | `60` | Секунд на поиск страницы регистрации |
| `GRACEFUL_SHUTDOWN_TIMEOUT` | `60` | Секунд на корректное завершение при остановке |

### 🌐 Прокси

| Параметр | По умолчанию | Описание |
|----------|-------------|----------|
| `REQUIRE_PROXY_PER_USER` | `True` | Требовать прокси для каждого пользователя |
| `ALLOW_GLOBAL_FALLBACK_PROXY` | `False` | Использовать глобальный прокси если нет личного |
| `PROXY_RETRY_ON_FAILURE` | `True` | Повторять при ошибке прокси |

### 🖥 Браузер и профили

| Параметр | По умолчанию | Описание |
|----------|-------------|----------|
| `BROWSER_MODE` | `one_per_user` | Режим: `one_per_user` — отдельный браузер на пользователя |
| `PROFILE_PER_USER` | `True` | Создавать отдельный профиль для каждого пользователя |
| `PROFILE_PERSISTENCE` | `True` | Сохранять профиль после завершения (cookies, история) |
| `SAVE_PROFILE_METADATA` | `True` | Сохранять `meta.json` с данными профиля |
| `CLOSE_BROWSER_AFTER_ALL_REGISTRATIONS` | `True` | Закрывать браузер после завершения всех регистраций |
| `SET_WINDOW_TITLE` | `True` | Устанавливать информативный заголовок окна браузера |
| `SHOW_BROWSER_WINDOWS` | `True` | Показывать окна браузеров (False = headless) |
| `WINDOW_TITLE_FORMAT` | `{username} \| {forum} \| {status}` | Шаблон заголовка окна |

### 🗂 Вкладки

| Параметр | По умолчанию | Описание |
|----------|-------------|----------|
| `TAB_PER_REGISTRATION` | `True` | Открывать новую вкладку для каждой регистрации |
| `CLOSE_TAB_AFTER_REGISTRATION` | `True` | Закрывать вкладку после обработки |
| `CLEAN_TAB_BEFORE_OPEN` | `False` | Очищать cookies перед открытием (False = сохраняем реализм) |

### 🔐 Капча

| Параметр | По умолчанию | Описание |
|----------|-------------|----------|
| `CAPTCHA["AUTO_SORT_BY_COST"]` | `False` | `True` — сортировать провайдеров по цене; `False` — по приоритету |
| `CAPTCHA["DEFAULT_TIMEOUT"]` | `120` | Таймаут ожидания решения капчи (сек) |
| `CAPTCHA["V3_MIN_SCORE"]` | `0.5` | Минимальный score для reCAPTCHA v3 (0.0–1.0) |
| `CAPTCHA["ALLOW_MANUAL_FALLBACK"]` | `True` | Переключаться в ручной режим если все провайдеры не сработали |

#### Провайдеры капч (`CAPTCHA_PROVIDERS_CONFIG`)

| Провайдер | Приоритет | Поддерживаемые типы | Переменная в .env |
|-----------|-----------|--------------------|--------------------|
| AZCaptcha | 1 | recaptcha_v2, hcaptcha, image | `AZCAPTCHA_API_KEY` |
| CapSolver | 5 | recaptcha_v2, v3, hcaptcha, turnstile, image | `CAPSOLVER_API_KEY` |
| 2Captcha | 10 | recaptcha_v2, v3, hcaptcha, turnstile, funcaptcha, geetest, image | `TWOCAPTCHA_API_KEY` |
| TrueCaptcha | 15 | image, recaptcha_v2 | `TRUECAPTCHA_API_KEY` |
| DeathByCaptcha | 20 | recaptcha_v2, hcaptcha, image | `DEATHBYCAPTCHA_API_KEY` |
| SolveCaptcha | 25 | recaptcha_v2, hcaptcha, image | `SOLVECAPTCHA_API_KEY` |
| EndCaptcha | 30 | recaptcha_v2, image | `ENDCAPTCHA_API_KEY` |
| Manual | 999 | все типы | не требуется |

#### Трекинг стоимости капч (`CAPTCHA_COST_TRACKING`)

| Параметр | По умолчанию | Описание |
|----------|-------------|----------|
| `ENABLED` | `True` | Включить автоматический расчёт стоимости |
| `LEARNED_COSTS_FILE` | `data/captcha_learned_costs.json` | Файл для сохранения вычисленных цен |
| `MIN_SOLVES_FOR_CALCULATION` | `3` | Минимум решений для расчёта средней цены |
| `DEFAULT_COST` | `0.002` | Цена по умолчанию ($) если нет данных |
| `OUTLIER_THRESHOLD` | `5.0` | Порог отсечения аномальных цен (множитель) |
| `LOG_LEARNED_PRICES` | `True` | Логировать вычисленные цены в консоль |

---

## 📄 Форматы файлов

### accounts.json
```json
[
    {
        "username": "myuser1",
        "email": "user1@example.com",
        "password": "StrongPass123",
        "proxy_id": 0,
        "custom_fields": {
            "referral": "REF123",
            "city": "Moscow"
        },
        "status": "pending",
        "attempts": 0,
        "last_attempt": null
    }
]
```

### proxies.txt
```
# Формат: protocol://[user:pass@]host:port
http://user:pass@1.2.3.4:8080
http://5.6.7.8:3128
socks5://9.10.11.12:1080
```

### results_new.txt (входной список форумов)
```
# Комментарии начинаются с #
https://forum1.com/
https://forum2.com/register
# https://forum3.com/  <- закомментировано, пропускается
https://forum4.com/
```

### results_ok_username.txt (успешные регистрации)
```
https://forum1.com/ 2026-02-24T15:30:22
https://forum2.com/ 2026-02-24T15:35:10
```

### results_bad_username.txt (неудачи)
```
https://forum3.com/ captcha_timeout 2026-02-24T15:40:12
https://forum4.com/ fields_not_found 2026-02-24T15:45:33
```

---

## 🧪 Тестирование

### Автоматические тесты (unit)

Запуск всех автоматических тестов:

```bash
pytest tests/ -v
```

Краткий вывод:
```bash
pytest tests/ -v --tb=short
```

Запуск конкретного модуля:
```bash
pytest tests/test_registration_controller.py -v
pytest tests/test_captcha_helper.py -v
pytest tests/test_main_orchestrator.py -v
```

Текущее покрытие: **139 тестов** (все проходят), 3 пропущены (интеграционные).

### Интеграционные тесты (ручной запуск)

Интеграционные тесты требуют реального браузера, прокси и API-ключей. Запускаются вручную с флагом:

```bash
pytest tests/ -v --integration
```

### Ручные проверки

**Проверка окружения** (зависимости, файлы, ключи):
```bash
python tests/check_setup.py
```

**Проверка браузера** (реальный запуск Chrome с тестовыми сценариями):
```bash
# Тест навигации и ввода текста
python tests/check_browser_controller.py

# Внутри файла доступны три сценария:
# - test_basic_navigation()     — базовая навигация
# - test_manual_captcha_mode()  — ручное решение капчи
# - test_auto_captcha_mode_with_extension() — авто-капча с расширением
```

> ⚠️ Файлы `check_*.py` не запускаются автоматически через `pytest` — только вручную через `python`.

### Проверка перед запуском

```bash
# Шаг 1: проверить окружение
python src/main.py --check

# Шаг 2: посмотреть план
python src/main.py --dry-run
```

---
## 🔌 Добавление нового провайдера капч

**Шаг 1.** Создайте файл `src/utils/captcha_providers/implementations/новый_провайдер.py`:
```Python
from __future__ import annotations
from utils.captcha_providers.base import CaptchaProvider, CaptchaResult

class NewProvider(CaptchaProvider):
    def __init__(self, api_key: str) -> None:
        self._api_key = api_key

    @property
    def name(self) -> str:
        return "NewProvider"

    def supports_type(self, captcha_type: str) -> bool:
        return captcha_type in ["recaptcha_v2", "image"]

    async def get_balance(self) -> float | None:
        # реализация запроса баланса
        ...

    async def solve(self, captcha_type: str, site_key: str, page_url: str, **kwargs) -> CaptchaResult:
        # реализация решения капчи
        ...
```

**Шаг 2.** Добавьте провайдер в `src/utils/captcha_providers/registry.py`:

```python
from utils.captcha_providers.implementations.new_provider import NewProvider

PROVIDER_CLASS_MAP = {
    ...
    "newprovider": NewProvider,
}
```

**Шаг 3.** Добавьте конфигурацию в `src/config/settings.py` в словарь `CAPTCHA_PROVIDERS_CONFIG`:
```python
"newprovider": {
    "name": "NewProvider",
    "enabled": True,
    "priority": 35,               # чем меньше — тем выше приоритет
    "env_key": "NEWPROVIDER_API_KEY",
    "supported_types": ["recaptcha_v2", "image"],
},
```

**Шаг 4.** Добавьте ключ в `.env`:
```env
NEWPROVIDER_API_KEY=ваш_ключ
```

---
## 🔧 Решение проблем

**Браузер не найден**
```
Проверьте наличие Chromium: which chromium || which chromium-browser
Или укажите путь вручную в browser_controller.py → EXTRA_BROWSER_PATHS
```

**ModuleNotFoundError**
```bash
# Убедитесь что запускаете из директории src/
cd src && python main.py
# Или что виртуальное окружение активировано
source .venv/bin/activate
```

**Капча не решается автоматически**
```
Проверьте API-ключ в .env
Убедитесь что баланс провайдера не равен нулю
Система автоматически переключится в ручной режим
```

**Прокси не подключается**
```
Проверьте формат: http://user:pass@host:port
Убедитесь что прокси доступен: curl --proxy http://host:port https://api.ipify.org
```

---

## 🤝 Вклад в проект

Мы рады любому вкладу в развитие проекта!

### Как внести вклад

1. **Fork** репозитория
2. Создайте ветку для вашей функции:
   ```bash
   git checkout -b feature/новая-функция
   ```
3. Внесите изменения и добавьте тесты
4. Убедитесь что все тесты проходят:
   ```bash
   pytest tests/ -v
   ```
5. Создайте **Pull Request** с описанием изменений

### Добавление нового шаблона форума

Создайте файл `src/templates/known_forums/название_движка.json` по образцу существующих шаблонов. Структура описана в `src/templates/common_fields.json`.

### Добавление нового провайдера капч

1. Создайте файл `src/utils/captcha_providers/implementations/новый_провайдер.py`
2. Унаследуйте класс от `CaptchaProvider` из `base.py`
3. Реализуйте все абстрактные методы
4. Зарегистрируйте провайдер в `registry.py` и `config/settings.py`

### Стиль кода

- Python 3.12+, `from __future__ import annotations`
- Типизация через `| None`, `TypedDict`
- Docstrings в Google style
- Логирование через `loguru`
- Форматирование: `black`, `isort`

### Сообщить об ошибке

Откройте [Issue](https://github.com/buxar/BuxarForumRegBot/issues) с описанием:
- Версия Python и ОС
- Шаги для воспроизведения
- Ожидаемое и фактическое поведение
- Логи из `src/logs/`

---

## 💖 Пожертвования

Если BuxarForumRegBot полезен для вас, пожалуйста, поддержите разработку:

### Криптовалюта
- **Bitcoin**: `bc1q5nq5046gxcng03el66vxswunfz7a7m0k658wkj`
- **Ethereum**: `0xbdAbB06907A69f8814DcF2Cc3a84Ef23ba3f9b00`
- **BNB**: `0xbdAbB06907A69f8814DcF2Cc3a84Ef23ba3f9b00`
- **USDT ERC20**: `0xbdAbB06907A69f8814DcF2Cc3a84Ef23ba3f9b00`
- **USDT TRC20**: `TYXzhFkVMovxXa8CRPyrGV78S7aC79WUFd`
- **TRON**: `TYXzhFkVMovxXa8CRPyrGV78S7aC79WUFd`

### Россия
- **ЮMoney**: [https://yoomoney.ru/to/4100173831748](https://yoomoney.ru/to/4100173831748)

### Переводы из любой страны мира
- **Paysera**: Номер счета: `EVP8610001034598`

Ваша поддержка помогает поддерживать и улучшать проект!

---

## 📜 Лицензия

Этот проект распространяется под лицензией [MIT](LICENSE).

---

<div align="center">

Сделано с ❤️ командой BuxarNET

[⬆ Наверх](#buxarforumregbot)

</div>
