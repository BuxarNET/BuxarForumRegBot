# 🐛 Задача: Исправление ошибок тестирования

## Описание задачи
Проект написан. В ходе анализа логов и кода выявлены ошибки. Все детали — в разделах ниже.

---

## 🎯 Согласованный план исправлений (9 этапов)

---

### Этап 1 — `selector_finder.py` → `find_registration_form`: score по `display_text`

**Метод:** `find_registration_form` (~строки 330–340)

**Корневая причина:**
В цикле подсчёта score для чекбоксов читается только `name/id/value`, а `display_text` (label с экрана) не используется. Для кнопок `btn.get_attribute("innerText")` у `<input type="submit">` всегда возвращает `None` — только `value` работает. Из-за этого поиск по `submit_kw` и `agree_kw` не учитывает видимый текст на экране. Кнопка «Я согласен с этими условиями» получает меньше очков чем должна.

**`_get_display_text` уже реализован корректно** — просто не вызывается в `find_registration_form`.

**Что изменить:**
В обоих циклах (`visible_checkboxes` и `visible_buttons`) добавить вызов `_get_display_text` и включить результат в `combined` для сравнения с ключевыми словами. Совпадение `display_text` с `submit_kw` / `agree_kw` даёт +3..+5 очков — столько же сколько совпадение по `name/id`:
```python
# Чекбоксы:
display_text = await self._get_display_text(cb)
combined = f"{name} {cb_id} {cb_val} {display_text.lower()}"
if any(kw in combined for kw in agree_kw):
    score += 3   # совпадение по любому полю включая display_text

# Кнопки:
display_text = await self._get_display_text(btn)
btn_combined = display_text.lower()
if any(kw in btn_combined for kw in submit_kw):
    score += 2
```

**Результат в логе:**
```
[#frmAgreement] score=17 | ..., agreement(agree_kw+3), 'Я согласен'(submit_kw+2)
```

---

### Этап 2 — `registration_controller.py` → `_get_selectors_for_block`: только реальные поля блока

**Метод:** `_get_selectors_for_block` (~строка 432)

**Корневая причина:**
```python
selectors.update(template_fields)   # ← ВСЕ поля шаблона без проверки
```
Блок `#frmAgreement` (только галочки + кнопка) получает `username`, `email`, `password` из шаблона `forum2x2` — хотя этих полей в блоке нет. Система предлагает заполнить несуществующие поля.

**Что изменить:**
Убрать `selectors.update(template_fields)`. Использовать новую логику с меткой `*_source` — она нужна Этапу 6 чтобы знать откуда взялось поле и нужно ли его сохранять в шаблон.

Совпадение определяется по **селектору ИЛИ по label** — оба варианта считаются «полем из шаблона»:

```python
async def _get_selectors_for_block(
    self,
    template: dict | None,
    block: dict,
) -> dict:
    """Формирует словарь селекторов только для полей реально присутствующих в текущем блоке.

    Для каждого поля проставляет метку *_source:
    - "template"      — селектор или label совпали с шаблоном (не сохраняем повторно)
    - "common_fields" — найдено эвристикой по ключевым словам
    - "manual"        — найдено без label (потребуется ручной ввод)
    """
    selectors: dict = {}

    if not template:
        template = {}

    template_fields = template.get("fields", {}) or {}

    STANDARD_KEYS = [
        "username", "email", "password", "confirm_password",
        "agree_checkbox", "submit_button", "captcha_indicator",
    ]

    # Локальный кэш DOM-проверок — живёт только внутри одного вызова метода
    # Избегает повторных page.query() для одного и того же селектора
    _dom_cache: dict[str, bool] = {}

    async def _exists_in_dom(sel: str) -> bool:
        if sel not in _dom_cache:
            el = await self.page.query(sel, raise_exc=False, timeout=1)
            _dom_cache[sel] = el is not None
            logger.debug(
                f"DOM-проверка: '{sel}' → "
                f"{'найден' if _dom_cache[sel] else 'отсутствует'}"
            )
        return _dom_cache[sel]

    for key in STANDARD_KEYS:
        # Что нашёл текущий блок
        block_val = block.get(key)
        block_label = block.get(f"{key}_label") or ""

        # Что уже есть в шаблоне
        tmpl_sel_raw = template_fields.get(key)
        tmpl_sel = (
            tmpl_sel_raw[0]
            if isinstance(tmpl_sel_raw, list) and tmpl_sel_raw
            else tmpl_sel_raw
        )
        tmpl_labels = template_fields.get(f"{key}_label", [])
        if isinstance(tmpl_labels, str):
            tmpl_labels = [tmpl_labels]

        # Совпадение по селектору
        is_match_by_selector = bool(tmpl_sel and block_val and tmpl_sel == block_val)

        # Совпадение по label (частичное вхождение в любую сторону)
        is_match_by_label = False
        if block_label and tmpl_labels:
            block_lower = block_label.lower()
            is_match_by_label = any(
                (lbl.lower() in block_lower) or (block_lower in lbl.lower())
                for lbl in tmpl_labels
                if lbl
            )

        if is_match_by_selector or is_match_by_label:
            # Поле из шаблона — берём как есть, повторно не сохраняем
            selectors[key] = block_val or tmpl_sel
            selectors[f"{key}_label"] = block_label
            selectors[f"{key}_source"] = "template"
            logger.debug(
                f"Поле '{key}' — совпадение "
                f"({'селектор' if is_match_by_selector else 'label'}) → source=template"
            )
            continue

        if block_val:
            # Новый вариант — ни селектор ни label не совпали с шаблоном
            selectors[key] = block_val
            selectors[f"{key}_label"] = block_label
            selectors[f"{key}_source"] = "common_fields" if block_label else "manual"
            logger.debug(
                f"Поле '{key}' — новый вариант → "
                f"source={selectors[f'{key}_source']}"
            )
            continue

        # Поле не найдено ни в блоке, ни совпадений с шаблоном нет
        # Последний шанс: шаблонный селектор существует в DOM текущей страницы
        if tmpl_sel and await _exists_in_dom(tmpl_sel):
            selectors[key] = tmpl_sel
            selectors[f"{key}_label"] = ""
            selectors[f"{key}_source"] = "template"
            logger.debug(
                f"Поле '{key}' — не в блоке, но шаблонный селектор найден в DOM → "
                f"source=template"
            )
        else:
            logger.debug(
                f"Поле '{key}' — не найдено ни в блоке, ни в DOM → пропускаем"
            )

    # Переносим все *_label из блока (для ручного ввода и custom полей)
    for k in list(block.keys()):
        if k.endswith("_label") and k not in selectors:
            selectors[k] = block[k]

    # custom_fields
    if "custom_fields" in block:
        selectors["custom_fields"] = block["custom_fields"]

    logger.debug(f"Итоговые селекторы для блока: {list(selectors.keys())}")
    return selectors
```

---

### Этап 3 — `registration_controller.py` → `register` + `_fill_fields`: `username_was_filled` только при реальном заполнении

**Методы:** `register` (~строка 222), `_fill_fields` (~строка 589)

**Корневая причина:**
```python
if selectors.get("username"):    # True если ключ есть в словаре (из шаблона)
    username_was_filled = True   # ставится независимо от реального заполнения
```
Из-за этого Правило 3 в `_check_result` срабатывает ложно → система считает регистрацию успешной когда ничего не заполнено.

**Что изменить:**

`_fill_fields` возвращает не `bool`, а `dict`:
```python
# Было:
return True

# Стало:
return {
    "ok": True,
    "filled": ["username", "email"],   # реально заполненные поля
    "skipped": ["password"],           # пропущенные пользователем
}
```
Внутри `_fill_fields` при каждом успешном заполнении поля добавляем `field_name` в `filled_fields: list[str]`.

