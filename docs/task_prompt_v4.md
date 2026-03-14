# 🐛 Задача: Исправление ошибок тестирования

## Описание задачи
Проект написан. В ходе анализа логов и кода выявлены ошибки. Все детали — в разделах ниже.

---

## 🎯 Согласованный план исправлений (9 этапов)

---

### Этап 1 — Решен
---

### Этап 2 — Решен
---

### Этап 3 — Решен

### Этап 4 — Решен

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

Твоя реализация:
async def _find_submit_in_form(
        self,
        form_selector: str | None,
        submit_keywords: list[str],
    ) -> str | None:
        """Ищет кнопку подтверждения внутри блока формы.

        Порядок поиска:
        1. Внутри block_selector (div/form указанный в шаблоне)
        2. Если не найдено — поднимается через closest('form') к родительской форме
        Внутри каждого контекста: сначала по тексту/value, потом первая по типу.

        Args:
            form_selector: CSS селектор блока или формы.
            submit_keywords: Ключевые слова для поиска по тексту кнопки.

        Returns:
            CSS селектор найденной кнопки или None.
        """
        if not form_selector:
            return None

        BTN_QUERY = "input[type='submit'], button[type='submit'], input[type='button'], button"

        async def _find_in_buttons(buttons: list) -> str | None:
            """Ищет подходящую кнопку в списке по тексту, затем первую по типу."""
            # Сначала по тексту/value через _get_display_text
            for btn in buttons:
                display = await self.selector_finder._get_display_text(btn)
                if any(kw in display.lower() for kw in submit_keywords):
                    sel = await self._generate_css_selector(btn)
                    logger.debug(f"Кнопка найдена по тексту {display!r}: {sel}")
                    return sel

            # Потом первая видимая по типу
            for btn in buttons:
                sel = await self._generate_css_selector(btn)
                if sel:
                    logger.debug(f"Кнопка найдена по типу (первая): {sel}")
                    return sel

            return None

        try:
            # Шаг 1 — ищем внутри block_selector
            block = await self.page.query(form_selector, timeout=3, raise_exc=False)
            if not block:
                logger.debug(f"Блок не найден: {form_selector}")
                return None

            buttons = await block.query(BTN_QUERY, find_all=True) or []
            logger.debug(
                f"Шаг 1: найдено {len(buttons)} кнопок внутри блока {form_selector!r}"
            )

            if buttons:
                result = await _find_in_buttons(buttons)
                if result:
                    return result

            # Шаг 2 — кнопка не найдена в блоке, поднимаемся к родительской <form>
            logger.debug(
                f"Шаг 1: кнопок не найдено внутри блока — "
                f"поднимаемся через closest('form'): {form_selector!r}"
            )
            try:
                response = await self.page.execute_script(
                    f"var el = document.querySelector({form_selector!r});"
                    f"var form = el ? el.closest('form') : null;"
                    f"if (!form) return null;"
                    f"var btns = form.querySelectorAll({BTN_QUERY!r});"
                    f"return Array.from(btns).map(function(b) {{"
                    f"  return b.id || b.name || b.className || null;"
                    f"}});"
                )
                hints = (
                    response.get("result", {}).get("result", {}).get("value") or []
                )
            except Exception as e:
                logger.debug(f"Шаг 2: execute_script ошибка: {e}")
                hints = []

            if not hints:
                logger.debug("Шаг 2: кнопок в родительской <form> не найдено")
                return None

            # Запрашиваем элементы через page.query уже внутри <form>
            parent_form = await self.page.execute_script(
                f"var el = document.querySelector({form_selector!r});"
                f"return el ? !!el.closest('form') : false;"
            )
            has_parent = (
                parent_form.get("result", {}).get("result", {}).get("value", False)
            )

            if not has_parent:
                logger.debug("Шаг 2: родительская <form> не найдена")
                return None

            # Строим временный селектор родительской формы и ищем кнопки в ней
            form_sel = (
                f"{form_selector} *:not({form_selector} *), "
                f"form:has({form_selector})"
            )
            parent = await self.page.execute_script(
                f"var el = document.querySelector({form_selector!r});"
                f"var form = el.closest('form');"
                f"form.setAttribute('data-pydoll-find', 'submit-search');"
                f"return true;"
            )
            form_el = await self.page.query(
                "form[data-pydoll-find='submit-search']",
                timeout=3,
                raise_exc=False,
            )
            # Убираем временный атрибут
            await self.page.execute_script(
                "var f = document.querySelector(\"form[data-pydoll-find='submit-search']\");"
                "if (f) f.removeAttribute('data-pydoll-find');"
            )

            if not form_el:
                logger.debug("Шаг 2: не удалось получить элемент родительской формы")
                return None

            buttons = await form_el.query(BTN_QUERY, find_all=True) or []
            logger.debug(
                f"Шаг 2: найдено {len(buttons)} кнопок в родительской <form>"
            )

            if buttons:
                result = await _find_in_buttons(buttons)
                if result:
                    return result

        except Exception as e:
            logger.warning(f"Ошибка поиска кнопки внутри формы: {e}")

        return None

    async def _find_submit_on_page(
        self,
        submit_keywords: list[str],
    ) -> str | None:
        """Ищет кнопку подтверждения по всей странице.

        Args:
            submit_keywords: Ключевые слова для поиска по тексту.

        Returns:
            CSS селектор найденной кнопки или None.
        """
        BTN_QUERY = "input[type='submit'], button[type='submit'], input[type='button'], button"
        try:
            all_buttons = await self.page.query(
                BTN_QUERY,
                find_all=True
            ) or []

            # Сначала по тексту/value через _get_display_text
            for btn in all_buttons:
                display = await self.selector_finder._get_display_text(btn)
                if any(kw in display.lower() for kw in submit_keywords):
                    sel = await self._generate_css_selector(btn)
                    logger.debug(f"Кнопка найдена по тексту на странице {display!r}: {sel}")
                    return sel

            # Потом первая по типу
            for btn in all_buttons:
                sel = await self._generate_css_selector(btn)
                if sel:
                    logger.debug(f"Кнопка найдена по типу на странице (первая): {sel}")
                    return sel

        except Exception as e:
            logger.warning(f"Ошибка поиска кнопки на странице: {e}")

        return None

