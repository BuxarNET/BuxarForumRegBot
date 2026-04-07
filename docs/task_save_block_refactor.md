# Задача: Рефакторинг логики сохранения селекторов в шаблон

## 1. Описание проблемы

### 1.1 Регрессия: динамические селекторы накапливаются в шаблоне

При каждом запуске регистрации на форуме XenForo в шаблон добавляются
новые селекторы с динамическими ID вида `#ctrl_<хэш>`:

```json
"password": [
    "input[name='password']",
    "#ctrl_pageLogin_password",
    "#ctrl_eb141216267ee54391095376e5c5fa40",
    "#ctrl_ad72e79d392243f69bf0f7e1d38b837d"
],
"confirm_password": [
    "input[name='password_confirm']",
    "#ctrl_996bd4e13eb24a2be11ea7fb1f6078e1",
    "#ctrl_6fe2d76f203f5d251fb639eba4e3a4af"
]
```

Селекторы вида `#ctrl_[a-f0-9]{32}` — динамические ID полей второй страницы
регистрации XenForo. Генерируются случайно при каждой сессии и не должны
сохраняться — при следующем запуске эти ID уже недействительны.
Проблема является регрессией после последних изменений в логике сохранения.

### 1.2 Архитектурная проблема: сохранение в двух местах без защит

Сохранение в шаблон происходит в **7 точках**:

**`_fill_fields` — 6 мест немедленной записи** (без ожидания итога регистрации):
- стр. 1138–1152 — стандартные поля (`password`, `email` и др.) → **основной источник бага**
- стр. 1250–1257 — `custom_fields` п.2 (ручной ввод)
- стр. 1283–1290 — `custom_fields` п.3 (автоопределение)
- стр. 1310–1317 — `custom_fields` п.4 (ручной ввод, нет значения)
- стр. 1755–1763 — `agree_step.checkboxes` (в `_handle_checkboxes`)
- стр. 2070–2080 — `submit_button` fallback (в `_submit_form`)

**`_save_block_to_template` — 1 метод отложенной записи** (только при подтверждённом успехе):
- Триггер А: финальный успех регистрации
- Триггер Б: переход на следующую страницу (`block_changed`)
- Триггер В: ручное подтверждение (`_confirm_test_mode → True`)

### 1.3 Пробелы в `_save_block_to_template`

Метод изначально не получал все необходимые данные. Сейчас **не обрабатывает**:
- `custom_fields` селекторы
- `agree_step.checkboxes`
- `submit_button` найденный через fallback-поиск
- `form_selector` — нет проверки на динамический ID
- фильтрацию динамических ID и по `source` для custom / checkboxes / submit
- дедубликацию и лимит длины списков селекторов
- обработку ошибок `update_template`
- полную синхронизацию `template` в памяти после записи

---

## 2. Затронутые файлы

| Файл | Место |
|---|---|
| `src/controllers/registration_controller.py` | `FillFieldsResult` TypedDict — добавить новые поля |
| `src/controllers/registration_controller.py` | Атрибуты класса: `_PATTERN_CTRL_HASH`, `_PATTERN_HEX_ID`, константы |
| `src/controllers/registration_controller.py` | `_is_dynamic_selector` — новый статический метод |
| `src/controllers/registration_controller.py` | `_fill_fields` — убрать все 6 мест записи, расширить возврат |
| `src/controllers/registration_controller.py` | `_handle_checkboxes` — убрать запись, изменить сигнатуру |
| `src/controllers/registration_controller.py` | `_handle_submit` / `_submit_form` — убрать запись, изменить сигнатуры |
| `src/controllers/registration_controller.py` | `_save_block_to_template` — новые аргументы, шаг 0, блоки Б/В/Г, memory update |
| `src/controllers/registration_controller.py` | `register()` — обновить все 3 вызова `_save_block_to_template` |
| `src/templates/known_forums/xenforo.json` | Очистить накопленные `#ctrl_<хэш>` вручную |

---