В `register()`:
```python
fill_result = await self._fill_fields(...)

if not fill_result["ok"]:
    return {...}  # timeout — было: if not filled

username_was_filled = "username" in fill_result["filled"]
```

---

### Этап 4 — `registration_controller.py` → `_handle_checkboxes`: `form_selector` список вместо строки

**Метод:** `_handle_checkboxes` (~строка 845)

**Корневая причина:**
`form_selector` из шаблона хранится как список `["#frmAgreement"]`. `page.query()` ожидает строку. Падает с `'list' object has no attribute 'startswith'` каждый раз когда есть шаблон с `form_selector`.

**Что изменить (две строки вместо одной):**
```python
# Было:
form_selector = (template or {}).get("registration_page", {}).get("form_selector")

# Стало:
_raw_fs = (template or {}).get("registration_page", {}).get("form_selector")
form_selector = (_raw_fs[0] if _raw_fs else None) if isinstance(_raw_fs, list) else _raw_fs
```

**Риск:** минимальный. Одна строка, логика ниже не меняется.

---

### Этап 5 — `registration_controller.py` → `_find_submit_in_form` + `_find_submit_on_page`: правильная кнопка внутри блока

**Методы:** `_find_submit_in_form` (~строка 1175), `_find_submit_on_page` (~строка 1210)

**Три независимые корневые причины:**

**Причина 1:** `btn.get_attribute("innerText")` для `<input type="submit">` всегда `None`. Поиск по `submit_keywords` не работает → код всегда падает в fallback `buttons[0]`.

**Причина 2:** `_generate_css_selector` возвращает глобальный селектор без привязки к форме. `human_click(sel)` находит первый элемент на всей странице — не тот что внутри нужного блока. Наш вариант `f"{form_selector} input[type='submit']"` не покрывает `<button>` без `type`.

**Причина 3:** Кнопка может быть найдена но невидима — `human_click` упадёт с «element is not visible».

Проблема общая для любой формы — не зависит от конкретного селектора.

**Что изменить:**

**1.** Заменить чтение `innerText` на `_get_display_text` (уже есть в проекте, умеет читать и `innerText` и `value`):
```python
# Было:
text = (btn.get_attribute("innerText") or "").strip().lower()
value = (btn.get_attribute("value") or "").lower()
btn_combined = f"{text} {value}"

# Стало:
btn_display = await self.selector_finder._get_display_text(btn)
btn_combined = btn_display.lower()
```

**2.** Генерацию селектора заменить на JS-поиск внутри DOM-элемента формы через `execute_script` (наш API — не `page.evaluate`):
```python
# Вместо _generate_css_selector — JS внутри формы:
response = await self.page.execute_script(f"""
    (function() {{
        var form = document.querySelector('{form_selector}');
        if (!form) return null;
        var buttons = Array.from(form.querySelectorAll(
            'input[type="submit"], button[type="submit"], button'
        ));
        if (!buttons.length) return null;
        var best = buttons[0];
        if (best.id) return '#' + best.id;
        if (best.name) return best.tagName.toLowerCase() + '[name="' + best.name + '"]';
        return null;
    }})()
""")
sel = response["result"]["result"]["value"] if response else None
```

**3.** После нахождения кнопки — проверка видимости и скролл если нужно:
```python
if sel:
    try:
        element = await self.page.query(sel, raise_exc=False, timeout=2)
        if element and not await element.is_visible():
            logger.debug(f"Кнопка найдена но не видима — скроллим: {sel}")
            await self.browser.scroll_to_element(element)
    except Exception as e:
        logger.debug(f"Проверка видимости кнопки: {e}")
```

**4.** Исправить вводящий в заблуждение лог:
```python
# Было:
logger.warning("Кнопка подтверждения не найдена — переходим в ручной режим")

# Стало:
logger.warning(
    f"Кнопка найдена ({found_selector}), "
    f"но нажать не удалось: {e} — переходим в ручной режим"
)
```

---

