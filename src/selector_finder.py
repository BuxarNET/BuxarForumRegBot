from __future__ import annotations

import asyncio
import json
import re
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
        
    @staticmethod
    def _keyword_match(text: str, keywords: list[str], word_boundary: bool = True) -> bool:
        """Проверяет совпадение текста с ключевыми словами.

        Args:
            text: Текст для поиска (уже в lower case).
            keywords: Список ключевых слов (уже в lower case).
            word_boundary: True — кастомная граница слова
                          (_ считается разделителем, [a-zA-Z0-9] — нет),
                          False — поиск подстроки (in).

        Returns:
            True если найдено хотя бы одно совпадение.
        """
        if not text or not keywords:
            return False

        if word_boundary:
            for kw in keywords:
                # Кастомная граница: _ считается разделителем
                pattern = rf'(?<![a-zA-Z0-9]){re.escape(kw)}(?![a-zA-Z0-9])'
                if re.search(pattern, text, re.IGNORECASE):
                    return True
            return False
        else:
            return any(kw in text for kw in keywords)


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
            tag_name = element.get_attribute("tagName")
            if not tag_name:
                outer = element.get_attribute("outerHTML")
                if outer:
                    match = re.match(r"<([a-z]+)", outer.lower())
                    if match:
                        tag_name = match.group(1)
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

    def _generate_css_selector(
        self,
        element,
        attrs: dict | None = None,
        form_element: object | None = None,
    ) -> str:
        """Генерирует уникальный CSS-селектор для элемента.

        Args:
            element: элемент Pydoll.
            attrs: уже вычисленные атрибуты элемента из _get_element_attrs.
            form_element: родительская форма/блок для контекстной генерации
                         (опционально, для неточных селекторов).

        Returns:
            CSS-селектор строкой. Для неточных селекторов (без id/name) добавляет
            контекст родительского элемента.
        """
        # Локальная функция экранирования для безопасности CSS-селекторов
        def _escape_attr(value: str) -> str:
            return value.replace("'", "\\'").replace('"', '\\"')

        try:
            el_id = element.get_attribute("id")
            if el_id:
                return f"#{el_id}"

            if attrs:
                tag = attrs.get("tagName") or ""
            else:
                tag = element.get_attribute("tagName") or ""

            if not tag:
                el_action = element.get_attribute("action")
                el_method = element.get_attribute("method")
                el_type = (element.get_attribute("type") or "").lower()
                el_value = element.get_attribute("value")
                if el_action or el_method:
                    tag = "form"
                elif el_type == "submit" and not el_value:
                    tag = "button"
                else:
                    tag = "input"
            tag = tag.lower()

            # Уточняющий контекст родительского элемента для неточных селекторов
            def _build_contextual_selector(base_selector: str) -> str:
                """Добавляет контекст родительского элемента к неточному селектору."""
                if not form_element:
                    return base_selector

                parent_tag = (form_element.get_attribute("tagName") or "form").lower()
                form_attrs = {
                    "id": form_element.get_attribute("id") or "",
                    "action": form_element.get_attribute("action") or "",
                    "name": form_element.get_attribute("name") or "",
                    "method": form_element.get_attribute("method") or "",
                }

                # Приоритет: id → action → name → method → bare parent_tag
                if form_attrs["id"]:
                    return f"{parent_tag}#{form_attrs['id']} {base_selector}"
                
                action_clean = form_attrs["action"].strip()
                # Исключаем бесполезные значения action и все javascript: схемы
                if action_clean and action_clean not in ["", "#"] and not action_clean.lower().startswith("javascript:"):
                    return f"{parent_tag}[action='{_escape_attr(action_clean)}'] {base_selector}"
                
                if form_attrs["name"]:
                    return f"{parent_tag}[name='{_escape_attr(form_attrs['name'])}'] {base_selector}"
                
                if form_attrs["method"]:
                    return f"{parent_tag}[method='{form_attrs['method']}'] {base_selector}"

                # Fallback: хотя бы привязка к родительскому тегу
                return f"{parent_tag} {base_selector}"

            # Специальная обработка для <form> — сама форма не нуждается в контексте
            if tag == "form":
                action = element.get_attribute("action") or ""
                form_name = element.get_attribute("name") or ""
                form_method = (element.get_attribute("method") or "").lower()
                
                if action and action not in ["", "#"] and not action.lower().startswith("javascript:"):
                    return f"form[action='{_escape_attr(action)}']"
                if form_name:
                    return f"form[name='{_escape_attr(form_name)}']"
                if form_method:
                    return f"form[method='{form_method}']"
                return "form"

            name = element.get_attribute("name")
            if name:
                return f"{tag}[name='{name}']"

            el_type = element.get_attribute("type")
            if el_type:
                base = f"{tag}[type='{el_type}']"
                # Для неточных селекторов (type без id/name) добавляем контекст родителя
                if tag in ("button", "input") and form_element:
                    return _build_contextual_selector(base)
                return base

            # Тег без атрибутов — добавляем контекст если возможно
            if form_element:
                return _build_contextual_selector(tag)
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

        # Собираем селекторы и label кнопки из шаблона для бонусных очков
        template_selectors: set[str] = set()
        template_submit_labels: set[str] = set()
        if template:
            fields = template.get("fields") or {}
            for key, val in fields.items():
                if key == "submit_button_label":
                    continue
                if isinstance(val, list):
                    template_selectors.update(v for v in val if v)
                elif val:
                    template_selectors.add(val)
            submit_label_raw = fields.get("submit_button_label")
            if isinstance(submit_label_raw, list):
                template_submit_labels.update(v.lower() for v in submit_label_raw if v)
            elif submit_label_raw:
                template_submit_labels.add(submit_label_raw.lower())
            agree_step = template.get("agree_step") or {}
            for cb in agree_step.get("checkboxes") or []:
                if cb:
                    template_selectors.add(cb)
            for btn in (agree_step.get("submit_button") or []):
                if btn:
                    template_selectors.add(btn)

        logger.debug(
            f"Шаблонных селекторов для бонуса: {len(template_selectors)}, "
            f"submit labels: {template_submit_labels or '—'}"
        )

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

                # Штраф за форму логина по action/name/id (не исключаем жёстко —
                # форум с объединённой формой логина/регистрации должен остаться кандидатом)
                login_form_penalty = False
                if self._keyword_match(action, skip_action_kw):
                    logger.debug(f"[{selector}] Штраф -20 по action='{action}'")
                    login_form_penalty = True
                elif self._keyword_match(name, skip_name_kw):
                    logger.debug(f"[{selector}] Штраф -20 по name='{name}'")
                    login_form_penalty = True
                elif self._keyword_match(form_id, skip_name_kw):
                    logger.debug(f"[{selector}] Штраф -20 по id='{form_id}'")
                    login_form_penalty = True

                candidates.append({
                    "element": form,
                    "selector": selector,
                    "is_form": True,
                    "login_form_penalty": login_form_penalty,
                })

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
                        if not self._keyword_match(combined, skip_field_kw, word_boundary=False):
                            all_skip = False
                            break
                    if all_skip:
                        logger.debug(f"[{selector}] Исключён — все поля нежелательные")
                        continue

                # Шаг 3.5: раздельная фильтрация по атрибутам формы и полям внутри
                # Атрибуты формы — штраф -20 (не исключаем жёстко:
                # форум с объединённой формой логина/регистрации должен остаться кандидатом)
                form_tokens: list[str] = []
                try:
                    form_tokens.append(element.get_attribute("action") or "")
                    form_tokens.append(element.get_attribute("name") or "")
                    form_tokens.append(element.get_attribute("id") or "")
                except Exception:
                    pass

                # Токены полей внутри формы — только для проверки нежелательных полей
                # Намеренно не используем для skip_action_kw/skip_name_kw:
                # input[name='login'] на форуме регистрации не должен исключать форму
                field_tokens: list[str] = []
                for inp in visible_inputs:
                    try:
                        field_tokens.append(inp.get_attribute("name") or "")
                        field_tokens.append(inp.get_attribute("id") or "")
                        field_tokens.append(inp.get_attribute("placeholder") or "")
                    except Exception:
                        pass
                for cb in visible_checkboxes:
                    try:
                        field_tokens.append(cb.get_attribute("name") or "")
                        field_tokens.append(cb.get_attribute("id") or "")
                        field_tokens.append(cb.get_attribute("value") or "")
                    except Exception:
                        pass

                # Жёсткое исключение если поля содержат нежелательные keywords
                # (newsletter/subscribe и т.д.) — используем строгие границы слова (\b)
                # чтобы "subscribe" не срабатывал на "pf_subscribe"
                combined_fields = "  ".join(field_tokens).lower()
                if field_tokens:
                    has_unwanted = False
                    if self._keyword_match(combined_fields, skip_field_kw):
                        has_unwanted = True
                    
                    if has_unwanted:
                        logger.debug(
                            f"[{selector}] Исключён по полям: найден нежелательный keyword (строгое совпадение)"
                        )
                        continue

                # Шаг 4: подсчёт score
                score = 0
                score_details: list[str] = []

                # Штраф за атрибуты формы логина (флаг из Патча 1)
                if cand.get("login_form_penalty"):
                    score -= 20
                    score_details.append("login_form_penalty(-20)")

                # Дополнительный штраф если атрибуты формы совпадают со skip_kw
                # (страховка для div-блоков у которых нет флага из Патча 1)
                # Двойной штраф (-40) возможен если совпали оба условия — намеренное
                # поведение для приоритизации форм с отдельной регистрацией
                combined_form = " ".join(form_tokens).lower()
                skip_kws = skip_action_kw + skip_name_kw
                if self._keyword_match(combined_form, skip_kws):
                    score -= 20
                    score_details.append("form_tokens_penalty(-20)")

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

                    if inp_type == "password" or self._keyword_match(combined, password_kw):
                        score += 5
                        score_details.append(f"{name or inp_id}(password_kw+5)")
                    elif self._keyword_match(combined, username_kw):
                        score += 5
                        score_details.append(f"{name or inp_id}(username_kw+5)")
                    elif self._keyword_match(combined, email_kw):
                        score += 5
                        score_details.append(f"{name or inp_id}(email_kw+5)")

                # Очки за чекбоксы согласия
                for cb in visible_checkboxes:
                    name = (cb.get_attribute("name") or "").lower()
                    cb_id = (cb.get_attribute("id") or "").lower()
                    cb_val = (cb.get_attribute("value") or "").lower()
                    cb_display = await self._get_display_text(cb)
                    combined = f"{name} {cb_id} {cb_val} {cb_display.lower()}"
                    if self._keyword_match(combined, agree_kw):
                        score += 3
                        # Показываем источник совпадения для отладки
                        match_source = (
                            "display_text"
                            if self._keyword_match(cb_display.lower(), agree_kw)
                            else "attrs"
                        )
                        score_details.append(
                            f"{name or cb_id}(agree_kw+3,src={match_source})"
                        )

                # Очки за кнопки по тексту и совпадению label из шаблона
                for btn in visible_buttons:
                    btn_display = await self._get_display_text(btn)
                    btn_text = btn_display.lower()
                    if template_submit_labels and any(
                        lbl in btn_text or btn_text in lbl
                        for lbl in template_submit_labels
                        if lbl
                    ):
                        score += 10
                        score_details.append(f"'{btn_text[:20]}'(submit_label+10)")
                    if self._keyword_match(btn_text, submit_kw):
                        score += 2
                        score_details.append(f"'{btn_text[:20]}'(submit_kw+2)")

                # Штраф за форму логина:
                # username без email → скорее всего форма логина
                # email без username → скорее всего форма логина
                has_username = any(
                    self._keyword_match(
                        ((inp.get_attribute("name") or "") + " " +
                         (inp.get_attribute("id") or "") + " " +
                         (inp.get_attribute("placeholder") or "")).lower(),
                        username_kw
                    )
                    for inp in visible_inputs
                )
                has_email = any(
                    self._keyword_match(
                        ((inp.get_attribute("name") or "") + " " +
                         (inp.get_attribute("id") or "") + " " +
                         (inp.get_attribute("placeholder") or "")).lower(),
                        email_kw
                    )
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
        """Возвращает видимый текст элемента.

        Универсальный механизм для всех типов полей:
        - Кнопки (submit/button) — value или innerText
        - Поля с id — label[for="id"]
        - Все типы полей (текстовые, radio, checkbox, select) —
          fallback через структуру формы (td, dl, tr, div)
        - Placeholder — последний резерв

        Гарантированно возвращает str (никогда None).
        Все JS-вызовы используют json.dumps для безопасной подстановки.
        """
        try:
            # Определяем атрибуты один раз
            el_id = element.get_attribute("id") or ""
            el_name = element.get_attribute("name") or ""
            el_type = (element.get_attribute("type") or "").lower()

            # Надёжное определение tagName
            tag = element.get_attribute("tagName")
            if not tag:
                el_value = element.get_attribute("value")
                if el_type == "submit" and not el_value:
                    tag = "button"
                else:
                    tag = "input"
            tag = tag.lower() if tag else "input"

            # ── 1. Кнопки (submit/button) — value или innerText ──────────────
            if el_type == "submit" or tag == "button":
                value = (element.get_attribute("value") or "").strip()
                if value:
                    return value[:80]

                # Приоритет 1: JS на объекте элемента
                try:
                    response = await element.execute_script(
                        'return this.innerText?.trim() || this.textContent?.trim() || ""',
                        return_by_value=True,
                    )
                    text = response.get("result", {}).get("result", {}).get("value", "") or ""
                    if text:
                        return text[:80]
                except Exception:
                    pass

                # Приоритет 2: глобальный querySelector
                try:
                    btn_selector = f"#{el_id}" if el_id else self._generate_css_selector(element)
                    js_sel = json.dumps(btn_selector)
                    response = await self.page.execute_script(
                        f"return document.querySelector({js_sel})?.innerText?.trim() || ''"
                    )
                    text = response.get("result", {}).get("result", {}).get("value", "") or ""
                    if text:
                        return text[:80]
                except Exception:
                    pass

                return ""

            # ── 2. Поля с id — label[for="id"] (безопасно через json.dumps) ──
            if el_id and el_type not in ("radio", "checkbox"):
                try:
                    js_id = json.dumps(el_id)
                    response = await self.page.execute_script(
                        f"return document.querySelector('label[for=' + {js_id} + ']')?.innerText?.trim() || ''"
                    )
                    text = response.get("result", {}).get("result", {}).get("value", "") or ""
                    if text:
                        return text[:80]
                except Exception:
                    pass

            # ── 3. Универсальный fallback — структура формы ──────────────────
            # Работает для всех типов: текстовые, radio, checkbox, select
            # Покрывает phpBB (td), XenForo (dl), современные формы (div.field)
            if el_type not in ("hidden", "submit", "button", "image", "reset"):
                try:
                    # Безопасный селектор: id или [name="..."] с CSS.escape внутри JS
                    if el_id:
                        js_selector_expr = json.dumps(f"#{el_id}")
                    elif el_name:
                        js_name = json.dumps(el_name)
                        js_selector_expr = f"'[name=' + CSS.escape({js_name}) + ']'"
                    else:
                        js_selector_expr = None

                    if js_selector_expr:
                        response = await self.page.execute_script(
                            f"""
                            (function() {{
                                var sel = {js_selector_expr};
                                var el = document.querySelector(sel);
                                if (!el) return '';

                                // Стратегия 1: предыдущая ячейка в таблице (phpBB, Sibmama)
                                var td = el.closest('td');
                                if (td) {{
                                    var prev = td.previousElementSibling;
                                    if (prev && (prev.tagName === 'TD' || prev.tagName === 'TH')) {{
                                        var t = prev.innerText?.trim();
                                        if (t && t.length > 1) return t;
                                    }}
                                }}

                                // Стратегия 2: dt в dl (XenForo, definition list)
                                var dl = el.closest('dl');
                                if (dl) {{
                                    var dt = dl.querySelector('dt');
                                    if (dt) {{
                                        var t = dt.innerText?.trim();
                                        if (t && t.length > 1) return t;
                                    }}
                                }}

                                // Стратегия 3: label/span в div.field / li / fieldset
                                var container = el.closest('fieldset, li, div.field, div.form-group');
                                if (container) {{
                                    var title = container.querySelector('label, legend, dt, .field-label, .question');
                                    if (title) {{
                                        var t = title.innerText?.trim();
                                        if (t && t.length > 1) return t;
                                    }}
                                }}

                                // Стратегия 4: первый span/label в той же строке tr
                                var tr = el.closest('tr');
                                if (tr) {{
                                    var firstLabel = tr.querySelector('span.gen, span.gensmall, span, label, b, strong');
                                    if (firstLabel) {{
                                        var t = firstLabel.innerText?.trim();
                                        // Убираем звёздочки обязательных полей
                                        t = t.replace(/[*:]+$/g, '').trim();
                                        if (t && t.length > 1) return t;
                                    }}
                                }}

                                return '';
                            }})()
                            """
                        )
                        text = response.get("result", {}).get("result", {}).get("value", "") or ""
                        if text:
                            return text[:80]
                except Exception:
                    pass

            # ── 4. Placeholder — последний резерв ────────────────────────────
            placeholder = (element.get_attribute("placeholder") or "").strip()
            if placeholder:
                return placeholder[:80]

            return ""

        except Exception as e:
            logger.debug(f"Ошибка получения display_text: {e}")
            return ""

    async def identify_fields(
        self,
        form_element,
        form_selector: str = "",
        template_submit_label: str = "",
    ) -> dict:
        """Анализирует поля внутри формы и классифицирует их.

        Определяет типы полей по атрибутам name/id/placeholder/type/display_text
        используя ключевые слова из common_fields.json.
        Для каждого поля читает display_text (label или value).
        Неизвестные поля добавляются в custom_fields.

        Args:
            form_element: Объект формы Pydoll для поиска полей внутри неё.
            form_selector: CSS-селектор формы — используется для построения
                контекстного селектора кнопки если кнопка неуникальна в DOM.
            template_submit_label: Текст кнопки submit из шаблона —
                используется как приоритетный критерий выбора кнопки.

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
        register_radio_keywords = [k.lower() for k in self.common_fields.get("register_radio_keywords", [])]
        honeypot_keywords: list[str] = [k.lower() for k in self.common_fields.get("honeypot_keywords", [])]
        skip_field_classes_set: set[str] = {
            k.lower() for k in self.common_fields.get("skip_field_classes", [])
        }
        service_field_keywords: list[str] = [
            k.lower() for k in self.common_fields.get("service_field_keywords", [])
            ]

        # 📍 ОПРЕДЕЛЯЕМ known_field_types ЗДЕСЬ, до цикла for element in inputs
        known_field_types = [
            ("dob_day",   self.common_fields.get("dob_day_keywords", [])),
            ("dob_month", self.common_fields.get("dob_month_keywords", [])),
            ("dob_year",  self.common_fields.get("dob_year_keywords", [])),
            ("city",      self.common_fields.get("city_keywords", [])),
            ("birthdate", self.common_fields.get("birthdate_keywords", [])),
            ("gender",    self.common_fields.get("gender_keywords", [])),
            ("firstname", self.common_fields.get("firstname_keywords", [])),
            ("lastname",  self.common_fields.get("lastname_keywords", [])),
            ("phone",     self.common_fields.get("phone_keywords", [])),
            ("website",   self.common_fields.get("website_keywords", [])),
            ("country",   self.common_fields.get("country_keywords", [])),
            ("timezone",  self.common_fields.get("timezone_keywords", [])),
        ]

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

        logger.debug(f"Найдено кнопок в форме: {len(buttons)}")

        # Приоритеты выбора кнопки submit (от высшего к низшему):
        # 1. template_label — точное совпадение текста с шаблоном
        # 2. keyword        — текст совпадает с submit_keywords
        # 3. text           — непустой текст + type=submit
        # 4. fallback       — любой type=submit
        submit_by_template: tuple[str, str, dict[str, str]] | None = None
        submit_by_keyword: tuple[str, str, dict[str, str]] | None = None
        submit_by_text: tuple[str, str, dict[str, str]] | None = None
        submit_fallback: tuple[str, str, dict[str, str]] | None = None

        tmpl_label_lower = template_submit_label.lower().strip() if template_submit_label else ""

        for button in buttons:
            try:
                attrs = self._get_element_attrs(button)
                selector = self._generate_css_selector(button, attrs, form_element)
                display_text = await self._get_display_text(button)
                combined_btn = f"{display_text} {attrs.get('value', '')} {attrs.get('name', '')}".lower()

                el_type = attrs.get("type", "").lower()
                el_tag = attrs.get("tagName", "").lower()
                is_type_submit = (
                    el_type == "submit"
                    or (el_tag == "button" and not el_type)
                )

                has_keyword = self._keyword_match(combined_btn, submit_keywords)
                has_text = bool(display_text.strip())
                has_template_label = bool(
                    tmpl_label_lower
                    and display_text.strip()
                    and (
                        tmpl_label_lower in display_text.lower()
                        or display_text.lower() in tmpl_label_lower
                    )
                )

                logger.debug(
                    f"Кнопка: selector={selector} | tag={attrs.get('tagName', '?')} "
                    f"| text='{display_text}' | submit={is_type_submit} "
                    f"| keyword={has_keyword} | template_label={has_template_label}"
                )

                if has_template_label and submit_by_template is None:
                    submit_by_template = (selector, display_text, attrs)
                elif has_keyword and submit_by_keyword is None:
                    submit_by_keyword = (selector, display_text, attrs)
                elif has_text and is_type_submit and submit_by_text is None:
                    submit_by_text = (selector, display_text, attrs)
                elif is_type_submit and submit_fallback is None:
                    submit_fallback = (selector, display_text, attrs)

            except Exception as e:
                logger.warning(f"Ошибка обработки кнопки: {e}")

        chosen = submit_by_template or submit_by_keyword or submit_by_text or submit_fallback
        if chosen:
            selector, display_text, attrs = chosen
            chosen_by = (
                "template_label" if chosen is submit_by_template
                else "keyword" if chosen is submit_by_keyword
                else "text" if chosen is submit_by_text
                else "fallback"
            )

            # Проверка уникальности и уточнение через form_selector
            # Только для простых селекторов (без пробелов — не контекстных)
            if form_selector and " " not in selector:
                try:
                    sel_js = json.dumps(selector)
                    count_resp = await self.page.execute_script(
                        f"return document.querySelectorAll({sel_js}).length"
                    )
                    count = (
                        count_resp.get("result", {}).get("result", {}).get("value", 1)
                        if isinstance(count_resp, dict) else 1
                    )
                    if count > 1:
                        selector = f"{form_selector} {selector}"
                        logger.debug(
                            f"Селектор кнопки уточнён контекстом формы "
                            f"(найдено {count} в DOM): {selector}"
                        )
                except Exception as e:
                    logger.debug(f"Не удалось проверить уникальность кнопки: {e}")

            result["submit_button"] = selector
            result["submit_button_label"] = display_text
            tag_name = attrs.get("tagName", "unknown")
            logger.debug(
                f"Кнопка submit найдена [{chosen_by}]: "
                f"{selector} | tag={tag_name} | '{display_text}'"
            )
        else:
            logger.warning(
                f"Кнопка submit не определена: "
                f"кнопок в форме={len(buttons)}, "
                f"template_label={'найдена' if submit_by_template else 'нет'}, "
                f"keyword={'найдена' if submit_by_keyword else 'нет'}, "
                f"text={'найдена' if submit_by_text else 'нет'}, "
                f"fallback={'найден' if submit_fallback else 'нет'}"
            )

        # Анализируем поля
        for element in inputs:
            try:
                attrs = self._get_element_attrs(element)

                # Уточняем tagName через JS если не определён — для select, textarea без id
                if attrs.get("tagName") in (None, "input"):
                    el_name = attrs.get("name") or ""
                    el_id_val = attrs.get("id") or ""
                    js_sel = (
                        f'"#{el_id_val}"' if el_id_val
                        else f'"[name=\\"{el_name}\\"]"' if el_name
                        else None
                    )
                    if js_sel:
                        try:
                            response = await self.page.execute_script(
                                f"return document.querySelector({js_sel})"
                                f"?.tagName?.toLowerCase() || null"
                            )
                            tag = response.get("result", {}).get("result", {}).get("value")
                            if tag:
                                attrs["tagName"] = tag
                                logger.debug(
                                    f"tagName уточнён через JS: '{tag}' "
                                    f"для '{el_name or el_id_val}'"
                                )
                        except Exception as e:
                            logger.debug(f"Не удалось получить tagName через JS: {e}")
                try:
                    _tab = element.get_attribute("tabindex")
                    if _tab is not None and int(str(_tab).strip()) < 0:
                        logger.debug(
                            f"Пропускаем поле с tabindex={_tab} (honeypot): "
                            f"{attrs.get('name') or attrs.get('id') or '?'}"
                        )
                        continue
                except (ValueError, TypeError):
                    pass

                selector = self._generate_css_selector(element, attrs)
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

                # Honeypot-проверка: поле является ловушкой если родительский контейнер
                # имеет класс "limited" (XenForo) или текст <p class="explain">
                # содержит слово из honeypot_keywords (универсальная проверка)
                try:
                    js_selector: str = json.dumps(selector)
                    hp_response: dict = await self.page.execute_script(
                        f"var el=document.querySelector({js_selector});"
                        f"if(!el)return null;"
                        f"var p=el.closest('dl,div,li,td');"
                        f"return JSON.stringify({{parentClass:p?p.className:'',"
                        f"explainText:p?(p.querySelector('.explain')||{{textContent:''}}).textContent||'':''}});"
                    )
                    hp_raw: str | None = hp_response.get("result", {}).get("result", {}).get("value")
                    if hp_raw:
                        hp_data: dict | None = json.loads(hp_raw)
                        if hp_data is not None:
                            parent_class: str = (hp_data.get("parentClass") or "").lower()
                            explain_text: str = (hp_data.get("explainText") or "").lower()
                            is_honeypot: bool = (
                                "limited" in parent_class
                                or self._keyword_match(explain_text, honeypot_keywords, word_boundary=False)
                            )
                            if is_honeypot:
                                logger.debug(f"Honeypot-поле пропущено: {selector}")
                                continue
                except Exception as e:
                    logger.debug(f"Ошибка проверки honeypot для '{selector}': {e}")

                # Пропускаем поля скрытые через родителя или помеченные классами
                # авто-заполнения (OptOut, AutoTimeZone и др. из skip_field_classes)
                try:
                    sf_response: dict = await self.page.execute_script(
                        f"""
                        var el = document.querySelector({json.dumps(selector)});
                        if (!el) return null;
                    
                        // 1. Проверка ВСЕХ родителей на display:none / visibility:hidden
                        // Исправляет баг: closest() останавливался на <td> внутри <tr style="display:none">
                        var p = el.parentElement;
                        var parentDisplay = '';
                        var parentVisibility = '';
                        while (p && p.tagName !== 'BODY') {{
                            var cs = window.getComputedStyle(p);
                            if (cs.display === 'none') {{ parentDisplay = 'none'; break; }}
                            if (cs.visibility === 'hidden') {{ parentVisibility = 'hidden'; break; }}
                            p = p.parentElement;
                        }}
                    
                        // 2. Сбор классов ближайшего контейнера (XenForo skip_field_classes)
                        var container = el.closest('dl,div,li,td,tr,form,section,article');
                        var parentClass = container ? (container.className || '') : '';
                    
                        // 3. Собственные стили элемента
                        var elStyle = window.getComputedStyle(el);
                    
                        return JSON.stringify({{
                            parentDisplay: parentDisplay,
                            parentVisibility: parentVisibility,
                            elDisplay: elStyle.display,
                            elVisibility: elStyle.visibility,
                            parentClass: parentClass,
                            elementClass: el.className || ''
                        }});
                        """
                    )
                    sf_raw: str | None = sf_response.get("result", {}).get("result", {}).get("value")
                    if sf_raw:
                        try:
                            sf_data: dict | None = json.loads(sf_raw)
                        except (json.JSONDecodeError, TypeError):
                            sf_data = None
                        if sf_data is not None:
                            parent_display: str = (sf_data.get("parentDisplay") or "").lower().strip()
                            parent_visibility: str = (sf_data.get("parentVisibility") or "").lower().strip()
                            el_display: str = (sf_data.get("elDisplay") or "").lower().strip()
                            el_visibility: str = (sf_data.get("elVisibility") or "").lower().strip()
                            parent_class: str = (sf_data.get("parentClass") or "").lower()
                            element_class: str = (sf_data.get("elementClass") or "").lower()
                            parent_classes_set: set[str] = set(parent_class.split())
                            element_classes_set: set[str] = set(element_class.split())
                            has_skip_class: bool = bool(
                                parent_classes_set & skip_field_classes_set
                                or element_classes_set & skip_field_classes_set
                            )
    
                            # Проверка видимости через computed style (getComputedStyle):
                            # покрывает inline-стили, CSS-классы, скрытие через родителя
                            is_hidden: bool = (
                                parent_display == "none"
                                or parent_visibility == "hidden"
                                or el_display == "none"
                                or el_visibility == "hidden"
                                or has_skip_class
                            )
    
                            if is_hidden:
                                # Детальное логирование причины для отладки на разных форумах
                                hide_reasons: list[str] = []
                                if parent_display == "none":
                                    hide_reasons.append("parent_display_none")
                                if parent_visibility == "hidden":
                                    hide_reasons.append("parent_visibility_hidden")
                                if el_display == "none":
                                    hide_reasons.append("el_display_none")
                                if el_visibility == "hidden":
                                    hide_reasons.append("el_visibility_hidden")
                                if has_skip_class:
                                    hide_reasons.append("skip_class")
    
                                logger.debug(
                                    f"Пропускаем скрытое/авто поле: {selector} "
                                    f"(причины: {', '.join(hide_reasons)})"
                                )
                                continue
                except Exception as e:
                    logger.debug(f"Ошибка проверки skip_field для '{selector}': {e}")

                # Пропускаем служебные поля капч (g-recaptcha-response, hCaptcha, Turnstile)
                # Проверка по id, name, class элемента (display_text исключён для избежания ложных срабатываний)
                element_class: str = (attrs.get("class") or "").lower()
                field_combined: str = f"{name} {el_id} {element_class}"
                if self._keyword_match(field_combined, service_field_keywords, word_boundary=False):
                    logger.debug(f"Служебное поле капчи пропущено: {selector}")
                    continue

                # Password поля
                if field_type == "password":
                    password_count += 1
                    if self._keyword_match(combined, confirm_keywords):
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
                    if self._keyword_match(combined, checkbox_skip_keywords):
                        logger.debug(f"Пропускаем нежелательный чекбокс: {selector} | '{display_text}'")
                        continue
                    if self._keyword_match(combined, agree_keywords):
                        if "agree_checkbox" not in result:
                            result["agree_checkbox"] = []
                        if selector not in result["agree_checkbox"]:
                            result["agree_checkbox"].append(selector)
                            if display_text and "agree_checkbox_label" not in result:
                                result["agree_checkbox_label"] = display_text
                            logger.debug(
                                f"Добавлен agree_checkbox [{len(result['agree_checkbox'])}]: "
                                f"{selector} | '{display_text}'"
                            )
                        continue
                    # Неизвестный чекбокс — в custom_fields
                    result["custom_fields"].append({
                        "name": attrs.get("name") or attrs.get("id") or "checkbox",
                        "selector": selector,
                        "type": "checkbox",
                        "display_text": display_text,
                    })
                    continue

                # Radio-кнопки
                if field_type == "radio":
                    # 1. register_radio (шлюз формы)
                    if self._keyword_match(combined, register_radio_keywords):
                        if "register_radio" not in result:
                            result["register_radio"] = selector
                            result["register_radio_value"] = attrs.get("value", "")
                            if display_text:
                                result["register_radio_label"] = display_text
                            logger.debug(f"Определён register_radio: {selector} | '{display_text}'")
                        else:
                            logger.warning(f"Дубликат register_radio пропущен: {selector}")
                        continue  # Не идём дальше
                
                    # 2. Все остальные radio (gender, подписки, ...)
                    radio_name = attrs.get("name") or attrs.get("id")
                    if not radio_name:
                        continue  # Без имени не обработаем группу
                
                    group_selector = f"input[name='{radio_name}']"
                
                    # Собираем опции (один раз на группу)
                    try:
                        js_sel = json.dumps(group_selector)
                        opts_js = f"""
                            JSON.stringify(
                                Array.from(document.querySelectorAll({js_sel})).map(r => ({{
                                    value: r.value || '',
                                    text: (r.labels?.length ? r.labels[0].innerText.trim() : ''),
                                    checked: r.checked
                                }}))
                            );
                        """
                        resp = await self.page.execute_script(opts_js)
                        raw_opts = resp.get("result", {}).get("result", {}).get("value", "[]")
                        options = json.loads(raw_opts) if isinstance(raw_opts, str) else []
                    except Exception as e:
                        logger.debug(f"Не удалось собрать опции radio {group_selector}: {e}")
                        options = []
                
                    # Определяем семантический тип через known_field_types
                    # Используем _keyword_match с границами слова:
                    # "пол" не должен матчить "notifyreply" или "display_text"
                    matched_type = None
                    for type_name, keywords in known_field_types:
                        if self._keyword_match(combined, [k.lower() for k in keywords]):
                            matched_type = type_name
                            break
    
                    field_name_for_cf = matched_type or radio_name
    
                    # Защита от дубликатов — сравниваем по selector (уникален для группы),
                    # а не по name (семантический тип может совпадать у разных групп)
                    if not any(
                        f.get("selector") == group_selector and f.get("type") == "radio"
                        for f in result["custom_fields"]
                    ):
                        result["custom_fields"].append({
                            "name": field_name_for_cf,
                            "selector": group_selector,
                            "type": "radio",
                            "options": options,
                            "display_text": display_text,
                        })
                        logger.debug(f"Добавлена radio-группа: {field_name_for_cf} ({group_selector})")
                    else:
                        logger.debug(
                            f"Пропущен дубликат radio-группы: {field_name_for_cf} "
                            f"({group_selector}) — уже есть"
                        )
                    continue  # Не проваливаемся в email/username

                # Email поля
                if field_type == "email" or self._keyword_match(combined, email_keywords):
                    if self._keyword_match(combined, confirm_email_keywords):
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
                if self._keyword_match(combined, username_keywords):
                    if "username" not in result:
                        result["username"] = selector
                        if display_text:
                            result["username_label"] = display_text
                        logger.debug(f"Определён username: {selector} | '{display_text}'")
                    continue

                matched_type = None
                for type_name, keywords in known_field_types:
                    if self._keyword_match(combined, [k.lower() for k in keywords]):
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
                tmpl_submit_label = ""
                if template:
                    tmpl_submit_label_raw = (
                        (template.get("fields") or {}).get("submit_button_label")
                    )
                    if isinstance(tmpl_submit_label_raw, list) and tmpl_submit_label_raw:
                        tmpl_submit_label = tmpl_submit_label_raw[0]
                    elif isinstance(tmpl_submit_label_raw, str):
                        tmpl_submit_label = tmpl_submit_label_raw

                fields = await self.identify_fields(
                    block["form_element"],
                    form_selector=block["form_selector"],
                    template_submit_label=tmpl_submit_label,
                )
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

    async def validate_registration_page(
        self,
        template: dict | None = None,
    ) -> dict:
        """Проверяет, является ли текущая страница страницей регистрации.

        Критерии (в порядке приоритета):
        1. agree_checkbox в лучшем блоке
        2. register_radio в лучшем блоке
        3. submit_button с текстом регистрации
        4. элементы из template.agree_step в DOM
        5. ссылка <a> с текстом согласия (многоэтапная регистрация phpBB)

        Args:
            template: Текущий шаблон для проверки agree_step.

        Returns:
            dict с ключами:
                is_valid: bool — страница является страницей регистрации
                found_trigger: str — какой триггер найден
                agree_link_href: str | None — href ссылки согласия (если найдена)
                blocks: list[dict] — результат analyze_current_page
        """
        await self._ensure_common_fields()
        blocks = await self.analyze_current_page(template=template)

        empty_result = {
            "is_valid": False,
            "found_trigger": "",
            "agree_link_href": None,
            "blocks": blocks,
        }

        if not blocks:
            logger.info("❌ Валидация: блоки не найдены")
            # Даже без блоков проверяем ссылку согласия (Этап 5)
            # Это критично для phpBB многоэтапной регистрации
            agree_link_href = await self._find_agree_link()
            if agree_link_href:
                return {
                    "is_valid": True,
                    "found_trigger": "agree_link",
                    "agree_link_href": agree_link_href,
                    "blocks": [],
                }
            return empty_result

        best_block = blocks[0]
        form_selector = best_block.get("form_selector", "")

        # Проверка 1: agree_checkbox
        if best_block.get("agree_checkbox"):
            logger.info(
                f"✅ Страница подтверждена (trigger=agree_checkbox, "
                f"block={form_selector})"
            )
            return {
                "is_valid": True,
                "found_trigger": "agree_checkbox",
                "agree_link_href": None,
                "blocks": blocks,
            }

        # Проверка 2: register_radio
        if best_block.get("register_radio"):
            logger.info(
                f"✅ Страница подтверждена (trigger=register_radio, "
                f"block={form_selector})"
            )
            return {
                "is_valid": True,
                "found_trigger": "register_radio",
                "agree_link_href": None,
                "blocks": blocks,
            }

        # Проверка 3: submit_button с текстом регистрации
        submit_label = (best_block.get("submit_button_label") or "").lower()
        if submit_label:
            submit_keywords = [
                k.lower() for k in self.common_fields.get("submit_keywords", [])
            ]
            if self._keyword_match(submit_label, submit_keywords):
                logger.info(
                    f"✅ Страница подтверждена "
                    f"(trigger=submit_button:{submit_label[:30]}, "
                    f"block={form_selector})"
                )
                return {
                    "is_valid": True,
                    "found_trigger": f"submit_button:{submit_label[:30]}",
                    "agree_link_href": None,
                    "blocks": blocks,
                }

        # Проверка 4: элементы из template.agree_step в DOM
        if template:
            agree_step = template.get("agree_step") or {}
            for sel in (agree_step.get("checkboxes") or []) + (agree_step.get("submit_button") or []):
                if not sel:
                    continue
                try:
                    element = await self.page.query(sel, timeout=3, raise_exc=False)
                    if element:
                        logger.info(
                            f"✅ Страница подтверждена "
                            f"(trigger=template_agree_step: {sel})"
                        )
                        return {
                            "is_valid": True,
                            "found_trigger": f"template_agree_step:{sel}",
                            "agree_link_href": None,
                            "blocks": blocks,
                        }
                except Exception:
                    pass

        # Проверка 5: ссылка согласия (многоэтапная регистрация)
        agree_link_href = await self._find_agree_link()
        if agree_link_href:
            return {
                "is_valid": True,
                "found_trigger": "agree_link",
                "agree_link_href": agree_link_href,
                "blocks": blocks,
            }

        logger.info("❌ Триггеры согласия не найдены")
        return empty_result

    async def _find_agree_link(self) -> str | None:
        """Ищет ссылку <a> с текстом согласия (многоэтапная регистрация phpBB).

        Использует ТОЛЬКО существующий agree_keywords из common_fields.json.
        
        Логика приоритетов:
        1. Слова-действия: "соглас", "принимаю", "accept", "agree" (приоритет)
        2. Fallback: любые другие keywords из agree_keywords
        3. Исключение: ссылки с отрицанием ("не согласен", "disagree")

        Returns:
            Абсолютный href ссылки согласия или None.
        """
        await self._ensure_common_fields()

        agree_keywords = [
            k.lower() for k in self.common_fields.get("agree_keywords", [])
        ]
        
        if not agree_keywords:
            logger.debug("Ключевые слова для поиска ссылки согласия не заданы")
            return None

        # Слова-действия (приоритетный поиск) — подмножество agree_keywords
        action_words = ["соглас", "принимаю", "accept", "agree", "принять", "подтверждаю"]
        
        # Отрицания для исключения (простая эвристика)
        negation_prefixes = ["не ", "not ", "dis"]

        try:
            links_js = """
                (function() {
                    var links = document.querySelectorAll('a');
                    var result = [];
                    for (var i = 0; i < links.length; i++) {
                        var text = (links[i].innerText || links[i].textContent || '').trim();
                        var href = links[i].href || '';
                        if (text && href) {
                            result.push({text: text.toLowerCase(), href: href});
                        }
                    }
                    return JSON.stringify(result);
                })()
            """
            response = await self.page.execute_script(links_js)
            links_raw = response.get("result", {}).get("result", {}).get("value", "[]")
            links = json.loads(links_raw) if isinstance(links_raw, str) else []

            # Вспомогательная функция: проверка на отрицание
            def has_negation(text: str) -> bool:
                """Проверяет, содержит ли текст отрицание перед keyword."""
                for prefix in negation_prefixes:
                    if prefix in text:
                        return True
                return False

            # Проход 1: приоритетный поиск слов-действий
            for link in links:
                link_text = link.get("text", "")
                if has_negation(link_text):
                    continue
                
                # Ищем слова-действия из agree_keywords
                for kw in agree_keywords:
                    if kw in action_words and self._keyword_match(link_text, [kw], word_boundary=False):
                        href = link.get("href", "")
                        logger.info(
                            f"✅ Найдена ссылка согласия (action): "
                            f"text='{link_text[:40]}', href='{href[:60]}'"
                        )
                        return href

            # Проход 2: fallback на любые agree_keywords
            for link in links:
                link_text = link.get("text", "")
                if has_negation(link_text):
                    continue
                
                for kw in agree_keywords:
                    if self._keyword_match(link_text, [kw], word_boundary=False):
                        href = link.get("href", "")
                        logger.info(
                            f"✅ Найдена ссылка согласия (fallback): "
                            f"text='{link_text[:40]}', href='{href[:60]}'"
                        )
                        return href

        except Exception as e:
            logger.debug(f"Ошибка поиска ссылки согласия: {e}")

        return None

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
    
    
