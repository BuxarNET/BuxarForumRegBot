# Архитектура и логика работы BuxarForumRegBot

## 1. Точка входа — `main.py`

```
python main.py [флаги]
│
├── --help       → вывод справки → выход
├── --check      → check_environment() → проверка файлов и зависимостей → выход
├── --report     → report_only() → чтение results_ok/bad → отчёт → выход
├── --dry-run    → dry_run() → план без браузеров → выход
│
└── (без флагов) → основной запуск
    ├── load_dotenv()          — загрузка API-ключей из .env
    ├── setup_logging()        — loguru: консоль (INFO) + файл (DEBUG)
    ├── check_environment()    — проверка файлов, зависимостей
    │   └── ошибки → sys.exit(1)
    ├── MainOrchestrator()
    ├── signal handlers        — Ctrl+C → orchestrator.shutdown()
    └── orchestrator.run()
```

---

## 2. Главный оркестратор — `MainOrchestrator.run()`

```
run()
│
├── _load_forums()
│   └── читает data/results_new.txt → список URL форумов
│   └── пустой список → logger.error → return stats
│
├── _load_accounts()
│   └── читает data/accounts.json → фильтрует status="pending"
│   └── подставляет пароли из переменных окружения (name='ENV_VAR')
│   └── пустой список → logger.error → return stats
│
├── для каждого пользователя → _get_resume_index()
│   ├── читает results_ok_{username}.txt
│   │   └── файл пустой/не существует → start_index = 0
│   │   └── файл есть → берём последний URL → ищем в all_forums → start_index = idx + 1
│   ├── читает results_bad_{username}.txt → собираем множество bad_urls
│   ├── начиная с start_index проверяем all_forums[start_index] in bad_urls
│   │   └── есть в bad → start_index += 1 → повторяем
│   │   └── нет в bad → start_index окончательный
│   │   └── все форумы в bad → logger.warning
│   └── forum_queue = all_forums[start_index:]
│
├── _init_proxy_manager() → ProxyManager.load_proxies()
│
├── asyncio.Semaphore(MAX_CONCURRENT_USERS)
│
└── asyncio.gather() → параллельный запуск _process_user() для каждого пользователя
    └── ошибки задач → logger.error
```

---

## 3. Обработка пользователя — `_process_user()`

```
_process_user(username, user_data, forum_queue, proxy_manager)
│
├── _get_proxy_for_user() → прокси из proxy_manager по proxy_id
│   └── proxy_id=None → работаем без прокси (если REQUIRE_PROXY_PER_USER=False)
│
├── BrowserController(proxy, user_data_dir=profiles/{username}, headless)
├── browser.start() → запуск Chrome
├── CaptchaExtensionHelper(page, stats_callback)
├── TemplateManager()
├── SelectorFinder(page, template_manager)
├── RegistrationController(browser, template_manager, selector_finder, page, config)
│
└── для каждого forum_url из forum_queue
    ├── shutdown_event → break
    └── _process_forum(...)
        (см. раздел 4)
│
├── captcha_helper.finalize() → сохранение learned costs
├── browser.stop()
└── _save_profile_meta() → data/profiles/{username}/meta.json
```

---

## 4. Обработка форума — `_process_forum()`

```
_process_forum(forum_url, ...)
│
└── ЦИКЛ RETRY (attempt = 0..MAX_REGISTRATION_RETRIES=3)
    │
    ├── browser.new_tab() → новая вкладка
    ├── browser.goto(forum_url)
    │   ├── 200 → продолжаем
    │   ├── не 200 → перезагрузка → снова не 200 → RuntimeError("page_unavailable")
    │   │   └── перехватывается → result.reason = "page_unavailable" → no-retry → break
    │   └── таймаут → PageLoadTimeout → result.reason = "timeout" → retry
    │
    ├── detect_engine(url, page_source)
    │   (см. раздел 5)
    │
    ├── asyncio.wait_for(reg_controller.register(...), timeout=TAB_TIMEOUT_SECONDS)
    │   (см. раздел 6)
    │
    ├── asyncio.TimeoutError → result.reason = "timeout"
    ├── PageLoadTimeout    → result.reason = "timeout"
    ├── RuntimeError("page_unavailable") → result.reason = "page_unavailable"
    └── Exception          → result.reason = "browser_crash"
    │
    ├── result.success=True → break
    ├── reason in NO_RETRY_REASONS → break (без retry)
    │   (page_unavailable, no_form_detected, registration_page_not_found,
    │    max_steps_exceeded, submit_failed, account_exists, proxy_failed,
    │    proxy_blocked, manual_fill_timeout, missing_fields,
    │    username_taken, email_taken, invalid_username, manual_rejected)
    └── attempt += 1 → задержка 2^attempt сек → retry
    │
    ├── result.success=True → _write_result(ok_file) → user_stats["success"] += 1
    └── result.success=False → _write_result(bad_file, reason) → user_stats["failed"] += 1
```

