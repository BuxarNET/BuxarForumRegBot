# Задача 4: Путаница Username vs Name (XenForo) — заполняется имя вместо логина

## 📋 Описание проблемы

**Симптом:** В браузере ошибка "Имя должно быть уникальное" — поле "Имя" на форуме это на самом деле Логин/Username, но бот заполняет его как `firstname` (имя из профиля "Виталий").

**Пример из лога:**
```
2026-03-24 15:53:09 | DEBUG | selector_finder:781 | 
    Определён username: #ctrl_username | 'Имя:'

2026-03-24 15:53:09 | DEBUG | selector_finder:820 | 
    custom_fields: firstname (#ctrl_f4a0723511183a4e7d29664a2a6fb711) | 'Имя:'

2026-03-24 15:53:19 | INFO | controllers.registration_controller:1382 | 
    Заполняем дополнительное поле: firstname (#ctrl_f4a0723511183a4e7d29664a2a6fb711)

2026-03-24 15:53:21 | DEBUG | controllers.browser_controller:279 | 
    Введён текст: Виталий
```

**Корневая причина:** На XenForo есть ДВА поля с меткой "Имя:":
1. `#ctrl_username` (name="username") — **honeypot**, определяется как `username` → пропускается (zero_size)
2. `#ctrl_f583c3099b582fc7c79c2e9458d8b277` (name="[hash]") — **реальное поле Username**, определяется как `firstname` → заполняется "Виталий"

**Результат:** Форум ожидает уникальный логин → ошибка "Имя должно быть уникальное".

---

## 📄 Примеры из HTML (XenForo)

**Honeypot поле (НЕ заполнять):**
```html
<dl class="ctrlUnit limited">
    <dt><label for="ctrl_username">Имя:</label></dt>
    <dd>
        <input type="text" name="username" id="ctrl_username" />
        <p class="explain">Пожалуйста, оставьте это поле пустым.</p>
    </dd>
</dl>
```

**Реальное поле Username (заполнять логином):**
```html
<dl class="ctrlUnit">
    <dt>
        <label for="ctrl_f583c3099b582fc7c79c2e9458d8b277">Имя:</label>
        <dfn>Обязательное поле</dfn>
    </dt>
    <dd>
        <input type="text" name="f583c3099b582fc7c79c2e9458d8b277" 
               id="ctrl_f583c3099b582fc7c79c2e9458d8b277" 
               autofocus="true" autocomplete="off" required />
        <p class="explain">Это имя будет отображаться в Ваших сообщениях...</p>
    </dd>
</dl>
```

**Общий признак реального Username на XenForo:**
- Метка "Имя:" (совпадает с `firstname_keywords`)
- **Атрибут `required` присутствует** ← ключевое отличие
- Нет класса `limited` у родителя
- Нет текста "оставьте это поле пустым"

---

## 📊 Лог ошибки (фрагмент)

```log
2026-03-24 15:53:09 | DEBUG | selector_finder:781 | 
    Определён username: #ctrl_username | 'Имя:'

2026-03-24 15:53:09 | DEBUG | selector_finder:820 | 
    custom_fields: firstname (#ctrl_f4a0723511183a4e7d29664a2a6fb711) | 'Имя:'

2026-03-24 15:53:19 | INFO | controllers.registration_controller:1382 | 
    Заполняем дополнительное поле: firstname (#ctrl_f4a0723511183a4e7d29664a2a6fb711)

2026-03-24 15:53:21 | DEBUG | controllers.browser_controller:279 | 
    Введён текст: Виталий

[В браузере ошибка] "Имя должно быть уникальное"
```

---

## ✅ Согласованный план решений (2 пункта)

### Пункт 1: Добавить "имя" в `username_keywords` (ВЫПОЛНЕНО ПОЛЬЗОВАТЕЛЕМ)

**Файл:** `src/templates/common_fields.json`

**Статус:** ✅ **Уже добавлено пользователем** как временное решение

**Пример содержимого:**
```json
"username_keywords": [
    "user", "login", "nick", "username", "handle", "псевдоним",
    "логин", "никнейм", "пользователь", "юзер", "ник",
    "имя"
]
```

**⚠️ Проблема без проверки `required`:**
- Просто добавить "имя" недостаточно — на других форумах "Имя:" может быть настоящим `firstname`
- Без проверки `required` — все поля "Имя:" будут определяться как `username`
- **Нужна дополнительная проверка атрибута `required`** для точного определения

---

### Пункт 2: Добавить проверку `required` в `selector_finder.py` → `identify_fields`

**Файл:** `src/selector_finder.py`

**Что делаем:**
- При проверке `username_keywords` с ключевым словом `"имя"` — требовать атрибут `required`
- Это отличит реальное поле Username (有 `required`) от поля FirstName (нет `required`)
- **Только для ключевого слова "имя"** — остальные username_keywords работают как раньше

**Логика проверки:**
```python
# Username поля (строка ~775-785)
if any(kw in combined for kw in username_keywords):
    # Специальная проверка для "имя" — требуется атрибут required
    if "имя" in combined:
        # Проверяем атрибут required у элемента
        is_required = element.get_attribute("required") is not None
        if not is_required:
            # Это не username, пропускаем для firstname (будет определён ниже)
            logger.debug(f"Поле 'Имя:' без required — пропускаем для username: {selector}")
        else:
            # Это username с required — определяем как username
            if "username" not in result:
                result["username"] = selector
                if display_text:
                    result["username_label"] = display_text
                logger.debug(f"Определён username (с required): {selector} | '{display_text}'")
            continue
    else:
        # Обычная проверка username (без "имя")
        if "username" not in result:
            result["username"] = selector
            if display_text:
                result["username_label"] = display_text
            logger.debug(f"Определён username: {selector} | '{display_text}'")
        continue
```

**Место вставки:** В `identify_fields()`, в блоке проверки username_keywords (строки ~775-785), перед классификацией в `firstname`.

**Преимущества:**
- ✅ Работает на XenForo — поле "Имя:" с `required` определяется как `username`
- ✅ Не ломает другие форумы — поле "Имя:" без `required` остаётся `firstname`
- ✅ Минимальное изменение — только для ключевого слова "имя"
- ✅ Соответствует временному решению пользователя — "имя" уже в `username_keywords`

---

## 📁 Затронутые файлы

| Файл | Изменение | Статус |
|------|-----------|--------|
| `src/templates/common_fields.json` | Добавить "имя" в `username_keywords` | ✅ Выполнено пользователем |
| `src/selector_finder.py` | Добавить проверку `required` для "имя" в `identify_fields` | ⏳ Требуется |

---

## ⏸️ Запрос подтверждения

**Прежде чем применять изменения:**

1. **Верно ли описана проблема?** Honeypot `#ctrl_username` определяется как `username` → пропускается, реальное поле `#ctrl_f583c3099b582fc7c79c2e9458d8b277` определяется как `firstname` → заполняется "Виталий" вместо логина.
2. **Согласны ли вы с планом?** 2 пункта: "имя" в `username_keywords` (уже добавлено) + проверка `required` в коде (только для "имя").
3. **Нужно ли учесть другие форумы?** Проверка `required` только для "имя" — остальные keywords работают как раньше, не ломает phpBB/SMF где "Имя:" = `firstname`.

**После подтверждения** — я предоставлю код изменений в формате diff/patch с контекстом.

## Процесс работы, Требования к исполнению и Формат отчёта

Строго соблюдать указания и требования из основного промта system_prompt, если явно не указано другое.
Если ты забыл правила или не понял, уточни у пользователя.

---

## ✅ Критерии приёмки

соотвествовать критериям приемки из system_prompt