Замечание колег:
Вымышленный метод element.is_visible()

python
1

В предоставленном browser_controller.py и selector_finder.py нет метода is_visible() у элементов Pydoll. Это нарушает правило «не выдумывать методы».
Рекомендация: Удалить проверку видимости или реализовать через page.query() с таймаутом.
2. Избыточно сложная логика поиска родительской формы
Код с closest('form'), временными атрибутами data-pydoll-find и множественными execute_script вызовами:

    Трудно поддерживать
    Может ломаться на сложных DOM-структурах
    Не протестирован на реальных форумах

Рекомендация: Упростить до одного подхода — либо поиск внутри блока, либо поиск по всей странице с валидацией.
3. Не применено исправление лога
В задаче указано:

python
1

В предоставленном коде это исправление отсутствует.

Очень сложная и хрупкая логика поиска родительской формы в _find_submit_in_form
Несколько execute_script с временными атрибутами (data-pydoll-find), ручное снятие атрибута, сложные селекторы вида *:not(...) и :has(...) — это выглядит как over-engineering и потенциальный источник багов.
Pydoll (как и большинство CDP-библиотек) не гарантирует стабильность работы с :has() и сложными комбинаторами на всех страницах.
Весь блок с closest('form') можно сильно упростить, используя уже существующий элемент block и его .closest('form') через один вызов JS.

Нет проверки видимости кнопки перед возвратом селектора
В предложенном коде есть только попытка скролла после нахождения, но если кнопка найдена невидимой и скролл не помог — селектор всё равно возвращается.
human_click потом упадёт с ошибкой «element is not visible», но это уже поздно — лучше отфильтровать невидимые кнопки ещё на этапе поиска.

_generate_css_selector вызывается повторно в цикле
В _find_in_buttons селектор генерируется дважды для одной и той же кнопки (сначала для проверки, потом для возврата).
Лучше генерировать один раз и хранить в словаре или списке кортежей.

Нет логирования, если найдена кнопка, но она не подходит по типу
Сейчас если кнопка найдена по тексту, но не submit/button — она всё равно может быть возвращена.
Желательно приоритет отдавать именно type="submit", а потом уже button без типа.