---

## 5. Определение движка — `TemplateManager.detect_engine()`

```
detect_engine(url, page_source)
│
├── tldextract → domain2 (например "donfisher")
├── ищем файл templates/known_forums/{domain2}.json → найден → возвращаем шаблон
│
├── иначе → парсим page_source через BeautifulSoup
│
├── Проверка meta[name=generator] → совпадение с движком → шаблон
├── Проверка html_contains → совпадение → шаблон
├── Проверка url_pattern → совпадение → шаблон
│
├── Проверка по платформам (forum_platforms.json)
│   └── совпадение домена → возвращаем platform_name, шаблон=None
│
└── ничего не найдено → engine="unknown", шаблон=None
```

---

## 6. Регистрация — `RegistrationController.register()`

```
register(account_data, engine_name, template)
│
├── проверка обязательных полей (username, email, password)
│   └── отсутствуют → return reason="missing_fields"
│
├── _navigate_to_registration_page(template)
│   │
│   ├── шаблон есть И reg_url = список с адресами
│   │   └── ЦИКЛ по вариантам URL
│   │       ├── urlparse(current_url) → base_url (схема + домен + путь без файла)
│   │       ├── full_url = base_url / variant_clean
│   │       ├── browser.goto(full_url)
│   │       │   ├── 200 → return True
│   │       │   └── не 200/таймаут → исключение → пробуем следующий вариант
│   │       └── все варианты не сработали → переходим к эвристике
│   │
│   ├── шаблон есть И reg_url = [] ИЛИ шаблона нет → эвристика
│   │   └── find_registration_link() → ищет ссылки с ключевыми словами на странице
│   │       ├── найдена → browser.goto(reg_link)
│   │       │   ├── 200 → return True
│   │       │   └── RuntimeError → logger.debug → падаем дальше
│   │       └── не найдена → падаем дальше
│   │
│   ├── проверка текущего URL на ключевые слова (register/signup/регистр)
│   │   └── совпадение → return True (уже на странице регистрации)
│   │
│   └── return False → raise RuntimeError("registration_page_not_found")
│       └── перехватывается в register() → return reason="registration_page_not_found"
│
├── SelectorFinder.analyze_current_page(template)
│   (см. раздел 7)
│   └── пустой список → return reason="no_form_detected"
│
└── ЦИКЛ по шагам (max_steps=5)
    │
    ├── берём current_block из all_blocks[current_block_index]
    │   └── индекс >= len → return reason="max_steps_exceeded"
    │
    ├── _get_selectors_for_block(template, block)
    │   ├── для каждого стандартного поля (username, email, password, ...)
    │   │   ├── есть в блоке И совпадает с шаблоном → source=template
    │   │   ├── есть в блоке, в шаблоне нет → source=common_fields/manual
    │   │   └── нет в блоке → DOM-проверка шаблонного селектора
    │   │       ├── найден в DOM → source=template
    │   │       └── не найден → пропускаем
    │   └── custom_fields берём из блока напрямую
    │
    ├── _make_block_snapshot(selectors) → снимок для сравнения после submit
    │
    ├── _fill_fields(selectors, account_data, template, engine_name)
    │   (см. раздел 8)
    │   ├── ok=False, reason="submit_failed" → current_block_index += 1 → continue
    │   └── ok=False, reason="manual_fill_timeout" → return reason="manual_fill_timeout"
    │
    ├── asyncio.sleep(2)
    │
    ├── _check_block_changed(snapshot, form_selector, template)
    │   ├── analyze_current_page() повторно
    │   ├── блок исчез → changed=True
    │   ├── снимок изменился → changed=True
    │   └── снимок не изменился → changed=False
    │
    ├── changed=True И есть поля → сохраняем блок в шаблон → продолжаем регистрацию
    ├── changed=True И нет полей → проверяем индикаторы успеха/ошибки
    └── changed=False → current_block_index += 1 → continue
```

---

## 7. Анализ страницы — `SelectorFinder.analyze_current_page()`

