# Задача 1: Honeypot-поля (ловушки для ботов) на XenForo

## 📋 Описание проблемы

**Симптом:** Запрос ручного ввода для полей с динамическими ID типа `#ctrl_custom_field_ntiym2ex` с меткой "Проверка:", хотя эти поля не должны заполняться.

**Пример из лога:**
```
ТРЕБУЕТСЯ РУЧНОЙ ВВОД
Поле:     80272cf8223d9ab8503aef5c693c8d60[ntiym2ex] — «Проверка:»
Селектор: #ctrl_custom_field_ntiym2ex
Задание:  Введите значение для поля '80272cf8223d9ab8503aef5c693c8d60[ntiym2ex]'
```

**Корневая причина:** В HTML есть поля с текстом `"Пожалуйста, оставьте это поле пустым"` — это анти-бот ловушки (honeypot). `SelectorFinder.identify_fields` классифицирует их как `custom_fields` и запрашивает ручной ввод.

**Текущая защита:** JS-проверка видимости в `_try_fill_element` иногда пропускает honeypot (если поле видно в момент проверки), но это ненадёжно и создаёт риски:
- Оператор может случайно заполнить honeypot → регистрация будет отклонена
- На других форумах honeypot могут быть видимыми → заполнятся автоматически
- Код полагается на случайность вместо явной логики

---

## 📄 Примеры из HTML (XenForo)

**Honeypot поле #1 (Имя):**
```html
<dl class="ctrlUnit limited">
    <dt><label for="ctrl_username">Имя:</label></dt>
    <dd>
        <input type="text" name="username" id="ctrl_username" />
        <p class="explain">Пожалуйста, оставьте это поле пустым.</p>
    </dd>
</dl>
```

**Honeypot поле #2 (E-mail):**
```html
<dl class="ctrlUnit limited">
    <dt><label for="ctrl_60d9f182f6449b4b06ed6bbea7392428">E-mail:</label></dt>
    <dd>
        <input type="email" name="60d9f182f6449b4b06ed6bbea7392428" id="ctrl_60d9f182f6449b4b06ed6bbea7392428" />
        <p class="explain">Пожалуйста, оставьте это поле пустым.</p>
    </dd>
</dl>
```

**Honeypot поле #3 (Custom field "Проверка"):**
```html
<dl class="ctrlUnit customFieldEditndq5ody limited">
    <dt><label for="ctrl_custom_field_ndq5ody">Проверка:</label></dt>
    <dd>
        <input type="text" name="03df4761f3b5dfa25897bee716e04c02[ndq5ody]" id="ctrl_custom_field_ndq5ody" />
        <p class="explain">Пожалуйста, оставьте это поле пустым.</p>
    </dd>
</dl>
```

**Общий признак всех honeypot:**
- Родительский `<dl>` имеет класс **`limited`**
- Внутри есть `<p class="explain">Пожалуйста, оставьте это поле пустым.</p>`

---

## 📊 Лог ошибки (фрагмент)

```log
2026-03-24 15:53:09 | DEBUG | selector_finder:820 | custom_fields: firstname (#ctrl_f4a0723511183a4e7d29664a2a6fb711) | 'Имя:'
2026-03-24 15:53:24 | WARNING | controllers.registration_controller:1445 | Все варианты профиля не подошли для 'timezone' — запрашиваем ручной ввод
2026-03-24 15:53:42 | INFO | controllers.registration_controller:1382 | Заполняем дополнительное поле: g-recaptcha-response (#g-recaptcha-response)
2026-03-24 15:53:42 | DEBUG | controllers.registration_controller:1415 | Поле 'g-recaptcha-response' одноразовое — запрашиваем ручной ввод
2026-03-24 15:54:12 | INFO | controllers.registration_controller:1520 | Доп. поле 'g-recaptcha-response' пропущено оператором — продолжаем
```

---

## ✅ Согласованный план решений (2 пункта)

### Пункт 1: Добавить `honeypot_keywords` в `common_fields.json`

**Файл:** `src/templates/common_fields.json`

**Что делаем:**
- Добавляем новый ключ `honeypot_keywords` с массивом ключевых слов
- **Все варианты текста выносим в JSON — никаких хардкодов в коде**
- Учитываем русские и английские формулировки для работы на разных форумах

**Пример содержимого:**
```json
"honeypot_keywords": [
    "оставьте это поле пустым",
    "оставьте поле пустым",
    "leave this field empty",
    "leave blank",
    "leave this blank",
    "honeypot",
    "спам-защита",
    "не заполнять"
]
```

**Преимущества:**
- ✅ Работает на любых форумах (не только XenForo)
- ✅ Легко добавлять новые keywords без изменения кода
- ✅ Соответствует архитектуре проекта (все keywords в `common_fields.json`)

---

### Пункт 2: Добавить проверку honeypot в `selector_finder.py` → `identify_fields`

**Файл:** `src/selector_finder.py`

**Что делаем:**
- Перед добавлением поля в `custom_fields` — проверяем на honeypot
- **Используем только данные из `common_fields.json`** (ключ `honeypot_keywords`)
- Проверяем 2 условия:
  1. Класс родительского `<dl>` содержит `"limited"` (XenForo-специфично, но безопасно)
  2. Текст в `<p class="explain">` содержит любое слово из `honeypot_keywords`

**Логика проверки:**
```python
# Загружаем honeypot_keywords из common_fields
honeypot_keywords = [k.lower() for k in self.common_fields.get("honeypot_keywords", [])]

# Для каждого поля — проверяем родителя и explain-текст
parent_dl = await self._get_parent_dl(element)
if parent_dl:
    classes = (parent_dl.get_attribute("class") or "").lower()
    explain_text = await self._get_explain_text(parent_dl)
    
    # Проверка по keywords из JSON
    is_honeypot = any(kw in explain_text for kw in honeypot_keywords)
    
    if is_honeypot or "limited" in classes:
        logger.debug(f"Honeypot обнаружен: {selector} — пропускаем")
        continue  # Не добавляем в custom_fields
```

**Преимущества:**
- ✅ Нет хардкодов — все keywords из JSON
- ✅ Работает на любых форумах (проверка по тексту универсальна)
- ✅ Класс `"limited"` — дополнительная защита для XenForo
- ✅ Поля не попадают в `custom_fields` → нет запроса ручного ввода

---

## 📁 Затронутые файлы

| Файл | Изменение |
|------|-----------|
| `src/templates/common_fields.json` | Добавить ключ `honeypot_keywords` |
| `src/selector_finder.py` | Добавить проверку honeypot в `identify_fields` |

---

## ⏸️ Запрос подтверждения

**Прежде чем применять изменения:**

1. **Верно ли описана проблема?** Honeypot определяются как custom_fields → запрашивается ручной ввод.
2. **Согласны ли вы с планом?** 2 пункта: JSON + проверка в коде (без хардкодов).
3. **Нужно ли добавить другие keywords?** Например для других языков или форумов.

**После подтверждения** — я предоставлю код изменений в формате diff/patch с контекстом.

## Процесс работы, Требования к исполнению и Формат отчёта

Строго соблюдать указания и требования из основного промта system_prompt, если явно не указано другое.
Если ты забыл правила или не понял, уточни у пользователя.

---

## ✅ Критерии приёмки

соотвествовать критериям приемки из system_prompt
