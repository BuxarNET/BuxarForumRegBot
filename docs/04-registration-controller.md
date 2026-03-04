# Промт 4: RegistrationController — логика регистрации на форуме

## 1. Цель
Создать класс `RegistrationController`, который реализует полный цикл регистрации на форуме, используя готовые компоненты:
- `BrowserController` — для управления браузером и выполнения действий.
- `TemplateManager` — для получения готовых шаблонов известных форумов.
- `SelectorFinder` — для эвристического поиска полей на неизвестных форумах.

Класс должен:
- Принимать данные учётной записи (логин, email, пароль + опциональные поля), объект браузера и объект текущей страницы.
- Определять способ получения селекторов (сначала шаблон, при отсутствии — эвристика).
- Выполнять последовательность действий: переход на страницу регистрации, заполнение полей, обработка чекбоксов, ожидание решения капчи, отправка формы, проверка результата.
- Возвращать структурированный результат с указанием успеха, причины неудачи и, при ошибке, скриншота страницы.

## 2. Общие требования
- Код на Python 3.12+ с асинхронным синтаксисом.
- В начале файла: `from __future__ import annotations`.
- Использовать библиотеки: `pydoll`, `loguru`, `aiofiles` (для скриншотов), `pathlib`.
- Модуль должен располагаться в `src/controllers/registration_controller.py`.
- Добавить docstrings для всех классов и публичных методов (Google или NumPy style).
- Строгая типизация (использовать `| None`, `list[str]`, `TypedDict` для сложных структур).
- Логирование через `loguru.logger`.
- Покрыть ключевую логику тестами (pytest) с использованием моков.

## 3. Класс `RegistrationController`

### 3.1. Инициализация
```python
from pathlib import Path
from typing import TypedDict, NotRequired

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
    custom_fields: NotRequired[dict[str, str]]

class RegistrationController:
    def __init__(
        self,
        browser_controller,
        template_manager,
        selector_finder,
        page,
        config: dict | None = None
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

        # Индикаторы для проверки результата (из config или дефолтные)
        self.success_indicators = self.config.get("success_indicators", [
            "thank you", "activate", "успешно", "created", "account has been"
        ])
        self.error_indicators = self.config.get("error_indicators", [
            "error", "failed", "invalid", "ошибка", "already taken"
        ])

        self.screenshot_dir = Path("data/screenshots")
        self.screenshot_dir.mkdir(parents=True, exist_ok=True)
```

### 3.2. Основной метод регистрации

#### `async def register(self, account_ AccountData) -> RegistrationResult`
Выполняет регистрацию на текущем форуме (предполагается, что браузер уже открыт на нужном сайте).

**Алгоритм:**

1. **Получение селекторов:**
   - Получить текущий URL: `url = await self.page.current_url`.
   - Получить HTML страницы: `page_source = await self.page.evaluate("document.documentElement.outerHTML")`.
   - Попытаться найти шаблон: `template = await self.template_manager.detect_template(url, page_source)`.
   - Если шаблон найден:
     - Использовать `template["fields"]` как словарь селекторов.
     - Извлечь `form_selector` из `template.get("registration_page", {}).get("form_selector")`.
   - Если шаблон не найден, вызвать эвристику:
     ```python
     selectors = await self.selector_finder.analyze_current_page()
     if selectors is None:
         return {
             "success": False,
             "message": "Registration form not found",
             "reason": "no_form_detected",
             "template_used": None,
             "screenshot": None,
             "form_data": account_data
         }
     # analyze_current_page() возвращает dict с ключами:
     # form_selector, form_element, username, email, password, confirm_password,
     # agree_checkbox, submit_button, captcha_indicator, custom_fields
     form_selector = selectors.get("form_selector")
     ```
   - Если селекторы не получены, вернуть ошибку.

2. **Переход на страницу регистрации:**
   - Использовать безопасное получение ключей:
     ```python
     reg_page = template.get("registration_page", {}) if template else {}
     if reg_page.get("url"):
         await self.browser.goto(url + reg_page["url"])
     ```
   - Иначе оставаться на текущей странице (предполагается, что мы уже на странице регистрации).