## 3. Схема будущей работы

### 3.1 `FillFieldsResult` TypedDict — обновлённая версия

```python
class FillFieldsResult(TypedDict):
    ok:                   bool
    filled:               list[str]
    skipped:              list[str]
    filled_from_outside:  list[str]                    # уже есть в коде, отсутствовало в TypedDict
    new_custom_selectors: dict[str, tuple[str, str]]   # ← НОВОЕ: {field: (sel, source)}
    new_checkboxes:       list[tuple[str, str]]        # ← НОВОЕ: [(sel, source), ...]
    found_submit:         tuple[str, str] | None       # ← НОВОЕ: (sel, source) | None
    reason:               NotRequired[str]             # уже есть — оставить как есть
```

### 3.2 Атрибуты и константы класса `RegistrationController`

```python
class RegistrationController:

    # Компилированные паттерны динамических ID — один раз при загрузке класса
    _PATTERN_CTRL_HASH: re.Pattern = re.compile(
        r"^#ctrl_[a-f0-9]{32}$", re.IGNORECASE
    )
    _PATTERN_HEX_ID: re.Pattern = re.compile(
        r"^#[a-f0-9]{16,}$", re.IGNORECASE
    )

    # Максимальное число селекторов для одного поля в шаблоне.
    # При превышении новый селектор не сохраняется, логируется WARNING.
    MAX_SELECTORS_PER_FIELD: int = 10

    # Префиксы динамических ID — расширяемый список.
    # Критерий срабатывания: starts_with(prefix) AND hex-суффикс ≥ 8 символов.
    DYNAMIC_ID_PREFIXES: tuple[str, ...] = (
        "#js_", "#random-", "#uuid-", "#generated-", "#id-",
    )
```

### 3.3 `_is_dynamic_selector()` — новый статический метод

```
_is_dynamic_selector(sel: str) -> bool
│
│  Возвращает True если селектор динамический (не сохранять).
│  Использует предкомпилированные атрибуты класса — без re.compile при каждом вызове.
│  Требует: import re  ← добавить в начало файла
│
├── Защита от пустой строки: if not sel → return False
│
├── Паттерн 1 (XenForo MD5): _PATTERN_CTRL_HASH
│    re.compile(r"^#ctrl_[a-f0-9]{32}$", re.IGNORECASE)
│    #ctrl_EB141216267EE54391095376E5C5FA40  → True   (32 hex, верхний регистр)
│    #ctrl_eb141216267ee54391095376e5c5fa40  → True   (32 hex, нижний регистр)
│    #ctrl_pageLogin_password               → False  (читаемый slug)
│    #ctrl_123                              → False  (не MD5-длина)
│
├── Паттерн 2 (чистый hex-ID): _PATTERN_HEX_ID
│    re.compile(r"^#[a-f0-9]{16,}$", re.IGNORECASE)
│    #a3f9c2d1e4b87654                      → True
│    #main                                  → False
│
├── Паттерн 3: расширяемые префиксы DYNAMIC_ID_PREFIXES + hex-суффикс ≥ 8 символов
│    for prefix in cls.DYNAMIC_ID_PREFIXES:
│        if sel.startswith(prefix):
│            suffix = sel[len(prefix):]
│            if re.match(r"^[a-f0-9\-]{8,}$", suffix, re.IGNORECASE):
│                return True
│
└── Всё остальное → False
     input[name='password']     → False
     button[type='submit']      → False
     #ctrl_pageLogin_password   → False
     form#register-form         → False
```

### 3.4 `_fill_fields()` — только заполнение, никакой записи

