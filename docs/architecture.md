# Архитектура системы регистрации на форумах

Ниже представлена детальная блок-схема ключевых компонентов. Каждый модуль и метод сопровождается кратким пояснением (1–2 строки) и схемой взаимодействия с указанием источников данных и логики проверок.

---

## 1. MainOrchestrator — главный управляющий модуль

`[MainOrchestrator.run()]` — главная точка входа, загружает данные и запускает обработку всех пользователей на всех форумах.

```
[MainOrchestrator.run()]
 │
 ├── [1] Загрузка данных
 │    ├── _load_forums() → список URL из data/results_new.txt (игнорируя строки с #)
 │    ├── _load_accounts() → список аккаунтов из data/accounts.json, фильтр по status == "pending"
 │    ├── _get_resume_index(username, all_forums) → индекс форума для продолжения
 │    │    └── читает data/results_ok_{username}.txt (последний успешный URL)
 │    │         и пропускает все URL из data/results_bad_{username}.txt
 │    └── _init_proxy_manager() → загружает прокси из data/proxies.txt (строки вида protocol://user:pass@host:port)
 │
 ├── [2] Параллельная обработка пользователей (семафор MAX_CONCURRENT_USERS)
 │    └── _process_user_with_semaphore() → ограничитель параллелизма
 │         │
 │         └── _process_user(username, user_data, forum_queue, proxy_manager) — создаёт браузер и обрабатывает все форумы пользователя
 │              │
 │              ├── Создание BrowserController (прокси, профиль data/profiles/{username}, headless)
 │              │    └── browser.start() → запуск Chrome через Pydoll
 │              │
 │              ├── Создание TemplateManager — загружает шаблоны (templates/known_forums/*.json) и общие поля (common_fields.json)
 │              ├── Создание SelectorFinder — эвристический анализ полей на странице
 │              ├── Создание CaptchaExtensionHelper — решение капч через API или вручную
 │              ├── Создание RegistrationController — основная логика регистрации
 │              │
 │              └── Для каждого forum_url в forum_queue:
 │                   └── _process_forum(...) — регистрация на одном форуме с retry-логикой
 │                        │
 │                        ├── browser.goto(forum_url) — переход на страницу
 │                        │    └── внутри BrowserController.goto():
 │                        │         ├── навигация с таймаутом (load_timeout)
 │                        │         ├── пауза (page_load_wait)
 │                        │         ├── проверка HTTP-статуса (через performance API)
 │                        │         └── при статусе != 200 — перезагрузка, при повторной ошибке → raise RuntimeError("page_unavailable")
 │                        │
 │                        ├── Определение движка и шаблона
 │                        │    └── template_manager.detect_engine(url, page_source) → (engine_name, template)
 │                        │         ├── извлечение домена второго уровня (domain2)
 │                        │         ├── прямой поиск файла templates/known_forums/{domain2}.json
 │                        │         ├── проверка платформы через forum_platforms.json и триггер-ссылки в HTML
 │                        │         ├── проверка движка через forum_engines.json (meta generator, html_signs)
 │                        │         └── если шаблон не найден — создаётся через add_template(engine_name)
 │                        │
 │                        ├── reg_controller.register(account_data, engine_name, template) — запуск регистрации
 │                        │    │
 │                        │    │  ┌────────────────────────────────────────────────────────────────────────────┐
 │                        │    │  │  RegistrationController.register() — основной процесс регистрации на форуме │
 │                        │    │  └────────────────────────────────────────────────────────────────────────────┘
 │                        │    │
 │                        │    └── (детали ниже)
 │                        │
 │                        └── Запись результата (ok или bad) + обновление статуса аккаунта
 │
 └── [3] Финальный отчёт
      └── _generate_final_report() → читает results_ok_* / results_bad_* и выводит статистику
```

---

## 2. RegistrationController.register() — основной процесс регистрации на форуме

`[RegistrationController.register(account_data, engine_name, template)]` — выполняет регистрацию: переходит на страницу, анализирует блоки, заполняет поля, отправляет форму и проверяет результат.

