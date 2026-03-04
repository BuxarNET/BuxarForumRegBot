# Промт 6: CaptchaExtensionHelper — модуль решения капч

## 1. Цель
Создать класс `CaptchaExtensionHelper`, который предоставляет единый асинхронный интерфейс для решения различных типов капч через внешних провайдеров с автоматическим расчётом стоимости и fallback-логикой.

**Поддерживаемые провайдеры:**
- **2Captcha** — мощный сервис (35+ типов капч, fallback для сложных случаев)
- **AZCaptcha** — бюджетная альтернатива
- **CapSolver** — сервис с бесплатным тестовым периодом
- **TrueCaptcha**, **DeathByCaptcha**, **SolveCaptcha**, **EndCaptcha** — дополнительные провайдеры
- **Manual** — ручной режим (всегда последний в цепочке, не требует API-ключа)

**Ключевые особенности:**
- Архитектура с провайдерами в отдельных модулях (легко добавлять новые)
- Конфигурация в `config/settings.py` (логика), API-ключи в `.env` (секреты)
- Автоматический расчёт стоимости на основе изменения баланса
- Проверка баланса только для использованных провайдеров (оптимизация API-запросов)
- Цепочка провайдеров с фильтрацией по типу капчи и fallback

## 2. Общие требования
- Код на Python 3.12+ с асинхронным синтаксисом (`async/await`).
- В начале каждого файла: `from __future__ import annotations`.
- Использовать библиотеки: `aiohttp`, `loguru`, `pathlib`, `python-dotenv`, `json`.
- Модули располагаются в `src/utils/captcha_providers/` (провайдеры) и `src/utils/captcha_helper.py` (фасад).
- Добавить docstrings для всех классов и публичных методов (Google style).
- Строгая типизация: использовать `| None`, `list[str]`, `TypedDict`, `Literal`.
- Логирование через `loguru.logger`.
- Покрыть ключевую логику тестами (pytest) с моками HTTP-запросов.

## 3. Структура проекта (дополнения к существующей)

```
forum_reg_bot/
├── config/
│   └── settings.py             # Конфигурация провайдеров (приоритеты, настройки)
├── utils/
│   ├── captcha_helper.py       # Фасад: единая точка входа
│   └── captcha_providers/      # Пакет с провайдерами
│       ├── __init__.py
│       ├── base.py             # Абстрактный класс CaptchaProvider
│       ├── registry.py         # Реестр и фабрика провайдеров
│       └── implementations/    # Реализации провайдеров
│           ├── __init__.py
│           ├── two_captcha.py
│           ├── az_captcha.py
│           ├── cap_solver.py
│           ├── true_captcha.py
│           ├── death_by_captcha.py
│           ├── solve_captcha.py
│           ├── end_captcha.py
│           └── manual.py
├── data/
│   └── captcha_learned_costs.json  # Автоматически вычисленные цены (создаётся при работе)
├── .env                        # API-ключи провайдеров
└── ...                         # остальные файлы проекта
```

## 4. Конфигурация

### 4.1. Файл `.env` (только API-ключи)
```
# CAPTCHA API KEYS (Secrets only)
TWOCAPTCHA_API_KEY=
AZCAPTCHA_API_KEY=
CAPSOLVER_API_KEY=
TRUECAPTCHA_API_KEY=
DEATHBYCAPTCHA_API_KEY=
SOLVECAPTCHA_API_KEY=
ENDCAPTCHA_API_KEY=
```

### 4.2. Файл `config/settings.py` (логика и приоритеты)

**Глобальные настройки:**
```python
CAPTCHA = {
    "AUTO_SORT_BY_COST": False,      # Если True — сортировать по цене, иначе по приоритету
    "DEFAULT_TIMEOUT": 120,          # Таймаут ожидания решения (сек)
    "V3_MIN_SCORE": 0.5,             # Минимальный score для reCAPTCHA v3
    "ALLOW_MANUAL_FALLBACK": True,   # Разрешить ручной режим как fallback
}
```