```
_fill_fields()
│
│  Аккумуляторы (хранят кортежи (selector, source)):
│  new_custom_selectors: dict[str, tuple[str, str]]  # {field: (sel, source)}
│  new_checkboxes:       list[tuple[str, str]]        # [(sel, source), ...]
│  found_submit:         tuple[str, str] | None       # (sel, source)
│
│  Допустимые значения source:
│  "template"      — взят из шаблона (не сохранять повторно)
│  "common_fields" — найден эвристикой с label
│  "manual"        — найден без label
│  "heuristic"     — для checkboxes найденных эвристикой
│  "profile"       — взят из профиля аккаунта
│  "auto"          — определён автоматически (known_field_map)
│  None / "" / "unknown" — БАГ в коде выше → logger.error + отброс в шаге 0
│
├── Шаг 0: register_radio
│    ├── авто/ручной клик
│    └── → filled_fields.append("register_radio")
│         source берётся из selectors["register_radio_source"]
│         ⚠️ Проверить: проставляет ли _get_selectors_for_block source для radio
│
├── Шаг 1a: стандартные поля (username, email, password, ...)
│    ├── перебор selectors_list → _try_fill_element()
│    ├── НЕ пишем в шаблон — только копим
│    └── → filled_fields.append(field_name)
│         source берётся из selectors[field_name + "_source"]
│
├── Шаг 1b: custom_fields
│    ├── п.2 из профиля / п.3 авто / п.4 ручной
│    ├── НЕ пишем в шаблон — только копим
│    ├── → filled_fields.append(field_name)
│    └── → new_custom_selectors[field_name] = (sel, source)
│
├── Шаг 2: agree_checkbox (из selectors)
│    └── → filled_fields.append("agree_checkbox")
│         source берётся из selectors["agree_checkbox_source"]
│
├── Шаг 2b: _handle_checkboxes()
│    ├── возвращает list[tuple[str, str]]  ← НОВАЯ СИГНАТУРА
│    ├── НЕ пишет в шаблон сам
│    └── → new_checkboxes = [(sel, source), ...]
│
├── Шаг 3: капча (без изменений)
│
├── Шаг 4: _handle_submit()
│    ├── возвращает tuple[bool, tuple[str, str] | None]  ← НОВАЯ СИГНАТУРА
│    ├── НЕ пишет в шаблон сам
│    └── → ok, found_submit = (sel, source) | None
│         ⚠️ Проверить: проставляет ли _get_selectors_for_block source для submit_button
│
└── Возврат FillFieldsResult:
    {
      "ok":                   bool,
      "filled":               filled_fields,
      "skipped":              skipped_fields,
      "filled_from_outside":  filled_from_outside,
      "new_custom_selectors": new_custom_selectors,  ← НОВОЕ
      "new_checkboxes":       new_checkboxes,        ← НОВОЕ
      "found_submit":         found_submit,          ← НОВОЕ
    }
```

### 3.5 `_save_block_to_template()` — единая точка записи

