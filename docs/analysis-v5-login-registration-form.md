````markdown
# Анализ задачи: Поддержка форумов с объединённой формой логина/регистрации

---

## 1. Карта зависимостей

```
task-login-registration-form.md
│
├── ЭТАП 1: Поддержка radio-кнопок регистрации
│   ├── selector_finder.py
│   │   ├── find_registration_form()
│   │   │   ├── Место 1 (строки 200–208)
│   │   │   │   └── жёсткое исключение по action/name/id формы → continue
│   │   │   └── Место 2 (строки 309–361)
│   │   │       ├── combined_block строится из атрибутов формы + всех полей
│   │   │       └── жёсткое исключение по combined_block → continue
│   │   └── identify_fields()
│   │       └── Место 3 (строки 619–752)
│   │           └── отсутствует ветка type="radio" → radio уходит в custom_fields
│   │
│   ├── registration_controller.py
│   │   ├── _get_selectors_for_block()
│   │   │   └── STANDARD_KEYS → добавить "register_radio"
│   │   └── _fill_fields()
│   │       └── Шаг 0 (НОВЫЙ) — клик register_radio до Шага 1
│   │
│   ├── common_fields.json
│   │   └── новый ключ: register_radio_keywords
│   │
│   └── xenforo.json
│       ├── registration_page.url → добавить "/login/"
│       └── register_step (НОВЫЙ): radio_selector, radio_value
│
└── ЭТАП 2: Исправление сохранения agree_checkbox
    └── registration_controller.py
        ├── _fill_fields() Шаг 2
        │   └── убрать отдельный update_template — сохранение через _save_block_to_template
        └── _make_block_snapshot()
            └── список standard не включает register_radio → снимок неполный
```

---

## 2. Анализ затронутых мест

---

### ЭТАП 1 — Поддержка radio-кнопок

---

#### Место 1 — `find_registration_form`, фильтрация по action/name/id (строки 200–208)

**Назначение:** собирает все `<form>` на странице, фильтрует нежелательные,
возвращает список кандидатов для дальнейшей оценки.

**Что делает сейчас:**
```python
if any(kw in action for kw in skip_action_kw):
    logger.debug(f"[{selector}] Исключена по action='{action}'")
    continue  # форма навсегда выброшена из кандидатов
if any(kw in name for kw in skip_name_kw):
    continue
if any(kw in form_id for kw in skip_name_kw):
    continue
```
Вход: `action`, `name`, `id` формы + списки ключевых слов из `common_fields.json`.
Выход: форма добавляется в `candidates` или пропускается через `continue`.
Побочный эффект: форма с `action="login/login"` (XenForo) навсегда выбрасывается.

**Зачем менять:** `forum-msk.info` использует одну форму для логина и регистрации
с `action="login/login"`. Жёсткое исключение не даёт системе рассмотреть её.

**Предлагаемое изменение:** заменить `continue` на `score -= 20` — форма остаётся
кандидатом, получает штраф и окажется последней при наличии лучших вариантов.

**Риск:** низкий.

---

#### Место 2 — `find_registration_form`, фильтрация по `combined_block` (строки 309–361)

**Что делает сейчас:**
```python
# Атрибуты формы + ВСЕ поля внутри — в одну строку
combined_block = " ".join(block_tokens).lower()

skip_reason = next(
    (kw for kw in skip_action_kw + skip_name_kw if kw in combined_block),
    None,
)
if skip_reason:
    continue  # форма выброшена
```
Побочный эффект: поле `input[name='login']` на форуме регистрации вызывает
ложное исключение всей формы.

**Зачем менять:** атрибуты формы и поля внутри имеют разную семантику —
нельзя проверять их одним правилом.

**Предлагаемое изменение:**
```python
# Форма — штраф -20 (не исключать жёстко)
form_tokens = [action, name, form_id]
if any(kw in token for token in form_tokens for kw in skip_action_kw):
    score -= 20
    score_details.append("action_login_penalty(-20)")

# Поля внутри — checkbox_skip_keywords остаются с continue (корректно)
```

**Риск:** низкий.

---

#### Место 3 — `identify_fields`, отсутствие ветки `radio` (строки 619–752)

**Что делает сейчас:**
`input[type='radio']` не имеет ветки → проваливается в `custom_fields` с типом `"radio"`.

**Зачем менять:** нужно явно определять radio-кнопки регистрации и возвращать
отдельным ключом по аналогии с `agree_checkbox`.

**Поведение при нескольких radio с совпадением по keywords:**
По аналогии с `password` — берём первую совпавшую, остальные radio того же
`name` игнорируем. Если совпало несколько — логируем предупреждение:
```
logger.warning("Найдено несколько radio регистрации — используем первую")
```

**Предлагаемое изменение:**
```python
if field_type == "radio":
    if any(kw in combined for kw in register_radio_keywords):
        if "register_radio" not in result:
            result["register_radio"] = selector
            result["register_radio_label"] = display_text
            result["register_radio_value"] = attrs.get("value", "")
            logger.debug(f"Определён register_radio: {selector} | '{display_text}'")
        else:
            logger.warning(f"Найдено несколько radio регистрации — пропускаем: {selector}")
    continue  # все radio не идут в custom_fields
```