**Настройки провайдеров:**
```python
CAPTCHA_PROVIDERS = {
    "azcaptcha": {
        "name": "AZCaptcha",
        "enabled": True,
        "priority": 1,               # Чем меньше, тем выше приоритет
        "env_key": "AZCAPTCHA_API_KEY",
        "supported_types": ["recaptcha_v2", "hcaptcha", "image"],
    },
    "capsolver": {
        "name": "CapSolver",
        "enabled": True,
        "priority": 5,
        "env_key": "CAPSOLVER_API_KEY",
        "supported_types": ["recaptcha_v2", "recaptcha_v3", "hcaptcha", "turnstile", "image"],
    },
    "2captcha": {
        "name": "2Captcha",
        "enabled": True,
        "priority": 10,
        "env_key": "TWOCAPTCHA_API_KEY",
        "supported_types": ["recaptcha_v2", "recaptcha_v3", "hcaptcha", "turnstile", "funcaptcha", "geetest", "image"],
    },
    # ... остальные провайдеры по аналогии ...
    "manual": {
        "name": "Manual Mode",
        "enabled": True,
        "priority": 999,             # Всегда последний (системное значение)
        "env_key": None,             # Не требует ключа
        "supported_types": ["*"],    # Поддерживает все типы
    },
}
```

**Настройки трекинга стоимости:**
```python
CAPTCHA_COST_TRACKING = {
    "ENABLED": True,
    "LEARNED_COSTS_FILE": "data/captcha_learned_costs.json",
    "MIN_SOLVES_FOR_CALCULATION": 3,   # Мин. решений для расчёта цены
    "DEFAULT_COST": 0.002,             # Дефолтная цена ($) при отсутствии данных
    "OUTLIER_THRESHOLD": 5.0,          # Порог для игнорирования выбросов (множитель)
    "LOG_LEARNED_PRICES": True,        # Логировать вычисленные цены
}
```

## 5. Архитектура классов

### 5.1. Абстрактный базовый класс (`base.py`)

**Класс `CaptchaProvider` (ABC):**

| Метод | Возврат | Описание |
|-------|---------|----------|
| `name` (property) | `str` | Уникальный ID провайдера (как в реестре) |
| `is_available()` | `bool` | Проверка наличия API-ключа и готовности |
| `supports_balance_check()` | `bool` | Поддерживает ли проверку баланса |
| `supports_type(captcha_type)` | `bool` | Поддерживает ли данный тип капчи |
| `get_balance()` | `float | None` | Запрос текущего баланса (если поддерживается) |
| `solve(captcha_type, site_key, page_url, **kwargs)` | `CaptchaResult` | Отправка задачи и ожидание решения |
| `get_cost_estimate(captcha_type)` | `float` | Возврат известной цены (из learned_costs или дефолт) |
| `report_bad(task_id)` | `bool` | Сообщение о неверном решении (для возврата средств) |

**TypedDict `CaptchaResult`:**
```python
class CaptchaResult(TypedDict):
    token: str | None           # Токен капчи
    score: float | None         # Для reCAPTCHA v3
    provider: str               # ID провайдера
    cost: float                 # Стоимость решения ($)
    solve_time: float           # Время решения (сек)
    captcha_type: str           # Тип капчи
```

### 5.2. Реестр провайдеров (`registry.py`)

**Функция `get_provider_chain(captcha_type: str) -> list[CaptchaProvider]`:**

**Алгоритм:**
1. Взять всех провайдеров из `CAPTCHA_PROVIDERS` где `enabled == True`
2. Проверить наличие API-ключа (через `os.getenv(env_key)`)
3. Отфильтровать по `supported_types` (исключить неподдерживающие данный тип)
4. Если `CAPTCHA["AUTO_SORT_BY_COST"] == True`:
   - Сортировать по цене (из `captcha_learned_costs.json`)
5. Иначе:
   - Сортировать по `priority` (возрастание)
6. Добавить `manual` в конец списка (если `ALLOW_MANUAL_FALLBACK == True`)
7. Вернуть список экземпляров провайдеров

### 5.3. Фасад (`captcha_helper.py`)

**Класс `CaptchaExtensionHelper`:**

**Инициализация:**
- Принимает `page` (объект страницы Pydoll)
- Принимает `stats_callback: Callable[[dict], None] | None`
  (опционально, для передачи статистики в AccountManager)
  Импорт: `from typing import Callable`
- Загружает `captcha_learned_costs.json` (если существует)
- Инициализирует структуры для трекинга:
  - `used_providers: set[str]`
  - `balance_snapshots: dict[str, float]`
  - `solve_counts: dict[(str, str), int]`
  - `last_captcha_type: dict[str, str]`

**Основной метод `async solve_captcha(captcha_type, site_key, page_url, **kwargs) -> str | None`:**