### Этап 6 — `registration_controller.py` → `_save_block_to_template` + `register`: сохранение только после успеха

**Методы:** `register` (~строка 250), `_save_block_to_template` (~строка 489)

**Корневые причины:**

**6.1** — вызов стоит **до** `_submit_form` и `_check_result`. Сохраняем незаполненный/неотправленный блок.

**6.2** — сохраняет все поля из `selectors` включая взятые из шаблона. Шаблон перезаписывается мусором.

**6.3** — неверное имя файла при сохранении (`forum2x2.json` вместо `forum2x2.ru.json`).
> ⚠️ Требует `template_manager.py` для анализа — реализуем после получения файла.

**Что изменить:**

**6.1** — переносим вызов в блок `if success:`:
```python
# Убрать отсюда (между капчей и _submit_form):
await self._save_block_to_template(...)

# Добавить сюда (в блок if success:):
if success:
    await self._save_block_to_template(
        block=current_block,
        selectors=selectors,
        filled_fields=fill_result["filled"],
        template=template,
        engine_name=engine_name,
    )
    return {"success": True, ...}
```

**6.2** — новая реализация `_save_block_to_template` с логикой `*_source` из Этапа 2:

```python
async def _save_block_to_template(
    self,
    block: dict,
    selectors: dict,
    filled_fields: list[str],
    template: dict | None,
    engine_name: str | None,
) -> None:
    """Сохраняет в шаблон только новые данные — поля с source != 'template'.

    Правила:
    - source == "template" → ничего не добавляем (уже есть в шаблоне)
    - source == "common_fields" или "manual" → добавляем селектор и label
    - Дубли селекторов и label не добавляем
    """
    if not engine_name or not template or not filled_fields:
        return

    logger.debug(f"Сохраняем в шаблон новые данные для полей: {filled_fields}")

    template_fields = template.setdefault("fields", {})
    changed = False

    for key in filled_fields:
        source = selectors.get(f"{key}_source", "unknown")

        if source == "template":
            logger.debug(f"Поле '{key}' — source=template → пропускаем")
            continue

        # Добавляем селектор если его ещё нет
        new_sel = selectors.get(key)
        if new_sel:
            sel_list = template_fields.setdefault(key, [])
            if not isinstance(sel_list, list):
                sel_list = [sel_list]
                template_fields[key] = sel_list
            if new_sel not in sel_list:
                sel_list.append(new_sel)
                logger.info(f"Добавлен новый селектор для '{key}': {new_sel}")
                changed = True

        # Добавляем label если его ещё нет (без дублей)
        new_label = selectors.get(f"{key}_label")
        if new_label:
            label_list = template_fields.setdefault(f"{key}_label", [])
            if not isinstance(label_list, list):
                label_list = [label_list]
                template_fields[f"{key}_label"] = label_list
            if new_label not in label_list:
                label_list.append(new_label)
                logger.info(f"Добавлен новый label для '{key}': «{new_label}»")
                changed = True

    if changed:
        await self.template_manager.update_template(
            engine_name=engine_name,
            new_data={"fields": template_fields},
        )
        logger.info(f"Шаблон '{engine_name}' обновлён")
    else:
        logger.debug("Новых данных для шаблона нет — файл не перезаписываем")
```

**Правило сохранения в шаблон (итоговое):**

| Источник (`*_source`) | Действие при успехе |
|---|---|
| `"template"` — совпал селектор или label | Не сохраняем — уже есть |
| `"common_fields"` — найдено эвристикой | Добавляем селектор и label если нет дублей |
| `"manual"` — найдено без label | Добавляем селектор и label если нет дублей |

---

### Этап 7 — `registration_controller.py` → `_check_result`: подробные логи

**Метод:** `_check_result` (~строки 1280–1305)

**Корневая причина:**
Логи «Правило 1: проверка индикаторов ошибки» — без деталей. При ложном срабатывании невозможно понять что именно проверялось и почему сработало.

