from __future__ import annotations
import asyncio

import json
import re
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse
from typing import TypedDict, NotRequired

from loguru import logger


class RegistrationResult(TypedDict):
    success: bool
    message: str
    reason: str | None
    template_used: str | None
    screenshot: str | None
    form_data: dict


class AccountData(TypedDict):
    username: str
    email: str
    password: str
    proxy_id: int
    custom_fields: dict[str, list[str] | None]
    status: str
    attempts: int
    last_attempt: str | None


class FillFieldsResult(TypedDict):
    ok:                   bool
    filled:               list[str]
    skipped:              list[str]
    filled_from_outside:  list[str]
    new_custom_selectors: dict[str, tuple[str, str]]
    new_checkboxes:       list[tuple[str, str]]
    found_submit:         tuple[str, str] | None
    reason:               NotRequired[str]


class RegistrationController:
    """
    Контроллер для регистрации аккаунтов на форумах.
    
    Использует BrowserController для управления браузером,
    TemplateManager для работы с шаблонами,
    SelectorFinder для эвристического поиска полей.
    """

    _PATTERN_CTRL_HASH: re.Pattern = re.compile(
        r"^#ctrl_[a-f0-9]{32}$", re.IGNORECASE
    )
    _PATTERN_HEX_ID: re.Pattern = re.compile(
        r"^#[a-f0-9]{16,}$", re.IGNORECASE
    )
    MAX_SELECTORS_PER_FIELD: int = 10
    DYNAMIC_ID_PREFIXES: tuple[str, ...] = (
        "#js_", "#random-", "#uuid-", "#generated-", "#id-",
    )

    def __init__(
        self,
        browser_controller,
        template_manager,
        selector_finder,
        page,
        config: dict | None = None,
        captcha_helper=None
    ):
        """
        Args:
            browser_controller: экземпляр BrowserController для управления браузером.
            template_manager: экземпляр TemplateManager для работы с шаблонами.
            selector_finder: экземпляр SelectorFinder для эвристического поиска полей.
            page: объект текущей страницы Pydoll для выполнения запросов к DOM.
            config: словарь с настройками (таймауты, индикаторы успеха/ошибки и др.).
        """
        self.browser = browser_controller
        self.template_manager = template_manager
        self.selector_finder = selector_finder
        self.page = page
        self.config = config or {}
        self.captcha_helper = captcha_helper
        
        # Индикаторы для проверки результата (из config или дефолтные)
        self.success_indicators = self.config.get("success_indicators", [
            "thank you", "activate", "успешно", "created", "account has been"
        ])
        self.error_indicators = self.config.get("error_indicators", [
            "error", "failed", "invalid", "ошибка", "already taken"
        ])
        
        self.screenshot_dir = Path(__file__).parent.parent / "data" / "screenshots"
        self.screenshot_dir.mkdir(parents=True, exist_ok=True)
        self._last_checkbox_failed = False
    
    async def register(
        self,
        account_data: AccountData,
        engine_name: str,
        template: dict | None = None,
    ) -> RegistrationResult:
        """
        Выполняет регистрацию на текущем форуме.
        
        Args:
            account_data: Данные аккаунта для регистрации
            engine_name: Имя движка/платформы (определяется в main_orchestrator)
            template: Загруженный или созданный шаблон (может быть None)
        """
        logger.info(f"Начинаем регистрацию пользователя: {account_data['username']}")

        # Проверка обязательных полей
        required = ("username", "email", "password")
        missing = [f for f in required if not account_data.get(f)]
        if missing:
            logger.warning(f"Пропуск аккаунта — отсутствуют поля: {missing}")
            return {
                "success": False,
                "message": f"Missing required fields: {missing}",
                "reason": "missing_fields",
                "template_used": None,
                "screenshot": None,
                "form_data": account_data
            }

        # Проверка статуса страницы и перезагрузка перенесены в browser_controller.goto()
        # engine_name и template уже получены снаружи (в main_orchestrator.py)
        # и переданы в register как аргументы

        try:
            # Переходим на страницу регистрации, используя готовый шаблон (или эвристику)
            await self._navigate_to_registration_page(template=template)

            username_was_filled = False  # Флаг: логин уже был заполнен в этой сессии
            max_steps = 5

            # Получаем список всех блоков на странице (отсортированы по score)
            all_blocks = await self.selector_finder.analyze_current_page(template=template)
            if not all_blocks:
                logger.warning("Блоки регистрации не найдены на странице")
                return {
                    "success": False,
                    "message": "Регистрационная форма не найдена",
                    "reason": "no_form_detected",
                    "template_used": template.get("name") if template else None,
                    "screenshot": None,
                    "form_data": account_data
                }

            logger.info(f"Найдено блоков для перебора: {len(all_blocks)}")
            for _i, _b in enumerate(all_blocks):
                logger.debug(
                    f"  Блок [{_i + 1}/{len(all_blocks)}]: "
                    f"{_b['form_selector']} | "
                    f"score={_b['score']} | "
                    f"template_matches={_b.get('template_matches', 0)}"
                )
            current_block_index = 0

            # 3. Универсальный цикл по шагам регистрации
            for step_num in range(1, max_steps + 1):
                logger.info(f"Шаг регистрации {step_num}/{max_steps}")

                # Берём текущий блок
                if current_block_index >= len(all_blocks):
                    logger.warning("Все блоки перебраны — регистрация не удалась")
                    test_result = await self._confirm_test_mode(
                        step_num=step_num,
                        success=False,
                        error_reason="no_form_detected",
                    )
                    if test_result is True:
                        await self._save_block_to_template(
                            block=current_block,
                            selectors=selectors,
                            filled_fields=filled_fields,
                            template=template,
                            engine_name=engine_name,
                            new_custom_selectors=fill_result.get("new_custom_selectors", {}) if fill_result else {},
                            new_checkboxes=fill_result.get("new_checkboxes", []) if fill_result else [],
                            found_submit=fill_result.get("found_submit") if fill_result else None,
                        )
                        await self._save_filled_to_profile(
                            filled_from_outside=fill_result.get("filled_from_outside", []) if fill_result else [],
                            skipped_fields=fill_result.get("skipped", []) if fill_result else [],
                            account_data=account_data,
                            username=account_data.get("username", ""),
                        )
                        return {
                            "success": True,
                            "message": "Регистрация завершена успешно (подтверждено в тесте)",
                            "reason": None,
                            "template_used": template.get("name") if template else "heuristic",
                            "screenshot": None,
                            "form_data": account_data
                        }
                    return {
                        "success": False,
                        "message": "Все блоки форм перебраны без успеха",
                        "reason": "no_form_detected",
                        "template_used": template.get("name") if template else None,
                        "screenshot": await self._take_screenshot("no_form", account_data.get("username")),
                        "form_data": account_data
                    }

                current_block = all_blocks[current_block_index]
                form_selector = current_block["form_selector"]
                logger.info(
                    f"Используем блок [{current_block_index + 1}/{len(all_blocks)}]: "
                    f"{form_selector} (score={current_block['score']}, "
                    f"template_matches={current_block.get('template_matches', 0)})"
                )

                # Получаем селекторы — из шаблона + текущий блок
                selectors = await self._get_selectors_for_block(
                    template=template,
                    block=current_block,
                )

                # Снимок блока ДО заполнения и submit
                block_snapshot = self._make_block_snapshot(selectors)

                # Заполняем поля
                fill_result = await self._fill_fields(selectors, account_data, template, engine_name, form_selector)
                username_was_filled = username_was_filled or ("username" in fill_result["filled"])
                filled_fields = fill_result["filled"]

                if not fill_result["ok"]:
                    reason = fill_result.get("reason", "")
                    if reason == "submit_failed":
                        # Кнопка не нажалась — блок не подошёл, пробуем следующий
                        logger.info(
                            f"Кнопка submit не нажата — "
                            f"блок '{form_selector}' не подошёл, переходим к следующему"
                        )
                        current_block_index += 1
                        continue
                    return {
                        "success": False,
                        "message": "Тайм-аут заполнения полей вручную",
                        "reason": "manual_fill_timeout",
                        "template_used": template["name"] if template else "heuristic",
                        "screenshot": await self._take_screenshot("manual_fill_timeout", account_data.get("username")),
                        "form_data": account_data
                    }

                # Ждём загрузки страницы после submit и проверяем наличие контента.
                # Если страница не загрузилась — выполняем refresh с увеличенным ожиданием.
                # Перенесено сюда из _check_result чтобы _check_block_changed
                # тоже получал уже загруженную страницу.
                page_load_wait: float = self.config.get("PAGE_LOAD_WAIT", 5.0)
                page_reload_timeout: float = self.config.get("FIND_REGISTRATION_PAGE_TIMEOUT", 60.0)
                min_content_length: int = self.config.get("MIN_PAGE_CONTENT_LENGTH", 300)

                await asyncio.sleep(page_load_wait)

                async def _get_body_html_inline() -> str:
                    """Возвращает полный innerHTML body — для проверки загрузки страницы."""
                    try:
                        resp = await self.page.execute_script(
                            "return document.body ? document.body.innerHTML : ''"
                        )
                        return resp.get("result", {}).get("result", {}).get("value") or ""
                    except Exception as e:
                        logger.error(f"Не удалось получить innerHTML body: {e}")
                        return ""

                full_html: str = await _get_body_html_inline()
                logger.debug(
                    f"Размер body.innerHTML после submit: {len(full_html)} симв."
                )

                if len(full_html) <= min_content_length:
                    logger.warning(
                        f"Страница не загрузилась после submit "
                        f"({len(full_html)} симв., порог={min_content_length}) — "
                        f"выполняем refresh и ожидаем {page_reload_timeout}с"
                    )
                    try:
                        await self.browser.refresh()
                        logger.info(
                            f"Refresh выполнен, ожидаем {page_reload_timeout}с"
                        )
                        await asyncio.sleep(page_reload_timeout)
                        full_html = await _get_body_html_inline()
                        logger.debug(
                            f"Размер body.innerHTML после refresh: {len(full_html)} симв."
                        )
                        if len(full_html) <= min_content_length:
                            logger.error(
                                f"Страница не загрузилась после refresh "
                                f"({len(full_html)} симв.) — продолжаем без гарантии"
                            )
                    except Exception as e:
                        logger.error(f"Ошибка refresh страницы: {e}")

                # Правило 1 — проверяем изменился ли блок после submit
                block_changed, has_any_fields, new_blocks = await self._check_block_changed(
                    snapshot=block_snapshot,
                    form_selector=form_selector,
                    template=template,
                )

                if block_changed and has_any_fields:
                    # Страница сменилась, есть поля — продолжаем регистрацию
                    logger.info(
                        f"Блок '{form_selector}' изменился, "
                        f"на странице есть поля — продолжаем регистрацию"
                    )
                    await self._save_block_to_template(
                        block=current_block,
                        selectors=selectors,
                        filled_fields=filled_fields,
                        template=template,
                        engine_name=engine_name,
                        new_custom_selectors=fill_result.get("new_custom_selectors", {}) if fill_result else {},
                        new_checkboxes=fill_result.get("new_checkboxes", []) if fill_result else [],
                        found_submit=fill_result.get("found_submit") if fill_result else None,
                    )
                    # Сохраняем пропуски страницы — она принята успешно
                    await self._save_filled_to_profile(
                        filled_from_outside=fill_result.get("filled_from_outside", []) if fill_result else [],
                        skipped_fields=fill_result.get("skipped", []) if fill_result else [],
                        account_data=account_data,
                        username=account_data.get("username", ""),
                    )
                    all_blocks = new_blocks
                    current_block_index = 0
                    if not all_blocks:
                        logger.warning("На новой странице блоки не найдены")
                    continue
                
                if block_changed and not has_any_fields:
                    # Блок изменился, полей нет — переходим к проверке индикаторов
                    logger.debug(
                        "Блок изменился, полей на странице нет — "
                        "переходим к проверке индикаторов"
                    )
                else:
                    # Блок не изменился — submit не сработал, пробуем следующий блок
                    logger.debug(
                        f"Блок '{form_selector}' не изменился — пробуем следующий блок"
                    )
                    current_block_index += 1
                    continue

                # Правила 2, 3, 4 — проверка индикаторов
                success, error_reason = await self._check_result(
                    template=template,
                    username_was_filled=username_was_filled,
                    engine_name=engine_name,
                )
                logger.debug(
                    f"Результат шага {step_num}: success={success}, "
                    f"error_reason={error_reason}, username_was_filled={username_was_filled}"
                )

                # Фатальная ошибка
                if error_reason and error_reason != "no_indicators":
                    screenshot_path = await self._take_screenshot(
                        prefix="error",
                        username=account_data.get("username")
                    )
                    logger.warning(
                        f"Регистрация не удалась [{account_data['username']}]: {error_reason}"
                    )
                    test_result = await self._confirm_test_mode(
                        step_num=step_num,
                        success=False,
                        error_reason=error_reason,
                    )
                    if test_result is True:
                        # Пользователь подтвердил успех — сохраняем и выходим
                        await self._save_block_to_template(
                            block=current_block,
                            selectors=selectors,
                            filled_fields=filled_fields,
                            template=template,
                            engine_name=engine_name,
                            new_custom_selectors=fill_result.get("new_custom_selectors", {}) if fill_result else {},
                            new_checkboxes=fill_result.get("new_checkboxes", []) if fill_result else [],
                            found_submit=fill_result.get("found_submit") if fill_result else None,
                        )
                        await self._save_filled_to_profile(
                            filled_from_outside=fill_result.get("filled_from_outside", []) if fill_result else [],
                            skipped_fields=fill_result.get("skipped", []) if fill_result else [],
                            account_data=account_data,
                            username=account_data.get("username", ""),
                        )
                        return {
                            "success": True,
                            "message": "Регистрация завершена успешно (подтверждено в тесте)",
                            "reason": None,
                            "template_used": template.get("name") if template else "heuristic",
                            "screenshot": None,
                            "form_data": account_data
                        }
                    return {
                        "success": False,
                        "message": f"Регистрация не удалась: {error_reason}",
                        "reason": error_reason,
                        "template_used": template.get("name") if template else "heuristic",
                        "screenshot": screenshot_path,
                        "form_data": account_data
                    }

                # Успех
                if success:
                    test_result = await self._confirm_test_mode(
                        step_num=step_num,
                        success=True,
                        error_reason=None,
                    )
                    if test_result is False:
                        # Пользователь отклонил — продолжаем перебор блоков
                        current_block_index += 1
                        continue
                    logger.info(f"Регистрация завершена успешно: {account_data['username']}")
                    await self._save_block_to_template(
                        block=current_block,
                        selectors=selectors,
                        filled_fields=filled_fields,
                        template=template,
                        engine_name=engine_name,
                        new_custom_selectors=fill_result.get("new_custom_selectors", {}) if fill_result else {},
                        new_checkboxes=fill_result.get("new_checkboxes", []) if fill_result else [],
                        found_submit=fill_result.get("found_submit") if fill_result else None,
                    )
                    # Сохраняем пропуски финальной страницы
                    await self._save_filled_to_profile(
                        filled_from_outside=fill_result.get("filled_from_outside", []) if fill_result else [],
                        skipped_fields=fill_result.get("skipped", []) if fill_result else [],
                        account_data=account_data,
                        username=account_data.get("username", ""),
                    )
                    return {
                        "success": True,
                        "message": "Регистрация завершена успешно",
                        "reason": None,
                        "template_used": template.get("name") if template else "heuristic",
                        "screenshot": None,
                        "form_data": account_data
                    }

                # no_indicators — переходим к следующему блоку
                logger.debug(
                    f"Индикаторов не найдено — переходим к следующему блоку"
                )
                current_block_index += 1
                          
            # Достигнут лимит шагов — ручное подтверждение
            logger.warning("Достигнут лимит шагов — запрашиваем ручное подтверждение")
            test_result = await self._confirm_test_mode(
                step_num=max_steps,
                success=False,
                error_reason="max_steps_exceeded",
            )
            if test_result is True:
                await self._save_block_to_template(
                    block=current_block,
                    selectors=selectors,
                    filled_fields=filled_fields,
                    template=template,
                    engine_name=engine_name,
                    new_custom_selectors=fill_result.get("new_custom_selectors", {}) if fill_result else {},
                    new_checkboxes=fill_result.get("new_checkboxes", []) if fill_result else [],
                    found_submit=fill_result.get("found_submit") if fill_result else None,
                )
                await self._save_filled_to_profile(
                    filled_from_outside=fill_result.get("filled_from_outside", []) if fill_result else [],
                    skipped_fields=fill_result.get("skipped", []) if fill_result else [],
                    account_data=account_data,
                    username=account_data.get("username", ""),
                )
                return {
                    "success": True,
                    "message": "Регистрация завершена успешно (ручное подтверждение)",
                    "reason": None,
                    "template_used": template.get("name") if template else "heuristic",
                    "screenshot": None,
                    "form_data": account_data
                }
            return {
                "success": False,
                "message": "Достигнут лимит шагов регистрации",
                "reason": "max_steps_exceeded",
                "template_used": template.get("name") if template else "heuristic",
                "screenshot": await self._take_screenshot("max_steps", account_data.get("username")),
                "form_data": account_data
            }

        except Exception as e:
            logger.exception(f"Ошибка регистрации пользователя {account_data['username']}: {type(e).__name__}: {e}")
            screenshot_path = await self._take_screenshot(
                prefix="exception",
                username=account_data.get("username")
            )
            return {
                "success": False,
                "message": f"Exception: {type(e).__name__}",
                "reason": f"exception: {type(e).__name__}",
                "template_used": None,
                "screenshot": screenshot_path,
                "form_data": account_data
            }

    async def _get_selectors_for_block(
        self,
        template: dict | None,
        block: dict,
    ) -> dict:
        """Формирует словарь селекторов только для полей реально присутствующих в блоке.

        Для каждого поля проставляет метку *_source:
        - "template"      — селектор или label совпали с шаблоном (не сохраняем повторно)
        - "common_fields" — найдено эвристикой, есть label (будет сохранено в шаблон при успешной регистрации)
        - "manual"        — найдено без label (будет сохранено в шаблон при успешной регистрации)

        Args:
            template: Текущий шаблон.
            block: Блок из analyze_current_page.

        Returns:
            Словарь селекторов полей с метками *_source.
        """
        selectors: dict = {}
        # get() уже возвращает {} при отсутствии ключа — двойной or {} не нужен
        template_fields = (template or {}).get("fields", {})

        STANDARD_KEYS = [
            "username", "email", "confirm_email", "password", "confirm_password",
            "agree_checkbox", "submit_button", "captcha_indicator",
            "register_radio",
        ]

        # Локальный кэш DOM-проверок — сбрасывается при каждом новом вызове метода,
        # не живёт между вызовами. Избегает повторных page.query() для одного селектора.
        _dom_cache: dict[str, bool] = {}

        async def _exists_in_dom(sel: str) -> bool:
            """Проверяет наличие элемента в DOM текущей страницы."""
            if sel not in _dom_cache:
                el = await self.page.query(sel, raise_exc=False, timeout=3)
                _dom_cache[sel] = el is not None
                logger.debug(
                    f"DOM-проверка: '{sel}' → "
                    f"{'найден' if _dom_cache[sel] else 'отсутствует'}"
                )
            return _dom_cache[sel]

        for key in STANDARD_KEYS:
            # Значение и label из текущего блока
            block_val = block.get(key)
            block_label = block.get(f"{key}_label") or ""

            # Значение и labels из шаблона
            tmpl_sel_raw = template_fields.get(key)

            # Пустой список в шаблоне — некорректные данные, логируем
            if isinstance(tmpl_sel_raw, list) and not tmpl_sel_raw:
                logger.debug(f"Шаблон для '{key}' содержит пустой список селекторов")

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

            # Совпадение по label (частичное вхождение в любую сторону, inline)
            is_match_by_label = bool(block_label and tmpl_labels) and any(
                (lbl.lower() in block_label.lower()) or (block_label.lower() in lbl.lower())
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
                source = "common_fields" if block_label else "manual"
                selectors[key] = block_val
                selectors[f"{key}_label"] = block_label
                selectors[f"{key}_source"] = source
                logger.debug(f"Поле '{key}' — новый вариант → source={source}")
                continue

            # Поле не найдено в блоке — последний шанс: шаблонный селектор в DOM
            if tmpl_sel and await _exists_in_dom(tmpl_sel):
                selectors[key] = tmpl_sel
                selectors[f"{key}_label"] = ""
                selectors[f"{key}_source"] = "template"
                logger.debug(
                    f"Поле '{key}' — не в блоке, шаблонный селектор найден в DOM → "
                    f"source=template"
                )
            else:
                logger.debug(
                    f"Поле '{key}' — не найдено ни в блоке ни в DOM → пропускаем"
                )

        # Переносим все *_label из блока (для ручного ввода и custom полей)
        for k in list(block.keys()):
            if k.endswith("_label") and k not in selectors:
                selectors[k] = block[k]

        # custom_fields берём напрямую из блока
        if "custom_fields" in block:
            selectors["custom_fields"] = block["custom_fields"]

        logger.debug(f"Итоговые селекторы для блока: {list(selectors.keys())}")
        return selectors
    
    def _make_block_snapshot(
        self,
        selectors: dict,
    ) -> frozenset[tuple[str, str, str, str, str]]:
        """Создаёт снимок полей блока для сравнения до и после submit.

        Снимок содержит пятёрки:
            (имя_поля, тип, селектор, label, value)

        Используется для определения изменений на странице после submit:
        - блок исчез
        - количество полей изменилось
        - названия/типы/значения полей изменились

        Args:
            selectors: Словарь селекторов из _get_selectors_for_block.

        Returns:
            frozenset пятёрок — порядок не важен, важен состав.
        """
        snapshot: set[tuple[str, str, str, str, str]] = set()

        # Стандартные поля
        standard = (
            "username", "email", "confirm_email", "password", "confirm_password",
            "agree_checkbox", "submit_button", "captcha_indicator",
            "register_radio",
        )
        for field_name in standard:
            sel = selectors.get(field_name)
            if not sel:
                continue
            # captcha_indicator может быть CaptchaInfo (dict) — берём selector
            if isinstance(sel, dict):
                sel = sel.get("selector") or ""
            if not sel:
                continue
            label = selectors.get(f"{field_name}_label", "") or ""
            snapshot.add((field_name, "", sel, label, ""))

        # custom_fields
        for custom in selectors.get("custom_fields", []):
            if not isinstance(custom, dict):
                continue
            name = custom.get("name", "")
            sel = custom.get("selector", "")
            field_type = custom.get("type", "")
            label = custom.get("display_text", "") or ""
            value = custom.get("value", "") or ""
            if sel:
                snapshot.add((name, field_type, sel, label, value))

        return frozenset(snapshot)
    
    async def _check_block_changed(
        self,
        snapshot: frozenset[tuple[str, str, str, str, str]],
        form_selector: str,
        template: dict | None,
    ) -> tuple[bool, bool, list[dict]]:
        """Проверяет изменился ли блок после submit.

        Сканирует страницу один раз и возвращает результаты сканирования
        вместе с выводом — чтобы избежать повторного вызова analyze_current_page
        в вызывающем коде.

        Args:
            snapshot: Снимок блока до submit из _make_block_snapshot.
            form_selector: CSS селектор формы текущего блока.
            template: Текущий шаблон.

        Returns:
            Кортеж (changed, has_any_fields, new_blocks):
                changed — True если блок изменился
                has_any_fields — True если на странице есть хоть какие-то блоки/поля
                new_blocks — результат analyze_current_page для переиспользования
        """
        new_blocks = await self.selector_finder.analyze_current_page(template=template)
        has_any_fields = bool(new_blocks)

        # Ищем тот же блок по form_selector
        same_block = next(
            (b for b in new_blocks if b["form_selector"] == form_selector),
            None,
        )

        if same_block is None:
            logger.debug(
                f"Блок '{form_selector}' после submit не найден — изменение обнаружено"
            )
            return True, has_any_fields, new_blocks

        # Блок есть — делаем новый снимок и сравниваем
        new_selectors = await self._get_selectors_for_block(
            template=template,
            block=same_block,
        )
        new_snapshot = self._make_block_snapshot(new_selectors)

        if new_snapshot != snapshot:
            logger.debug(
                f"Блок '{form_selector}' изменился после submit — "
                f"было {len(snapshot)} элементов, стало {len(new_snapshot)}"
            )
            return True, has_any_fields, new_blocks

        logger.debug(
            f"Блок '{form_selector}' не изменился после submit — "
            f"пробуем следующий блок"
        )
        return False, has_any_fields, new_blocks

    async def _save_block_to_template(
        self,
        block: dict,
        selectors: dict,
        filled_fields: list[str],
        template: dict | None,
        engine_name: str | None,
        new_custom_selectors: dict[str, tuple[str, str]] | None = None,
        new_checkboxes: list[tuple[str, str]] | None = None,
        found_submit: tuple[str, str] | None = None,
    ) -> None:
        """Сохраняет в шаблон только новые данные текущего блока.

        Вызывается только после подтверждённого успеха:
        - Триггер А: финальная успешная регистрация
        - Триггер Б: переход на следующую страницу (блок принят)
        - Триггер В: ручное подтверждение оператором

        Шаг 0: единая фильтрация всех входных данных до блоков А–Г.
        Два критерия отсечения:
        - source in (None, "", "unknown") → ошибка в коде выше, пропускаем с ERROR
        - source == "template"            → уже в шаблоне, пропускаем
        - _is_dynamic_selector(sel)       → динамический ID, пропускаем

        Блоки записи:
        - Блок А: стандартные поля из filled_fields
        - Блок Б: custom_fields из new_custom_selectors
        - Блок В: agree_step.checkboxes из new_checkboxes
        - Блок Г: submit_button fallback из found_submit
        - Блок Д: form_selector из block

        Args:
            block: Блок из analyze_current_page.
            selectors: Словарь селекторов с метками *_source из _get_selectors_for_block.
            filled_fields: Список реально заполненных полей из _fill_fields.
            template: Текущий шаблон в памяти (для проверки дублей).
            engine_name: Название движка = имя файла шаблона без расширения.
            new_custom_selectors: Новые селекторы custom-полей: {field: (sel, source)}.
            new_checkboxes: Новые чекбоксы эвристики: [(sel, source), ...].
            found_submit: Найденный fallback-селектор кнопки: (sel, source) | None.
        """
        if not engine_name:
            logger.debug("Сохранение в шаблон пропущено: нет engine_name")
            return

        new_custom_selectors = new_custom_selectors or {}
        new_checkboxes = new_checkboxes or []

        template_fields = (template or {}).get("fields", {})
        fields_to_save: dict = {}
        checkboxes_to_save: list[str] = []

        # ── Шаг 0: единая фильтрация (ОДИН РАЗ до всех блоков) ──────────────

        def _check_source(src: str | None, sel: str, field: str) -> bool:
            """Возвращает True если селектор прошёл фильтр и может быть сохранён."""
            if src in (None, "", "unknown"):
                logger.error(
                    f"Селектор без валидного source: '{sel}' "
                    f"для поля '{field}' — пропускаем"
                )
                return False
            if src == "template":
                logger.debug(
                    f"Поле '{field}': source=template → пропускаем"
                )
                return False
            if self._is_dynamic_selector(sel):
                logger.debug(
                    f"Отсечён динамический селектор '{field}': {sel} "
                    f"[source={src}]"
                )
                return False
            return True

        filled_fields_clean: list[str] = [
            f for f in filled_fields
            if _check_source(
                selectors.get(f"{f}_source"),
                selectors.get(f, ""),
                f,
            )
        ]

        new_custom_clean: dict[str, str] = {
            field: sel
            for field, (sel, src) in new_custom_selectors.items()
            if _check_source(src, sel, field)
        }

        new_checkboxes_clean: list[str] = [
            sel
            for sel, src in new_checkboxes
            if _check_source(src, sel, "checkbox")
        ]

        found_submit_clean: str | None = None
        if found_submit:
            sel, src = found_submit
            if _check_source(src, sel, "submit_button"):
                found_submit_clean = sel

        # ── Блок А: стандартные поля ──────────────────────────────────────────

        for key in filled_fields_clean:
            selector = selectors.get(key)
            label = selectors.get(f"{key}_label", "")
            source = selectors.get(f"{key}_source", "")

            if selector:
                existing = template_fields.get(key)
                existing_list = (
                    existing if isinstance(existing, list)
                    else ([existing] if existing else [])
                )
                if len(existing_list) >= self.MAX_SELECTORS_PER_FIELD:
                    logger.warning(
                        f"Превышен лимит селекторов для '{key}' "
                        f"({len(existing_list)}/{self.MAX_SELECTORS_PER_FIELD}) "
                        f"— '{selector}' не сохранён"
                    )
                elif selector not in existing_list:
                    fields_to_save[key] = selector
                    logger.info(
                        f"Новый селектор для '{key}' [{source}]: {selector}"
                    )
                else:
                    logger.debug(
                        f"Селектор для '{key}' уже в шаблоне → пропускаем"
                    )

            if label:
                existing_label = template_fields.get(f"{key}_label")
                existing_label_list = (
                    existing_label if isinstance(existing_label, list)
                    else ([existing_label] if existing_label else [])
                )
                if label not in existing_label_list:
                    fields_to_save[f"{key}_label"] = label
                    logger.info(f"Новый label для '{key}': «{label}»")
                else:
                    logger.debug(
                        f"Label для '{key}' уже в шаблоне → пропускаем"
                    )

        # ── Блок Б: custom_fields ─────────────────────────────────────────────

        for field, sel in new_custom_clean.items():
            existing = template_fields.get(field)
            existing_list = (
                existing if isinstance(existing, list)
                else ([existing] if existing else [])
            )
            if len(existing_list) >= self.MAX_SELECTORS_PER_FIELD:
                logger.warning(
                    f"Превышен лимит селекторов для custom '{field}' "
                    f"({len(existing_list)}/{self.MAX_SELECTORS_PER_FIELD}) "
                    f"— '{sel}' не сохранён"
                )
            elif sel not in existing_list:
                fields_to_save[field] = sel
                logger.info(f"Новый селектор custom-поля '{field}': {sel}")
            else:
                logger.debug(
                    f"Селектор custom '{field}' уже в шаблоне → пропускаем"
                )

        # ── Блок В: agree_step.checkboxes ─────────────────────────────────────

        existing_checkboxes: list[str] = (
            (template or {}).get("agree_step", {}).get("checkboxes") or []
        )
        for sel in new_checkboxes_clean:
            if len(existing_checkboxes) >= self.MAX_SELECTORS_PER_FIELD:
                logger.warning(
                    f"Превышен лимит чекбоксов "
                    f"({len(existing_checkboxes)}/{self.MAX_SELECTORS_PER_FIELD}) "
                    f"— '{sel}' не сохранён"
                )
            elif sel not in existing_checkboxes:
                checkboxes_to_save.append(sel)
                logger.info(f"Новый чекбокс согласия: {sel}")
            else:
                logger.debug(f"Чекбокс '{sel}' уже в шаблоне → пропускаем")

        # ── Блок Г: submit_button fallback ────────────────────────────────────

        if found_submit_clean:
            existing_submit = template_fields.get("submit_button")
            existing_submit_list = (
                existing_submit if isinstance(existing_submit, list)
                else ([existing_submit] if existing_submit else [])
            )
            if len(existing_submit_list) >= self.MAX_SELECTORS_PER_FIELD:
                logger.warning(
                    f"Превышен лимит для submit_button "
                    f"({len(existing_submit_list)}/{self.MAX_SELECTORS_PER_FIELD}) "
                    f"— '{found_submit_clean}' не сохранён"
                )
            elif found_submit_clean not in existing_submit_list:
                fields_to_save["submit_button"] = found_submit_clean
                logger.info(
                    f"Новый fallback-селектор submit_button: {found_submit_clean}"
                )
            else:
                logger.debug(
                    f"submit_button '{found_submit_clean}' уже в шаблоне → пропускаем"
                )

        # ── Блок Д: form_selector ─────────────────────────────────────────────

        new_form_selector: str | None = None
        raw_form_selector = block.get("form_selector")
        form_source = block.get("form_selector_source", "heuristic")

        if raw_form_selector:
            if form_source in (None, "", "unknown"):
                logger.error(
                    f"form_selector без валидного source: "
                    f"'{raw_form_selector}' — пропускаем"
                )
            elif form_source == "template":
                logger.debug(
                    f"Отсечён form_selector: '{raw_form_selector}' "
                    f"[source=template]"
                )
            elif self._is_dynamic_selector(raw_form_selector):
                logger.debug(
                    f"Отсечён динамический form_selector: '{raw_form_selector}'"
                )
            else:
                new_form_selector = raw_form_selector

        # ── Проверка наличия данных для записи ───────────────────────────────

        if not fields_to_save and not checkboxes_to_save and not new_form_selector:
            logger.debug("Нет новых данных для сохранения в шаблон")
            return

        # ── Один вызов update_template ────────────────────────────────────────

        new_data: dict = {}

        if new_form_selector:
            new_data["registration_page"] = {
                "form_selector": [new_form_selector]
            }
        if fields_to_save:
            new_data["fields"] = fields_to_save
        if checkboxes_to_save:
            new_data["agree_step"] = {"checkboxes": checkboxes_to_save}

        try:
            await self.template_manager.update_template(
                engine_name=engine_name,
                new_data=new_data,
            )
            logger.info(
                f"Шаблон '{engine_name}' обновлён: "
                f"полей={list(fields_to_save.keys())}, "
                f"чекбоксов={len(checkboxes_to_save)}"
            )
        except Exception as e:
            logger.error(
                f"Не удалось обновить шаблон '{engine_name}': {e}"
            )
            return

        # ── Синхронизация template в памяти (только после успешной записи) ───

        if not template:
            return

        if fields_to_save:
            for key, val in fields_to_save.items():
                existing = (
                    template.setdefault("fields", {})
                            .setdefault(key, [])
                )
                if isinstance(existing, str):
                    existing = [existing]
                    template["fields"][key] = existing
                if val not in existing:
                    existing.append(val)

        if checkboxes_to_save:
            existing = (
                template.setdefault("agree_step", {})
                        .setdefault("checkboxes", [])
            )
            for sel in checkboxes_to_save:
                if sel not in existing:
                    existing.append(sel)

        if found_submit_clean:
            existing_sub = (
                template.setdefault("fields", {})
                        .setdefault("submit_button", [])
            )
            if isinstance(existing_sub, str):
                existing_sub = [existing_sub]
                template["fields"]["submit_button"] = existing_sub
            if found_submit_clean not in existing_sub:
                existing_sub.append(found_submit_clean)

        if new_form_selector:
            existing_fs = (
                template.setdefault("registration_page", {})
                        .setdefault("form_selector", [])
            )
            if isinstance(existing_fs, str):
                existing_fs = [existing_fs]
                template["registration_page"]["form_selector"] = existing_fs
            if new_form_selector not in existing_fs:
                existing_fs.append(new_form_selector)

    async def _save_filled_to_profile(
        self,
        filled_from_outside: list[str],
        skipped_fields: list[str],
        account_data: AccountData,
        username: str,
    ) -> None:
        """Сохраняет в профиль значения полей заполненных из п.3/п.4 и пропущенных.

        Вызывается строго при подтверждённом успехе страницы.
        Значения уже обновлены в account_data["custom_fields"] в памяти.
        Сохраняет все изменения на диск одним запросом.
        """
        if not username:
            logger.warning("_save_filled_to_profile: username пустой — сохранение пропущено")
            return

        all_fields = list(dict.fromkeys(filled_from_outside + skipped_fields))
        if not all_fields:
            logger.debug("Нет новых данных для сохранения в профиль")
            return

        profile_custom = account_data.get("custom_fields", {}) or {}
        fields_to_save: dict[str, list[str] | list] = {}

        for field_name in all_fields:
            if field_name in profile_custom:
                fields_to_save[field_name] = profile_custom[field_name]
                logger.debug(
                    f"Сохраняем поле профиля [{username}]: "
                    f"{field_name}={profile_custom[field_name]!r}"
                )

        if not fields_to_save:
            logger.debug("Нет изменённых полей для сохранения в профиль")
            return

        try:
            await self.template_manager._update_account_profile(username, fields_to_save)
            logger.info(
                f"Сохранены поля в профиль [{username}]: "
                f"{list(fields_to_save.keys())}"
            )
        except Exception as e:
            logger.warning(f"Не удалось сохранить поля в профиль: {e}")
            
    async def _navigate_to_registration_page(self, template: dict | None) -> bool:
        """Переходит на страницу регистрации.
    
        Сначала пробует шаблон, затем ищет ссылку эвристически.
    
        Returns:
            True если перешли на страницу регистрации, False если не нашли.
        """
        if template:
            reg_page = template.get("registration_page", {})
            reg_url = reg_page.get("url")
            if isinstance(reg_url, list) and reg_url:
                current_url = await self.page.current_url
                parsed = urlparse(current_url)
                # Обрезаем последний сегмент пути (файл/страница)
                # /forum_vb/showthread.php → /forum_vb
                # /index.php              → (пусто)
                base_path = parsed.path.rsplit("/", 1)[0]
                base_url = f"{parsed.scheme}://{parsed.netloc}{base_path}"
                for idx, url_variant in enumerate(reg_url):
                    variant_clean = url_variant.lstrip("/")
                    full_url = f"{base_url}/{variant_clean}" if variant_clean else base_url
                    logger.debug(
                        f"Пробуем вариант URL регистрации "
                        f"[{idx + 1}/{len(reg_url)}]: {full_url}"
                    )
                    try:
                        await self.browser.goto(full_url)
                        logger.info(
                            f"Перешли на страницу регистрации по шаблону: {full_url}"
                        )
                        return True
                    except Exception as e:
                        logger.debug(
                            f"Вариант [{idx + 1}/{len(reg_url)}] недоступен "
                            f"({type(e).__name__}): {full_url}"
                        )
                logger.debug(
                    "Все варианты URL из шаблона не сработали — "
                    "переходим к эвристике"
                )
                
        # Эвристика запускается в трёх случаях:
        # 1. template = None — движок не определён, шаблона нет
        # 2. reg_url = [] — шаблон есть, но URL регистрации не указан
        # 3. Все варианты URL из шаблона не сработали (недоступны)
        # Ищем ссылку на регистрацию на текущей странице форума
        logger.info("Ищем ссылку на регистрацию...")
        reg_link = await self.selector_finder.find_registration_link()
        if reg_link:
            await self.browser.goto(reg_link)
            logger.info(f"Перешел на страницу регистрации: {reg_link}")
            return True
    
        # Возможно уже на странице регистрации
        current_url = await self.page.current_url
        if any(kw in current_url.lower() for kw in ["register", "signup", "регистр"]):
            logger.info("Уже на странице регистрации (проверка URL)")
            return True
    
        logger.warning("Страница регистрации не найдена")
        return False
          
    async def _fill_fields(
        self,
        selectors: dict,
        account_data: AccountData,
        template: dict | None = None,
        engine_name: str | None = None,
        form_selector: str | None = None,
    ) -> FillFieldsResult:
        """Заполняет поля формы регистрации.

        Порядок работы:
        1. Поля ввода (standard + custom) — авто, ручной если селектор есть но не сработал
        2. Чекбоксы — авто, ручной если найдены но не нажались
        3. Капча — авто (API), ручной если API не справился
        4. Кнопка submit — авто, ручной если селектор есть но не нажалась
        При пропуске ручного ввода на любом шаге — возвращает ok: False.

        Args:
            selectors: Словарь селекторов полей.
            account_data: Данные аккаунта пользователя.
            template: Текущий шаблон (только для чтения — не обновляется внутри метода).
            engine_name: Название движка (передаётся в аккумуляторы для контекста).
            form_selector: Селектор формы (для передачи в _handle_submit).

        Returns:
            FillFieldsResult со следующими полями:
            - ok: False только при таймауте ручного ввода
            - filled: список реально заполненных полей
            - skipped: список пропущенных пользователем полей
            - filled_from_outside: заполнены из п.3/п.4 — не из профиля/шаблона
            - new_custom_selectors: новые селекторы custom-полей {field: (sel, source)}
            - new_checkboxes: новые чекбоксы эвристики [(sel, source), ...]
            - found_submit: найденный fallback-селектор кнопки (sel, source) | None
        """
        logger.info("=== Начало заполнения полей формы ===")
        username = account_data.get("username", "")

        filled_fields: list[str] = []                        # реально заполненные поля
        skipped_fields: list[str] = []                       # пропущенные пользователем
        filled_from_outside: list[str] = []                  # заполнены из п.3/п.4 — не из профиля/шаблона
        new_custom_selectors: dict[str, tuple[str, str]] = {}  # новые селекторы custom-полей: {field: (sel, source)}
        new_checkboxes: list[tuple[str, str]] = []           # новые чекбоксы эвристики: [(sel, source), ...]
        found_submit: tuple[str, str] | None = None          # найденный fallback-селектор кнопки: (sel, source)

        # Загружаем ключевые слова
        common_fields = await self.template_manager.get_common_fields()
        agree_keywords = [k.lower() for k in common_fields.get("agree_keywords", [])]
        checkbox_skip_keywords = [k.lower() for k in common_fields.get("checkbox_skip_keywords", [])]
        one_time_field_keywords = [k.lower() for k in common_fields.get("one_time_field_keywords", [])]

        # Шаг 0: radio-кнопка регистрации
        # Должен выполняться до заполнения полей — на XenForo выбор radio
        # меняет доступность полей формы (username/email/password становятся активными)
        logger.info("Шаг 0: обработка radio-кнопки регистрации")
        register_radio_selector = selectors.get("register_radio")
        if not register_radio_selector:
            logger.debug("radio-кнопка регистрации не найдена в блоке — пропускаем Шаг 0")
        else:
            radio_source = selectors.get("register_radio_source", "manual")
            radio_clicked = False

            if radio_source == "template":
                # Значение из шаблона — кликаем без запроса
                try:
                    await self.browser.human_click(register_radio_selector)
                    await asyncio.sleep(0.5)
                    filled_fields.append("register_radio")
                    radio_clicked = True
                    logger.info(f"radio регистрации нажата (шаблон): {register_radio_selector}")
                except Exception as e:
                    logger.warning(f"Не удалось нажать radio регистрации ({register_radio_selector}): {e}")

            elif radio_source in ("common_fields", "manual"):
                # common_fields или manual — пробуем кликнуть
                try:
                    await self.browser.human_click(register_radio_selector)
                    await asyncio.sleep(0.5)
                    filled_fields.append("register_radio")
                    radio_clicked = True
                    logger.info(
                        f"radio регистрации нажата "
                        f"({'common_fields' if radio_source == 'common_fields' else 'ручной ввод'}): "
                        f"{register_radio_selector}"
                    )
                except Exception as e:
                    logger.warning(
                        f"Не удалось нажать radio регистрации ({register_radio_selector}): {e} "
                        f"— запрашиваем ручной ввод"
                    )
                    manual_confirm = await self._ask_manual_input(
                        field_name="register_radio",
                        selector_hint=register_radio_selector,
                        hint="Выберите radio-кнопку регистрации вручную в браузере и нажмите Enter",
                        display_text=selectors.get("register_radio_label", ""),
                    )
                    if manual_confirm:
                        # Оператор подтвердил выбор — фиксируем как заполненное
                        filled_fields.append("register_radio")
                        radio_clicked = True
                        logger.info(
                            f"radio регистрации успешно подтверждена оператором: "
                            f"{register_radio_selector}"
                        )
                    else:
                        logger.info("radio регистрации пропущена оператором — продолжаем")

            if not radio_clicked and radio_source != "template":
                logger.debug("radio регистрации не нажата — продолжаем заполнение")

        # Шаг 1: поля ввода
        logger.info("Шаг 1: заполнение полей ввода")
        standard_fields = {
            "username": account_data.get("username"),
            "email": account_data.get("email"),
            "confirm_email": account_data.get("email"),
            "password": account_data.get("password"),
            "confirm_password": account_data.get("password"),
        }

        for field_name, value in standard_fields.items():
            selector = selectors.get(field_name)
            if not selector:
                logger.debug(f"Селектор для '{field_name}' не найден — пропускаем")
                continue

            logger.info(f"Заполняем поле: {field_name}")
            selectors_list = selector if isinstance(selector, list) else [selector]
            field_filled = False
            used_selector = None

            # Накапливаем опции select если несколько селекторов
            last_select_options: list[str] = []
            not_visible_count: int = 0

            for sel in selectors_list:
                raw = await self._try_fill_element(sel, value, field_name)
                fill_status, sel_options = self._unpack_fill_result(raw)
                if sel_options:
                    last_select_options = sel_options
                if fill_status == "filled":
                    field_filled = True
                    used_selector = sel
                    break
                elif fill_status == "already_filled":
                    field_filled = True
                    logger.debug(f"Поле '{field_name}' уже заполнено — пропускаем")
                    break
                elif fill_status == "not_visible":
                    not_visible_count += 1
                    logger.debug(
                        f"Поле '{field_name}' скрыто/отключено ({sel}) — "
                        f"пробуем следующий селектор"
                    )
                    continue
                else:
                    logger.debug(
                        f"Поле '{field_name}' не найдено ({sel}) — "
                        f"пробуем следующий селектор"
                    )

            if not field_filled:
                # Все селекторы недоступны (disabled/скрыты) — молча пропускаем
                if not_visible_count == len(selectors_list):
                    logger.debug(
                        f"Поле '{field_name}' недоступно во всех селекторах — "
                        f"пропускаем без ручного ввода"
                    )
                    skipped_fields.append(field_name)
                    continue  # ← не переходим к ручному вводу
                # Иначе — селекторы не сработали по другой причине, запрашиваем ручной ввод
                logger.warning(
                    f"Не удалось заполнить поле '{field_name}' автоматически — "
                    f"запрашиваем ручной ввод"
                )
                manual_value = await self._ask_manual_input(
                    field_name=field_name,
                    selector_hint=selector if isinstance(selector, str) else selectors_list[0] if selectors_list else "",
                    hint=f"Введите значение для поля '{field_name}'",
                    display_text=selectors.get(f"{field_name}_label", ""),
                    options=last_select_options if last_select_options else None,
                )
                if manual_value:
                    for sel in selectors_list:
                        raw = await self._try_fill_element(sel, manual_value, field_name)
                        fill_status, _ = self._unpack_fill_result(raw)
                        if fill_status in ("filled", "already_filled"):
                            used_selector = sel
                            field_filled = True
                            break
                    if not field_filled:
                        # После ручного ввода поле всё равно не заполнено — пропускаем без повтора
                        logger.warning(f"Поле '{field_name}' не удалось заполнить после ручного ввода — пропускаем")
                        skipped_fields.append(field_name)
                else:
                    logger.info(f"Поле '{field_name}' пропущено оператором — продолжаем")
                    skipped_fields.append(field_name)

            if field_filled:
                filled_fields.append(field_name)

        # (продолжение шага 1) custom_fields
        profile_custom = account_data.get("custom_fields", {}) or {}

        for custom_field in selectors.get("custom_fields", []):
            if not isinstance(custom_field, dict):
                continue
            field_name = custom_field.get("name")
            sel = custom_field.get("selector")

            if not field_name or not sel:
                continue

            logger.info(f"Заполняем дополнительное поле: {field_name} ({sel})")

            # Одноразовые поля — всегда запрашиваем ручной ввод, никогда не сохраняем
            is_one_time = any(kw in field_name.lower() for kw in one_time_field_keywords)

            # ── чекбокс — клик без текстового ввода ──
            if custom_field.get("type") == "checkbox":
                try:
                    element = await self.page.query(sel, timeout=3, raise_exc=False)
                    if element is None:
                        logger.debug(f"Чекбокс '{field_name}' не найден в DOM — пропускаем")
                        skipped_fields.append(field_name)
                    else:
                        already_checked = element.get_attribute("checked") is not None
                        if already_checked:
                            logger.info(f"Чекбокс '{field_name}' уже отмечен — пропускаем клик")
                        else:
                            await self.browser.human_click(sel)
                            logger.info(f"Чекбокс '{field_name}' отмечен: {sel}")
                        filled_fields.append(field_name)  # элемент найден — успех
                        if not is_one_time:
                            source = custom_field.get("source", "manual")
                            new_checkboxes.append((sel, source))  # аккумулятор
                except Exception as e:
                    logger.warning(f"Не удалось обработать чекбокс '{field_name}' ({sel}): {e}")
                    skipped_fields.append(field_name)
                continue
            # ── конец ветки checkbox ──

            # Одноразовые поля — всегда ручной ввод, в профиль не сохраняем
            value = None if is_one_time else profile_custom.get(field_name)

            if is_one_time:
                logger.debug(f"Поле '{field_name}' одноразовое — запрашиваем ручной ввод")

            # п.2: [] = оператор намеренно пропускал — пропускаем молча
            elif isinstance(value, list) and len(value) == 0:
                logger.debug(f"Поле '{field_name}' пропускалось ранее — пропускаем молча")
                skipped_fields.append(field_name)
                continue

            # п.2: есть варианты в профиле — перебираем по порядку
            if value and not is_one_time:
                values_to_try = value if isinstance(value, list) else [value]
                fill_status: str = "not_found"
                select_options: list[str] = []

                for v in values_to_try:
                    logger.debug(f"Пробуем вариант '{v}' для '{field_name}' из профиля")
                    raw = await self._try_fill_element(sel, v, field_name)
                    fs, sel_opts = self._unpack_fill_result(raw)
                    if sel_opts:
                        select_options.extend(
                            opt for opt in sel_opts if opt not in select_options
                        )
                    if fs == "filled":
                        fill_status = "filled"
                        filled_fields.append(field_name)
                        break  # п.2: нашли вариант — НЕ трогаем account_data
                    fill_status = fs

                if fill_status != "filled":
                    # п.2 → п.4: все варианты не подошли — ручной ввод
                    logger.warning(
                        f"Все варианты профиля не подошли для '{field_name}' — "
                        f"запрашиваем ручной ввод"
                    )
                    manual_value = await self._ask_manual_input(
                        field_name=field_name,
                        selector_hint=sel,
                        hint=f"Выберите значение для поля '{field_name}'",
                        display_text=custom_field.get("display_text", ""),
                        options=select_options if select_options else None,
                    )
                    if manual_value:
                        raw2 = await self._try_fill_element(sel, manual_value, field_name)
                        fill_status2, _ = self._unpack_fill_result(raw2)
                        if fill_status2 == "filled":
                            filled_fields.append(field_name)
                            filled_from_outside.append(field_name)  # п.4
                            existing = account_data.get("custom_fields", {}).get(field_name)
                            new_list = list(existing) if isinstance(existing, list) else []
                            if manual_value not in new_list:
                                new_list.append(manual_value)
                            account_data.setdefault("custom_fields", {})[field_name] = new_list
                            if not is_one_time:
                                new_custom_selectors[field_name] = (sel, "manual")  # аккумулятор

            else:
                # п.3: автоопределение через known_field_types
                known_field_map: dict[str, str | None] = {
                    "city":      account_data.get("city"),
                    "country":   account_data.get("country"),
                    "gender":    account_data.get("gender"),
                    "firstname": account_data.get("firstname"),
                    "lastname":  account_data.get("lastname"),
                    "phone":     account_data.get("phone"),
                    "website":   account_data.get("website"),
                    "timezone":  account_data.get("timezone"),
                    "birthdate": account_data.get("birthdate"),
                }
                auto_value = known_field_map.get(field_name)

                if auto_value and not is_one_time:
                    logger.debug(f"Автоопределение '{field_name}' = '{auto_value}'")
                    raw = await self._try_fill_element(sel, auto_value, field_name)
                    fs, _ = self._unpack_fill_result(raw)
                    if fs == "filled":
                        filled_fields.append(field_name)
                        filled_from_outside.append(field_name)  # п.3
                        account_data.setdefault("custom_fields", {})[field_name] = [auto_value]
                        if not is_one_time:
                            new_custom_selectors[field_name] = (sel, "auto")  # аккумулятор
                        continue

                # п.4: ручной ввод
                logger.warning(
                    f"Значение для '{field_name}' не найдено — запрашиваем ручной ввод"
                )
                manual_value = await self._ask_manual_input(
                    field_name=field_name,
                    selector_hint=sel,
                    hint=f"Введите значение для поля '{field_name}'",
                    display_text=custom_field.get("display_text", ""),
                )
                if manual_value:
                    result = await self._try_fill_element(sel, manual_value, field_name)
                    if result == "filled":
                        filled_fields.append(field_name)
                        filled_from_outside.append(field_name)  # п.4
                        account_data.setdefault("custom_fields", {})[field_name] = [manual_value]
                        if not is_one_time:
                            new_custom_selectors[field_name] = (sel, "manual")  # аккумулятор
                else:
                    # п.4: пропуск → [] в память
                    logger.info(f"Доп. поле '{field_name}' пропущено оператором — продолжаем")
                    skipped_fields.append(field_name)
                    if not is_one_time:
                        account_data.setdefault("custom_fields", {})[field_name] = []

        # Шаг 2: чекбокс согласия из блока
        logger.info("Шаг 2: обработка чекбокса согласия")
        agree_selector = selectors.get("agree_checkbox")
        if agree_selector:
            try:
                element = await self.page.query(agree_selector, timeout=3, raise_exc=False)
                if element is None:
                    logger.warning(f"Чекбокс согласия '{agree_selector}' не найден в DOM — пропускаем")
                    skipped_fields.append("agree_checkbox")
                else:
                    # Проверяем что элемент является полем ввода а не кнопкой
                    _input_types = {
                        "checkbox", "radio", "text", "email", "password",
                        "number", "tel", "url", "date", "time", "file",
                        "range", "color", "search"
                    }
                    el_type = (element.get_attribute("type") or "text").lower()
                    if el_type not in _input_types:
                        logger.debug(
                            f"Элемент agree_checkbox имеет type='{el_type}' — "
                            f"это кнопка, пропускаем шаг 2"
                        )
                    else:
                        already_checked = element.get_attribute("checked") is not None
                        if already_checked:
                            logger.info(f"Чекбокс согласия уже отмечен ({agree_selector}) — пропускаем клик")
                        else:
                            await self.browser.human_click(agree_selector)
                            logger.info(f"Чекбокс согласия отмечен: {agree_selector}")
                        filled_fields.append("agree_checkbox")
                    # Сохранение через _save_block_to_template при успехе регистрации —
                    # agree_checkbox в STANDARD_KEYS, source проверяется автоматически
            except Exception as e:
                logger.warning(f"Не удалось обработать чекбокс согласия ({agree_selector}): {e}")
                skipped_fields.append("agree_checkbox")
        else:
            logger.debug("Чекбокс согласия в блоке не найден — пропускаем")

        # Шаг 3: капча
        logger.info("Шаг 3: обработка капчи")
        captcha_ok = await self._handle_captcha(selectors)
        if not captcha_ok:
            logger.warning("Капча не решена — форум в бад")
            return {
                "ok":                   False,
                "reason":               "captcha_timeout",
                "filled":               filled_fields,
                "skipped":              skipped_fields,
                "filled_from_outside":  filled_from_outside,
                "new_custom_selectors": new_custom_selectors,
                "new_checkboxes":       new_checkboxes,
                "found_submit":         found_submit,
            }

        # Шаг 4: кнопка submit
        logger.info("Шаг 4: нажатие кнопки submit")
        submit_ok, found_submit = await self._handle_submit(selectors, form_selector)
        if not submit_ok:
            logger.warning("Кнопка submit не нажата — форум в бад")
            return {
                "ok":                   False,
                "reason":               "submit_failed",
                "filled":               filled_fields,
                "skipped":              skipped_fields,
                "filled_from_outside":  filled_from_outside,
                "new_custom_selectors": new_custom_selectors,
                "new_checkboxes":       new_checkboxes,
                "found_submit":         found_submit,
            }

        logger.info(
            f"=== Заполнение полей завершено: "
            f"заполнено={filled_fields}, пропущено={skipped_fields} ==="
        )
        return {
            "ok":                   True,
            "filled":               filled_fields,
            "skipped":              skipped_fields,
            "filled_from_outside":  filled_from_outside,
            "new_custom_selectors": new_custom_selectors,
            "new_checkboxes":       new_checkboxes,
            "found_submit":         found_submit,
        }
        
        
    def _unpack_fill_result(
        self,
        result: str | tuple[str, list[str]],
    ) -> tuple[str, list[str]]:
        """Распаковывает результат _try_fill_element в единый формат.

        Args:
            result: Строка статуса или кортеж (статус, список_опций).

        Returns:
            Кортеж (статус, список_опций). Список пуст если поле не select.
        """
        if isinstance(result, tuple):
            return result
        return result, []

    async def _try_fill_element(
        self, selector: str, value: str, field_name: str
    ) -> str | tuple[str, list[str]]:
        """Пробует заполнить элемент формы по селектору.

        Для <select> ищет опцию по частичному совпадению текста.
        Если опция не найдена — возвращает ("not_found", [список_опций])
        чтобы вызывающий код мог показать варианты пользователю.

        Args:
            selector: CSS селектор элемента.
            value: Значение для заполнения.
            field_name: Имя поля (для логирования).

        Returns:
            "filled" — успешно заполнено,
            "already_filled" — поле уже заполнено,
            "not_visible" — элемент невидим,
            "not_found" — элемент не найден или ошибка,
            ("not_found", options) — select: значение не найдено в списке опций.
        """
        try:
            element = await self.page.query(selector, timeout=3, raise_exc=False)
            if not element:
                logger.debug(f"Элемент не найден: {selector}")
                return "not_found"

            # Определяем тип элемента
            tag = (element.get_attribute("tagName") or "").lower()
            current_val = (element.get_attribute("value") or "").strip()

            # Проверка видимости и доступности через JS —
            # покрывает все способы скрытия: disabled, aria-disabled,
            # display:none, visibility:hidden, opacity:0, pointer-events:none,
            # скрытие через родителя или CSS-класс (getComputedStyle)
            sel_js = json.dumps(selector)
            try:
                vis_response = await self.page.execute_script(f"""
                    (function() {{
                        var el = document.querySelector({sel_js});
                        if (!el) return JSON.stringify({{visible: false, reason: 'not_found'}});

                        // el.disabled учитывает и атрибут и состояние DOM
                        if (el.disabled)
                            return JSON.stringify({{visible: false, reason: 'disabled'}});

                        if ((el.getAttribute('aria-disabled') || '').toLowerCase() === 'true')
                            return JSON.stringify({{visible: false, reason: 'aria_disabled'}});

                        // getComputedStyle — ловит скрытие через любой CSS
                        // включая классы и родителей
                        var style = window.getComputedStyle(el);
                        if (style.display === 'none')
                            return JSON.stringify({{visible: false, reason: 'display_none'}});
                        if (style.visibility === 'hidden')
                            return JSON.stringify({{visible: false, reason: 'visibility_hidden'}});
                        if (style.opacity === '0')
                            return JSON.stringify({{visible: false, reason: 'opacity_zero'}});
                        if (style.pointerEvents === 'none')
                            return JSON.stringify({{visible: false, reason: 'pointer_events_none'}});

                        // getBoundingClientRect — ловит скрытие через нулевые размеры
                        var rect = el.getBoundingClientRect();
                        if (rect.width === 0 && rect.height === 0)
                            return JSON.stringify({{visible: false, reason: 'zero_size'}});

                        return JSON.stringify({{visible: true, reason: 'ok'}});
                    }})()
                """)
                vis_raw: str = (
                    vis_response.get("result", {}).get("result", {}).get("value") or ""
                )
                if not vis_raw:
                    logger.warning(
                        f"Пустой ответ проверки видимости '{field_name}' "
                        f"({selector}) — считаем недоступным"
                    )
                    return "not_visible"
                try:
                    vis_data: dict = json.loads(vis_raw)
                except (json.JSONDecodeError, TypeError) as json_err:
                    logger.warning(
                        f"Невалидный JSON от проверки видимости '{field_name}' "
                        f"({selector}): {json_err} — считаем недоступным"
                    )
                    return "not_visible"
                is_visible: bool = vis_data.get("visible", False)
                vis_reason: str = vis_data.get("reason", "unknown")
                if not is_visible:
                    logger.debug(
                        f"Поле '{field_name}' недоступно ({vis_reason}) — "
                        f"пропускаем: {selector}"
                    )
                    return "not_visible"

                logger.debug(
                    f"Поле '{field_name}' доступно ({vis_reason}) — "
                    f"продолжаем заполнение: {selector}"
                )

            except Exception as vis_err:
                # Если JS-проверка упала — логируем и продолжаем
                # чтобы не блокировать заполнение из-за ошибки диагностики
                logger.debug(
                    f"Не удалось проверить видимость '{field_name}' "
                    f"({selector}): {vis_err} — продолжаем"
                )

            # --- Обработка <select> ---
            if tag == "select":
                return await self._try_fill_select(element, selector, value, field_name)

            # Защита от перезаполнения (get_attribute — синхронный)
            if current_val and len(current_val) > 3:
                logger.debug(f"Поле '{field_name}' уже содержит значение — пропускаем")
                return "already_filled"

            await self.browser.human_type(selector, value)

            logger.info(f"Поле '{field_name}' успешно заполнено: {selector}")
            return "filled"

        except Exception as e:
            err = str(e).lower()
            if "visible" in err or "notvisible" in err:
                logger.debug(f"Поле '{field_name}' невидимо: {selector}")
                return "not_visible"
            logger.warning(f"Ошибка заполнения '{field_name}' ({selector}): {e}")
            return "not_found"
        
    async def _try_fill_select(
        self,
        element: Any,
        selector: str,
        value: str,
        field_name: str,
    ) -> str | tuple[str, list[str]]:
        """Выбирает опцию в <select> по частичному совпадению текста.

        Args:
            element: Найденный DOM-элемент.
            selector: CSS селектор элемента <select>.
            value: Искомое значение (частичное совпадение с текстом опции).
            field_name: Имя поля (для логирования).

        Returns:
            "filled" — опция выбрана,
            "not_visible" — элемент отключён,
            ("not_found", options) — совпадение не найдено или список пуст,
                                     options — список доступных вариантов.
        """
        import json as _json

        # Проверка disabled
        if element.get_attribute("disabled") is not None:
            logger.debug(f"Select '{field_name}' отключён ({selector})")
            return "not_visible"

        try:
            selector_js = _json.dumps(selector)

            # Получаем все опции через JS
            response = await self.page.execute_script(f"""
                var el = document.querySelector({selector_js});
                if (!el) return null;
                return Array.from(el.options).map(function(o) {{
                    return {{value: o.value, text: o.text.trim()}};
                }});
            """)
            options_raw = (
                response.get("result", {}).get("result", {}).get("value") or []
            )
            options: list[dict] = options_raw if isinstance(options_raw, list) else []
            option_texts: list[str] = [o["text"] for o in options if o.get("text")]

            # Пустой список — элемент возможно ещё не загружен
            if not option_texts:
                logger.warning(
                    f"Select '{field_name}' не содержит опций — "
                    f"возможно ещё не загружен ({selector})"
                )
                return "not_found", []

            # Ищем частичное совпадение (регистр игнорируем)
            value_lower = value.lower()
            matched = next(
                (o for o in options if value_lower in o.get("text", "").lower()),
                None,
            )

            if not matched:
                logger.warning(
                    f"Поле '{field_name}': значение '{value}' не найдено "
                    f"в списке опций ({selector})"
                )
                return "not_found", option_texts

            # Выбираем опцию через JS и отправляем событие change
            opt_value_js = _json.dumps(matched["value"])
            await self.page.execute_script(f"""
                var el = document.querySelector({selector_js});
                el.value = {opt_value_js};
                el.dispatchEvent(new Event('change', {{bubbles: true}}));
            """)
            logger.info(
                f"Поле '{field_name}' выбрана опция: "
                f"'{matched['text']}' ({selector})"
            )
            return "filled"

        except Exception as e:
            logger.warning(
                f"Ошибка заполнения select '{field_name}' ({selector}): {e}"
            )
            return "not_found", []

    async def _handle_checkboxes(
        self,
        selectors: dict,
        template: dict | None,
        agree_keywords: list[str],
        checkbox_skip_keywords: list[str],
    ) -> list[tuple[str, str]]:
        """Ставит галочки согласия из шаблона и эвристики.

        Порядок:
        1. Галочки из шаблона agree_step.checkboxes — source='template'
        2. Эвристика — ищет чекбоксы по ключевым словам внутри формы — source='heuristic'
        3. Если шаблона нет — ставим все подходящие чекбоксы на странице

        Args:
            selectors: Словарь селекторов.
            template: Текущий шаблон.
            agree_keywords: Ключевые слова согласия.
            checkbox_skip_keywords: Ключевые слова для пропуска.

        Returns:
            Список кортежей (selector, source) для всех успешно нажатых чекбоксов.
            source='template' — галочка из шаблона, source='heuristic' — найдена эвристикой.
        """
        checked_selectors: list[str] = []
        new_checkboxes: list[tuple[str, str]] = []

        # Этап 1: галочки из шаблона
        agree_step = (template or {}).get("agree_step", {}) or {}
        template_checkboxes = agree_step.get("checkboxes") or []

        for cb_selector in template_checkboxes:
            try:
                await self.browser.human_click(cb_selector)
                logger.info(f"Галочка из шаблона поставлена: {cb_selector}")
                checked_selectors.append(cb_selector)
                new_checkboxes.append((cb_selector, "template"))
            except Exception as e:
                logger.warning(f"Не удалось поставить галочку из шаблона '{cb_selector}': {e}")

        # Этап 2: эвристика — ищем чекбоксы по ключевым словам
        logger.debug("Поиск дополнительных галочек эвристикой")
        _raw_fs = (template or {}).get("registration_page", {}).get("form_selector")
        form_selector = (_raw_fs[0] if _raw_fs else None) if isinstance(_raw_fs, list) else _raw_fs
        all_checkboxes: list = []

        try:
            search_root = None
            if form_selector:
                form = await self.page.query(form_selector, timeout=3, raise_exc=False)
                if form:
                    search_root = form
                    logger.debug(f"Поиск чекбоксов внутри формы: {form_selector}")

            if search_root:
                all_checkboxes = await search_root.query(
                    "input[type='checkbox']", find_all=True
                ) or []

            if not all_checkboxes:
                logger.debug("Чекбоксы внутри формы не найдены — ищем по всей странице")
                all_checkboxes = await self.page.query(
                    "input[type='checkbox']", find_all=True
                ) or []
        except Exception as e:
            logger.warning(f"Ошибка поиска чекбоксов: {e}")

        for cb in all_checkboxes:
            try:
                name = (cb.get_attribute("name") or "").lower()
                cb_id = (cb.get_attribute("id") or "").lower()
                value = (cb.get_attribute("value") or "").lower()
                combined = f"{name} {cb_id} {value}"

                if any(skip in combined for skip in checkbox_skip_keywords):
                    logger.debug(f"Пропускаем нежелательный чекбокс: {name or cb_id}")
                    continue

                cb_selector = await self._generate_css_selector(cb)
                if cb_selector in checked_selectors:
                    continue

                should_check = (
                    any(kw in combined for kw in agree_keywords)
                    or not template
                )

                if should_check:
                    try:
                        await self.browser.human_click(cb_selector)
                        logger.info(f"Галочка поставлена эвристикой: {cb_selector}")
                        checked_selectors.append(cb_selector)
                        new_checkboxes.append((cb_selector, "heuristic"))
                    except Exception as e:
                        logger.warning(f"Не удалось поставить галочку {cb_selector}: {e}")

            except Exception as e:
                logger.debug(f"Ошибка обработки чекбокса: {e}")

        # Этап 3: ручной режим — только если чекбоксы найдены но не нажались
        self._last_checkbox_failed = False
        if all_checkboxes and not checked_selectors:
            logger.warning("Чекбоксы найдены, но нажать не удалось — запрашиваем ручной ввод")
            print("\n" + "=" * 60)
            print("ТРЕБУЕТСЯ РУЧНОЙ ВВОД: ГАЛОЧКИ СОГЛАСИЯ")
            print("Поставьте галочки вручную в браузере, затем нажмите Enter.")
            print("Нажмите Enter без действий — форум будет помечен как неудача.")
            print("-" * 60)
            print("Найденные чекбоксы:")
            for cb in all_checkboxes:
                try:
                    cb_name = (cb.get_attribute("name") or "").strip()
                    cb_id = (cb.get_attribute("id") or "").strip()
                    cb_selector = await self._generate_css_selector(cb)
                    label = cb_name or cb_id or "?"
                    print(f"  Поле:     {label}")
                    print(f"  Селектор: {cb_selector}")
                except Exception:
                    pass
            print("=" * 60)
            try:
                confirm = await asyncio.wait_for(
                    asyncio.get_running_loop().run_in_executor(None, input, ">>> "),
                    timeout=self.config.get("MANUAL_FIELD_FILL_TIMEOUT", 120)
                )
                if not (confirm or "").strip():
                    logger.warning("Ручной ввод галочек пропущен — неудача")
                    self._last_checkbox_failed = True
                else:
                    logger.info("Ручной ввод галочек: пользователь подтвердил")
            except asyncio.TimeoutError:
                logger.warning("Таймаут ручного ввода галочек — неудача")
                self._last_checkbox_failed = True
        elif not all_checkboxes:
            logger.debug("Чекбоксы на странице не найдены — пропускаем")

        return new_checkboxes
    
    async def _confirm_test_mode(
        self,
        step_num: int,
        success: bool,
        error_reason: str | None,
    ) -> bool | None:
        """Запрашивает подтверждение результата в режиме тестирования.

        Вызывается только при финальном успехе или финальной ошибке регистрации.
        В промежуточных шагах (смена страницы, перебор блоков) не используется.

        Args:
            step_num: Номер текущего шага.
            success: Текущий результат.
            error_reason: Причина ошибки или None.

        Returns:
            True — пользователь подтвердил успех,
            False — пользователь отклонил,
            None — режим тестирования выключен или таймаут.
        """
        test_mode = self.config.get("TEST_MODE", False)
        if not test_mode:
            return None

        print("\n" + "=" * 60)
        print(f"РЕЖИМ ТЕСТИРОВАНИЯ — шаг {step_num}")
        print(f"Результат: {'УСПЕХ' if success else 'ОШИБКА: ' + str(error_reason)}")
        print("  + (успех) — регистрация прошла успешно")
        print("  - (неудача) — регистрация не удалась")
        print("=" * 60)
        try:
            confirm = await asyncio.wait_for(
                asyncio.get_running_loop().run_in_executor(None, input, ">>> "),
                timeout=self.config.get("MANUAL_FIELD_FILL_TIMEOUT", 120)
            )
            confirm = (confirm or "").strip()
            if confirm == "+":
                logger.info("Тест: пользователь подтвердил успех")
                return True
            else:
                logger.info("Тест: пользователь отклонил результат")
                return False
        except asyncio.TimeoutError:
            logger.warning("Тест: таймаут подтверждения — оставляем исходный результат")
            return None

    async def _ask_manual_input(
        self,
        field_name: str,
        selector_hint: str,
        hint: str,
        display_text: str = "",
        options: list[str] | None = None,
    ) -> str:
        """Запрашивает ручной ввод значения поля в консоли.

        Args:
            field_name: Системное имя поля.
            selector_hint: Селектор поля (для отображения пользователю).
            hint: Подсказка для пользователя.
            display_text: Видимое название поля на странице (из label).
            options: Список доступных вариантов для <select> полей.
                     Если пустой список — варианты недоступны (не загружены).

        Returns:
            Введённое значение или пустую строку если пользователь нажал Enter.
        """
        timeout = self.config.get("MANUAL_FIELD_FILL_TIMEOUT", 120)
        print("\n" + "=" * 60)
        print("ТРЕБУЕТСЯ РУЧНОЙ ВВОД")
        if display_text:
            print(f"Поле:     {field_name} — «{display_text}»")
            print(f"Селектор: {selector_hint}")
        else:
            print(f"Поле:     {field_name} ({selector_hint})")
        print(f"Задание:  {hint}")
        if options is None:
            pass  # обычное текстовое поле — варианты не нужны
        elif options:
            print("Доступные варианты:")
            for opt in options:
                print(f"  {opt}")
        else:
            print("Список вариантов недоступен — посмотрите в браузер.")
        print("Нажмите Enter без ввода чтобы пропустить поле.")
        print("=" * 60)
        try:
            value = await asyncio.wait_for(
                asyncio.get_running_loop().run_in_executor(None, input, ">>> "),
                timeout=timeout,
            )
            return (value or "").strip()
        except asyncio.TimeoutError:
            logger.warning(f"Таймаут ручного ввода поля '{field_name}'")
            return ""

    async def _handle_captcha(self, selectors: dict) -> bool:
        """Обрабатывает капчу.

        Сценарии:
            1. Капча не найдена → продолжаем
            2. Invisible капча → пауза 3с → продолжаем
            3. Есть API-провайдер + site_key → решаем через API → продолжаем
            4. Нет провайдера / API упал → ручной ввод в консоль → продолжаем

        Returns:
            True всегда — стандартные проверки после submit сами определят результат.
        """
        captcha_info = selectors.get("captcha_indicator")
        if not captcha_info:
            logger.info("Капча не обнаружена — продолжаем")
            return True

        # Совместимость: captcha_indicator может быть строкой (старый формат)
        if isinstance(captcha_info, str):
            captcha_info = {
                "selector": captcha_info,
                "captcha_type": "recaptcha_v2",
                "site_key": None,
                "invisible": False,
            }

        captcha_type = captcha_info.get("captcha_type", "recaptcha_v2")
        site_key = captcha_info.get("site_key")
        invisible = captcha_info.get("invisible", False)

        # Сценарий 1: invisible капча — ждём 3с и идём дальше
        if invisible:
            logger.info("Invisible капча — ожидаем 3с и продолжаем")
            await asyncio.sleep(3)
            return True

        logger.info(
            f"Капча обнаружена: тип={captcha_type}, "
            f"site_key={'есть' if site_key else 'нет'}"
        )
        timeout = self.config.get("manual_captcha_timeout", 300)

        # Сценарий 2: API-провайдер
        if self.captcha_helper and site_key:
            try:
                page_url = await self.page.current_url
                token = await self.captcha_helper.solve_captcha(
                    captcha_type=captcha_type,
                    site_key=site_key,
                    page_url=page_url,
                )
                if token:
                    logger.info("Капча решена автоматически через API")
                    return True
                logger.warning("Captcha API не справился — переходим в ручной режим")
            except Exception as e:
                logger.warning(f"Ошибка API-решения капчи: {e} — переходим в ручной режим")

        # Сценарий 3: ручной ввод
        print("\n" + "=" * 60)
        print("ТРЕБУЕТСЯ РУЧНОЙ ВВОД: КАПЧА")
        print("Решите капчу в браузере, затем нажмите Enter.")
        print("=" * 60)
        try:
            await asyncio.wait_for(
                asyncio.get_running_loop().run_in_executor(None, input, ">>> "),
                timeout=timeout,
            )
            logger.info("Капча подтверждена оператором — продолжаем")
        except asyncio.TimeoutError:
            logger.warning(f"Таймаут ожидания капчи ({timeout}с) — продолжаем")
        return True
    
    async def _handle_submit(
        self,
        selectors: dict,
        form_selector: str | None,
    ) -> tuple[bool, tuple[str, str] | None]:
        """Нажимает кнопку submit. Ручной режим если авто не сработало.

        Returns:
            Кортеж (ok, found_submit):
            ok — True если кнопка нажата (авто или вручную), False если таймаут/пропуск.
            found_submit — (selector, source) если найден новый селектор кнопки,
                           None если использован селектор из шаблона или ручной режим.
        """
        submit_selector_raw = selectors.get("submit_button")
        if not submit_selector_raw:
            found_selector = await self._submit_form(selectors, form_selector)
            if found_selector is None:
                logger.warning("_submit_form не нашёл кнопку — неудача")
                return False, None
            return True, (found_selector, "heuristic")

        submit_list = (
            submit_selector_raw
            if isinstance(submit_selector_raw, list)
            else [submit_selector_raw]
        )
        for sel in submit_list:
            if not sel:
                continue
            try:
                await asyncio.wait_for(self.browser.human_click(sel), timeout=4.0)
                logger.info(f"Кнопка submit нажата: {sel}")
                return True, None
            except Exception as e:
                exc_type = type(e).__name__
                exc_msg = str(e) or "(пустое)"
                logger.debug(
                    f"Кнопка submit недоступна ({sel}): "
                    f"type={exc_type}, message={exc_msg}"
                )

        logger.warning("Не удалось нажать кнопку submit авто — запрашиваем ручной ввод")
        submit_label = selectors.get("submit_button_label") or ""
        submit_sel = submit_list[0] if submit_list else ""
        print("\n" + "=" * 60)
        print("ТРЕБУЕТСЯ РУЧНОЙ ВВОД: КНОПКА ПОДТВЕРЖДЕНИЯ")
        if submit_label:
            print(f"Кнопка:   «{submit_label}»")
        print(f"Селектор: {submit_sel}")
        print("Нажмите кнопку вручную в браузере, затем нажмите Enter.")
        print("Нажмите Enter без действий — форум будет помечен как неудача.")
        print("=" * 60)
        try:
            confirm = await asyncio.wait_for(
                asyncio.get_running_loop().run_in_executor(None, input, ">>> "),
                timeout=self.config.get("MANUAL_FIELD_FILL_TIMEOUT", 120)
            )
            if (confirm or "").strip():
                logger.info("Ручное нажатие кнопки: пользователь подтвердил")
                return True, None
            logger.warning("Ручное нажатие кнопки пропущено — неудача")
            return False, None
        except asyncio.TimeoutError:
            logger.warning("Таймаут ручного нажатия кнопки — неудача")
            return False, None
            
    async def _submit_form(
        self,
        selectors: dict,
        form_selector: str | None,
    ) -> str | None:
        """Нажимает кнопку подтверждения формы регистрации.

        Порядок поиска кнопки:
        1. Селектор из шаблона/эвристики (список вариантов)
        2. Поиск по тексту кнопки внутри формы (submit_keywords)
        3. Поиск по типу кнопки внутри формы
        4. Поиск по всей странице
        5. Ручной режим

        Args:
            selectors: Словарь селекторов полей.
            form_selector: Селектор формы.

        Returns:
            CSS-селектор нажатой кнопки если найдена эвристикой, None в остальных случаях.
        """
        logger.info("=== Отправка формы ===")

        common_fields = await self.template_manager.get_common_fields()
        submit_keywords = [k.lower() for k in common_fields.get("submit_keywords", [])]

        submit_selector_raw = selectors.get("submit_button")
        selectors_list = (
            submit_selector_raw if isinstance(submit_selector_raw, list)
            else [submit_selector_raw] if submit_selector_raw
            else []
        )

        for sel in selectors_list:
            if not sel:
                continue
            try:
                logger.debug(f"Пробуем кнопку из селектора: {sel}")
                await asyncio.wait_for(
                    self.browser.human_click(sel),
                    timeout=4.0
                )
                logger.info(f"Форма отправлена кнопкой из селектора: {sel}")
                return None
            except asyncio.TimeoutError:
                logger.debug(f"Таймаут кнопки: {sel}")
            except Exception as e:
                logger.debug(f"Кнопка недоступна ({sel}): {e}")

        found_selector = await self._find_submit_in_form(
            form_selector=form_selector,
            submit_keywords=submit_keywords,
        )

        if not found_selector:
            logger.debug("Кнопка внутри формы не найдена — ищем по всей странице")
            found_selector = await self._find_submit_on_page(
                submit_keywords=submit_keywords,
            )

        if found_selector:
            try:
                await self.browser.human_click(found_selector)
                logger.info(f"Форма отправлена найденной кнопкой: {found_selector}")
                return found_selector
            except Exception as e:
                logger.warning(
                    f"Кнопка найдена ({found_selector}), "
                    f"но нажать не удалось: {e} — переходим в ручной режим"
                )

        logger.warning("Кнопка не найдена ни в форме ни на странице — ручной режим")
        print("\n" + "=" * 60)
        print("НЕ УДАЛОСЬ НАЙТИ КНОПКУ ПОДТВЕРЖДЕНИЯ")
        print("Нажмите кнопку вручную в браузере, затем нажмите Enter.")
        print("Нажмите Enter без действий — форум будет помечен как неудача.")
        print("=" * 60)
        try:
            confirm = await asyncio.wait_for(
                asyncio.get_running_loop().run_in_executor(None, input, ">>> "),
                timeout=self.config.get("MANUAL_FIELD_FILL_TIMEOUT", 120)
            )
            if not (confirm or "").strip():
                logger.warning("Ручное подтверждение формы пропущено — неудача")
                return None
            logger.info("Ручное подтверждение: пользователь нажал Enter")
            return None
        except asyncio.TimeoutError:
            logger.error("Таймаут ручного подтверждения формы")
            return None
    
    async def _find_button_in_context(
        self,
        context_selector: str,
        submit_keywords: list[str],
    ) -> str | None:
        """Ищет кнопку подтверждения внутри указанного DOM-контекста через JS.

        Единственный JS-вызов атомарно выполняет:
        - Фильтрацию невидимых кнопок через getComputedStyle
        - Поиск по тексту / value / label (приоритет)
        - Fallback на первую видимую кнопку с надёжным селектором

        Уровни надёжности селектора (по убыванию):
        1. #id               — абсолютно точный
        2. tag[name="..."]   — почти всегда уникален в форме
        3. tag[type="submit"] — уточняется nth-of-type если таких несколько
        4. button:not([type]) — только если единственная такая в контексте
        5. null              — надёжного селектора нет, пропускаем кнопку

        Args:
            context_selector: CSS-селектор родительского блока или формы.
            submit_keywords: Ключевые слова для поиска по тексту кнопки.

        Returns:
            CSS-селектор кнопки или None если не найдена.
        """
        if not context_selector:
            return None

        BTN_QUERY = (
            "input[type='submit'], button[type='submit'], "
            "input[type='button'], button:not([type])"
        )

        try:
            response = await self.page.execute_script(f"""
                (function() {{
                    var ctx = document.querySelector({context_selector!r});
                    if (!ctx) return null;

                    var buttons = Array.from(ctx.querySelectorAll({BTN_QUERY!r}));
                    if (!buttons.length) return null;

                    var keywords = {submit_keywords!r};

                    function isVisible(btn) {{
                        var style = window.getComputedStyle(btn);
                        return (
                            style.display !== 'none' &&
                            style.visibility !== 'hidden' &&
                            style.opacity !== '0'
                        );
                    }}

                    function getSelector(btn) {{
                        // Уровень 1: id — абсолютно точный
                        if (btn.id) return '#' + btn.id;

                        // Уровень 2: name — почти всегда уникален в форме
                        if (btn.name) {{
                            return btn.tagName.toLowerCase() + '[name="' + btn.name + '"]';
                        }}

                        var tag = btn.tagName.toLowerCase();

                        // Уровень 3: type="submit" — уточняем позицией если таких несколько
                        if (btn.type === 'submit') {{
                            var submitBtns = Array.from(
                                ctx.querySelectorAll(tag + '[type="submit"]')
                            );
                            if (submitBtns.length === 1) {{
                                return tag + '[type="submit"]';
                            }}
                            var idx = submitBtns.indexOf(btn);
                            if (idx >= 0) {{
                                return tag + '[type="submit"]:nth-of-type(' + (idx + 1) + ')';
                            }}
                        }}

                        // Уровень 4: button без type — только если единственная в контексте
                        if (tag === 'button') {{
                            var plainBtns = Array.from(
                                ctx.querySelectorAll('button:not([type])')
                            );
                            if (plainBtns.length === 1) return 'button:not([type])';
                        }}

                        // Надёжного селектора нет — пропускаем
                        return null;
                    }}

                    // Шаг 1: поиск по тексту/value/label среди видимых кнопок
                    for (var i = 0; i < buttons.length; i++) {{
                        var btn = buttons[i];
                        if (!isVisible(btn)) continue;
                        var sel = getSelector(btn);
                        if (!sel) continue;

                        var text = (btn.innerText || btn.value || '').toLowerCase().trim();
                        var label = '';
                        if (btn.id) {{
                            var lbl = document.querySelector('label[for="' + btn.id + '"]');
                            if (lbl) label = (lbl.innerText || '').toLowerCase().trim();
                        }}
                        var combined = text + ' ' + label;
                        if (keywords.some(function(kw) {{ return combined.includes(kw); }})) {{
                            return {{selector: sel, found_by: 'text', display: text || label}};
                        }}
                    }}

                    // Шаг 2: fallback — первая видимая кнопка с надёжным селектором
                    for (var i = 0; i < buttons.length; i++) {{
                        var btn = buttons[i];
                        if (!isVisible(btn)) continue;
                        var sel = getSelector(btn);
                        if (sel) {{
                            var display = (btn.innerText || btn.value || '').trim();
                            return {{selector: sel, found_by: 'type', display: display}};
                        }}
                    }}

                    return null;
                }})()
            """)

            result = response.get("result", {}).get("result", {}).get("value")
            if not result:
                logger.debug(
                    f"Кнопка не найдена или нет надёжного селектора "
                    f"в контексте {context_selector!r}"
                )
                return None

            selector = result.get("selector")
            found_by = result.get("found_by", "unknown")
            display = result.get("display", "")

            # Уточняем контекстом чтобы не найти одноимённый элемент вне блока
            full_selector = f"{context_selector} {selector}"

            logger.debug(
                f"Кнопка найдена [{found_by}] текст={display!r} "
                f"селектор={full_selector!r}"
            )
            return full_selector

        except Exception as e:
            logger.debug(
                f"Ошибка поиска кнопки в контексте {context_selector!r}: {e}"
            )

        return None
    
    async def _find_submit_in_form(
        self,
        form_selector: str | None,
        submit_keywords: list[str],
    ) -> str | None:
        """Ищет кнопку подтверждения внутри блока формы.

        Порядок поиска:
        1. Внутри block_selector (div/form указанный в шаблоне)
        2. Если не найдено — поднимается к ближайшей родительской <form>

        Args:
            form_selector: CSS-селектор блока или формы.
            submit_keywords: Ключевые слова для поиска по тексту кнопки.

        Returns:
            CSS-селектор найденной кнопки или None.
        """
        if not form_selector:
            return None

        # Шаг 1: ищем внутри указанного блока
        result = await self._find_button_in_context(form_selector, submit_keywords)
        if result:
            return result

        # Шаг 2: поднимаемся к ближайшей <form> через один JS-вызов
        logger.debug(
            f"Кнопка не найдена внутри блока — "
            f"ищем в ближайшей <form>: {form_selector!r}"
        )
        try:
            response = await self.page.execute_script(f"""
                (function() {{
                    var el = document.querySelector({form_selector!r});
                    if (!el) return null;
                    var form = el.closest('form');
                    if (!form) return null;
                    // Только id — className нестабилен (пробелы, динамические классы)
                    if (form.id) return '#' + form.id;
                    return null;
                }})()
            """)
            parent_sel = (
                response.get("result", {}).get("result", {}).get("value")
            )
            if parent_sel:
                result = await self._find_button_in_context(
                    parent_sel, submit_keywords
                )
                if result:
                    return result
        except Exception as e:
            logger.debug(f"Ошибка поиска родительской <form>: {e}")

        return None

    async def _find_submit_on_page(
        self,
        submit_keywords: list[str],
    ) -> str | None:
        """Ищет кнопку подтверждения по всей странице.

        Использует _find_button_in_context с body как корневым контекстом.
        После нахождения скроллит к элементу — кнопка может быть вне viewport.

        Args:
            submit_keywords: Ключевые слова для поиска по тексту.

        Returns:
            CSS-селектор найденной кнопки или None.
        """
        try:
            result = await self._find_button_in_context("body", submit_keywords)
            if result:
                # Скроллим — кнопка видима по CSS но может быть вне viewport
                try:
                    element = await self.page.query(
                        result, timeout=2, raise_exc=False
                    )
                    if element:
                        await self.browser.scroll_to_element(element)
                except Exception as e:
                    logger.debug(f"Скролл к кнопке не выполнен: {e}")
                return result

        except Exception as e:
            logger.warning(f"Ошибка поиска кнопки на странице: {e}")

        return None
    
    async def _check_result(
        self,
        template: dict | None,
        username_was_filled: bool = False,
        engine_name: str | None = None,
    ) -> tuple[bool, str | None]:
        """Проверяет результат регистрации после отправки формы.

        Правила в порядке приоритета:
        1. Индикаторы ошибки → False
        2. Индикаторы успеха → True
        3. Анализ полей (только если username_was_filled) → True/False
        4. Ручное подтверждение (только при достижении лимита шагов) → True/False
        Fallback → False, "no_indicators"

        Args:
            template: Текущий шаблон форума.
            username_was_filled: Флаг что логин уже вводился в этой сессии.
            engine_name: Название движка для обновления шаблона.

        Returns:
            Кортеж (успех, причина_ошибки).
        """
        logger.info("=== Проверка результата регистрации ===")

        # Проверка результата регистрации:
        # Пауза и проверка загрузки страницы выполнены в register() после submit.
        # Здесь только извлечение видимого текста и анализ индикаторов.
        # Случай «контент есть, но индикаторы не найдены» — штатное поведение (no_indicators).

        # Смягчённая проверка видимости — только для _check_result.
        # Убраны проверки opacity и aria-hidden: анимированные/прозрачные элементы
        # могут содержать индикаторы успеха/ошибки регистрации.
        # Изменение изолировано — SelectorFinder использует строгую проверку.
        _extract_text_js: str = """
            (function() {
                var SKIP_TAGS = ['script','style','noscript','head',
                                 'header','nav','footer','aside'];
                var SKIP_CLASSES = ['header','footer','nav','sidebar',
                                    'menu','navigation','breadcrumb'];

                function isPotentiallyVisible(el) {
                    if (!el.getBoundingClientRect) return true;
                    var style = window.getComputedStyle(el);
                    // Проверяем только жёсткое скрытие — opacity и aria-hidden
                    // намеренно игнорируем: элемент может быть в процессе анимации
                    return style.display !== 'none' && style.visibility !== 'hidden';
                }

                function shouldSkip(el) {
                    var tag = el.tagName ? el.tagName.toLowerCase() : '';
                    if (SKIP_TAGS.indexOf(tag) > -1) return true;
                    var classes = (el.className || '').toLowerCase().split(/\s+/);
                    var id = (el.id || '').toLowerCase();
                    for (var i = 0; i < SKIP_CLASSES.length; i++) {
                        if (classes.indexOf(SKIP_CLASSES[i]) > -1) return true;
                        if (id === SKIP_CLASSES[i]) return true;
                    }
                    return false;
                }

                function collectText(el, parts) {
                    if (!el || el.nodeType === 8) return; // комментарии — пропускаем
                    if (el.nodeType === 3) {              // текстовый узел
                        var t = el.textContent.trim();
                        if (t) parts.push(t);
                        return;
                    }
                    if (!isPotentiallyVisible(el)) return;
                    if (shouldSkip(el)) return;
                    for (var i = 0; i < el.childNodes.length; i++) {
                        collectText(el.childNodes[i], parts);
                    }
                }

                // Перебираем кандидатов — берём первый давший непустой результат
                var rootCandidates = [
                    document.querySelector('main'),
                    document.querySelector('#content'),
                    document.querySelector('.content'),
                    document.querySelector('article'),
                    document.body
                ];

                var parts = [];
                for (var i = 0; i < rootCandidates.length; i++) {
                    if (rootCandidates[i]) {
                        collectText(rootCandidates[i], parts);
                        if (parts.length > 0) break;
                    }
                }

                // Fallback: если все фильтры дали пустой результат (NoJs, CSS-скрытие) —
                // берём весь текст body без проверки видимости
                if (parts.length === 0) {
                    return document.body.innerText || document.body.textContent || '';
                }

                return parts.join('\n');
            })()
        """

        async def _get_visible_text() -> str:
            """Извлекает видимый текст страницы через JS — для анализа индикаторов."""
            try:
                response = await self.page.execute_script(_extract_text_js)
                return response.get("result", {}).get("result", {}).get("value") or ""
            except Exception as e:
                logger.error(f"Не удалось получить видимый текст страницы: {e}")
                return ""

        # Получаем видимый текст для анализа индикаторов
        visible_text: str = await _get_visible_text()
        visible_text_lower: str = visible_text.lower()
        logger.debug(
            f"Видимый текст для проверки ({len(visible_text)} симв.): "
            f"{visible_text[:200]}..."
        )

        # Получаем индикаторы из шаблона
        success_indicators = (
            template.get("success_indicators") if template else None
        ) or self.success_indicators

        error_indicators = (
            template.get("error_indicators") if template else None
        ) or self.error_indicators

        # Правило 1: индикаторы ошибки
        if not error_indicators:
            logger.debug("Правило 1: список индикаторов ошибки пуст — пропускаем")
        else:
            logger.debug(f"Правило 1: проверяем {len(error_indicators)} индикаторов ошибки")
            for phrase in error_indicators:
                found = phrase.lower() in visible_text_lower
                logger.debug(f"  Правило 1: {phrase!r} → {'НАЙДЕН ⚠' if found else 'не найден'}")
                if found:
                    logger.warning(f"Индикатор ошибки сработал: {phrase!r}")
                    return False, "registration_error"

        # Правило 2: индикаторы успеха
        if not success_indicators:
            logger.debug("Правило 2: список индикаторов успеха пуст — пропускаем")
        else:
            logger.debug(f"Правило 2: проверяем {len(success_indicators)} индикаторов успеха")
            for phrase in success_indicators:
                found = phrase.lower() in visible_text_lower
                logger.debug(f"  Правило 2: {phrase!r} → {'НАЙДЕН ✓' if found else 'не найден'}")
                if found:
                    logger.info(f"Индикатор успеха сработал: {phrase!r}")
                    return True, None

        # Правило 3: анализ полей (только если логин уже вводился)
        logger.debug(
            f"Правило 3: username_was_filled={username_was_filled} — "
            f"{'запускаем' if username_was_filled else 'пропускаем'} анализ полей"
        )
        if username_was_filled:

            try:
                # Вариант А: проверяем наличие password-полей вне формы логина
                result_a = await self._check_fields_variant_a(visible_text_lower)

                # Вариант Б: проверяем наличие любых полей регистрации вне формы логина
                # result_b = await self._check_fields_variant_b(visible_text_lower)

                # Используем Вариант А (раскомментируй Б для теста)
                if result_a is not None:
                    logger.debug(f"Правило 3 (Вариант А): результат={result_a}")
                    return result_a, None if result_a else "no_indicators"

            except Exception as e:
                logger.warning(f"Ошибка анализа полей: {e}")

        # Fallback
        logger.debug("Явных индикаторов не найдено — продолжаем цикл")
        return False, "no_indicators"

    async def _check_fields_variant_a(self, page_source_lower: str) -> bool | None:
        """Вариант А анализа полей: проверяет наличие password-полей вне формы логина.

        Если есть видимые поля типа password вне формы логина → продолжаем.
        Если нет → регистрация завершена.

        Args:
            page_source_lower: HTML страницы в нижнем регистре.

        Returns:
            True если успех, False если продолжаем, None если не удалось определить.
        """
        try:
            response = await self.page.execute_script("""
                window.__variantA = (function() {
                    function inSkip(el) {
                        var p = el;
                        while (p) {
                            var tag = p.tagName ? p.tagName.toLowerCase() : '';
                            var cls = p.className ? p.className.toLowerCase() : '';
                            var id = p.id ? p.id.toLowerCase() : '';
                            var name = p.getAttribute ? (p.getAttribute('name') || '').toLowerCase() : '';
                            if (['header','nav','footer','aside'].indexOf(tag) > -1) return true;
                            if (cls.indexOf('header') > -1 || cls.indexOf('footer') > -1) return true;
                            if (cls.indexOf('nav') > -1 || cls.indexOf('module') > -1) return true;
                            if (tag === 'form' && (name === 'login' || id === 'login')) return true;
                            var action = p.getAttribute ? (p.getAttribute('action') || '').toLowerCase() : '';
                            if (tag === 'form' && (action.indexOf('login') > -1 || action.indexOf('signin') > -1)) return true;
                            p = p.parentElement;
                        }
                        return false;
                    }
                    var inputs = document.querySelectorAll('input[type="password"]');
                    var count = 0;
                    for (var i = 0; i < inputs.length; i++) {
                        var el = inputs[i];
                        var style = window.getComputedStyle(el);
                        if (style.display === 'none' || style.visibility === 'hidden') continue;
                        if (inSkip(el)) continue;
                        count++;
                    }
                    return {passwordCount: count};
                })();
            """)
            response2 = await self.page.execute_script(
                "return JSON.stringify(window.__variantA)"
            )
            raw = response2.get("result", {}).get("result", {}).get("value", "{}")
            val = json.loads(raw) if raw else {}
            password_count = val.get("passwordCount", 0)
            logger.debug(f"Вариант А: найдено password-полей вне формы логина: {password_count}")

            if password_count > 0:
                logger.debug("Вариант А: есть поля password → продолжаем")
                return False
            else:
                logger.info("Вариант А: нет полей password вне формы логина → успех")
                return True

        except Exception as e:
            logger.warning(f"Ошибка Варианта А: {e}")
            return None

    async def _check_fields_variant_b(self, page_source_lower: str) -> bool | None:
        """Вариант Б анализа полей: проверяет наличие любых полей регистрации вне формы логина.

        Если есть поля кроме username/password → продолжаем.
        Если только username/password или нет полей → успех.

        Args:
            page_source_lower: HTML страницы в нижнем регистре.

        Returns:
            True если успех, False если продолжаем, None если не удалось определить.
        """
        try:
            response = await self.page.execute_script("""
                window.__variantB = (function() {
                    function inSkip(el) {
                        var p = el;
                        while (p) {
                            var tag = p.tagName ? p.tagName.toLowerCase() : '';
                            var cls = p.className ? p.className.toLowerCase() : '';
                            var id = p.id ? p.id.toLowerCase() : '';
                            var name = p.getAttribute ? (p.getAttribute('name') || '').toLowerCase() : '';
                            if (['header','nav','footer','aside'].indexOf(tag) > -1) return true;
                            if (cls.indexOf('header') > -1 || cls.indexOf('footer') > -1) return true;
                            if (cls.indexOf('nav') > -1 || cls.indexOf('module') > -1) return true;
                            if (tag === 'form' && (name === 'login' || id === 'login')) return true;
                            var action = p.getAttribute ? (p.getAttribute('action') || '').toLowerCase() : '';
                            if (tag === 'form' && (action.indexOf('login') > -1 || action.indexOf('signin') > -1)) return true;
                            p = p.parentElement;
                        }
                        return false;
                    }
                    var unKw = ['username','login','user','email','логин','имя'];
                    var inputs = document.querySelectorAll(
                        'input:not([type=hidden]):not([type=submit]):not([type=button]):not([type=image]):not([type=reset]), textarea, select'
                    );
                    var otherCount = 0;
                    var hasUsername = false;
                    var hasPassword = false;
                    for (var i = 0; i < inputs.length; i++) {
                        var el = inputs[i];
                        var style = window.getComputedStyle(el);
                        if (style.display === 'none' || style.visibility === 'hidden') continue;
                        if (inSkip(el)) continue;
                        var type = (el.type || '').toLowerCase();
                        var name = (el.name || '').toLowerCase();
                        var id = (el.id || '').toLowerCase();
                        if (type === 'password') { hasPassword = true; continue; }
                        var isUsername = false;
                        for (var j = 0; j < unKw.length; j++) {
                            if (name.indexOf(unKw[j]) > -1 || id.indexOf(unKw[j]) > -1) {
                                isUsername = true; break;
                            }
                        }
                        if (isUsername) { hasUsername = true; continue; }
                        otherCount++;
                    }
                    return {otherCount: otherCount, hasUsername: hasUsername, hasPassword: hasPassword};
                })();
            """)
            response2 = await self.page.execute_script(
                "return JSON.stringify(window.__variantB)"
            )
            raw = response2.get("result", {}).get("result", {}).get("value", "{}")
            val = json.loads(raw) if raw else {}
            other_count = val.get("otherCount", 0)
            has_username = val.get("hasUsername", False)
            has_password = val.get("hasPassword", False)
            logger.debug(
                f"Вариант Б: другие поля={other_count}, "
                f"username={has_username}, password={has_password}"
            )

            if other_count > 0:
                logger.debug("Вариант Б: есть дополнительные поля → продолжаем")
                return False
            else:
                logger.info("Вариант Б: нет дополнительных полей → успех")
                return True

        except Exception as e:
            logger.warning(f"Ошибка Варианта Б: {e}")
            return None
 
    async def _take_screenshot(self, prefix: str = "error", username: str | None = None) -> str:
        """
        Делает скриншот текущей страницы.
        
        Args:
            prefix: префикс для имени файла.
            username: имя пользователя (опционально).
        
        Returns:
            Путь к сохранённому файлу.
        """
        try:
            # Формируем имя файла
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            if username:
                filename = f"{prefix}_{username}_{timestamp}.png"
            else:
                filename = f"{prefix}_{timestamp}.png"
            
            filepath = self.screenshot_dir / filename
            
            # Сохраняем скриншот
            await self.page.take_screenshot(path=str(filepath))
            
            logger.info(f"Screenshot saved: {filepath}")
            return str(filepath)
            
        except Exception as e:
            logger.error(f"Failed to take screenshot: {e}")
            return None
    
    async def _generate_css_selector(self, element) -> str:
        """
        Генерирует CSS-селектор для элемента.
        
        Args:
            element: элемент Pydoll.
        
        Returns:
            CSS-селектор в виде строки.
        """
        try:
            el_id = element.get_attribute("id")
            if el_id:
                return f"#{el_id}"
    
            tag = element.get_attribute("tagName")
            tag = tag.lower() if tag else "input"
    
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

    @classmethod
    def _is_dynamic_selector(cls, sel: str) -> bool:
        """Проверяет является ли CSS-селектор динамическим (нестабильным).

        Динамические селекторы генерируются случайно при каждой сессии
        и не должны сохраняться в шаблон — при следующем запуске они
        уже недействительны.

        Паттерны:
        - XenForo MD5: ``#ctrl_`` + ровно 32 hex-символа
        - Чистый hex-ID: ``#`` + 16 и более hex-символов
        - Расширяемые префиксы из ``DYNAMIC_ID_PREFIXES`` + hex-суффикс ≥ 8 символов

        Args:
            sel: CSS-селектор для проверки.

        Returns:
            True если селектор динамический, False если стабильный.
        """
        if not sel:
            return False

        if cls._PATTERN_CTRL_HASH.match(sel):
            return True

        if cls._PATTERN_HEX_ID.match(sel):
            return True

        for prefix in cls.DYNAMIC_ID_PREFIXES:
            if sel.startswith(prefix):
                suffix = sel[len(prefix):]
                if re.match(r"^[a-f0-9\-]{8,}$", suffix, re.IGNORECASE):
                    return True

        return False
    
    