**Алгоритм:**
1. Получить цепочку провайдеров через `get_provider_chain(captcha_type)`
2. Для каждого провайдера в цепочке:
   - **При первом использовании:** запросить баланс, сохранить в `balance_snapshots`
   - Вызвать `provider.solve(...)`
   - При успехе:
     - Записать в `solve_counts[(provider, captcha_type)]`
     - Проверить смену типа капчи → если сменился, пересчитать стоимость
     - Внедрить токен в страницу через `_inject_token()`
     - Вернуть токен
   - При ошибке:
     - Логировать предупреждение
     - Перейти к следующему провайдеру (fallback)
3. Если все провайдеры исчерпаны → вернуть `None`

**Метод `_inject_token(token, captcha_type)`:**
- Внедряет токен в страницу через `page.evaluate(js_code)`
- Поддерживаемые типы: `recaptcha_v2`, `recaptcha_v3`, `hcaptcha`, `turnstile`
- Для каждого типа — свой JS-сниппет (установить значение в textarea, вызвать callback)

**Метод `finalize()`:**
- Вызывается при завершении работы бота
- Для каждого провайдера в `used_providers`:
  - Запросить текущий баланс
  - Пересчитать стоимость для последнего типа капчи
  - Сохранить в `captcha_learned_costs.json`

## 6. Алгоритм расчёта стоимости

### 6.1. Когда проверять баланс
| Событие | Действие |
|---------|----------|
| **Первое использование провайдера** | Запросить баланс, сохранить в `balance_snapshots` |
| **Смена типа капчи** | Запросить баланс, пересчитать стоимость для предыдущего типа |
| **Завершение работы бота** | Запросить баланс, пересчитать стоимость для последнего типа |
| **Провайдер не использовался** | Не проверять баланс вообще |

### 6.2. Формула расчёта
```
spent = previous_balance - current_balance
count = solve_counts[(provider, captcha_type)]

if count >= MIN_SOLVES_FOR_CALCULATION и spent > 0:
    cost_per_solve = spent / count
    
    # Проверка на выбросы
    if cost_per_solve > DEFAULT_COST * OUTLIER_THRESHOLD:
        Игнорировать (логировать WARNING)
    else:
        Сохранить в captcha_learned_costs.json
        Логировать: "💡 Learned: {provider}/{type} = ${cost_per_solve:.4f}"
```

### 6.3. Структура `captcha_learned_costs.json`
```json
{
    "2captcha": {
        "recaptcha_v2": 0.0015,
        "recaptcha_v3": 0.0015,
        "hcaptcha": 0.0015,
        "turnstile": 0.0015
    },
    "azcaptcha": {
        "recaptcha_v2": 0.0005,
        "hcaptcha": 0.0005
    },
    "capsolver": {
        "recaptcha_v2": 0.0008,
        "turnstile": 0.0010
    }
}
```

## 7. Поддерживаемые типы капч

| Тип | Описание | Особенности |
|-----|----------|-------------|
| `recaptcha_v2` | Checkbox "I'm not a robot" | Требует sitekey, внедрение в `#g-recaptcha-response` |
| `recaptcha_v3` | Невидимая, score-based | Требует `action`, проверка `score >= V3_MIN_SCORE` |
| `hcaptcha` | Аналог reCAPTCHA от Intuition Machines | Селектор `#hcaptcha-response` |
| `turnstile` | Cloudflare Turnstile | Callback-based, без checkbox |
| `funcaptcha` | FunCaptcha (Arkose) | Требует специальную задачу |
| `geetest` | GeeTest | Требует challenge/response |
| `image` | Текстовая или картинка | Требует скриншот или base64 |

## 8. Обработка ошибок

| Тип ошибки | Действие фасада |
|------------|-----------------|
| `CaptchaUnsupportedError` | Пропустить провайдера, попробовать следующего (WARNING) |
| `CaptchaTimeoutError` | Пропустить, попробовать следующего (WARNING) |
| `CaptchaFailedError` | Пропустить, попробовать следующего (WARNING) |
| `APIKeyError` / `NoBalanceError` | Исключить провайдера из цепочки (ERROR) |
| `NetworkError` | Пропустить, попробовать следующего (WARNING) |
| **Все провайдеры исчерпаны** | Вернуть `None`, логировать CRITICAL |

## 9. Ручной режим (`manual.py`)

**Особенности:**
- Не требует API-ключа
- Не проверяет баланс
- Всегда последний в цепочке (priority = 999)
- **Алгоритм:**
  1. Логировать: `"Manual captcha solving required. Waiting for user..."`
  2. Опционально: подсветить элемент капчи через JS
  3. Polling: каждые 2 сек проверять, появился ли токен в DOM
  4. Таймаут: 300 сек (настраивается)
  5. Если токен найден — вернуть его, иначе `None`