```
RegistrationController.register(account_data, engine_name, template)
 │
 ├── [1] Проверка обязательных полей (username, email, password) → если нет → возврат с ошибкой
 │
 ├── [2] Переход на страницу регистрации
 │    └── _navigate_to_registration_page(template)
 │         ├── если есть template["registration_page"]["url"] — перебирает варианты URL
 │         └── иначе (или если не сработало) → selector_finder.find_registration_link() → переход
 │
 ├── [3] Анализ страницы → selector_finder.analyze_current_page(template)
 │    │   (результат: список блоков с полями, отсортированный по score)
 │    └── (детали см. в разделе "SelectorFinder.analyze_current_page")
 │
 ├── [4] Цикл по шагам регистрации (max_steps = 5) и по блокам
 │    │   Для каждого шага:
 │    │
 │    ├── Выбор текущего блока (по индексу)
 │    │
 │    ├── Получение селекторов для блока
 │    │    └── _get_selectors_for_block(template, block)
 │    │         ├── для каждого стандартного поля (username, email, password, confirm_email, confirm_password, agree_checkbox, submit_button, captcha_indicator, register_radio)
 │    │         │    └── сравнивает селектор/label с шаблоном (template_fields)
 │    │         │         ├── если совпадает → помечает source = "template"
 │    │         │         ├── если есть block_val → source = "common_fields" (если label) иначе "manual"
 │    │         │         └── если нет в блоке, но шаблонный селектор есть в DOM → берёт его (source="template")
 │    │         └── custom_fields берёт из блока напрямую
 │    │
 │    ├── Создание снимка блока (до заполнения) → _make_block_snapshot(selectors)
 │    │    └── сохраняет пятёрки (имя_поля, тип, селектор, label, value) для сравнения после submit
 │    │
 │    ├── Заполнение полей → _fill_fields(selectors, account_data, template, engine_name, form_selector)
 │    │    │   (подробности в разделе "_fill_fields")
 │    │    └── возвращает {"ok": bool, "filled": list, "skipped": list, "filled_from_outside": list}
 │    │
 │    ├── Если fill_result["ok"] == False → возврат с ошибкой (manual_fill_timeout / submit_failed)
 │    │
 │    ├── После submit — пауза и проверка загрузки страницы (с refresh при необходимости)
 │    │
 │    ├── Проверка изменился ли блок → _check_block_changed(snapshot, form_selector, template)
 │    │    │   (анализирует страницу через selector_finder.analyze_current_page)
 │    │    └── возвращает (changed, has_any_fields, new_blocks)
 │    │
 │    ├── Если блок изменился и есть поля → сохраняем шаблон, обновляем all_blocks, обнуляем индекс, continue
 │    │
 │    ├── Если блок не изменился → переходим к следующему блоку (current_block_index++)
 │    │
 │    ├── Проверка результата регистрации → _check_result(template, username_was_filled, engine_name)
 │    │    │   (анализ индикаторов успеха/ошибки и анализ полей)
 │    │    └── возвращает (success, error_reason)
 │    │
 │    ├── Если error_reason != "no_indicators" → ошибка (регистрация не удалась)
 │    │    └── запрос подтверждения (если TEST_MODE) → возможно сохранение шаблона и возврат успеха
 │    │
 │    ├── Если success == True → успех: сохраняем шаблон, сохраняем пропуски в профиль, возвращаем успех
 │    │
 │    └── Если нет индикаторов (no_indicators) → переходим к следующему блоку (current_block_index++)
 │
 └── [5] Если достигнут лимит шагов → ручное подтверждение (TEST_MODE) и возврат результата
```

---

## 3. _fill_fields(selectors, account_data, template, engine_name, form_selector) — заполнение полей формы