**Что изменить — добавить детальный вывод по каждому индикатору:**
```python
logger.debug(
    f"Правило 1: проверяем {len(error_indicators)} индикаторов ошибки: "
    f"{error_indicators}"
)
for phrase in error_indicators:
    found = phrase.lower() in page_source_lower
    logger.debug(f"  Правило 1: '{phrase}' → {'НАЙДЕН ⚠' if found else 'не найден'}")
    if found:
        return False, "registration_error"

logger.debug(
    f"Правило 2: проверяем {len(success_indicators)} индикаторов успеха: "
    f"{success_indicators}"
)
for phrase in success_indicators:
    found = phrase.lower() in page_source_lower
    logger.debug(f"  Правило 2: '{phrase}' → {'НАЙДЕН ✓' if found else 'не найден'}")
    if found:
        return True, None

logger.debug(
    f"Правило 3: username_was_filled={username_was_filled} — "
    f"{'запускаем анализ полей' if username_was_filled else 'пропускаем'}"
)
```

---

### Этап 8 — `registration_controller.py` → `_ask_manual_input` + `_fill_fields`: показ `display_text` вместе с селектором

**Методы:** `_ask_manual_input` (~строка 939), `_fill_fields` (~строка 670)

**Корневая причина:**
`display_text` собирается в `identify_fields` и хранится в `selectors` под ключами `*_label`, но `_fill_fields` не передаёт его в `_ask_manual_input`. Пользователь видит только системное имя и селектор.

Показываем **и** `display_text` **и** селектор — вместе, не вместо.

**Изменение в `_ask_manual_input`** — добавляем параметр `display_text: str = ""`:
```python
async def _ask_manual_input(
    self,
    field_name: str,
    selector_hint: str,
    hint: str,
    display_text: str = "",
) -> str:
    ...
    print(f"ТРЕБУЕТСЯ РУЧНОЙ ВВОД")
    if display_text:
        print(f"Поле:     {field_name} — «{display_text}»")
        print(f"Селектор: {selector_hint}")
    else:
        print(f"Поле:     {field_name} ({selector_hint})")
    print(f"Задание:  {hint}")
```

**Изменение в `_fill_fields`** — передаём `display_text` из `selectors`:
```python
manual_value = await self._ask_manual_input(
    field_name=field_name,
    selector_hint=selector if isinstance(selector, str) else selectors_list[0],
    display_text=selectors.get(f"{field_name}_label", ""),
    hint=f"Введите значение для поля '{field_name}'"
)
```

**Итоговый вид для пользователя:**
```
============================================================
ТРЕБУЕТСЯ РУЧНОЙ ВВОД
Поле:     username — «Имя пользователя»
Селектор: input[name='username']
Задание:  Введите значение для поля 'username'
Нажмите Enter без ввода чтобы пропустить поле.
============================================================
```

---

### Этап 9 — `browser_controller.py` + `main_orchestrator.py` + `registration_controller.py`

**Методы:** `goto` в `browser_controller.py`, `_process_forum` в `main_orchestrator.py`, `register` в `registration_controller.py`

#### Корневые причины

**9.A — Пауза ~5 минут:**
`browser.goto()` не имеет таймаута — ждёт бесконечно пока не сработает внешний `asyncio.wait_for(TAB_TIMEOUT_SECONDS=300)`. Логика с `http_status` в `registration_controller` даже не успевает сработать.

**9.B — Лишний простой при не-200:**
`None != 200` → `True` → `window.location.reload()` + `sleep(find_timeout=60)` — 60 секунд потери при каждой проблемной странице. Логика повторной попытки **не убирается** — она переносится в `browser_controller.goto()` и retry-цикл оркестратора. Функциональность сохраняется, ответственность на правильном уровне.

**9.C — Вкладки не закрываются:**
`finally` блок в `_process_forum` содержит `pass`. Оба конфига `TAB_PER_REGISTRATION` и `CLOSE_TAB_AFTER_REGISTRATION` не работают — заглушки. Реализуем с нуля.