3. **Заполнение полей:**
   - Для каждого стандартного поля (`username`, `email`, `password`, `confirm_password`, `agree_checkbox`) проверить наличие селектора в полученном словаре.
   - Если селектор есть, выполнить соответствующее действие:
     - Для текстовых полей: `await self.browser.human_type(selector, value)`.
     - Для `agree_checkbox`: `await self.browser.human_click(selector)` (клик, не ввод текста).
   - Для поля `confirm_password`:
     - **Только если селектор есть в словаре** — заполнить тем же значением, что и `password`.
     - Если селектор отсутствует — пропустить (поле не требуется на этой форме).
   - Для кастомных полей:
     - Взять из `account_data.get("custom_fields", {})`.
     - Для каждого кастомного поля проверить наличие селектора в `selectors.get("custom_fields", [])` или в `template.get("custom_fields", {})`.
     - Если селектор найден, заполнить значением из `account_data`.

4. **Ожидание решения капчи:**
   - Если в шаблоне или результате эвристики есть `captcha_indicator` (селектор элемента капчи), то вызвать `await self.browser.wait_for_captcha_solved()` с учётом настроек (авто/ручной режим).
   - Если капча не решена за таймаут, прервать регистрацию, сделать скриншот, вернуть ошибку.

5. **Отправка формы:**
   - Найти кнопку отправки: `submit_selector = selectors.get("submit_button")`.
   - Если `submit_selector` не найден:
     - Если `form_selector` есть: `form = await self.page.query(form_selector)`, затем `buttons = await form.query_all('button[type="submit"], input[type="submit"]')`.
     - Если `form_selector` нет — вернуть ошибку `"submit_button_not_found"`.
     - Если кнопки найдены, сгенерировать селектор для первой: `submit_selector = self._generate_css_selector(buttons[0])`.
   - Выполнить клик: `await self.browser.human_click(submit_selector)`.

6. **Проверка результата:**
   - Подождать 3-5 секунд для загрузки следующей страницы.
   - Получить новый HTML: `new_page_source = await self.page.evaluate("document.documentElement.outerHTML")`.
   - Использовать индикаторы с приоритетом: шаблон → config → дефолт:
     ```python
     if template:
         success_indicators = template.get("success_indicators", self.success_indicators)
         error_indicators = template.get("error_indicators", self.error_indicators)
     else:
         success_indicators = self.success_indicators
         error_indicators = self.error_indicators
     ```
   - Проверить наличие индикаторов успеха/ошибки (регистронезависимо, через `in`).
   - Если явных индикаторов нет, считать регистрацию успешной, если URL изменился или появилось сообщение об успехе.
   - В случае ошибки сделать скриншот: `_take_screenshot(prefix="error", username=account_data.get("username"))`.

7. **Возврат результата:**
   - Сформировать словарь `RegistrationResult` и вернуть его.

### 3.3. Вспомогательные методы

#### `async def _take_screenshot(self, prefix: str, username: str | None = None) -> str`
- Делает скриншот текущей страницы через `await self.page.screenshot()`.
- Формирует имя файла без двойных подчёркиваний:
  ```python
  timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
  if username:
      filename = f"{prefix}_{username}_{timestamp}.png"
  else:
      filename = f"{prefix}_{timestamp}.png"
  ```
- Сохраняет в `self.screenshot_dir`.
- Возвращает полный путь к файлу.

#### `def _check_success(self, page_source: str, indicators: list[str]) -> bool`
- Проверяет наличие любой из строк `indicators` в `page_source` (регистронезависимо, просто `in`).

#### `def _check_error(self, page_source: str, indicators: list[str]) -> str | None`
- Возвращает первую найденную строку ошибки или `None`.

#### `def _generate_css_selector(self, element) -> str`
- Вспомогательный метод для генерации CSS-селектора из элемента Pydoll (если нужно).
- Приоритет: `id` → `name` → путь через `nth-child`.

### 3.4. Обработка ошибок и повторные попытки
- Внутри `register` предусмотреть `try/except Exception as e` для отлова любых исключений, возникающих при работе с Pydoll.
- При возникновении исключения:
  - Логировать ошибку через `logger.error`.
  - Сделать скриншот: `_take_screenshot(prefix="exception", username=account_data.get("username"))`.
  - Вернуть результат с `success=False`, `reason=f"exception: {type(e).__name__}"`, и путём к скриншоту.
- Для временных сбоев (например, сетевых) можно реализовать повторные попытки, но это вынесено в главный оркестратор.

## 4. Формат данных аккаунтов

Аккаунты загружаются из внешнего файла `data/accounts.json`:

```json
[
    {
        "username": "testuser1",
        "email": "test1@example.com",
        "password": "StrongPass123",
        "proxy_id": 2,
        "custom_fields": {
            "referral": "REF123",
            "city": "Moscow"
        },
        "status": "pending",
        "attempts": 0,
        "last_attempt": null
    },
    {
        "username": "testuser2",
        "email": "test2@example.com",
        "password": "AnotherPass456",
        "proxy_id": 1,
        "custom_fields": {}
        "status": "pending",
        "attempts": 0,
        "last_attempt": null
    }
]
```

**Требования:**
- `username`, `email`, `password` — обязательные поля.
- `custom_fields` — опционально, содержит дополнительные поля для конкретных форумов.
- `RegistrationController` должен заполнять поля из `custom_fields`, если соответствующие селекторы есть в шаблоне или результате эвристики.
- Если в `custom_fields` есть поле, для которого нет селектора — пропустить с логированием на уровне `debug`.

## 5. Тестирование

### 5.1. Unit-тесты (с моками)
- Создать моки для `BrowserController`, `TemplateManager`, `SelectorFinder` и объекта `page`.
- Проверить логику выбора шаблона/эвристики.
- Проверить заполнение полей (моки должны зафиксировать вызовы `human_type` с правильными селекторами).
- Проверить, что `confirm_password` заполняется только при наличии селектора.
- Проверить обработку успеха/ошибки.
- Проверить, что при `analyze_current_page() is None` возвращается корректная ошибка.

### 5.2. Интеграционные тесты (на живых форумах)
- Пометить маркером `@pytest.mark.integration`.
- Использовать тестовые аккаунты на реальных форумах (например, демо-версии phpBB, XenForo).
- Проверить, что регистрация проходит успешно (или возвращается ожидаемая ошибка, например, "username already taken").

### 5.3. Пример тестового сценария
```python
@pytest.mark.asyncio
async def test_registration_success(mocker):
    # mock TemplateManager возвращает шаблон
    # mock SelectorFinder не вызывается
    # mock page и browser проверяют вызовы
    controller = RegistrationController(...)
    result = await controller.register({
        "username": "test",
        "email": "test@test.com",
        "password": "123456"
    })
    assert result["success"] is True
```

## 6. Критерии готовности
- [ ] Класс `RegistrationController` реализован с указанными методами.
- [ ] Интеграция с `BrowserController`, `TemplateManager`, `SelectorFinder` работает.
- [ ] При наличии шаблона используются его селекторы.
- [ ] При отсутствии шаблона вызывается `SelectorFinder.analyze_current_page()` с проверкой на `None`.
- [ ] Заполнение полей выполняется корректно: `confirm_password` только при наличии селектора, `agree_checkbox` — клик.
- [ ] Поиск кнопки отправки унифицирован через `form_selector`.
- [ ] Ожидание капчи работает (авто и ручной режим через `BrowserController`).
- [ ] Проверка результата работает с приоритетом: шаблон → config → дефолт.
- [ ] При ошибке создаётся скриншот с именем `{prefix}_{username}_{timestamp}.png` (без двойных подчёркиваний).
- [ ] Поддержка `custom_fields` из `accounts_to_register.json`.
- [ ] Логирование ведётся через `loguru`.
- [ ] Тесты (unit) проходят успешно.

## 7. Согласованность с Промтом #3

### 7.1. SelectorFinder.analyze_current_page()
- Возвращает `dict | None`.
- При успехе возвращает словарь с ключами:
  ```python
  {
      "form_selector": str,
      "form_element": Element,  # опционально, для отладки
      "username": str | None,
      "email": str | None,
      "password": str | None,
      "confirm_password": str | None,
      "agree_checkbox": str | None,
      "submit_button": str | None,
      "captcha_indicator": str | None,
      "custom_fields": list[dict]
  }
  ```
- `RegistrationController` проверяет возврат на `None` перед использованием.

### 7.2. Формат JSON-шаблона
- Опциональные поля `registration_page`, `success_indicators`, `error_indicators` получаются через `.get()` с дефолтными значениями.
- Пример шаблона из Промта #3 полностью совместим.

### 7.3. Доступ к странице
- И `SelectorFinder`, и `RegistrationController` получают объект `page` напрямую и используют единообразный API: `await page.evaluate()`, `await page.query()`, `await page.query_all()`, `await page.screenshot()`.

После выполнения этого промта можно будет переходить к созданию `DataManagers`, `CaptchaExtensionHelper` и `MainOrchestrator`.