`[_fill_fields(...)]` — заполняет поля формы: radio-кнопку регистрации, стандартные поля, custom-поля, чекбокс согласия, капчу и кнопку submit. Использует данные из профиля, шаблона и ручной ввод при необходимости. Запись в шаблон не выполняется — все новые данные накапливаются в аккумуляторах и передаются в `_save_block_to_template` при подтверждённом успехе.
```
_fill_fields(selectors, account_data, template, engine_name, form_selector)
 │
 ├── Аккумуляторы новых данных (заполняются в процессе, не пишутся в шаблон сразу):
 │    ├── new_custom_selectors: dict[str, tuple[str, str]]  # {field: (sel, source)}
 │    ├── new_checkboxes: list[tuple[str, str]]             # [(sel, source), ...]
 │    └── found_submit: tuple[str, str] | None              # (sel, source)
 │
 ├── Шаг 0: radio-кнопка регистрации (register_radio)
 │    ├── если нет в selectors → пропуск
 │    ├── если source == "template" → human_click(selector)
 │    ├── если source in ("common_fields", "manual") → попытка кликнуть, при ошибке → ручной ввод
 │    └── если нажата → filled_fields.append("register_radio")
 │
 ├── Шаг 1: заполнение полей ввода (standard_fields + custom_fields)
 │    │
 │    ├── Стандартные поля (username, email, confirm_email, password, confirm_password)
 │    │    ├── для каждого поля: попытка заполнить через _try_fill_element
 │    │    │    └── внутри _try_fill_element:
 │    │    │         ├── уточнение tagName через JS если get_attribute("tagName") вернул None
 │    │    │         │    └── document.querySelector(selector)?.tagName?.toLowerCase()
 │    │    │         ├── проверка видимости/доступности элемента (JS: getComputedStyle, disabled, aria-disabled)
 │    │    │         ├── если <select> → _try_fill_select
 │    │    │         │    ├── получает опции через JSON.stringify(Array.from(el.options))
 │    │    │         │    ├── ищет совпадение по тексту опции (частичное) ИЛИ по значению (точное)
 │    │    │         │    └── если опция не найдена → возвращает ("not_found", список опций)
 │    │    │         ├── иначе → human_type (если поле не заполнено)
 │    │    │         └── возвращает статус: "filled"/"already_filled"/"not_visible"/"not_found"
 │    │    ├── если все селекторы не сработали → _ask_manual_input (ручной ввод)
 │    │    └── запись в шаблон НЕ выполняется — данные накапливаются через filled_fields
 │    │
 │    └── Custom_fields (из selectors["custom_fields"])
 │         ├── если тип == "checkbox" → human_click (если не отмечен)
 │         │    └── если успешно и не одноразовое → new_checkboxes.append((sel, source))
 │         ├── если одноразовое поле (one_time_field_keywords) → всегда ручной ввод, не сохраняем
 │         ├── иначе если есть значение в account_data["custom_fields"] → перебираем варианты
 │         │    ├── если ни один не подошёл → ручной ввод
 │         │    └── если заполнено → new_custom_selectors[field] = (sel, "manual")
 │         ├── если нет значения в профиле → автоопределение через known_field_types:
 │         │    city, birthdate, gender, firstname, lastname, phone, website, country, timezone,
 │         │    dob_day, dob_month, dob_year  ← новые типы для разделённых полей даты рождения
 │         │    └── если автоопределилось → new_custom_selectors[field] = (sel, "auto")
 │         └── если нет авто → ручной ввод → new_custom_selectors[field] = (sel, "manual")
 │
 ├── Шаг 2: чекбокс согласия (agree_checkbox)
 │    ├── проверяем тип элемента (не кнопка)
 │    ├── если не отмечен → human_click
 │    └── filled_fields.append("agree_checkbox")
 │
 ├── Шаг 3: капча → _handle_captcha(selectors)
 │    ├── если нет captcha_indicator → True
 │    ├── если invisible → пауза 3с → True
 │    ├── иначе если есть captcha_helper и site_key → решаем через API
 │    └── иначе → ручной ввод (ожидание Enter)
 │
 ├── Шаг 4: кнопка submit → _handle_submit(selectors, form_selector)
 │    ├── если submit_button в selectors → перебираем селекторы и кликаем → возвращает (True, None)
 │    ├── если не сработало → _submit_form (поиск эвристикой)
 │    │    └── если найдена новая кнопка → возвращает (True, (found_selector, "heuristic"))
 │    └── если авто не сработало → ручной ввод → возвращает (True, None) или (False, None)
 │         └── found_submit из возврата сохраняется в аккумулятор
 │
 └── Возвращает FillFieldsResult:
      {"ok": bool, "filled": [...], "skipped": [...], "filled_from_outside": [...],
       "new_custom_selectors": {...}, "new_checkboxes": [...], "found_submit": (...) | None}
```


---

## 4. _check_result(template, username_was_filled, engine_name) — анализ результата после submit

`[_check_result(...)]` — проверяет, успешна ли регистрация, анализируя индикаторы успеха/ошибки и наличие полей на странице.

```
_check_result(template, username_was_filled, engine_name)
 │
 ├── Получение видимого текста страницы через JS (игнорирует header/footer и невидимые элементы)
 │
 ├── Проверка индикаторов ошибки (error_indicators) → если найдено → возврат (False, reason)
 │
 ├── Проверка индикаторов успеха (success_indicators) → если найдено → возврат (True, None)
 │
 ├── Если username_was_filled == True:
 │    └── Вариант А: _check_fields_variant_a() — поиск полей типа password вне формы логина
 │         ├── через JS проверяет наличие видимых password-полей, не принадлежащих форме логина
 │         ├── если есть → возврат (False, None) — продолжаем регистрацию
 │         └── если нет → возврат (True, None) — успех
 │
 └── Если ничего не сработало → возврат (False, "no_indicators")
```

---

## 5. _check_block_changed(snapshot, form_selector, template) — проверка изменения блока после submit

`[_check_block_changed(...)]` — определяет, изменилась ли страница после отправки формы (блок исчез или состав полей изменился). Используется для перехода на следующую страницу регистрации.

```
_check_block_changed(snapshot, form_selector, template)
 │
 ├── Получение нового списка блоков → selector_finder.analyze_current_page(template)
 │
 ├── Поиск блока с таким же form_selector
 │    ├── если не найден → возврат (True, has_any_fields, new_blocks) — блок изменился
 │    └── если найден:
 │         ├── _get_selectors_for_block(template, same_block) → новые селекторы
 │         ├── _make_block_snapshot(new_selectors) → новый снимок
 │         └── сравниваем со старым снимком
 │              ├── если разные → (True, has_any_fields, new_blocks)
 │              └── если одинаковые → (False, has_any_fields, new_blocks)
```

---

## 6. _save_block_to_template(block, selectors, filled_fields, template, engine_name, new_custom_selectors, new_checkboxes, found_submit) — единая точка записи в шаблон