---

#### 9.1 — `browser_controller.py`: новый `goto()` + `close_tab()`

Переменные берём существующие — **новые не добавляем**:
- `PAGE_LOAD_WAIT = 5` → пауза после загрузки для медленных сайтов
- `FIND_REGISTRATION_PAGE_TIMEOUT = 60` → таймаут ожидания загрузки

```python
async def goto(
    self,
    url: str,
    page_load_wait: float = 5.0,
    load_timeout: float = 60.0,
) -> None:
    """Переходит по URL в текущей вкладке.

    Ждёт загрузки страницы не более load_timeout секунд.
    При успехе делает паузу page_load_wait секунд для медленных сайтов.
    При таймауте бросает исключение — оркестратор обработает через retry.

    Args:
        url: адрес страницы.
        page_load_wait: пауза после успешной загрузки (сек).
        load_timeout: максимальное время ожидания загрузки (сек).
    """
    try:
        await asyncio.wait_for(
            self._current_tab.go_to(url),
            timeout=load_timeout
        )
        logger.info(f"Перешли на {url}")
    except asyncio.TimeoutError:
        logger.warning(f"Таймаут загрузки страницы ({load_timeout}с): {url}")
        raise

    # Пауза только при успешной загрузке — не в finally
    if page_load_wait > 0:
        logger.debug(f"Пауза {page_load_wait}с после загрузки страницы")
        await asyncio.sleep(page_load_wait)
```

**Новый метод `close_tab()`:**
```python
async def close_tab(self, tab=None) -> None:
    """Закрывает вкладку.

    Args:
        tab: вкладка для закрытия. Если None — закрывает текущую.
    """
    target = tab or self._current_tab
    if not target:
        return
    try:
        await target.close()
        logger.debug("Вкладка закрыта")
    except Exception as e:
        logger.warning(f"Не удалось закрыть вкладку: {e}")
```

---

#### 9.2 — `main_orchestrator.py` → `_process_forum`: Вариант Б (`TAB_PER_REGISTRATION`)

Значения из конфига передаём в `goto()` — новые переменные не создаём:
- `TAB_PER_REGISTRATION = True` → открывать новую вкладку для каждой попытки
- `CLOSE_TAB_AFTER_REGISTRATION = False` → закрывать вкладку после регистрации
- Защита: единственная вкладка **никогда не закрывается** (счётчик `tab_count`)

```python
tab = None
tab_count = 0   # счётчик открытых вкладок — защита от закрытия единственной

while attempt < max_retries:
    try:
        if self._config.get("TAB_PER_REGISTRATION", True):
            tab = await browser.new_tab()
            tab_count += 1
            reg_controller.page = tab
            reg_controller.selector_finder.page = tab

        await browser.goto(
            forum_url,
            page_load_wait=self._config.get("PAGE_LOAD_WAIT", 5),
            load_timeout=self._config.get("FIND_REGISTRATION_PAGE_TIMEOUT", 60),
        )

        result = await asyncio.wait_for(
            reg_controller.register(user_data),
            timeout=self._config.get("TAB_TIMEOUT_SECONDS", 300),
        )

    except asyncio.TimeoutError:
        logger.warning(f"User {username} @ {forum_url}: таймаут регистрации")
        result = {"success": False, "reason": "timeout", ...}

    except Exception as e:
        logger.error(f"User {username} @ {forum_url}: {type(e).__name__}: {e}")
        result = {"success": False, "reason": "browser_crash", ...}

    finally:
        # Закрываем вкладку только если:
        # 1. Конфиг разрешает CLOSE_TAB_AFTER_REGISTRATION
        # 2. TAB_PER_REGISTRATION включён
        # 3. Вкладка была открыта в этой итерации
        # 4. Это не единственная вкладка (tab_count > 1)
        if (
            self._config.get("CLOSE_TAB_AFTER_REGISTRATION", True)
            and self._config.get("TAB_PER_REGISTRATION", True)
            and tab is not None
            and tab_count > 1
        ):
            await browser.close_tab(tab)
            tab_count -= 1
            tab = None
```

