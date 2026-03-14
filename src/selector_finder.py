from __future__ import annotations

import asyncio
import json
from pathlib import Path

import aiofiles
from loguru import logger
from typing import TypedDict


class CaptchaInfo(TypedDict):
    """Информация об обнаруженной капче."""
    selector: str
    captcha_type: str
    site_key: str | None
    invisible: bool



CAPTCHA_SELECTORS = [
    'iframe[src*="recaptcha"]',
    'iframe[src*="hcaptcha"]',
    'iframe[src*="turnstile"]',
    '.g-recaptcha',
    '.h-captcha',
    '#captcha',
]


class SelectorFinder:
    """Эвристический анализатор полей регистрационной формы."""

    def __init__(
        self,
        page,
        template_manager=None,
        common_fields_path: str = "templates/common_fields.json",
    ) -> None:
        self.page = page
        self.template_manager = template_manager
        self._common_fields_path = Path(common_fields_path)
        self.common_fields: dict = {}

    async def _ensure_common_fields(self) -> None:
        """Загружает common_fields.json если ещё не загружен."""
        if self.common_fields:
            return
        if self.template_manager:
            self.common_fields = await self.template_manager.get_common_fields()
            return
        # fallback — загружаем напрямую
        try:
            async with aiofiles.open(self._common_fields_path, encoding="utf-8") as f:
                content = await f.read()
            self.common_fields = json.loads(content)
        except FileNotFoundError:
            logger.warning(f"Файл common_fields не найден: {self._common_fields_path}. Используются значения по умолчанию.")
            self.common_fields = _default_common_fields()
        except json.JSONDecodeError as e:
            logger.warning(f"Ошибка парсинга common_fields: {e}. Используются значения по умолчанию.")
            self.common_fields = _default_common_fields()

    def _get_element_attrs(self, element) -> dict:
        """Получает атрибуты элемента через get_attribute (не async в Pydoll)."""
        try:
            # 🔧 ИСПРАВЛЕНИЕ: надёжное определение tagName
            tag_name = element.get_attribute("tagName")
            if not tag_name:
                el_type = (element.get_attribute("type") or "").lower()
                el_value = element.get_attribute("value")
                if el_type == "submit" and not el_value:
                    tag_name = "button"
                else:
                    tag_name = "input"
            return {
                "type": (element.get_attribute("type") or "").lower(),
                "name": (element.get_attribute("name") or "").lower(),
                "id": (element.get_attribute("id") or "").lower(),
                "placeholder": (element.get_attribute("placeholder") or "").lower(),
                "value": (element.get_attribute("value") or ""),
                "tagName": tag_name.lower(),
                "label": "",
            }
        except Exception as e:
            logger.warning(f"Ошибка получения атрибутов: {e}")
            return {"type": "", "name": "", "id": "", "placeholder": "", "value": "", "tagName": "input", "label": ""}

    def _generate_css_selector(self, element) -> str:
        """Генерирует уникальный CSS-селектор для элемента (не async)."""
        try:
            el_id = element.get_attribute("id")
            if el_id:
                return f"#{el_id}"

            # 🔧 ИСПРАВЛЕНИЕ: надёжное определение tagName
            tag = element.get_attribute("tagName")
            if not tag:
                # Эвристика: button[type=submit] без value
                el_type = (element.get_attribute("type") or "").lower()
                el_value = element.get_attribute("value")
                if el_type == "submit" and not el_value:
                    tag = "button"
                else:
                    tag = "input"
            tag = tag.lower()

            name = element.get_attribute("name")
            if name:
                return f"{tag}[name='{name}']"

            el_type = element.get_attribute("type")
            if el_type:
                return f"{tag}[type='{el_type}']"

            return tag

        except Exception as e:
            logger.warning(f"Ошибка генерации CSS-селектора: {e}")
            return "unknown"

    async def find_registration_form(
        self,
        template: dict | None = None,
    ) -> list[dict]:
        """Ищет все блоки с полями регистрации и возвращает отсортированный список.

        Алгоритм:
        1. Собираем все <form> и <div> содержащие поля ввода
        2. Фильтруем <form> по action/name/id (из common_fields skip_form_*)
        3. Фильтруем блоки у которых 100% полей — нежелательные
        4. Считаем score для каждого блока (поля + ключевые слова + совпадения с шаблоном)
        5. Возвращаем список блоков отсортированных по score (лучший первый)

        Args:
            template: Текущий шаблон для бонусных очков score.

        Returns:
            Список словарей {"form_selector": str, "form_element": element, "score": int}
            отсортированных по score от высшего к низшему. Пустой список если ничего не найдено.
        """
        await self._ensure_common_fields()

        # Ключевые слова для фильтрации из common_fields
        skip_action_kw = [k.lower() for k in self.common_fields.get("skip_form_action_keywords", [
            "login", "signin", "sign-in", "logon", "войти",
            "search", "поиск", "find", "query", "cart", "checkout"
        ])]
        skip_name_kw = [k.lower() for k in self.common_fields.get("skip_form_name_keywords", [
            "login", "search", "logon", "signin", "cart"
        ])]

        # Ключевые слова для score из common_fields
        username_kw = [k.lower() for k in self.common_fields.get("username_keywords", [])]
        email_kw = [k.lower() for k in self.common_fields.get("email_keywords", [])]
        password_kw = [k.lower() for k in self.common_fields.get("password_keywords", [])]
        agree_kw = [k.lower() for k in self.common_fields.get("agree_keywords", [])]
        submit_kw = [k.lower() for k in self.common_fields.get("submit_keywords", [])]
        skip_field_kw = [k.lower() for k in self.common_fields.get("checkbox_skip_keywords", [])]

        # Собираем селекторы из шаблона для бонусных очков
        template_selectors: set[str] = set()
        if template:
            fields = template.get("fields") or {}
            for val in fields.values():
                if isinstance(val, list):
                    template_selectors.update(v for v in val if v)
                elif val:
                    template_selectors.add(val)
            agree_step = template.get("agree_step") or {}
            for cb in agree_step.get("checkboxes") or []:
                if cb:
                    template_selectors.add(cb)
            for btn in (agree_step.get("submit_button") or []):
                if btn:
                    template_selectors.add(btn)

        logger.debug(f"Шаблонных селекторов для бонуса: {len(template_selectors)}")

        # --- Шаг 1: собираем все <form> ---
        candidates: list[dict] = []

        try:
            forms = await self.page.query("form", find_all=True, timeout=0, raise_exc=False) or []
        except Exception as e:
            logger.error(f"Ошибка получения форм: {e}")
            forms = []

        logger.debug(f"Найдено форм на странице: {len(forms)}")

        for form in forms:
            try:
                attrs = self._get_element_attrs(form)
                action = attrs.get("action", "")
                name = attrs.get("name", "")
                form_id = attrs.get("id", "")
                selector = self._generate_css_selector(form)

                # Фильтрация по action/name/id формы
                if any(kw in action for kw in skip_action_kw):
                    logger.debug(f"[{selector}] Исключена по action='{action}'")
                    continue
                if any(kw in name for kw in skip_name_kw):
                    logger.debug(f"[{selector}] Исключена по name='{name}'")
                    continue
                if any(kw in form_id for kw in skip_name_kw):
                    logger.debug(f"[{selector}] Исключена по id='{form_id}'")
                    continue

                candidates.append({"element": form, "selector": selector, "is_form": True})

            except Exception as e:
                logger.warning(f"Ошибка обработки формы: {e}")

        # --- Шаг 2: собираем <div> с полями (только если форм нет или мало) ---
        # Всегда ищем div-блоки — они могут содержать нужные поля не в <form>
        try:
            all_inputs = await self.page.query(
                "input:not([type='hidden']):not([type='submit']):not([type='button']), textarea, select",
                find_all=True, timeout=0, raise_exc=False
            ) or []
        except Exception as e:
            logger.warning(f"Ошибка получения всех полей: {e}")
            all_inputs = []

        # Ищем родительские div которые содержат поля но не являются формой
        div_parents: dict[str, object] = {}
        for inp in all_inputs:
            try:
                # Проверяем что поле не внутри уже найденной формы
                already_in_form = False
                for cand in candidates:
                    cand_sel = cand["selector"]
                    # Простая проверка по id — если у формы есть id
                    if cand_sel.startswith("#"):
                        form_id_check = cand_sel[1:]
                        inp_name = inp.get_attribute("name") or ""
                        # Если поле имеет связь с формой через form= атрибут
                        inp_form = inp.get_attribute("form") or ""
                        if inp_form == form_id_check:
                            already_in_form = True
                            break

                # Пробуем найти ближайший div-контейнер через JS
                # (упрощённо — пропускаем если не нашли)
            except Exception:
                pass

        logger.debug(f"Кандидатов для оценки: {len(candidates)}")

        # --- Шаг 3 + 4: фильтрация и подсчёт score ---
        scored_blocks: list[dict] = []

        for cand in candidates:
            element = cand["element"]
            selector = cand["selector"]

            try:
                # Получаем все видимые поля блока
                inputs = await element.query(
                    "input:not([type='hidden']):not([type='submit']):not([type='button']):not([type='image']):not([type='reset']), textarea, select",
                    find_all=True, timeout=0, raise_exc=False
                ) or []

                buttons = await element.query(
                    "input[type='submit'], button[type='submit'], input[type='button'], button",
                    find_all=True, timeout=0, raise_exc=False
                ) or []

                checkboxes = await element.query(
                    "input[type='checkbox']",
                    find_all=True, timeout=0, raise_exc=False
                ) or []

                # Фильтруем видимые поля
                visible_inputs = []
                for inp in inputs:
                    style = (inp.get_attribute("style") or "").replace(" ", "")
                    if "display:none" not in style and "visibility:hidden" not in style:
                        visible_inputs.append(inp)

                visible_checkboxes = []
                for cb in checkboxes:
                    style = (cb.get_attribute("style") or "").replace(" ", "")
                    if "display:none" not in style and "visibility:hidden" not in style:
                        visible_checkboxes.append(cb)

                visible_buttons = []
                for btn in buttons:
                    style = (btn.get_attribute("style") or "").replace(" ", "")
                    if "display:none" not in style and "visibility:hidden" not in style:
                        visible_buttons.append(btn)

                # Шаг 3: фильтрация — если 100% полей нежелательные
                if visible_inputs and not visible_checkboxes:
                    all_skip = True
                    for inp in visible_inputs:
                        name = (inp.get_attribute("name") or "").lower()
                        inp_id = (inp.get_attribute("id") or "").lower()
                        inp_type = (inp.get_attribute("type") or "").lower()
                        combined = f"{name} {inp_id} {inp_type}"
                        if not any(kw in combined for kw in skip_field_kw):
                            all_skip = False
                            break
                    if all_skip:
                        logger.debug(f"[{selector}] Исключён — все поля нежелательные")
                        continue

                # Шаг 3.5: фильтрация по содержимому блока
                # Собираем все текстовые признаки из всех элементов блока
                block_tokens: list[str] = []

                # Атрибуты формы
                try:
                    block_tokens.append(element.get_attribute("action") or "")
                    block_tokens.append(element.get_attribute("name") or "")
                    block_tokens.append(element.get_attribute("id") or "")
                except Exception:
                    pass

                # Поля ввода: name, id, placeholder, type
                for inp in visible_inputs:
                    try:
                        block_tokens.append(inp.get_attribute("name") or "")
                        block_tokens.append(inp.get_attribute("id") or "")
                        block_tokens.append(inp.get_attribute("placeholder") or "")
                        block_tokens.append(inp.get_attribute("type") or "")
                    except Exception:
                        pass

                # Кнопки: name, id, value + текст label
                for btn in visible_buttons:
                    try:
                        block_tokens.append(btn.get_attribute("name") or "")
                        block_tokens.append(btn.get_attribute("id") or "")
                        block_tokens.append(btn.get_attribute("value") or "")
                        block_tokens.append(await self._get_display_text(btn))
                    except Exception:
                        pass

                # Чекбоксы: name, id, value
                for cb in visible_checkboxes:
                    try:
                        block_tokens.append(cb.get_attribute("name") or "")
                        block_tokens.append(cb.get_attribute("id") or "")
                        block_tokens.append(cb.get_attribute("value") or "")
                    except Exception:
                        pass

                combined_block = " ".join(block_tokens).lower()

                # Проверяем по обоим спискам исключений
                skip_reason = next(
                    (kw for kw in skip_action_kw + skip_name_kw if kw in combined_block),
                    None,
                )
                if skip_reason:
                    logger.debug(
                        f"[{selector}] Исключён по содержимому блока: '{skip_reason}'"
                    )
                    continue

                # Шаг 4: подсчёт score
                score = 0
                score_details: list[str] = []

                # Базовые очки
                password_count = sum(
                    1 for inp in visible_inputs
                    if (inp.get_attribute("type") or "").lower() == "password"
                )
                score += len(visible_inputs)  # +1 за каждое поле
                score += password_count * 3   # +3 за password поле
                if visible_buttons:
                    score += 2                # +2 за submit
                    score_details.append(f"submit+2")
                if visible_checkboxes:
                    score += len(visible_checkboxes)  # +1 за чекбокс

                score_details.append(f"поля={len(visible_inputs)}")
                score_details.append(f"password={password_count}(x3)")
                score_details.append(f"чекбоксы={len(visible_checkboxes)}")

                # Очки за совпадения с шаблоном (+10 за каждый)
                template_matches = 0
                for inp in list(visible_inputs) + list(visible_checkboxes) + list(visible_buttons):
                    inp_sel = self._generate_css_selector(inp)
                    if inp_sel in template_selectors:
                        template_matches += 1
                        score += 10
                        score_details.append(f"{inp_sel}(шаблон+10)")

                # Очки за ключевые слова из common_fields
                for inp in visible_inputs:
                    name = (inp.get_attribute("name") or "").lower()
                    inp_id = (inp.get_attribute("id") or "").lower()
                    placeholder = (inp.get_attribute("placeholder") or "").lower()
                    inp_type = (inp.get_attribute("type") or "").lower()
                    combined = f"{name} {inp_id} {placeholder} {inp_type}"

                    if inp_type == "password" or any(kw in combined for kw in password_kw):
                        score += 5
                        score_details.append(f"{name or inp_id}(password_kw+5)")
                    elif any(kw in combined for kw in username_kw):
                        score += 5
                        score_details.append(f"{name or inp_id}(username_kw+5)")
                    elif any(kw in combined for kw in email_kw):
                        score += 5
                        score_details.append(f"{name or inp_id}(email_kw+5)")

                # Очки за чекбоксы согласия
                for cb in visible_checkboxes:
                    name = (cb.get_attribute("name") or "").lower()
                    cb_id = (cb.get_attribute("id") or "").lower()
                    cb_val = (cb.get_attribute("value") or "").lower()
                    cb_display = await self._get_display_text(cb)
                    combined = f"{name} {cb_id} {cb_val} {cb_display.lower()}"
                    if any(kw in combined for kw in agree_kw):
                        score += 3
                        # Показываем источник совпадения для отладки
                        match_source = (
                            "display_text"
                            if any(kw in cb_display.lower() for kw in agree_kw)
                            else "attrs"
                        )
                        score_details.append(
                            f"{name or cb_id}(agree_kw+3,src={match_source})"
                        )

                # Очки за кнопки по тексту
                for btn in visible_buttons:
                    btn_display = await self._get_display_text(btn)
                    btn_text = btn_display.lower()
                    if any(kw in btn_text for kw in submit_kw):
                        score += 2
                        score_details.append(f"'{btn_text[:20]}'(submit_kw+2)")

                # Штраф за форму логина:
                # username без email → скорее всего форма логина
                # email без username → скорее всего форма логина
                has_username = any(
                    any(kw in (
                        (inp.get_attribute("name") or "") + " " +
                        (inp.get_attribute("id") or "") + " " +
                        (inp.get_attribute("placeholder") or "")
                    ).lower() for kw in username_kw)
                    for inp in visible_inputs
                )
                has_email = any(
                    any(kw in (
                        (inp.get_attribute("name") or "") + " " +
                        (inp.get_attribute("id") or "") + " " +
                        (inp.get_attribute("placeholder") or "")
                    ).lower() for kw in email_kw)
                    for inp in visible_inputs
                )
                if has_username and not has_email:
                    score -= 20
                    score_details.append("username_без_email(логин?-20)")
                elif has_email and not has_username:
                    score -= 20
                    score_details.append("email_без_username(логин?-20)")

                logger.debug(
                    f"[{selector}] score={score} | "
                    f"{', '.join(score_details)}"
                )

                scored_blocks.append({
                    "form_selector": selector,
                    "form_element": element,
                    "score": score,
                    "template_matches": template_matches,
                })

            except Exception as e:
                logger.warning(f"Ошибка оценки блока {selector}: {e}")
                continue

        if not scored_blocks:
            logger.info("Ни одного подходящего блока не найдено")
            return []

        # Сортировка по score
        scored_blocks.sort(key=lambda b: b["score"], reverse=True)

        logger.info(
            f"Найдено блоков: {len(scored_blocks)}, "
            f"лучший: {scored_blocks[0]['form_selector']} (score={scored_blocks[0]['score']})"
        )

        return scored_blocks
    
    async def _get_display_text(self, element) -> str:
        """Возвращает видимый текст элемента."""
        try:
            el_id = element.get_attribute("id") or ""
            el_type = (element.get_attribute("type") or "").lower()
            # 🔧 ИСПРАВЛЕНИЕ: надёжное определение tagName
            tag = element.get_attribute("tagName")
            if not tag:
                el_value = element.get_attribute("value")
                if el_type == "submit" and not el_value:
                    tag = "button"
                else:
                    tag = "input"
            tag = tag.lower()

            # Для кнопок — value или innerText
            if el_type == "submit" or tag == "button":
                value = (element.get_attribute("value") or "").strip()
                if value:
                    return value
                # button innerText через JS
                try:
                    btn_selector = f"#{el_id}" if el_id else self._generate_css_selector(element)
                    response = await self.page.execute_script(
                        f"return document.querySelector('{btn_selector}')?.innerText?.trim() || ''"
                    )
                    text = response.get("result", {}).get("result", {}).get("value", "")
                    if text:
                        return text
                except Exception as e:
                    logger.debug(f"Не удалось получить innerText кнопки: {e}")
                return ""

            # Для полей и чекбоксов — label[for="id"]
            if el_id:
                try:
                    response = await self.page.execute_script(
                        f"return document.querySelector('label[for=\"{el_id}\"]')?.innerText?.trim() || ''"
                    )
                    text = response.get("result", {}).get("result", {}).get("value", "")
                    if text:
                        return text
                except Exception:
                    pass

            # Fallback — placeholder или name
            placeholder = (element.get_attribute("placeholder") or "").strip()
            if placeholder:
                return placeholder

            return ""

        except Exception as e:
            logger.debug(f"Ошибка получения display_text: {e}")
            return ""

    async def identify_fields(self, form_element) -> dict:
        """Анализирует поля внутри формы и классифицирует их.

        Определяет типы полей по атрибутам name/id/placeholder/type/display_text
        используя ключевые слова из common_fields.json.
        Для каждого поля читает display_text (label или value).
        Неизвестные поля добавляются в custom_fields.

        Returns:
            Словарь с найденными селекторами и display_text полей.
            Формат стандартных полей: selector строка.
            Формат display_text: fieldname_label строка (первый найденный текст).
            custom_fields: список {"name", "selector", "type", "display_text"}.
        """
        await self._ensure_common_fields()

        result: dict = {"custom_fields": []}
        password_count = 0

        # Загружаем ключевые слова
        submit_keywords = [k.lower() for k in self.common_fields.get("submit_keywords", [])]
        agree_keywords = [k.lower() for k in self.common_fields.get("agree_keywords", [])]
        checkbox_skip_keywords = [k.lower() for k in self.common_fields.get("checkbox_skip_keywords", [])]
        username_keywords = [k.lower() for k in self.common_fields.get("username_keywords", [])]
        email_keywords = [k.lower() for k in self.common_fields.get("email_keywords", [])]
        confirm_keywords = [k.lower() for k in self.common_fields.get("confirm_password_keywords", [])]
        confirm_email_keywords = [k.lower() for k in self.common_fields.get("confirm_email_keywords", [])]

        # Получаем все поля формы
        try:
            inputs = await form_element.query(
                "input, textarea, select",
                find_all=True, timeout=0, raise_exc=False
            ) or []
        except Exception as e:
            logger.error(f"Ошибка получения полей формы: {e}")
            return result

        # Получаем кнопки submit
        try:
            buttons = await form_element.query(
                'button[type="submit"], input[type="submit"], button',
                find_all=True, timeout=0, raise_exc=False
            ) or []
        except Exception as e:
            logger.warning(f"Ошибка получения кнопок формы: {e}")
            buttons = []

        # Определяем кнопку submit
        for button in buttons:
            try:
                attrs = self._get_element_attrs(button)
                selector = self._generate_css_selector(button)
                display_text = await self._get_display_text(button)
                combined_btn = f"{display_text} {attrs.get('value', '')} {attrs.get('name', '')}".lower()

                is_submit = (
                    attrs.get("type") == "submit"
                    or any(kw in combined_btn for kw in submit_keywords)
                )
                if is_submit and "submit_button" not in result:
                    result["submit_button"] = selector
                    result["submit_button_label"] = display_text
                    # 🔧 Логирование tagName для отладки
                    tag_name = attrs.get("tagName", "unknown")
                    logger.debug(f"Кнопка submit найдена: {selector} | tag={tag_name} | '{display_text}'")
            except Exception as e:
                logger.warning(f"Ошибка обработки кнопки: {e}")

        # Анализируем поля
        for element in inputs:
            try:
                attrs = self._get_element_attrs(element)
                selector = self._generate_css_selector(element)
                display_text = await self._get_display_text(element)

                field_type = (attrs.get("type") or "text").lower()
                name = (attrs.get("name") or "").lower()
                el_id = (attrs.get("id") or "").lower()
                placeholder = (attrs.get("placeholder") or "").lower()
                # combined включает display_text для более точного определения
                combined = f"{name} {el_id} {placeholder} {display_text.lower()}"

                # Пропускаем скрытые и служебные поля
                if field_type in ("hidden", "submit", "button", "image", "reset"):
                    continue

                # Проверяем видимость
                style = (attrs.get("style") or "").replace(" ", "")
                if "display:none" in style or "visibility:hidden" in style:
                    logger.debug(f"Пропускаем невидимое поле: {selector}")
                    continue

                # Password поля
                if field_type == "password":
                    password_count += 1
                    if any(kw in combined for kw in confirm_keywords):
                        result["confirm_password"] = selector
                        if display_text:
                            result["confirm_password_label"] = display_text
                        logger.debug(f"Определён confirm_password: {selector} | '{display_text}'")
                    elif not result.get("password"):
                        result["password"] = selector
                        if display_text:
                            result["password_label"] = display_text
                        logger.debug(f"Определён password: {selector} | '{display_text}'")
                    elif not result.get("confirm_password"):
                        result["confirm_password"] = selector
                        if display_text:
                            result["confirm_password_label"] = display_text
                        logger.debug(f"Определён confirm_password (второй): {selector} | '{display_text}'")
                    continue

                # Чекбоксы
                if field_type == "checkbox":
                    if any(kw in combined for kw in checkbox_skip_keywords):
                        logger.debug(f"Пропускаем нежелательный чекбокс: {selector} | '{display_text}'")
                        continue
                    if any(kw in combined for kw in agree_keywords):
                        if "agree_checkbox" not in result:
                            result["agree_checkbox"] = selector
                            if display_text:
                                result["agree_checkbox_label"] = display_text
                            logger.debug(f"Определён agree_checkbox: {selector} | '{display_text}'")
                        continue
                    # Неизвестный чекбокс — в custom_fields
                    result["custom_fields"].append({
                        "name": attrs.get("name") or attrs.get("id") or "checkbox",
                        "selector": selector,
                        "type": "checkbox",
                        "display_text": display_text,
                    })
                    continue
                
                # Email поля
                if field_type == "email" or any(kw in combined for kw in email_keywords):
                    if any(kw in combined for kw in confirm_email_keywords):
                        # Точное определение confirm_email по ключевым словам
                        result["confirm_email"] = selector
                        if display_text:
                            result["confirm_email_label"] = display_text
                        logger.debug(f"Определён confirm_email: {selector} | '{display_text}'")
                    elif "email" not in result:
                        # Первое email-поле
                        result["email"] = selector
                        if display_text:
                            result["email_label"] = display_text
                        logger.debug(f"Определён email: {selector} | '{display_text}'")
                    elif "confirm_email" not in result:
                        # Второе email-поле — позиционный fallback
                        result["confirm_email"] = selector
                        if display_text:
                            result["confirm_email_label"] = display_text
                        logger.debug(f"Определён confirm_email (второй): {selector} | '{display_text}'")
                    continue

                # Username поля
                if any(kw in combined for kw in username_keywords):
                    if "username" not in result:
                        result["username"] = selector
                        if display_text:
                            result["username_label"] = display_text
                        logger.debug(f"Определён username: {selector} | '{display_text}'")
                    continue

                # Определяем тип по ключевым словам
                known_field_types = [
                    ("city", self.common_fields.get("city_keywords", [])),
                    ("birthdate", self.common_fields.get("birthdate_keywords", [])),
                    ("gender", self.common_fields.get("gender_keywords", [])),
                    ("firstname", self.common_fields.get("firstname_keywords", [])),
                    ("lastname", self.common_fields.get("lastname_keywords", [])),
                    ("phone", self.common_fields.get("phone_keywords", [])),
                    ("website", self.common_fields.get("website_keywords", [])),
                    ("country", self.common_fields.get("country_keywords", [])),
                    ("timezone", self.common_fields.get("timezone_keywords", [])),
                ]

                matched_type = None
                for type_name, keywords in known_field_types:
                    if any(kw.lower() in combined for kw in keywords):
                        matched_type = type_name
                        break

                # Неизвестные поля — в custom_fields
                field_label = (
                    matched_type
                    or attrs.get("name")
                    or attrs.get("id")
                    or attrs.get("placeholder")
                    or "unknown"
                )
                result["custom_fields"].append({
                    "name": field_label,
                    "selector": selector,
                    "type": field_type or "text",
                    "display_text": display_text,
                })
                logger.debug(f"custom_fields: {field_label} ({selector}) | '{display_text}'")

            except Exception as e:
                logger.warning(f"Ошибка обработки поля: {e}")
                continue

        logger.info(
            f"Найдено полей: username={bool(result.get('username'))}, "
            f"email={bool(result.get('email'))}, "
            f"password={bool(result.get('password'))}, "
            f"confirm={bool(result.get('confirm_password'))}, "
            f"agree={bool(result.get('agree_checkbox'))}, "
            f"submit='{result.get('submit_button_label', '')}', "
            f"custom={len(result.get('custom_fields', []))}"
        )
        return result

    async def detect_captcha(self) -> CaptchaInfo | None:
        """Проверяет наличие капчи на текущей странице.

        Returns:
            CaptchaInfo с ключами selector, captcha_type, site_key, invisible
            или None если капча не найдена.
        """
        import json as _json

        TYPE_MAP: dict[str, str] = {
            "recaptcha":            "recaptcha_v2",
            "hcaptcha":             "hcaptcha",
            "h-captcha":            "hcaptcha",
            "turnstile":            "turnstile",
            "cf-turnstile":         "turnstile",
            "cloudflare-turnstile": "turnstile",
        }
        CONTAINER_MAP: dict[str, str] = {
            "recaptcha_v2": ".g-recaptcha",
            "hcaptcha":     ".h-captcha",
            "turnstile":    ".cf-turnstile",
        }

        found: list[CaptchaInfo] = []

        for selector in CAPTCHA_SELECTORS:
            try:
                element = await self.page.query(selector, timeout=0, raise_exc=False)
                if element is None:
                    continue

                # Определяем тип по селектору
                captcha_type = next(
                    (t for k, t in TYPE_MAP.items() if k in selector),
                    "recaptcha_v2",
                )
                if captcha_type == "recaptcha_v2" and "recaptcha" not in selector:
                    logger.warning(
                        f"Капча '{selector}' — тип не определён точно, "
                        f"используем recaptcha_v2 по умолчанию"
                    )

                site_key: str | None = None
                invisible = False

                # Шаг 1: атрибуты на текущем элементе
                try:
                    site_key = element.get_attribute("data-sitekey") or None
                    data_size = (element.get_attribute("data-size") or "").lower()
                    invisible = data_size == "invisible"
                except Exception:
                    pass

                # Шаг 2: если site_key не найден — ищем через JS closest()
                # json.dumps() гарантирует безопасную подстановку в JS
                if not site_key and captcha_type in CONTAINER_MAP:
                    container_selector = CONTAINER_MAP[captcha_type]
                    js_sel = _json.dumps(selector)
                    js_container = _json.dumps(container_selector)
                    try:
                        response = await self.page.execute_script(
                            f"""
                            (function() {{
                                var frames = document.querySelectorAll({js_sel});
                                for (var i = 0; i < frames.length; i++) {{
                                    var container = frames[i].closest({js_container});
                                    if (container) {{
                                        return {{
                                            sitekey: container.getAttribute('data-sitekey') || null,
                                            size: container.getAttribute('data-size') || ''
                                        }};
                                    }}
                                }}
                                return null;
                            }})()
                            """
                        )
                        data = response.get("result", {}).get("result", {}).get("value")
                        if data:
                            site_key = data.get("sitekey") or None
                            invisible = (data.get("size") or "").lower() == "invisible"
                    except Exception as e:
                        logger.debug(f"Не удалось получить контейнер капчи через JS: {e}")

                key_info = f"*** ({len(site_key)} симв.)" if site_key else "не найден"
                logger.info(
                    f"Обнаружена капча: {selector} | "
                    f"тип={captcha_type}, invisible={invisible}, "
                    f"site_key={key_info}"
                )
                found.append(CaptchaInfo(
                    selector=selector,
                    captcha_type=captcha_type,
                    site_key=site_key,
                    invisible=invisible,
                ))

            except Exception:
                continue

        if not found:
            return None

        if len(found) > 1:
            logger.warning(
                f"На странице найдено несколько капч ({len(found)}) — "
                f"используем первую: {found[0]['selector']}"
            )

        return found[0]

    async def find_registration_link(self, timeout: int = 60) -> str | None:
        """Ищет ссылку на страницу регистрации на текущей странице."""
        keywords = [
            "register", "registration", "signup", "sign-up", "sign_up",
            "регистрация", "зарегистрироваться", "регистрироваться",
            "create account", "new account",
        ]

        await asyncio.sleep(3)

        try:
            response = await self.page.execute_script(
                "return Array.from(document.querySelectorAll('a')).map(a => a.href + '|||' + a.innerText).join('%%%')"
            )
            raw = response.get("result", {}).get("result", {}).get("value", "")
        except Exception as e:
            logger.error(f"Ошибка получения ссылок через JS: {e}")
            return None

        if not raw:
            logger.warning("Ссылок на странице не найдено")
            return None

        links_data = []
        for item in raw.split("%%%"):
            parts = item.split("|||", 1)
            if len(parts) == 2:
                links_data.append({"href": parts[0], "text": parts[1]})

        logger.debug(f"Найдено ссылок на странице: {len(links_data)}")

        for item in links_data:
            try:
                href = item["href"].lower()
                text = item["text"].lower().strip()
                logger.debug(f"  Ссылка: [{text[:30]}] -> {href[:60]}")
                if any(kw in href or kw in text for kw in keywords):
                    logger.info(f"Найдена ссылка на регистрацию: {item['href']}")
                    return item["href"]
            except Exception:
                continue

        logger.debug("Ссылка на регистрацию не найдена")
        return None

    async def analyze_current_page(
        self,
        template: dict | None = None,
    ) -> list[dict]:
        """Выполняет полный анализ текущей страницы регистрации.

        Возвращает список всех найденных блоков с полями,
        отсортированных по score (лучший первый).
        Для каждого блока определяются поля через identify_fields.

        Args:
            template: Текущий шаблон для бонусных очков score.

        Returns:
            Список словарей с полями блоков или пустой список.
        """
        blocks = await self.find_registration_form(template=template)
        if not blocks:
            logger.info("Блоки регистрации не найдены — анализ прерван.")
            return []

        captcha = await self.detect_captcha()
        result = []

        for block in blocks:
            try:
                fields = await self.identify_fields(block["form_element"])
                result.append({
                    "form_selector": block["form_selector"],
                    "score": block["score"],
                    "template_matches": block.get("template_matches", 0),
                    **fields,
                    "captcha_indicator": captcha,
                })
            except Exception as e:
                logger.warning(f"Ошибка анализа блока {block['form_selector']}: {e}")

        logger.info(f"Проанализировано блоков: {len(result)}")
        return result

def _default_common_fields() -> dict:
    """Возвращает значения common_fields по умолчанию."""
    return {
        "agree_keywords": ["agree", "terms", "rules", "согласен", "правила"],
        "submit_keywords": ["register", "sign up", "create account", "зарегистрироваться"],
        "username_keywords": ["user", "login", "nick", "username", "логин"],
        "email_keywords": ["email", "mail", "e-mail"],
        "one_time_field_keywords": [
            "captcha", "imagestring", "image_string", "seccode",
            "answer", "question", "secret", "код", "code"
        ],
    }
    
    