`[_save_block_to_template(...)]` — единственная точка записи в шаблон. Вызывается только при подтверждённом успехе. Фильтрует динамические и шаблонные селекторы, сохраняет новые данные по всем типам полей, обновляет template в памяти только после успешной записи на диск.
```
_save_block_to_template(block, selectors, filled_fields, template, engine_name,
                        new_custom_selectors, new_checkboxes, found_submit)
 │
 ├── Вызывается ТОЛЬКО при подтверждённом успехе:
 │    ├── Триггер А: финальный успех (_check_result → success=True)
 │    ├── Триггер Б: переход на следующую страницу (block_changed and has_any_fields)
 │    └── Триггер В: ручное подтверждение (_confirm_test_mode → True)
 │
 ├── Если нет engine_name → выход
 │
 ├── Шаг 0: единая фильтрация ВСЕХ входных данных (один раз, до блоков А–Г)
 │    ├── _check_source(src, sel, field) → три критерия отсечения:
 │    │    ├── source in (None, "", "unknown") → logger.error + отброс (баг в коде выше)
 │    │    ├── source == "template" → пропуск (уже в шаблоне)
 │    │    └── _is_dynamic_selector(sel) → пропуск (динамический ID, напр. #ctrl_<MD5>)
 │    ├── filled_fields_clean  — стандартные поля прошедшие фильтр
 │    ├── new_custom_clean     — custom-поля прошедшие фильтр
 │    ├── new_checkboxes_clean — чекбоксы прошедшие фильтр
 │    └── found_submit_clean   — кнопка submit прошедшая фильтр или None
 │
 ├── Блок А: стандартные поля ← filled_fields_clean
 │    ├── лимит MAX_SELECTORS_PER_FIELD (10) → warning + пропуск при превышении
 │    ├── selector not in existing_list → fields_to_save[key] = selector
 │    └── label not in existing_label_list → fields_to_save[key_label] = label
 │
 ├── Блок Б: custom_fields ← new_custom_clean
 │    ├── лимит MAX_SELECTORS_PER_FIELD → warning + пропуск
 │    └── sel not in template["fields"][field] → fields_to_save[field] = sel
 │
 ├── Блок В: agree_step.checkboxes ← new_checkboxes_clean
 │    ├── лимит MAX_SELECTORS_PER_FIELD → warning + пропуск
 │    └── sel not in existing_checkboxes → checkboxes_to_save.append(sel)
 │
 ├── Блок Г: submit_button fallback ← found_submit_clean
 │    ├── лимит MAX_SELECTORS_PER_FIELD → warning + пропуск
 │    └── sel not in existing_submit → fields_to_save["submit_button"] = sel
 │
 ├── Блок Д: form_selector из блока
 │    ├── проверка source И _is_dynamic_selector
 │    └── если прошёл → new_data["registration_page"]["form_selector"] = [form_selector]
 │
 ├── Если нет новых данных → выход
 │
 ├── Один вызов update_template(engine_name, new_data):
 │    try: await template_manager.update_template(...)
 │    except: logger.error + return  ← template в памяти НЕ обновляем при ошибке
 │
 └── Синхронизация template в памяти (только после успешной записи):
      ├── fields_to_save → template["fields"][key].append(val)  # всегда список
      ├── checkboxes_to_save → template["agree_step"]["checkboxes"].extend(...)
      ├── found_submit_clean → template["fields"]["submit_button"].append(sel)
      └── form_selector → template["registration_page"]["form_selector"].append(sel)
```

---

## 7. _save_filled_to_profile(filled_from_outside, skipped_fields, account_data, username) — сохранение заполненных полей в профиль пользователя

`[_save_filled_to_profile(...)]` — сохраняет в accounts.json значения полей, которые были заполнены из автоопределения (filled_from_outside) и поля, которые оператор пропустил (skipped_fields). Обновляет custom_fields аккаунта в памяти и на диске.

```
_save_filled_to_profile(filled_from_outside, skipped_fields, account_data, username)
 │
 ├── Если username пуст → выход
 │
 ├── all_fields = уникальный список filled_from_outside + skipped_fields
 │
 ├── profile_custom = account_data.get("custom_fields", {})
 │
 ├── fields_to_save = {}
 │
 ├── Для каждого field_name в all_fields:
 │    └── если field_name есть в profile_custom → fields_to_save[field_name] = profile_custom[field_name]
 │
 ├── Если fields_to_save не пуст → вызов template_manager._update_account_profile(username, fields_to_save)
 │    └── обновляет accounts.json, дописывая новые значения в custom_fields (не перезаписывая существующие)
 │
 └── Логирует результат
```

---

## 8. SelectorFinder.analyze_current_page(template) — анализ страницы и поиск блоков