---

#### 9.3 — `registration_controller.py` → `register`: перенос логики перезагрузки

Логика повторной попытки загрузки переносится в `browser_controller.goto()` и retry-цикл оркестратора — не убирается совсем.

Из `registration_controller.register()` убираем:
- `page_load_wait = self.config.get("page_load_wait", 5)`
- `find_timeout = self.config.get("find_registration_page_timeout", 60)`
- `await asyncio.sleep(page_load_wait)`
- весь блок `if http_status != 200` с `reload()` и `sleep(find_timeout)`

Из `reg_config` в `main_orchestrator.py` убираем два ключа которые больше не нужны `registration_controller`:
```python
# Удалить из reg_config:
# "find_registration_page_timeout": self._config.get("FIND_REGISTRATION_PAGE_TIMEOUT", 60),
# "page_load_wait": self._config.get("PAGE_LOAD_WAIT", 5),
```

Остаётся только однократная проверка HTTP статуса — без `reload` и без `sleep`:
```python
# Было: None != 200 → reload → sleep(60) → повторная проверка
# Стало: только проверка, retry — задача оркестратора
if http_status is not None and http_status != 200:
    logger.warning(f"Форум вернул статус {http_status} — недоступен: {url}")
    return {
        "success": False,
        "message": f"Форум недоступен (HTTP {http_status})",
        "reason": "page_unavailable",
        "template_used": None,
        "screenshot": await self._take_screenshot(
            f"http_error_{http_status}",
            account_data.get("username")
        ),
        "form_data": account_data,
    }
# http_status = None — не удалось прочитать статус, продолжаем
```

> Скриншот при ошибке HTTP сохраняется с префиксом `http_error_{status}` — помогает отличить 403/429/503 от редиректа.

---

## 📊 Сводная таблица всех этапов

| # | Файл(ы) | Метод(ы) | Суть изменения | Строки | Риск |
|---|---------|----------|---------------|--------|------|
| 1 | `selector_finder.py` | `find_registration_form` | `display_text` в score (+3..+5 за совпадение с ключевыми словами) | ~330–340 | Низкий |
| 2 | `registration_controller.py` | `_get_selectors_for_block` | Логика `*_source` + локальный DOM-кэш + логирование | ~432 | Средний |
| 3 | `registration_controller.py` | `register` + `_fill_fields` | `_fill_fields` возвращает `dict`, `username_was_filled` по факту | ~222, ~589 | Средний |
| 4 | `registration_controller.py` | `_handle_checkboxes` | `list → str` для `form_selector` | ~845 | Минимальный |
| 5 | `registration_controller.py` | `_find_submit_in_form` + `_find_submit_on_page` | `_get_display_text` + JS-поиск внутри формы + проверка видимости | ~1175, ~1210 | Низкий |
| 6 | `registration_controller.py` | `_save_block_to_template` + `register` | Перенос после успеха + только новые поля по `*_source` + без дублей | ~250, ~489 | Средний |
| 7 | `registration_controller.py` | `_check_result` | Подробные логи по каждому индикатору | ~1280 | Минимальный |
| 8 | `registration_controller.py` | `_ask_manual_input` + `_fill_fields` | `display_text` и селектор вместе в ручном вводе | ~939, ~670 | Минимальный |
| 9 | `browser_controller.py` + `main_orchestrator.py` + `registration_controller.py` | `goto` + `close_tab` + retry-цикл + `register` | Таймаут навигации + управление вкладками + перенос логики перезагрузки | ~129, ~575–625, ~96–140 | Средний |

---

## 📋 Требования к исполнению