```
_save_block_to_template(
    block, selectors, filled_fields, template, engine_name,
    new_custom_selectors: dict[str, tuple[str, str]],  ← НОВЫЙ АРГУМЕНТ
    new_checkboxes:       list[tuple[str, str]],       ← НОВЫЙ АРГУМЕНТ
    found_submit:         tuple[str, str] | None,      ← НОВЫЙ АРГУМЕНТ
)
│
│  Вызывается ТОЛЬКО при подтверждённом успехе (3 места в register()):
│  Триггер А — финальный успех (_check_result → success=True)
│  Триггер Б — переход на след. страницу (block_changed and has_any_fields)
│  Триггер В — ручное подтверждение (_confirm_test_mode → True)
│
├── Шаг 0: единая фильтрация ВСЕХ входных данных (ОДИН РАЗ, до блоков А–Г)
│    │
│    │  Правила отсечения (применяются в порядке):
│    │  1. source in (None, "", "unknown") → logger.error(...) + continue  ← ВСЕГДА БАГ ВЫШЕ
│    │  2. source == "template"            → logger.debug(...) + continue  ← уже в шаблоне
│    │  3. _is_dynamic_selector(sel)       → logger.debug(...) + continue  ← динамический ID
│    │  Прошедшие все три проверки — идут в блоки А–Г
│    │
│    │  Единый текст для правила 1 (используется во всех четырёх коллекциях):
│    │  logger.error(
│    │      f"Селектор без валидного source: {sel} "
│    │      f"для поля/элемента '{field or 'submit/checkbox'}' — пропускаем"
│    │  )
│    │
│    ├── filled_fields_clean: list[str]
│    │   for field in filled_fields:
│    │       src = selectors.get(f"{field}_source")
│    │       sel = selectors.get(field, "")
│    │       if src in (None, "", "unknown"):
│    │           logger.error(...)  # правило 1
│    │           continue
│    │       if src == "template": continue  # logger.debug
│    │       if _is_dynamic_selector(sel): continue  # logger.debug
│    │       filled_fields_clean.append(field)
│    │
│    ├── new_custom_clean: dict[str, str]  # {field: sel}
│    │   for field, (sel, src) in new_custom_selectors.items():
│    │       (аналогичные проверки)
│    │
│    ├── new_checkboxes_clean: list[str]
│    │   for sel, src in new_checkboxes:
│    │       (аналогичные проверки)
│    │
│    └── found_submit_clean: str | None
│        (аналогичные проверки для found_submit[0], found_submit[1])
│
├── Блок А: стандартные поля  ← работает с filled_fields_clean
│    ├── итерация по filled_fields_clean
│    ├── лимит: len(existing_list) >= MAX_SELECTORS_PER_FIELD →
│    │         logger.warning(f"Превышен лимит для '{field}' — '{sel}' не сохранён")
│    │         continue
│    ├── selector not in existing_list → в fields_to_save
│    └── label not in existing_label_list → в fields_to_save
│
├── Блок Б: custom_fields  ← НОВЫЙ, работает с new_custom_clean
│    ├── итерация по new_custom_clean
│    ├── лимит: аналогично блоку А
│    └── sel not in template["fields"].get(field_name, []) → в fields_to_save
│
├── Блок В: agree_step.checkboxes  ← НОВЫЙ, работает с new_checkboxes_clean
│    ├── итерация по new_checkboxes_clean
│    ├── лимит: аналогично блоку А
│    └── sel not in existing_checkboxes → в checkboxes_to_save
│
├── Блок Г: submit_button fallback  ← НОВЫЙ, работает с found_submit_clean
│    ├── если found_submit_clean не None
│    ├── лимит: len(existing_submit) >= MAX_SELECTORS_PER_FIELD →
│    │         logger.warning(...) — одиночное значение, return не нужен
│    └── found_submit_clean not in existing_submit → в fields_to_save["submit_button"]
│
├── Блок Д: form_selector  ← ДОБАВЛЕНЫ ПРОВЕРКИ source И _is_dynamic_selector
│    ├── new_form_selector = block.get("form_selector")
│    ├── form_source = block.get("form_selector_source", "heuristic")
│    │   # если source не передаётся в block — считаем "heuristic" и сохраняем
│    ├── если form_source in (None, "", "unknown"):
│    │       logger.error(f"form_selector без валидного source: {new_form_selector} — пропускаем")
│    ├── иначе если form_source == "template":
│    │       logger.debug(f"Отсечён form_selector: {new_form_selector} [source=template]")
│    ├── иначе если _is_dynamic_selector(new_form_selector):
│    │       logger.debug(f"Отсечён динамический form_selector: {new_form_selector}")
│    └── иначе:
│            new_data["registration_page"] = {"form_selector": [new_form_selector]}
│            # передаём список сразу — единообразие с форматом template_manager
│
├── Один вызов update_template() — с обработкой ошибок:
│    try:
│        await self.template_manager.update_template(engine_name, new_data)
│        # memory update ТОЛЬКО после успеха ↓
│    except Exception as e:
│        logger.error(f"Не удалось обновить шаблон '{engine_name}': {e}")
│        return  # template в памяти НЕ обновляем — избегаем рассинхронизации
│
└── Полная синхронизация template в памяти (все ветки, только при успехе):
     │
     ├── fields_to_save →
     │       for key, val in fields_to_save.items():
     │           template.setdefault("fields", {})[key] = val
     │
     ├── checkboxes_to_save →
     │       existing = template.setdefault("agree_step", {})
     │                          .setdefault("checkboxes", [])
     │       for sel in checkboxes_to_save:
     │           if sel not in existing:
     │               existing.append(sel)
     │
     ├── found_submit_clean →  (унифицированный формат — всегда list)
     │       existing = template.setdefault("fields", {})
     │                          .setdefault("submit_button", [])
     │       if isinstance(existing, str):   # старый формат — конвертируем
     │           existing = [existing]
     │           template["fields"]["submit_button"] = existing
     │       if found_submit_clean not in existing:
     │           existing.append(found_submit_clean)
     │
     └── form_selector →
             template.setdefault("registration_page", {})
                     ["form_selector"] = [form_selector]
             # всегда список — единообразие с форматом template_manager
```