`[SelectorFinder.analyze_current_page(template)]` — сканирует DOM, находит все подходящие блоки (формы и div с полями), классифицирует поля, определяет капчу, возвращает отсортированный список блоков с их селекторами и метаданными.
```
SelectorFinder.analyze_current_page(template)
 │
 ├── find_registration_form(template) → поиск всех подходящих блоков (forms/div) с подсчётом score
 │    │
 │    ├── собирает все <form> через page.query("form")
 │    ├── если форм мало (или нет), собирает <div>, содержащие поля ввода
 │    ├── для каждого блока:
 │    │    ├── получает видимые поля (input, textarea, select, checkbox, submit-кнопки)
 │    │    ├── фильтрует нежелательные поля (по checkbox_skip_keywords)
 │    │    ├── штрафует за признаки формы логина (-20 за action/name/id, -20 за username без email)
 │    │    ├── начисляет очки:
 │    │    │    +1 за каждое поле
 │    │    │    +3 за пароль
 │    │    │    +2 за submit-кнопку
 │    │    │    +1 за чекбокс
 │    │    │   +10 за совпадение селектора с шаблоном
 │    │    │    +5 за ключевые слова username/email/password
 │    │    │    +3 за ключевые слова согласия
 │    │    └── сохраняет блок с score и template_matches
 │    └── сортирует по score (убывание)
 │
 ├── Для каждого найденного блока:
 │    │
 │    ├── identify_fields(block["form_element"]) → классификация полей
 │    │    │
 │    │    ├── извлекает все поля (input, textarea, select, button)
 │    │    ├── определяет submit_button (по типу, ключевым словам submit_keywords)
 │    │    ├── для каждого поля:
 │    │    │    ├── attrs = _get_element_attrs(element)
 │    │    │    │
 │    │    │    ├── Уточнение tagName через JS если get_attribute("tagName") вернул None:
 │    │    │    │    └── document.querySelector(selector)?.tagName?.toLowerCase()
 │    │    │    │         └── обновляет attrs["tagName"] — гарантирует "select" для <select> без id
 │    │    │    │
 │    │    │    ├── selector = _generate_css_selector(element, attrs)
 │    │    │    │    └── использует attrs["tagName"] вместо повторного get_attribute
 │    │    │    │         → select[name='dob_month'] вместо input[name='dob_month']
 │    │    │    │
 │    │    │    └── определяет тип поля по атрибутам/display_text:
 │    │    │         password, confirm_password, email, confirm_email, username,
 │    │    │         agree_checkbox, register_radio,
 │    │    │         а также known_field_types в порядке приоритета:
 │    │    │         dob_day, dob_month, dob_year  ← НОВОЕ (приоритет выше birthdate)
 │    │    │         city, birthdate, gender, firstname, lastname,
 │    │    │         phone, website, country, timezone
 │    │    │         └── для каждого поля читает display_text (label из for, placeholder, значение)
 │    │    └── неопознанные поля → custom_fields (с типом, селектором, display_text)
 │    │
 │    ├── detect_captcha() → определяет наличие капчи (selector, type, site_key, invisible)
 │    │    └── ищет iframe с src, содержащим recaptcha/hcaptcha/turnstile, или элементы .g-recaptcha и т.д.
 │    │        извлекает data-sitekey и data-size
 │    │
 │    └── собирает результат: {form_selector, score, template_matches, **fields, captcha_indicator}
 │
 └── возвращает список всех блоков с полями
```

---

## 9. TemplateManager.detect_engine(url, page_source) — определение движка/платформы

`[TemplateManager.detect_engine(url, page_source)]` — определяет название движка (например, xenforo, phpbb) и загружает соответствующий шаблон из файла. Использует домен, мета-теги, триггер-ссылки.

```
TemplateManager.detect_engine(url, page_source)
 │
 ├── Извлечение домена второго уровня (domain2) через tldextract
 │
 ├── Прямой поиск файла templates/known_forums/{domain2}.json
 │    └── если найден → возвращает (domain2, template)
 │
 ├── Поиск платформы через forum_platforms.json:
 │    ├── если domain2 совпадает с полем "platforms" → platform = domain2
 │    ├── иначе сканирует HTML на наличие триггер-ссылок (platform_link_triggers)
 │    │    └── из href извлекает домен платформы
 │    └── если платформа определена → add_template(platform) (создаёт шаблон) и возвращает (platform, template)
 │
 ├── Поиск движка через forum_engines.json:
 │    ├── проверяет мета-тег generator
 │    ├── проверяет наличие html_signs (строк в HTML)
 │    └── если движок определён → ищет файл {engine}.json, если нет → add_template(engine)
 │
 └── Fallback → возвращает (domain2 или registered_domain, None)
```

---

## 10. TemplateManager.update_template(engine_name, new_data) — обновление шаблона

`[TemplateManager.update_template(engine_name, new_data)]` — обновляет файл шаблона новыми селекторами, label, индикаторами и другой информацией, не перезаписывая существующие данные.

```
TemplateManager.update_template(engine_name, new_data)
 │
 ├── Читает файл templates/known_forums/{engine_name}.json (если не существует, создаётся через add_template)
 │
 ├── _merge_template(target, new_data):
 │    ├── для fields: каждый field_name получает список селекторов (добавляет новые)
 │    ├── для success_indicators/error_indicators/custom_fields: дополняет списки
 │    ├── для agree_step: дополняет checkboxes
 │    └── для registration_page: дополняет url и form_selector
 │
 ├── Сохраняет обновлённый шаблон в файл
 │
 └── Обновляет кэш self.templates (если уже загружен)
```

