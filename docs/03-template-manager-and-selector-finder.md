# Промт 3: Разработка модулей TemplateManager и SelectorFinder

## 1. Цель
Разработать два модуля для определения структуры регистрационной формы на форуме:
- **`TemplateManager`** — загрузка и предоставление готовых JSON-шаблонов для известных форумных движков.
- **`SelectorFinder`** — эвристический анализ страницы регистрации для автоматического определения полей и их селекторов.

Модули должны возвращать данные в едином формате, пригодном для использования в `RegistrationController` (интерфейс взаимодействия будет определён позже).

## 2. Общие требования
- Код должен быть написан на Python 3.12+ с использованием асинхронного синтаксиса (`async/await`).
- В начале каждого файла добавить: `from __future__ import annotations`.
- Использовать библиотеки: `pydoll` (уже установлена), `beautifulsoup4`, `loguru`, `aiofiles` (для асинхронной работы с файлами).
- Все модули должны располагаться в пакете `src` (т.е. файлы `src/template_manager.py`, `src/selector_finder.py`).
- Добавить docstrings для всех классов и публичных методов (Google или NumPy style).
- Код должен строго соответствовать стандарту аннотаций типов Python 3.12 (использовать `| None` вместо `Optional`, `list[str]` вместо `List[str]` и т.д.).
- Использовать `logger = loguru.logger` для логирования.
- Покрыть ключевую логику тестами (pytest).

## 3. Модуль template_manager.py

### 3.1. Класс `TemplateManager`
**Расположение:** `src/template_manager.py`

#### `__init__(self, templates_dir: str = "templates/known_forums")`
- Сохраняет путь к директории с шаблонами в `self.templates_dir`.
- Инициализирует `self.templates: list[dict] = []`.
- **Не выполняет** синхронной загрузки при инициализации — загрузка будет происходить при первом вызове `_load_templates()`.

#### `async _load_templates(self) -> None`
Асинхронно сканирует `templates_dir`, читает все `.json` файлы.
- Использовать `aiofiles` или `asyncio.to_thread` для открытия файлов (так как `json.load` блокирующий).
- Парсит содержимое и сохраняет в `self.templates` (список словарей).
- При ошибке парсинга конкретного файла логировать предупреждение через `logger.warning()` и продолжать обработку остальных.
- Добавить флаг `self._loaded: bool = True` после успешной загрузки.

#### `async detect_template(self, url: str, page_source: str) -> dict | None`
Проходит по всем загруженным шаблонам и проверяет правила из секции `detect`:
- **`url_pattern`**: если задан, должен присутствовать в `url` (простое вхождение строки, `in`).
- **`meta_tags`**: список словарей `{"name": "...", "content": "..."}`. Проверяется наличие meta-тега с указанными атрибутами в `page_source`. Парсинг HTML через BeautifulSoup выполнить в `asyncio.to_thread` (так как BS4 синхронный).
- **`html_contains`**: список строк, каждая из которых должна присутствовать в `page_source` (поиск подстроки).
- Все условия внутри одного шаблона соединяются по **AND**.
- Возвращает первый подходящий шаблон или `None`.

#### `async get_template_by_name(self, name: str) -> dict | None`
- Поиск шаблона по полю `name` (без учёта регистра).
- Предварительно вызывает `_load_templates()`, если шаблоны ещё не загружены (`if not self._loaded`).
- Возвращает найденный шаблон или `None`.

#### `async get_all_templates(self) -> list[dict]`
- Возвращает список всех загруженных шаблонов.
- Предварительно вызывает `_load_templates()`, если шаблоны ещё не загружены.

#### `async add_template(self, template_data: dict, filename: str | None = None) -> str`
- Сохраняет `template_data` в JSON-файл в директорию шаблонов.
- Использует `aiofiles` для асинхронной записи.
- Если `filename` не указан, генерирует имя из поля `name` (или `domain`, если есть), заменяя недопустимые символы на `_`.
- После сохранения вызывает `_load_templates()` для обновления кэша.
- Возвращает путь к сохранённому файлу.