```
analyze_current_page(template)
│
├── find_registration_form(template)
│   │
│   ├── page.query("form") → все формы на странице
│   │   └── фильтрация по action/name/id (login, search, signin, ...)
│   │
│   ├── для каждой формы-кандидата
│   │   ├── собираем visible_inputs, visible_buttons, visible_checkboxes
│   │   ├── фильтр: 100% полей нежелательные → пропускаем
│   │   ├── фильтр: combined_block содержит skip-ключевые слова → пропускаем
│   │   └── подсчёт score:
│   │       ├── +1 за каждое поле ввода
│   │       ├── +3 за каждый password
│   │       ├── +2 за наличие кнопки submit
│   │       ├── +1 за каждый чекбокс
│   │       ├── +10 за каждый элемент совпадающий с шаблоном
│   │       ├── +5 за username/email/password ключевые слова
│   │       ├── +3 за agree ключевые слова
│   │       ├── -20 за username без email (форма логина?)
│   │       └── -20 за email без username (форма логина?)
│   │
│   └── сортировка по score (лучший первый)
│
├── detect_captcha()
│   ├── проверяем CAPTCHA_SELECTORS (recaptcha, hcaptcha, turnstile, ...)
│   ├── найдена → определяем тип, site_key, invisible
│   └── не найдена → None
│
└── для каждого блока → identify_fields(form_element)
    ├── определяем submit_button (type=submit или по submit_keywords)
    ├── для каждого input/textarea/select:
    │   ├── hidden/submit/button → пропускаем
    │   ├── невидимый → пропускаем
    │   ├── type=password → password или confirm_password
    │   ├── type=checkbox
    │   │   ├── checkbox_skip_keywords → пропускаем
    │   │   ├── agree_keywords → agree_checkbox
    │   │   └── остальные → custom_fields
    │   ├── email или email_keywords → email или confirm_email
    │   ├── username_keywords → username
    │   └── остальные → custom_fields (с определением типа по known_field_types)
    └── возвращает dict с полями + captcha_indicator
```

---

## 8. Заполнение полей — `_fill_fields()`

```
_fill_fields(selectors, account_data, template, engine_name)
│
├── ШАГ 1: стандартные поля ввода
│   (username, email, confirm_email, password, confirm_password)
│   │
│   └── для каждого поля
│       ├── селектор не найден → пропускаем
│       └── _try_fill_element(selector, value, field_name)
│           ├── элемент не найден → "not_found"
│           ├── tag=select → _try_fill_select() → выбор опции по тексту
│           ├── current_val длиннее 3 символов → "already_filled" → пропускаем
│           ├── element.click() → установка фокуса  ← исправлено в этой сессии
│           ├── human_type() → посимвольный ввод
│           └── "filled" → обновляем шаблон
│           │
│           └── все селекторы не сработали → _ask_manual_input()
│               ├── пользователь ввёл → повторная попытка заполнения
│               └── пропустил → skipped_fields
│
├── ШАГ 1 (продолжение): custom_fields
│   └── для каждого custom поля
│       ├── one_time_field_keywords → запрашиваем ручной ввод
│       ├── значение из профиля аккаунта → _try_fill_element()
│       └── не найдено → _ask_manual_input()
│
├── ШАГ 2: чекбокс согласия
│   ├── agree_selector не найден → пропускаем
│   ├── элемент не найден в DOM → skipped
│   ├── el_type не в {checkbox, radio, text, ...} → это кнопка → пропускаем
│   │   (защита от input[type=submit] как agree на phpBB)  ← исправлено в этой сессии
│   ├── уже отмечен → пропускаем клик
│   └── кликаем → filled_fields.append("agree_checkbox")
│       └── обновляем шаблон (agree_step.checkboxes)
│
├── ШАГ 3: капча
│   ├── captcha_indicator = None → "Капча не обнаружена — продолжаем"
│   └── captcha_indicator есть
│       ├── captcha_helper.solve_captcha() → цепочка провайдеров
│       │   ├── провайдер решил → токен внедрён в DOM → продолжаем
│       │   └── все провайдеры исчерпаны → ручной режим
│       └── ручной режим → browser.wait_for_captcha_solved()
│           └── таймаут → return reason="captcha_timeout"
│
└── ШАГ 4: кнопка submit
    └── _handle_submit(selectors, form_selector, template, engine_name)
        ├── submit_selector найден → scroll → click → return True
        ├── не найден → TimeoutError → запрашиваем ручное нажатие
        │   ├── подтверждено → return True
        │   └── таймаут/отказ → return False → reason="submit_failed"
        └── обновляем шаблон (agree_step.submit_button)
```

---