---

## 11. AccountManager.update_account_status(username, reason) — обновление статуса аккаунта

`[AccountManager.update_account_status(username, reason)]` — обновляет поле last_attempt и (при неудаче) last_error в accounts.json. Используется для отслеживания попыток регистрации.

```
AccountManager.update_account_status(username, reason)
 │
 ├── Загружает accounts.json
 │
 ├── Находит аккаунт по username
 │
 ├── Обновляет last_attempt = текущее время ISO
 │
 ├── Если reason передан → добавляет last_error = {"reason": reason, "proxy_used": proxy}
 │
 └── Сохраняет accounts.json
```

---

## 12. TemplateManager.detect_engine — уточнение работы с forum_platforms.json и forum_engines.json

```
template_manager.detect_engine(url, page_source)
 │
 ├── извлечение domain2 через tldextract (например, "xenforo.com")
 │
 ├── прямое чтение templates/known_forums/{domain2}.json (если есть)
 │
 ├── проверка платформы через forum_platforms.json
 │    ├── platforms: список доменов бесплатных хостингов (forum2x2.ru, ucoz.ru и др.)
 │    └── platform_link_triggers: фразы в ссылках ("создать форум", "create a forum")
 │         └── если найдена такая ссылка, из её href извлекается домен платформы
 │
 ├── проверка движка через forum_engines.json
 │    ├── для каждого движка проверяется meta_generator (содержимое <meta name="generator">)
 │    └── ищутся html_signs (строки в HTML, например "powered by phpbb")
 │
 └── если движок/платформа определены, создаётся шаблон через add_template(engine_name)
```

---

## 13. CaptchaExtensionHelper.solve_captcha — фасад решения капч через цепочку провайдеров

```
CaptchaExtensionHelper.solve_captcha(captcha_type, site_key, page_url)
 │
 ├── Получение цепочки провайдеров для captcha_type через get_provider_chain()
 │    └── из CAPTCHA_PROVIDERS_CONFIG (config/settings.py) формируется список:
 │         ├── сортировка по priority (чем меньше число, тем выше приоритет)
 │         ├── учитывается enabled и supported_types (если "*" — все типы)
 │         └── для каждого провайдера инициализируется экземпляр с api_key из .env
 │
 ├── Для каждого провайдера в цепочке:
 │    │
 │    ├── При первом использовании провайдера: _snapshot_balance()
 │    │    └── сохраняет текущий баланс в self.balance_snapshots (для расчёта стоимости)
 │    │
 │    ├── Для manual провайдера: устанавливает страницу (provider.set_page(page))
 │    │
 │    ├── Вызов provider.solve(captcha_type, site_key, page_url, **kwargs)
 │    │    │
 │    │    ├── TwoCaptchaProvider.solve():
 │    │    │    ├── _submit_task() → POST /in.php (method=userrecaptcha, googlekey и др.)
 │    │    │    └── _wait_for_result() → polling /res.php каждые 5 сек до готовности
 │    │    │
 │    │    ├── CapSolverProvider.solve():
 │    │    │    ├── _create_task() → POST /createTask (тип ReCaptchaV2Task и т.д.)
 │    │    │    └── _get_task_result() → polling /getTaskResult каждые 3 сек
 │    │    │
 │    │    ├── AZCaptchaProvider.solve(): аналогичен 2Captcha, но API совместим
 │    │    │
 │    │    └── ManualProvider.solve():
 │    │         ├── подсвечивает капчу на странице (JS-подсветка)
 │    │         └── _poll_for_token() → polling каждые 2 сек, ожидание токена
 │    │
 │    ├── Если успешно:
 │    │    ├── _inject_token(token, captcha_type) → внедряет токен в DOM (через JS)
 │    │    ├── обновляет счётчики решений (solve_counts)
 │    │    ├── вызывает stats_callback (для AccountManager)
 │    │    └── возвращает токен
 │    │
 │    └── Если ошибка (APIKeyError, NoBalanceError, CaptchaFailedError и т.д.) → переход к следующему провайдеру
 │
 └── Если все провайдеры исчерпаны → возврат None
```

---

## 14. CaptchaExtensionHelper.finalize — трекинг стоимости решений капч