### Стандарты проекта
- ✅ Python 3.12 (аннотации типов, match/case, f-строки)
- ✅ Весь код, комментарии, логи — **на русском языке**
- ✅ Pydoll — **НЕ выдумывать API**, сверяться с `browser_controller.py`
- ✅ Рефакторинг — **запрашивать разрешение** перед изменением работающих функций
- ✅ Приоритет: Безопасность > Стабильность > Производительность

### Специфика этой задачи

#### 1. Анализ и запросы контекста (итеративный процесс)
- [ ] **Изучи план:** Проанализируй этапы выше, определи затронутые модули и файлы.
- [ ] **Запроси контекст через команды:** Если нужно увидеть код — **не проси текст, генерируй готовую команду для терминала**.
    - **Формат:** Только команды чтения (`grep`, `sed -n 'p'`, `cat`, `head`, `tail`).
    - **Запрещено:** Команды записи (`sed -i`, `echo >`, `rm`, `mv`).
    - **Пример:**
      ```bash
      sed -n '432,488p' src/controllers/registration_controller.py
      grep -A 10 "def _get_display_text" src/selector_finder.py
      ```
- [ ] **Жди вывода:** После вставки результата команды — продолжай анализ.
- [ ] **Карта зависимостей:** Видишь вызов внешнего метода — запроси его реализацию через `grep`.

#### 2. Предложение решения
- [ ] Выяви корневые причины на основе плана и полученного кода.
- [ ] Предложи исправления с обоснованием.
- [ ] **Запроси подтверждение** перед генерацией кода.

#### 3. Формат выдачи кода (патч)
- [ ] **Не присылай файл целиком.** Только diff/patch.
- [ ] Указывай полный путь к файлу.
- [ ] Показывай контекст: 3–5 строк до и после изменения.
- [ ] Чётко обозначай место вставки.

---

## 📝 Формат отчёта по каждому этапу

```markdown
### Этап #{{номер}}
**Файл:** `путь/к/файлу.py:строка`
**Метод:** `имя_метода`

#### 🔍 Анализ
- **Корневая причина:** {{почему происходит}}
- **Влияние:** {{какие функции затронуты}}
- **Риски исправления:** {{что может сломаться}}

#### 💡 Решение
- **Что предлагаю изменить:** {{описание}}
- **Почему это сработает:** {{обоснование}}

#### ❓ Запрос подтверждения
Хотите применить это изменение? Это затронет: {{список функций}}
```

---

## ✅ Критерии приёмки

- [ ] Все 9 этапов реализованы и согласованы с пользователем
- [ ] Код соответствует Python 3.12 и стандартам проекта
- [ ] `browser.goto()` имеет таймаут, использует `PAGE_LOAD_WAIT` и `FIND_REGISTRATION_PAGE_TIMEOUT`
- [ ] Пауза `page_load_wait` только при успешной загрузке — не в `finally`
- [ ] Вкладки управляются через `TAB_PER_REGISTRATION` и `CLOSE_TAB_AFTER_REGISTRATION`
- [ ] Единственная вкладка никогда не закрывается (счётчик `tab_count`)
- [ ] Сохранение в шаблон только при успехе, только поля с `source != "template"`, без дублей
- [ ] `username_was_filled` ставится только при реальном заполнении поля
- [ ] Совпадение поля с шаблоном определяется по селектору **или** по label
- [ ] DOM-кэш в `_get_selectors_for_block` локальный — не живёт между вызовами
- [ ] Кнопка submit ищется JS-запросом внутри DOM-элемента формы
- [ ] После нахождения кнопки — проверка видимости и скролл если нужно
- [ ] Ручной ввод показывает `display_text` и селектор вместе
- [ ] Скриншот при HTTP-ошибке сохраняется с префиксом `http_error_{status}`
- [ ] Логика повторной попытки загрузки перенесена в `goto()` + retry оркестратора — не убрана

---

> 💡 **Для ИИ-агента:** Начни с анализа плана и запроси нужный код через команды терминала. Если что-то непонятно — задавай уточняющие вопросы. Не вноси изменения без подтверждения.