### 3.6 Изменения сигнатур

```python
# _handle_checkboxes
# БЫЛО:
async def _handle_checkboxes(
    self, selectors, template, agree_keywords,
    checkbox_skip_keywords, engine_name=None,
) -> None

# СТАНЕТ:
async def _handle_checkboxes(
    self, selectors, template, agree_keywords,
    checkbox_skip_keywords,
    # engine_name убран — метод больше не пишет в шаблон
) -> list[tuple[str, str]]
# Возвращает [(selector, source), ...]
# source: "template" — из шаблона, "heuristic" — найден эвристикой


# _handle_submit
# БЫЛО:
async def _handle_submit(
    self, selectors, form_selector,
    template=None, engine_name=None,
) -> bool

# СТАНЕТ:
async def _handle_submit(
    self, selectors, form_selector,
    # template и engine_name убраны
) -> tuple[bool, tuple[str, str] | None]
# Возвращает (ok, (selector, source) | None)


# _submit_form
# БЫЛО:
async def _submit_form(
    self, selectors, form_selector,
    template=None, engine_name=None,
) -> None

# СТАНЕТ:
async def _submit_form(
    self, selectors, form_selector,
) -> str | None
# Возвращает найденный selector или None
```

---

## 4. Предварительные проверки (выполнить до шага 2)

```bash
# Проверить проставляет ли _get_selectors_for_block source для register_radio и submit_button
grep -n "_source" src/controllers/registration_controller.py | grep -E "register_radio|submit_button"

# Проверить все вызовы _handle_checkboxes (ожидаем 1)
grep -n "_handle_checkboxes" src/controllers/registration_controller.py

# Проверить все вызовы _handle_submit (ожидаем 1)
grep -n "_handle_submit" src/controllers/registration_controller.py

# Проверить все вызовы _save_block_to_template (ожидаем 3)
grep -n "_save_block_to_template" src/controllers/registration_controller.py

# Убедиться что engine_name в _handle_checkboxes используется только для update_template
grep -n "engine_name" src/controllers/registration_controller.py | awk -F: '$2 > 1615 && $2 < 1764'
```

---

## 5. Пошаговый план реализации

| Шаг | Что меняем | Действие |
|---|---|---|
| 1 | `import re` + атрибуты класса + `_is_dynamic_selector` | Добавить `import re`; атрибуты `_PATTERN_CTRL_HASH`, `_PATTERN_HEX_ID`, `MAX_SELECTORS_PER_FIELD`, `DYNAMIC_ID_PREFIXES`; новый статический метод |
| 2 | `_handle_checkboxes` | Убрать `engine_name`; убрать `update_template`; вернуть `list[tuple[str, str]]` |
| 3 | `_handle_submit` / `_submit_form` | Убрать `template` и `engine_name`; вернуть `tuple[bool, tuple[str,str] \| None]` |
| 4 | `FillFieldsResult` + `_fill_fields` | Обновить TypedDict; убрать 6 мест записи; добавить аккумуляторы с source; обновить вызовы шагов 2–3; расширить возврат |
| 5 | `_save_block_to_template` | Новые аргументы; шаг 0 (тройной фильтр + логирование); блоки Б/В/Г; блок Д с проверкой; лимиты; `try/except`; полный memory update |
| 6 | `register()` | Передать новые аргументы из `fill_result` во все 3 вызова `_save_block_to_template` |
| 7 | `xenforo.json` | Вручную очистить накопленные `#ctrl_<хэш>` |