```
CaptchaExtensionHelper.finalize()
 │
 ├── Для каждого провайдера, который использовался (self.used_providers):
 │    │
 │    ├── Если провайдер поддерживает проверку баланса:
 │    │    ├── _recalculate_cost(provider, last_captcha_type)
 │    │    │    ├── получение текущего баланса
 │    │    │    ├── расчёт потраченной суммы = snapshot_balance - current_balance
 │    │    │    ├── количество решений = solve_counts[(provider, captcha_type)]
 │    │    │    ├── если решений >= MIN_SOLVES_FOR_CALCULATION (3):
 │    │    │    │    ├── cost_per_solve = spent / count
 │    │    │    │    ├── проверка на выброс (не более DEFAULT_COST * OUTLIER_THRESHOLD)
 │    │    │    │    └── сохранение в self._learned_costs[provider][captcha_type]
 │    │    │    └── обновление snapshot_balance = current_balance, обнуление счётчика
 │    │    │
 │    │    └── _save_learned_costs() → запись в data/captcha_learned_costs.json
 │    │
 │    └── (если провайдер не поддерживает баланс — пропускается)
 │
 └── Логирование выученных цен (если LOG_LEARNED_PRICES = True)
```

---

## 15. SelectorFinder — использование common_fields.json для идентификации полей
```
identify_fields(form_element)
 │
 ├── загружает common_fields через _ensure_common_fields()
 │    └── common_fields.json содержит:
 │         ├── username_keywords, email_keywords, password_keywords
 │         ├── confirm_email_keywords, confirm_password_keywords
 │         ├── agree_keywords, submit_keywords, register_radio_keywords
 │         ├── checkbox_skip_keywords (newsletter, subscribe...)
 │         ├── известные custom: city_keywords, birthdate_keywords, gender_keywords,
 │             firstname_keywords, lastname_keywords, phone_keywords,
 │             website_keywords, country_keywords, timezone_keywords
 │         ├── dob_day_keywords   ← НОВОЕ: ["day", "день"]
 │         ├── dob_month_keywords ← НОВОЕ: ["month", "месяц"]
 │         ├── dob_year_keywords  ← НОВОЕ: ["year", "год"]
 │         └── one_time_field_keywords (капча, код и т.д.)
 │
 ├── для каждого поля извлекает атрибуты (name, id, placeholder, type) и display_text (label)
 │    └── combined = f"{name} {id} {placeholder} {display_text}"
 │
 ├── применяет ключевые слова в порядке приоритета:
 │    ├── password → password_keywords / confirm_password_keywords
 │    ├── email → email_keywords / confirm_email_keywords
 │    ├── username → username_keywords
 │    ├── agree_checkbox → agree_keywords (исключая checkbox_skip_keywords)
 │    ├── register_radio → register_radio_keywords
 │    ├── custom поля → dob_day/month/year (приоритет), затем остальные known_field_types
 │    └── остальные → custom_fields
 │
 └── возвращает структуру с селекторами и метками (fieldname_label)
```

---

## 16. Ключевые настройки из config/settings.py, влияющие на поведение

| Раздел | Ключ | Назначение |
|--------|------|------------|
| **Параллелизм** | `MAX_CONCURRENT_USERS` | количество одновременно работающих браузеров |
| **Таймауты** | `MANUAL_CAPTCHA_TIMEOUT`, `MANUAL_FIELD_FILL_TIMEOUT`, `FIND_REGISTRATION_PAGE_TIMEOUT` | время ожидания ручных действий |
| **Retry** | `MAX_REGISTRATION_RETRIES`, `NO_RETRY_REASONS` | количество повторных попыток и причины, при которых повтор не нужен |
| **Капча** | `CAPTCHA` (AUTO_SORT_BY_COST, DEFAULT_TIMEOUT, ALLOW_MANUAL_FALLBACK) | общие настройки решения капч |
| **Капча — провайдеры** | `CAPTCHA_PROVIDERS_CONFIG` | приоритет, включение, поддерживаемые типы, env_key |
| **Трекинг стоимости** | `CAPTCHA_COST_TRACKING` | включение, мин. количество решений, порог выброса |
| **Отчёты** | `SHOW_FINAL_REPORT`, `SHOW_FAILED_DETAILS`, `ERROR_MESSAGES_RU` | вывод итогового отчёта и локализация ошибок |

---

## 17. Передача статистики капчи в MainOrchestrator

```
MainOrchestrator._on_captcha_stats(stats: dict)
 │
 ├── если stats["success"] == True:
 │    ├── если stats["provider"] == "manual" → self._stats["captcha_manual"] += 1
 │    └── иначе → self._stats["captcha_auto"] += 1
 └── если False → self._stats["captcha_failed"] += 1
```

Эти значения затем выводятся в финальном отчёте.

### 18. ProxyManager — загрузка и проверка прокси

`[ProxyManager]` — загружает прокси из файла, проверяет работоспособность, поддерживает round‑robin. В текущей реализации `MainOrchestrator` использует его только как источник списка (доступ по индексу), но класс предоставляет расширенные возможности.