## 9. Капча — `CaptchaExtensionHelper.solve_captcha()`

```
solve_captcha(captcha_type, site_key, page_url)
│
├── get_provider_chain(captcha_type)
│   └── нет провайдеров → return None
│
└── ЦИКЛ по провайдерам (по приоритету/цене)
    ├── первое использование → snapshot_balance()
    ├── смена типа капчи → recalculate_cost()
    ├── provider.solve(captcha_type, site_key, page_url)
    │   ├── успех → _inject_token() → stats_callback() → return token
    │   ├── APIKeyError/NoBalanceError → исключаем провайдера → следующий
    │   └── Exception → stats_callback(success=False) → следующий
    └── все исчерпаны → return None
```

---

## 10. Проверка результата — `_check_result()`

```
_check_result(template, username_was_filled, engine_name)
│
├── Вариант А: проверяем password-поля вне формы логина через JS
│   ├── есть password-поля → продолжаем (False)
│   └── нет password-полей → успех (True)
│
├── Вариант Б: проверяем любые поля регистрации вне формы логина
│   ├── есть дополнительные поля → продолжаем (False)
│   └── нет полей → успех (True)
│
├── Правило 3: success_indicators в HTML → успех
├── Правило 4: error_indicators в HTML
│   ├── username_taken → reason="username_taken"
│   ├── email_taken → reason="email_taken"
│   └── прочие ошибки → reason="registration_failed"
│
└── ничего не определено → reason="no_indicators" (не фатально)
```

---

## 11. Запись результатов

```
Успех:
├── _write_result(ok_file, forum_url, [timestamp])
│   → results_ok_{username}.txt: "https://forum.ru 2026-03-19T20:00:00"
├── user_stats["success"] += 1
└── account_manager.update_account_status(username) → last_attempt в accounts.json

Неудача:
├── _write_result(bad_file, forum_url, [reason, timestamp])
│   → results_bad_{username}.txt: "https://forum.ru page_unavailable 2026-03-19T20:00:00"
├── user_stats["failed"] += 1
└── account_manager.update_account_status(username, reason)
```

---

## 12. Завершение сессии

```
orchestrator.run() завершён
│
├── _generate_final_report()
│   ├── читает все results_ok_*.txt и results_bad_*.txt
│   └── выводит таблицу: форумов, пользователей, успехов, неудач, капч
│
└── Graceful shutdown (Ctrl+C → orchestrator.shutdown())
    ├── _shutdown_event.set() → все циклы форумов останавливаются
    ├── browser.stop() для всех активных браузеров
    └── _generate_final_report() если ещё не выведен
```

---

## 13. Ключевые конфигурационные параметры (`config/settings.py`)

| Параметр | Описание |
|---|---|
| `MAX_CONCURRENT_USERS` | Параллельных пользователей |
| `MAX_REGISTRATION_RETRIES` | Попыток на форум (default: 3) |
| `TAB_PER_REGISTRATION` | Новая вкладка на каждый форум |
| `PAGE_LOAD_WAIT` | Пауза после загрузки страницы |
| `TAB_TIMEOUT_SECONDS` | Таймаут всей регистрации |
| `NO_RETRY_REASONS` | Причины без retry (fatal errors) |
| `SHOW_BROWSER_WINDOWS` | Показывать браузер |
| `RESULTS_DIR` | Директория результатов |
| `FORUMS_SOURCE_FILE` | Список форумов |

---

## 14. Изменения внесённые в сессии 2026-03-19

| # | Файл | Изменение |
|---|---|---|
| 1 | `browser_controller.py` | `human_type()` — добавлен `element.click()` перед вводом для установки фокуса |
| 2 | `config/settings.py` | `NO_RETRY_REASONS` — добавлены `max_steps_exceeded`, `submit_failed` |
| 3 | `main_orchestrator.py` | `_get_resume_index()` — пропуск форумов из bad-списка при старте |
| 4 | `registration_controller.py` | `_navigate_to_registration_page()` — обработка списка URL из шаблона с правильным base_url через urlparse |
| 5 | `registration_controller.py` | `_navigate_to_registration_page()` — удалён лишний повторный goto после цикла вариантов |
| 6 | `registration_controller.py` | `_navigate_to_registration_page()` — исправлен комментарий логики эвристики |
| 7 | `registration_controller.py` | `_fill_fields()` шаг 2 — защита от `input[type=submit]` как agree_checkbox (phpBB) |
| 8 | `registration_controller.py` | `_try_fill_element()` — временные отладочные логи убраны |