В _find_submit_on_page нет скролла к найденной кнопке
Если кнопка найдена внизу страницы и не в viewport — human_click может упасть.
Нужно добавить ту же проверку видимости + скролл, как в предложенном фрагменте.

Лог «Кнопка найдена, но нажать не удалось»
Предложенный вариант хорош, но он должен быть в месте, где происходит human_click, а не в _find_submit_in_form.
В текущем коде метода поиска такой логики вообще нет — она должна быть в вызывающем коде.

Предложение колег как решить вопрос с поиском кнопки (смотри только эту часть кода):
async def _find_button_in_context(
    self,
    context_selector: str,
    submit_keywords: list[str],
) -> str | None:
    """Ищет кнопку подтверждения внутри указанного контекста (форма/блок).

    Использует JS-поиск через querySelectorAll внутри элемента,
    возвращает селектор, привязанный к контексту.

    Args:
        context_selector: CSS-селектор родительского блока/формы.
        submit_keywords: Ключевые слова для поиска по тексту кнопки.

    Returns:
        CSS-селектор кнопки (относительно контекста) или None.
    """
    if not context_selector:
        return None

    BTN_QUERY = "input[type='submit'], button[type='submit'], input[type='button'], button:not([type])"

    try:
        # JS-поиск внутри контекста: сначала по тексту, потом по типу
        response = await self.page.execute_script(f"""
            (function() {{
                var ctx = document.querySelector({context_selector!r});
                if (!ctx) return null;

                var buttons = Array.from(ctx.querySelectorAll({BTN_QUERY!r}));
                if (!buttons.length) return null;

                var keywords = {submit_keywords!r};

                // Сначала ищем по тексту/label/value
                for (var btn of buttons) {{
                    var text = (btn.innerText || btn.value || '').toLowerCase().trim();
                    var label = '';
                    if (btn.id) {{
                        var lbl = document.querySelector('label[for="' + btn.id + '"]');
                        if (lbl) label = lbl.innerText.toLowerCase().trim();
                    }}
                    var combined = text + ' ' + label;
                    if (keywords.some(kw => combined.includes(kw))) {{
                        // Возвращаем селектор относительно контекста
                        return {{
                            selector: btn.id ? '#' + btn.id :
                                     btn.name ? btn.tagName.toLowerCase() + '[name="' + btn.name + '"]' :
                                     null,
                            is_direct_child: btn.parentElement === ctx
                        }};
                    }}
                }}

                // Fallback: первая видимая кнопка
                for (var btn of buttons) {{
                    var style = window.getComputedStyle(btn);
                    if (style.display !== 'none' && style.visibility !== 'hidden') {{
                        return {{
                            selector: btn.id ? '#' + btn.id :
                                     btn.name ? btn.tagName.toLowerCase() + '[name="' + btn.name + '"]' :
                                     null,
                            is_direct_child: btn.parentElement === ctx
                        }};
                    }}
                }}

                return null;
            }})()
        """)

        result = response.get("result", {}).get("result", {}).get("value")
        if result and result.get("selector"):
            selector = result["selector"]
            # Если кнопка не прямой потомок — уточняем контекстом
            if not result.get("is_direct_child"):
                return f"{context_selector} {selector}"
            return selector

    except Exception as e:
        logger.debug(f"Ошибка поиска кнопки в контексте {context_selector!r}: {e}")

    return None

    async def _find_submit_in_form(
    self,
    form_selector: str | None,
    submit_keywords: list[str],
) -> str | None:
    """Ищет кнопку подтверждения внутри блока формы."""

    # 1. Поиск внутри указанного контекста
    if form_selector:
        found = await self._find_button_in_context(form_selector, submit_keywords)
        if found:
            logger.debug(f"Кнопка найдена в контексте {form_selector!r}: {found}")
            return found

    # 2. Если не найдено — пробуем подняться на уровень выше (родитель формы)
    if form_selector:
        try:
            # JS: найти ближайшую форму или общий контейнер
            parent_ctx = await self.page.execute_script(f"""
                (function() {{
                    var el = document.querySelector({form_selector!r});
                    if (!el) return null;
                    // Если элемент внутри формы — берём форму
                    var form = el.closest('form');
                    if (form) return form.id ? '#' + form.id :
                                   form.className ? 'form.' + form.className.split(' ')[0] : 'form';
                    // Иначе берём ближайший значимый контейнер
                    var parent = el.parentElement;
                    while (parent && parent !== document.body) {{
                        if (parent.id) return '#' + parent.id;
                        if (parent.className) {{
                            var cls = parent.className.split(' ')[0];
                            if (cls) return parent.tagName.toLowerCase() + '.' + cls;
                        }}
                        parent = parent.parentElement;
                    }}
                    return null;
                }})()
            """)
            parent_sel = parent_ctx.get("result", {}).get("result", {}).get("value")
            if parent_sel:
                found = await self._find_button_in_context(parent_sel, submit_keywords)
                if found:
                    logger.debug(f"Кнопка найдена в родительском контексте {parent_sel!r}: {found}")
                    return found
        except Exception as e:
            logger.debug(f"Ошибка поиска в родительском контексте: {e}")

    return None