**Риск:** низкий. Новая ветка добавляется до блока `custom_fields`.

---

#### `_get_selectors_for_block` — добавление `register_radio` в STANDARD_KEYS

**Что делает сейчас:**
```python
STANDARD_KEYS = [
    "username", "email", "confirm_email", "password", "confirm_password",
    "agree_checkbox", "submit_button", "captcha_indicator",
]
```
`register_radio` отсутствует → не получает `*_source` → выпадает из
`_save_block_to_template`.

**Зачем менять:** чтобы `register_radio` проходил через общий механизм
сохранения наравне со всеми остальными полями.

**Предлагаемое изменение:**
```python
STANDARD_KEYS = [
    "username", "email", "confirm_email", "password", "confirm_password",
    "agree_checkbox", "submit_button", "captcha_indicator",
    "register_radio",  # НОВОЕ
]
```

**Риск:** низкий.

---

#### `register_step` в `xenforo.json` — формат

По аналогии с `agree_step`. `radio_selector` включает `value` для однозначной
идентификации нужной кнопки среди группы:

```json
"register_step": {
    "radio_selector": "input[name='register'][value='1']",
    "radio_value": "1"
}
```

Для сравнения — существующая `agree_step`:
```json
"agree_step": {
    "checkboxes": [],
    "submit_button": []
}
```

---

### ЭТАП 2 — Исправление сохранения `agree_checkbox`

---

#### `_make_block_snapshot` — отсутствует `register_radio` в списке `standard`

**Назначение:** создаёт снимок полей блока до submit для последующего сравнения
в `_check_block_changed`. Если снимок до и после отличается — блок изменился,
регистрация продвигается дальше.

**Что делает сейчас:**
```python
standard = (
    "username", "email", "confirm_email", "password", "confirm_password",
    "agree_checkbox", "submit_button", "captcha_indicator",
)
```
Список `standard` жёстко задан и дублирует `STANDARD_KEYS` из
`_get_selectors_for_block`. `register_radio` отсутствует → radio-кнопка
не попадает в снимок.

**Зачем менять:** после клика radio (Шаг 0) форма на XenForo меняется —
появляются поля username/email/password. Но если `register_radio` не в снимке,
`_check_block_changed` сравнивает неполные снимки → может ложно решить что
блок не изменился → система перейдёт к следующему блоку вместо продолжения.

**Предлагаемое изменение:**
```python
standard = (
    "username", "email", "confirm_email", "password", "confirm_password",
    "agree_checkbox", "submit_button", "captcha_indicator",
    "register_radio",  # НОВОЕ
)
```

**Риск:** низкий. Расширение списка, существующая логика не меняется.

---

#### `_fill_fields` Шаг 2 — лишний `update_template`

**Что делает сейчас:**
```python
if engine_name:
    await self.template_manager.update_template(
        engine_name=engine_name,
        new_data={"agree_step": {"checkboxes": [agree_selector]}},
    )
```
Проверяется только `engine_name`. Source не проверяется → запись идёт
**всегда**, даже если `source="template"` (данные уже в шаблоне).

**Ответы на вопросы коллеги:**

**Q: Гарантированно ли `agree_checkbox` попадёт в `_save_block_to_template`?**
Да. Строка 1226 уже добавляет его в `filled_fields` при успешном клике:
```python
filled_fields.append("agree_checkbox")
```
`_save_block_to_template` итерируется по `filled_fields` → `agree_checkbox`
там есть → обрабатывается.

**Q: Есть ли случаи когда `filled_fields` не включает `agree_checkbox`
хотя клик был?**
Один случай — элемент найден но `el_type not in _input_types` (это кнопка,
не чекбокс). В этом случае клика тоже нет — логика консистентна.

**Q: Что происходит если `source="template"`? Должно ли сохраняться заново?**
Сейчас (до Этапа 2) — отдельный `update_template` перезаписывает его обратно.
`_merge_template` находит данные уже в шаблоне — файл физически не меняется,
но вызов лишний.
После Этапа 2 — `_save_block_to_template` проверит `source="template"` и
**пропустит**. Это правильное поведение.

**Зачем менять:** убрать дублирование. `agree_checkbox` уже в `STANDARD_KEYS`,
уже получает `*_source`, уже попадает в `filled_fields` — общий механизм
полностью покрывает задачу.

**Риск:** низкий. Поведение не меняется — только убирается лишний I/O.

---

## 3. Схема модуля `selector_finder.py`