## 10. Интеграция с другими модулями

### 10.1. С `RegistrationController` (Промт #4)
- `RegistrationController` определяет тип капчи (из шаблона или `SelectorFinder`)
- Вызывает: `await captcha_helper.solve_captcha(captcha_type, site_key, page_url)`
- Получает токен или `None`
- Использует токен для продолжения регистрации

### 10.2. С `AccountManager` (Промт #5)
- `stats_callback` передаёт статистику в `AccountManager`
- Сохраняется: провайдер, тип капчи, успех, стоимость, время решения
- Для отчётов и аналитики расходов

### 10.3. С `BrowserController` (Промт #2)
- Использует объект `page` для взаимодействия с DOM
- Методы: `page.evaluate()`, `page.query()`, `page.get_url()`

## 11. Тестирование

### 11.1. Unit-тесты (с моками)
- Мок `aiohttp.ClientSession` для эмуляции ответов провайдеров
- Проверить выбор провайдера по приоритету
- Проверить fallback-логику при ошибках
- Проверить обработку reCAPTCHA v3 score
- Проверить ручной режим (мок polling)
- Проверить расчёт стоимости (мок баланса)

### 11.2. Интеграционные тесты (опционально)
- Пометить `@pytest.mark.integration`
- Использовать демо-страницы:
  - `https://www.google.com/recaptcha/api2/demo`
  - `https://hcaptcha.com/demo`
  - `https://challenges.cloudflare.com/turnstile/`
- Требуют реальные API-ключи (запускать вручную)

### 11.3. Тестирование расчёта стоимости
- Создать мок провайдера с изменяемым балансом
- Сымитировать N решений капчи
- Проверить корректность расчёта `cost_per_solve`
- Проверить сохранение в `captcha_learned_costs.json`

## 12. Критерии готовности

- [ ] Абстрактный класс `CaptchaProvider` с 8 методами
- [ ] Реализованы минимум 3 провайдера (2Captcha, CapSolver, Manual)
- [ ] Реестр провайдеров с фильтрацией по типу и приоритету
- [ ] Фасад `CaptchaExtensionHelper` с fallback-логикой
- [ ] Автоматический расчёт стоимости на основе баланса
- [ ] Проверка баланса только для использованных провайдеров
- [ ] Сохранение цен в `data/captcha_learned_costs.json`
- [ ] Внедрение токена в страницу для всех типов капч
- [ ] Ручной режим с polling токена
- [ ] Статистика и `stats_callback` для AccountManager
- [ ] Загрузка конфигурации из `config/settings.py`
- [ ] API-ключи из `.env` через `python-dotenv`
- [ ] Unit-тесты с моками покрывают основную логику
- [ ] Все публичные методы имеют docstrings и аннотации типов
- [ ] Логирование через `loguru` с разными уровнями (DEBUG, INFO, WARNING, ERROR)

## 13. Согласованность с предыдущими промтами

### 13.1. Типы данных
- `CaptchaResult` совместим с `RegistrationResult` из Промта #4
- Формат статистики совместим с `AccountManager.log_registration()` из Промта #5

### 13.2. Конфигурация
- API-ключи в `.env` (как в Промте #5)
- Логика в `config/settings.py` (новая структура)
- Пути через `pathlib.Path` для кроссплатформенности

### 13.3. Интеграция
- `RegistrationController` вызывает `CaptchaExtensionHelper.solve_captcha()`
- `CaptchaExtensionHelper` использует `BrowserController.page`
- Статистика передаётся в `AccountManager` через callback

## 14. Финальные инструкции

- Не дублировать логику детекции типа капчи в фасаде (это делает `RegistrationController` / `SelectorFinder`)
- Каждый провайдер — отдельный файл в `implementations/`
- Для добавления нового провайдера: создать файл, унаследовать `CaptchaProvider`, добавить в реестр
- Использовать параметризованные запросы к API провайдеров (защита от инъекций не нужна, но валидация входных данных — да)
- Обрабатывать исключения с логированием (не использовать bare `except`)
- Сохранять `captcha_learned_costs.json` с форматированием (indent=4) для читаемости
- При первом сохранении цены для пары (провайдер, тип) логировать предложение пользователю

После выполнения этого промта можно будет переходить к созданию `MainOrchestrator`, который объединит все компоненты в единый рабочий поток.
