# Project Context: BuxarForumRegBot

## 1. Краткое описание проекта

**BuxarForumRegBot** — асинхронная система для массовой автоматической регистрации аккаунтов на форумах. Система работает в связке с реальным браузером Chrome через библиотеку **Pydoll**, что обеспечивает максимальную совместимость с защитами форумов и реалистичное поведение.

Основная ценность проекта — способность работать как с известными движками форумов через шаблоны, так и с неизвестными форумами через эвристический анализ HTML-форм. Система поддерживает параллельную работу нескольких пользователей, автоматическое решение капч через цепочку провайдеров с fallback, сохранение состояния и восстановление после сбоев.

## 2. Структура проекта (ключевые директории)

├── docs
│   ├── 01-setup-and-config.md
│   ├── 02-browser-controller.md
│   ├── 03-template-manager-and-selector-finder.md
│   ├── 04-registration-controller.md
│   ├── 05-datamanagers.md
│   ├── 06-CaptchaExtensionHelper.md
│   ├── 07-MainOrchestrator.md
│   ├── project_context.md
│   └── system_prompt.md
├── logs
├── src
│   ├── config
│   │   ├── logging.conf
│   │   └── settings.py
│   ├── controllers
│   │   ├── browser_controller.py
│   │   └── registration_controller.py
│   ├── data
│   │   ├── profiles
│   │   ├── screenshots
│   │   ├── accounts.json
│   │   ├── proxies.txt
│   │   ├── results_new.txt
│   ├── extensions
│   │   └── nopecha_solver
│   │       ├── config.json
│   │       └── manifest.json
│   ├── logs
│   ├── orchestrator
│   │   └── main_orchestrator.py
│   ├── templates
│   │   ├── known_forums
│   │   │   ├── forum2x2.json
│   │   │   ├── forum2x2.ru.json
│   │   │   ├── phpbb.json
│   │   │   ├── smf.json
│   │   │   ├── vbulletin.json
│   │   │   └── xenforo.json
│   │   ├── common_fields.json
│   │   ├── default.json
│   │   ├── forum_engines.json
│   │   ├── forum_platforms.json
│   │   └── heuristic_rules.json
│   ├── utils
│   │   ├── captcha_providers
│   │   │   ├── implementations
│   │   │   │   ├── az_captcha.py
│   │   │   │   ├── cap_solver.py
│   │   │   │   ├── manual.py
│   │   │   │   ├── _stubs.py
│   │   │   │   └── two_captcha.py
│   │   │   ├── base.py
│   │   │   └── registry.py
│   │   ├── account_manager.py
│   │   ├── captcha_helper.py
│   │   └── proxy_manager.py
│   ├── main.py
│   ├── selector_finder.py
│   ├── selector_finder.py_old
│   └── template_manager.py
├── tests
│   ├── integration
│   │   └── test_registration_flow.py
│   ├── check_browser_controller.py
│   ├── check_setup.py
│   ├── conftest.py
│   ├── test_captcha_helper.py
│   ├── test_datamanagers.py
│   ├── test_integration.py
│   ├── test_main_orchestrator.py
│   ├── test_registration_controller.py
│   ├── test_selector_finder.py
│   └── test_template_manager.py
├── pytest.ini
├── README_ENG.md
├── README.md
└── requirements.txt