#### `_generate_filename(self, template_data: dict) -> str`
- Вспомогательный метод для генерации имени файла.
- Приоритет: поле `name` → поле `domain` → timestamp.
- Очищает имя от недопустимых символов (`/`, `\`, `:`, `*`, `?`, `"`, `<`, `>`, `|`).

---

## 4. Модуль selector_finder.py

### 4.1. Класс `SelectorFinder`
**Расположение:** `src/selector_finder.py`

**Важно:** Класс не должен напрямую обращаться к защищённым атрибутам `BrowserController`. Вместо этого в конструктор передаётся объект текущей страницы (вкладки) Pydoll, через который выполняются все запросы к DOM.

#### `__init__(self, page, common_fields_path: str = "templates/common_fields.json")`
- `page` – объект текущей страницы (таб) из Pydoll (полученный, например, через `browser._current_tab` или `browser.start_page`). Предполагается, что вызывающий код уже открыл нужную страницу.
- Загружает `common_fields.json` с помощью `aiofiles` (асинхронно) и сохраняет словарь в `self.common_fields: dict`.
- Инициализирует `self.page = page`.

#### `async find_registration_form(self) -> dict | None`
Ищет на текущей странице форму регистрации. Возвращает словарь с полями `form_selector` (строка) и `form_element` (объект элемента Pydoll) или `None`, если форма не найдена.

**Алгоритм:**
1. Получить все элементы `form` через `await self.page.query_all('form')` (или актуальный метод pydoll для получения списка элементов).
2. Если форм нет, вернуть `None`.
3. Для каждой формы собрать информацию:
   - Количество полей `input[type="password"]` (через `await form.query_all('input[type="password"]')`).
   - Наличие кнопки отправки (`input[type="submit"]` или `button[type="submit"]`).
   - Общее количество полей ввода (для возможного ранжирования).
4. Выбрать форму с максимальным числом полей пароля. Если несколько — предпочесть ту, у которой есть кнопка отправки.
5. Сгенерировать уникальный CSS-селектор для выбранной формы через метод `_generate_css_selector(element)`.
6. Вернуть `{"form_selector": selector, "form_element": element}`.

#### `async identify_fields(self, form_element) -> dict`
Анализирует поля внутри переданной формы. Возвращает словарь с селекторами для стандартных полей и список кастомных полей.

**Алгоритм:**
1. Получить все поля ввода внутри формы: `inputs = await form_element.query_all('input, textarea, select')`.
2. Получить кнопки: `buttons = await form_element.query_all('button[type="submit"], input[type="submit"]')`.
3. Для каждого поля собрать атрибуты с помощью `await element.evaluate(js_code)`, где JS-код возвращает словарь:

```js
{
    type: el.type,
    name: el.name,
    id: el.id,
    placeholder: el.placeholder,
    value: el.value,
    label: el.labels?.[0]?.innerText || '',
    tagName: el.tagName.toLowerCase()
}
```

4. **Классификация поля:**
   - **Password:** Если `type === 'password'`. Если найдено два таких поля, первое — `password`, второе — `confirm_password`.
   - **Email:** Если `type === 'email'` ИЛИ в `name`/`id`/`placeholder` есть слово "email".
   - **Username:** Если в `name`/`id`/`placeholder` есть слова "user", "login", "nick" (и это не email).
   - **Checkbox:** Если `type === 'checkbox'` и `label` содержит слова из `self.common_fields["agree_keywords"]`.
   - **Submit:** Для кнопок — если `type === 'submit'` ИЛИ текст кнопки есть в `self.common_fields["submit_keywords"]`.
   - **Custom:** Все остальные поля добавить в список `custom_fields`.
5. **Генерация селектора:**
   - Для каждого найденного поля сформировать CSS-селектор через метод `_generate_css_selector(element)`.
   - Логика метода: если есть `id` → `#id`, иначе если есть `name` → `tag[name='value']`, иначе → путь через структуру DOM с использованием `nth-child`.
6. **Возврат результата:**
   Вернуть словарь вида:

```python
{
    "username": "input#username",
    "email": "input[name='email']",
    "password": "input[type='password']:nth-of-type(1)",
    "confirm_password": "input[type='password']:nth-of-type(2)",
    "agree_checkbox": "input#agree",
    "submit_button": "button[type='submit']",
    "custom_fields": [
        {"name": "birthday", "selector": "input#dob", "type": "text"}
    ]
}
```

   Если поле не найдено, ключ может отсутствовать или иметь значение `None`.

#### `_generate_css_selector(self, element) -> str`
Вспомогательный метод для генерации уникального CSS-селектора.
- **Приоритет 1:** Если есть `id` → вернуть `#{id}`.
- **Приоритет 2:** Если есть `name` → вернуть `{tag}[name='{name}']`.
- **Приоритет 3:** Сгенерировать путь через DOM-дерево (подняться к родителям, использовать `:nth-child()`).
- Для псевдо-селекторов использовать `:nth-of-type()` для надёжности.

#### `async detect_captcha(self) -> str | None`
Проверяет наличие капчи на странице.
1. Использует список известных селекторов капч:

```python
captcha_selectors = [
    'iframe[src*="recaptcha"]',
    'iframe[src*="hcaptcha"]',
    'iframe[src*="turnstile"]',
    '.g-recaptcha',
    '.h-captcha',
    '#captcha'
]
```

2. Выполняет поиск через `await self.page.query(selector)` для каждого варианта.
3. Возвращает строку-селектор первой найденной капчи или `None`.

#### ✨ **Convenience-метод:** `async analyze_current_page(self) -> dict | None`
Выполняет полный анализ текущей страницы и возвращает объединённый результат.

**Алгоритм:**
1. Вызвать `find_registration_form()`. Если форма не найдена, вернуть `None`.
2. Для найденной формы вызвать `identify_fields(form_result["form_element"])`.
3. Вызвать `detect_captcha()` и добавить результат в поле `captcha_indicator` итогового словаря.
4. Объединить результаты в один словарь, включающий:
   - `form_selector` (из первого шага)
   - все поля, возвращённые `identify_fields` (`username`, `email`, `password`, `confirm_password`, `agree_checkbox`, `submit_button`, `custom_fields`)
   - `captcha_indicator` (из третьего шага)
5. Вернуть итоговый словарь или `None`, если форма не найдена.

**Пример возвращаемого значения:**
```python
{
    "form_selector": "form#register",
    "username": "input#username",
    "email": "input[name='email']",
    "password": "input[type='password']:nth-of-type(1)",
    "confirm_password": "input[type='password']:nth-of-type(2)",
    "agree_checkbox": "input#agree",
    "submit_button": "button[type='submit']",
    "captcha_indicator": "iframe[src*='recaptcha']",
    "custom_fields": [
        {"name": "birthday", "selector": "input#dob", "type": "text"}
    ]
}
```

---

## 5. Формат JSON-шаблонов (расширенный)

Пример структуры шаблона в `templates/known_forums/xenforo.json`:

```json
{
    "name": "XenForo",
    "domain": "xenforo.com",
    "detect": {
        "url_pattern": "register",
        "meta_tags": [
            {"name": "generator", "content": "XenForo"}
        ],
        "html_contains": [
            "xf-register"
        ]
    },
    "registration_page": {
        "url": "/register",
        "form_selector": "form#register-form"
    },
    "fields": {
        "username": "input[name='username']",
        "email": "input[name='email']",
        "password": "input[name='password']",
        "confirm_password": "input[name='password_confirm']",
        "agree_checkbox": "input[name='agree']",
        "submit_button": "button[type='submit']",
        "captcha_indicator": "iframe[src*='recaptcha']"
    },
    "custom_fields": [],
    "success_indicators": [
        "thank you for registering",
        "account has been created"
    ],
    "error_indicators": [
        "username is already taken",
        "email already exists"
    ]
}
```

**Пояснения:**
- Поля `registration_page`, `success_indicators`, `error_indicators` являются опциональными, но при наличии используются в `RegistrationController` для навигации и проверки результата.
- В `fields` перечислены селекторы для всех стандартных полей.
- `custom_fields` — массив дополнительных полей, которые могут присутствовать на форуме.

Пример структуры `templates/common_fields.json`:

```json
{
    "agree_keywords": ["agree", "terms", "rules", "согласен", "правила"],
    "submit_keywords": ["register", "sign up", "create account", "зарегистрироваться"],
    "username_keywords": ["user", "login", "nick", "username", "логин"],
    "email_keywords": ["email", "mail", "e-mail"]
}
```

---

## 6. Тестирование (pytest)

### 6.1. Unit-тесты (основные)
- Использовать `unittest.mock` для эмуляции объекта `page` и методов `query`, `query_all`, `evaluate`.
- **TemplateManager:**
  - Проверить логику парсинга JSON (мок `aiofiles`).
  - Проверить фильтрацию шаблонов по `url_pattern` и `meta_tags` (мок `asyncio.to_thread` + BeautifulSoup).
  - Проверить генерацию имён файлов в `add_template`.
- **SelectorFinder:**
  - Проверить логику классификации полей (передать мок с атрибутами `type='email'`, `name='user'` и убедиться, что они распознаны верно).
  - Проверить `_generate_css_selector` с разными комбинациями атрибутов.
  - Проверить `detect_captcha` с моками на разные селекторы.
  - Проверить метод `analyze_current_page` (должен вызывать три внутренних метода и объединять результаты).

### 6.2. Integration-тесты (опционально)
- Пометить тесты маркером `@pytest.mark.integration`.
- Использовать реальные URL популярных форумов (например, XenForo, phpBB демо-страницы).
- **Важно:** Эти тесты не должны блокировать CI/CD по умолчанию (запускать только по флагу `--integration` или вручную).

### 6.3. Структура тестов

```
tests/
├── conftest.py          # Фикстуры, маркеры
├── test_template_manager.py
└── test_selector_finder.py
```

---

## 7. Финальные инструкции
- Убедиться, что все импорты корректны и совместимы с Python 3.12.
- Все асинхронные методы должны иметь префикс `async` и вызываться с `await`.
- Блокирующие операции (файлы, BeautifulSoup) должны быть обёрнуты в `asyncio.to_thread` или использовать `aiofiles`.
- Код должен быть готов к вставке в проект без дополнительных правок синтаксиса.
- Добавить обработку исключений с логированием (не использовать bare `except`).
- Все публичные методы должны иметь docstring с описанием параметров, возвращаемого значения и возможных исключений.
```