и вариант 2:
async def _find_submit_in_form(
    self,
    form_selector: str | None,
    submit_keywords: list[str],
) -> str | None:
    """Ищет кнопку подтверждения сначала внутри блока, потом в ближайшей <form>.

    Это покрывает случаи, когда поля и кнопка находятся в соседних div'ах
    внутри одной формы (как в твоём примере).
    """
    if not form_selector:
        return None

    BTN_QUERY = "input[type='submit'], button[type='submit'], button, input[type='button']"

    # Вспомогательная функция (уже с _get_display_text)
    async def _find_in_buttons(buttons: list) -> str | None:
        # Сначала по видимому тексту
        for btn in buttons:
            display = await self.selector_finder._get_display_text(btn)
            if any(kw in display.lower() for kw in submit_keywords):
                sel = await self._generate_css_selector(btn)
                logger.debug(f"Кнопка найдена по тексту: {display!r} → {sel}")
                return sel
        # Потом первая кнопка по типу
        for btn in buttons:
            sel = await self._generate_css_selector(btn)
            if sel:
                logger.debug(f"Кнопка найдена по типу (первая): {sel}")
                return sel
        return None

    # === Шаг 1: ищем внутри текущего блока ===
    block = await self.page.query(form_selector, timeout=3, raise_exc=False)
    if block:
        buttons = await block.query(BTN_QUERY, find_all=True) or []
        logger.debug(f"Найдено {len(buttons)} кнопок внутри блока {form_selector}")
        if buttons:
            result = await _find_in_buttons(buttons)
            if result:
                return result

    # === Шаг 2: кнопка не найдена — поднимаемся к ближайшей форме ===
    logger.debug(f"Кнопка не найдена внутри блока — ищем в ближайшей <form>")
    try:
        # Получаем элемент формы через closest('form')
        response = await self.page.execute_script(
            f"""
            var block = document.querySelector({form_selector!r});
            if (!block) return null;
            var form = block.closest('form') || block.parentElement;
            return form ? {{id: form.id, selector: form.tagName.toLowerCase()}} : null;
            """
        )
        form_info = response.get("result", {}).get("result", {}).get("value")
        if not form_info:
            return None

        # Ищем кнопки уже внутри найденной формы
        form_buttons = await self.page.query(BTN_QUERY, find_all=True) or []
        if form_buttons:
            result = await _find_in_buttons(form_buttons)
            if result:
                return result

    except Exception as e:
        logger.debug(f"Не удалось найти родительскую форму: {e}")

    return None

    В _submit_form после вызова _find_submit_in_form добавь проверку видимости (это решает причину №3):
Pythonif found_selector:
    try:
        element = await self.page.query(found_selector, timeout=2, raise_exc=False)
        if element:
            if not await element.is_visible():
                logger.debug(f"Кнопка найдена, но не видима — скроллим")
                await self.browser.scroll_to_element(element)
            await self.browser.human_click(found_selector)
            ...
    except Exception as e:
        logger.warning(f"Кнопка найдена ({found_selector}), но нажать не удалось: {e}")


Задача этапа 5: Сравнить свое решение, первым делом замечания колег по реализации поиска кнопки.
Сравнить подходы и пояснить мне какой лучше и чем.
Далее учитываем остальные замечания и пишем по каждому согласен или нет и почему.


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

### Этап 7 — Решен

---

### Этап 8 — Решен
---

### Этап 9 — Решен
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