> **Важно:** шаг 5 (`_save_block_to_template`) выполняется **до** шага 6 (`register()`).
> Сначала расширяем метод — потом адаптируем вызовы.

---

## 6. Процесс работы и требования к исполнению

Строго соблюдать указания из `system_prompt`. Если правила забыты — уточнить.

### Протокол рефакторинга

Перед изменением каждого метода:
1. Проверить нет ли других мест использования изменяемого аргумента или поведения
2. Запросить актуальный контекст через команды терминала если файл мог измениться
3. Оценить риски: может ли изменение сломать работающую логику
4. Описать план изменений и запросить подтверждение
5. Только после подтверждения — генерировать код

### Формат вывода кода

- Код в блоке с указанием языка (` ```python `)
- Сначала краткое пояснение решения без кода
- Ждать подтверждение пользователя
- После подтверждения — предоставить код в виде diff/patch
- Не присылать файл целиком
- Указывать полный путь к файлу
- Показывать 3–5 строк контекста ДО и ПОСЛЕ изменения
- При полной замене метода — указывать какой метод меняем, не выводя старый код
- В коде не должно быть временных комментариев «ДО / ПОСЛЕ»

### Порядок реализации шагов

- Реализовывать строго по одному шагу за раз
- После каждого шага ждать подтверждения перед следующим
- Если в процессе обнаруживаются новые зависимости — остановиться и сообщить

---

## 7. Критерии приёмки

- [ ] `import re` добавлен в начало файла
- [ ] Паттерны `_PATTERN_CTRL_HASH` и `_PATTERN_HEX_ID` — атрибуты класса, компилируются один раз
- [ ] `_is_dynamic_selector` — статический метод, использует атрибуты класса
- [ ] Динамические селекторы `#ctrl_[a-f0-9]{32}` (case-insensitive) не сохраняются
- [ ] Статические селекторы (`input[name='password']`, `#ctrl_pageLogin_password`) сохраняются корректно
- [ ] `FillFieldsResult` TypedDict обновлён: добавлены `filled_from_outside`, `new_custom_selectors`, `new_checkboxes`, `found_submit`
- [ ] Все 6 мест немедленной записи в `_fill_fields` удалены
- [ ] `_handle_checkboxes` не пишет в шаблон сам, возвращает `list[tuple[str, str]]`
- [ ] `_handle_submit` / `_submit_form` не пишут в шаблон сами
- [ ] `_save_block_to_template` обрабатывает custom, checkboxes, submit fallback (блоки Б, В, Г)
- [ ] Шаг 0: тройной фильтр — `source in (None, "", "unknown")` → ERROR+отброс; `"template"` → пропуск; динамический → пропуск
- [ ] Блок Д: `form_selector` проверяется по source И `_is_dynamic_selector`; сохраняется как список `[form_selector]`
- [ ] Memory sync `registration_page.form_selector` — всегда список
- [ ] Лимит `MAX_SELECTORS_PER_FIELD = 10` соблюдается во всех блоках А–Г с `logger.warning`
- [ ] `update_template` обёрнут в `try/except`; memory update только при успехе
- [ ] Memory sync `submit_button` — всегда `list`, конвертация старого строкового формата
- [ ] Template в памяти синхронизируется по всем веткам: `fields`, `agree_step.checkboxes`, `registration_page`
- [ ] `xenforo.json` очищен от накопленных лишних селекторов
- [ ] Все изменения согласованы с пользователем пошагово
- [ ] Код соответствует Python 3.12 и стандартам проекта (PEP 8, type hints, docstrings на русском)