```
ProxyManager.__init__(proxy_file, check_timeout=5)
 │
 └── сохраняет путь к файлу и таймаут проверки

ProxyManager.load_proxies()
 │
 ├── читает data/proxies.txt построчно через aiofiles
 ├── игнорирует пустые строки и строки с "#"
 ├── если в строке нет "://", добавляет "http://" (дефолтный протокол)
 ├── сохраняет результат в self.proxies
 └── логирует количество загруженных прокси

ProxyManager.check_proxy(proxy)
 │
 ├── определяет протокол: socks4://, socks5:// или http://
 │    ├── для SOCKS: создаёт ProxyConnector из aiohttp_socks (если установлен)
 │    └── для HTTP: использует параметр proxy в запросе
 ├── отправляет GET запрос к http://httpbin.org/ip с таймаутом check_timeout
 └── возвращает True если статус 200 и в ответе есть поле "origin"

ProxyManager.get_next_proxy(check=True)
 │
 ├── если self.proxies пуст → None
 ├── циклически перебирает прокси (round‑robin) через self.current_index
 ├── если check == True:
 │    └── вызывает check_proxy() для каждого прокси, пропускает нерабочие
 ├── возвращает строку прокси или None если ни один не прошёл проверку
 └── увеличивает self.current_index

ProxyManager.refresh_proxies()
 │
 ├── сбрасывает self.current_index = 0
 └── вызывает load_proxies() для перезагрузки из файла

В MainOrchestrator используется упрощённый доступ:
    _get_proxy_for_user(user, proxy_manager)
        ├── если proxy_id is None → возвращает None (работа без прокси)
        ├── иначе возвращает proxy_manager.proxies[proxy_id] (прямой доступ по индексу)
        └── проверка работоспособности не выполняется
```

---

### 19. CaptchaProviderRegistry — построение цепочки провайдеров

`[get_provider_chain(captcha_type)]` — функция из `utils/captcha_providers/registry.py`, которая формирует список активных провайдеров на основе конфигурации из `config/settings.py`.

```
get_provider_chain(captcha_type)
 │
 ├── загружает CAPTCHA_PROVIDERS_CONFIG (словарь конфигурации)
 ├── загружает CAPTCHA (общие настройки, включая AUTO_SORT_BY_COST, ALLOW_MANUAL_FALLBACK)
 ├── загружает CAPTCHA_COST_TRACKING для learned_costs
 │
 ├── _load_learned_costs() → читает data/captcha_learned_costs.json (если есть)
 │
 ├── _get_cost_for_provider(provider_id, captcha_type, learned_costs) → цена из learned_costs или DEFAULT_COST
 │
 ├── _create_provider(provider_id, api_key) → создаёт экземпляр провайдера
 │    └── импортирует класс из implementations/ и возвращает его экземпляр
 │         (передаёт api_key и learned_costs)
 │
 ├── для каждого провайдера в CAPTCHA_PROVIDERS_CONFIG:
 │    ├── если enabled == False → пропуск
 │    ├── если provider_id == "manual":
 │    │    └── сохраняет для добавления в конец (если ALLOW_MANUAL_FALLBACK)
 │    ├── иначе:
 │    │    ├── проверяет наличие env_key в окружении (os.getenv)
 │    │    ├── если ключа нет → пропуск
 │    │    ├── проверяет supported_types (если "*" или captcha_type в списке) → пропуск иначе
 │    │    └── добавляет экземпляр провайдера в цепочку chain
 │
 ├── Сортировка chain:
 │    ├── если CAPTCHA["AUTO_SORT_BY_COST"] == True:
 │    │    └── сортирует по цене (через _get_cost_for_provider)
 │    └── иначе:
 │         └── сортирует по приоритету (поле priority из конфигурации провайдера)
 │
 ├── Если ALLOW_MANUAL_FALLBACK и manual_provider создан → chain.append(manual_provider)
 │
 └── возвращает список экземпляров CaptchaProvider
```

---

### 20. Базовые классы и исключения провайдеров (base.py)

`[CaptchaProvider]` — абстрактный базовый класс, определяющий интерфейс для всех провайдеров. Каждый провайдер должен реализовать все абстрактные методы.

```
CaptchaProvider (ABC)
 │
 ├── @property name: str — уникальное имя провайдера (должно совпадать с ключом в конфигурации)
 │
 ├── is_available() → bool — проверка наличия API‑ключа
 ├── supports_balance_check() → bool — может ли провайдер возвращать баланс
 ├── supports_type(captcha_type) → bool — поддерживает ли данный тип капчи
 │
 ├── async get_balance() → float | None — запрос баланса (если поддерживается)
 ├── async solve(captcha_type, site_key, page_url, **kwargs) → CaptchaResult
 ├── get_cost_estimate(captcha_type) → float — цена из learned_costs или DEFAULT_COST
 ├── async report_bad(task_id) → bool — сообщить о неверном решении
 │
 └── CaptchaResult (TypedDict):
      ├── token: str | None
      ├── score: float | None
      ├── provider: str
      ├── cost: float
      ├── solve_time: float
      └── captcha_type: str

Исключения (все наследуются от Exception):
    CaptchaUnsupportedError  — тип капчи не поддерживается
    CaptchaTimeoutError      — истекло время ожидания
    CaptchaFailedError       — провайдер не смог решить
    APIKeyError              — неверный или отсутствующий ключ
    NoBalanceError           — недостаточно средств
    NetworkError             — ошибка сети
```