```
identify_fields(form_element)
│
├── загружаем common_fields (register_radio_keywords, agree_keywords, ...)
├── получаем все input/textarea/select внутри формы
│
└── для каждого элемента:
    │
    ├── type=hidden/submit/button → пропустить
    ├── невидимый               → пропустить
    │
    ├── type=password → password / confirm_password
    │
    ├── type=checkbox
    │   ├── checkbox_skip_keywords → пропустить
    │   ├── agree_keywords         → agree_checkbox
    │   └── остальные              → custom_fields
    │
    ├── type=radio  ← НОВОЕ
    │   ├── register_radio_keywords совпал
    │   │   ├── первый найденный   → register_radio + register_radio_value
    │   │   └── повторный          → warning, пропустить
    │   └── не совпал              → пропустить (не в custom_fields)
    │
    ├── type=email / email_keywords → email / confirm_email
    ├── username_keywords           → username
    ├── known_field_types           → city/gender/phone/... → custom_fields
    └── остальные                   → custom_fields


find_registration_form(template)
│
├── собираем все <form> на странице
│
└── для каждой формы:
    │
    ├── БЫЛО: action/name/id совпал с skip_kw → continue
    │   СТАЛО: совпал → score -= 20
    │
    ├── собираем visible_inputs, visible_buttons, visible_checkboxes
    │
    ├── БЫЛО: combined_block (форма + все поля) совпал → continue
    │   СТАЛО:
    │   ├── form_tokens (action/name/id формы) совпал → score -= 20
    │   └── checkbox_skip_keywords по полям → continue (без изменений)
    │
    └── подсчёт score → сортировка → возврат списка кандидатов
```

---

## 4. Схема модуля `registration_controller.py`

```
_get_selectors_for_block(template, block)
│
└── STANDARD_KEYS = [
        "username", "email", "confirm_email", "password", "confirm_password",
        "agree_checkbox", "submit_button", "captcha_indicator",
        "register_radio",  ← НОВОЕ
    ]
    │
    └── для каждого ключа:
        ├── совпадение по селектору или label → source="template"
        ├── найдено в блоке, нет в шаблоне   → source="common_fields" или "manual"
        └── не найдено в блоке, есть в DOM   → source="template"


_fill_fields(selectors, account_data, ...)
│
├── Шаг 0: register_radio  ← НОВЫЙ
│   ├── нет selectors["register_radio"]
│   │   └── молча пропустить → Шаг 1
│   └── есть selectors["register_radio"]:
│       ├── source="template"
│       │   └── клик → sleep(0.5) → filled_fields.append("register_radio") → Шаг 1
│       ├── source="common_fields"
│       │   └── клик → sleep(0.5) → filled_fields.append("register_radio") → Шаг 1
│       └── source="manual"
│           ├── _ask_manual_input
│           │   ├── ввёл → клик → sleep(0.5)
│           │   │         → filled_fields.append("register_radio") → Шаг 1
│           │   └── пропуск → Шаг 1
│           └── [сохранение — через _save_block_to_template при успехе]
│
├── Шаг 1: standard_fields (username, email, password, ...)
│
├── Шаг 2: agree_checkbox
│   ├── клик → filled_fields.append("agree_checkbox")
│   └── УБРАТЬ отдельный update_template ← ЭТАП 2
│       [сохранение — через _save_block_to_template при успехе]
│
├── Шаг 3: капча
└── Шаг 4: submit


_make_block_snapshot(selectors)
│   Вызывается ДО submit — фиксирует состояние блока
│
└── standard = (
        "username", "email", "confirm_email", "password", "confirm_password",
        "agree_checkbox", "submit_button", "captcha_indicator",
        "register_radio",  ← НОВОЕ
    )
    └── для каждого поля — добавляем (имя, тип, селектор, label, value) в frozenset
    └── custom_fields — добавляем отдельно


_save_block_to_template(block, selectors, filled_fields, ...)
│   Вызывается ТОЛЬКО при подтверждённом успехе регистрации
│
└── для каждого поля из filled_fields:
    ├── source="template"      → пропустить (уже есть в шаблоне)
    ├── source="common_fields" → сохранить selector + label
    └── source="manual"        → сохранить selector + label
    │
    └── один вызов update_template со всеми новыми данными
        (включая agree_checkbox и register_radio если source != "template")
```

---

## 5. Предлагаемые патчи

### Этап 1 — Поддержка radio-кнопок

| № | Файл | Изменение |
|---|---|---|
| Патч 1 | `selector_finder.py` | Место 1: три `continue` → `score -= 20` |
| Патч 2 | `selector_finder.py` | Место 2: `form_tokens` (штраф) вместо `combined_block` (исключение) |
| Патч 3 | `selector_finder.py` | `identify_fields`: новая ветка `type="radio"` → ключ `register_radio` |
| Патч 4 | `registration_controller.py` | `STANDARD_KEYS`: добавить `"register_radio"` |
| Патч 5 | `registration_controller.py` | `_fill_fields`: новый Шаг 0 — клик register_radio |
| Патч 6 | `xenforo.json` | Добавить `/login/` в url + секцию `register_step` |
| Патч 7 | `common_fields.json` | Новый ключ `register_radio_keywords` |

### Этап 2 — Исправление сохранения agree_checkbox

| № | Файл | Изменение |
|---|---|---|
| Патч 8 | `registration_controller.py` | `_fill_fields` Шаг 2: убрать отдельный `update_template` для `agree_checkbox` |
| Патч 9 | `registration_controller.py` | `_make_block_snapshot`: добавить `"register_radio"` в список `standard` |

---

## 6. Ожидает подтверждения

Все замечания учтены. Жду подтверждения на генерацию патчей.
````
