# Задача 3: Ложное срабатывание для служебных полей капчи (g-recaptcha-response)

## 📋 Описание проблемы

**Симптом:** Запрос ручного ввода для служебного поля reCAPTCHA, хотя капча решается нормально через расширение.

**Пример из лога:**
```
ТРЕБУЕТСЯ РУЧНОЙ ВВОД
Поле:     g-recaptcha-response (#g-recaptcha-response)
Задание:  Введите значение для поля 'g-recaptcha-response'
Нажмите Enter без ввода чтобы пропустить поле.
============================================================
>>>
[32m15:54:12  [0m |   [1mINFO      [0m | Доп. поле 'g-recaptcha-response' пропущено оператором — продолжаем
```

**Корневая причина:** `SelectorFinder.identify_fields` определяет `g-recaptcha-response` как `custom_field` (нет совпадения с keywords). Поле помечается как одноразовое (keyword `"response"` попадает в `one_time_field_keywords`) → для `custom_fields` с `one_time_field=True` всегда запрашивается ручной ввод.

**Реальность:** Это служебное поле reCAPTCHA — заполняется **автоматически токеном** после решения капчи через расширение. Ручной ввод не требуется.

**Риски:**
- Лишний запрос оператора на каждой форме с капчей
- Путаница — оператор не понимает что вводить
- Код полагается на ручной пропуск вместо явной логики

---

## 📄 Примеры из HTML (XenForo)

**Служебное поле reCAPTCHA:**
```html
<noscript>
    <textarea id="g-recaptcha-response" 
              name="g-recaptcha-response" 
              class="g-recaptcha-response" 
              style="width: 250px; height: 80px; 
                     border: 1px solid #c1c1c1; 
                     margin: 0px; padding: 0px; resize: none;" 
              value=""></textarea>
</noscript>
```

**Общий признак служебных полей капч:**
- `id` содержит `"response"` (g-recaptcha-response, h-captcha-response, etc.)
- `name` содержит `"response"`
- `class` содержит `"g-recaptcha-response"` или `"h-captcha-response"`
- Поле находится внутри `<noscript>` или скрыто через CSS

---

## 📊 Лог ошибки (фрагмент)

```log
2026-03-24 15:53:42 | INFO     | controllers.registration_controller:1382 | 
    Заполняем дополнительное поле: g-recaptcha-response (#g-recaptcha-response)

2026-03-24 15:53:42 | DEBUG    | controllers.registration_controller:1415 | 
    Поле 'g-recaptcha-response' одноразовое — запрашиваем ручной ввод

2026-03-24 15:53:42 | WARNING  | controllers.registration_controller:1501 | 
    Значение для 'g-recaptcha-response' не найдено — запрашиваем ручной ввод

2026-03-24 15:54:12 | INFO     | controllers.registration_controller:1520 | 
    Доп. поле 'g-recaptcha-response' пропущено оператором — продолжаем
```

---

## ✅ Согласованный план решений (2 пункта)

### Пункт 1: Добавить `service_field_keywords` в `common_fields.json`

**Файл:** `src/templates/common_fields.json`

**Что делаем:**
- Добавляем новый ключ `service_field_keywords` с массивом ключевых слов
- **Используем универсальное ключевое слово `"response"`** — оно покрывает все провайдеры капч (g-recaptcha-response, h-captcha-response, cf-turnstile-response, etc.)
- **Не перечисляем каждый провайдер отдельно** — слово `"response"` достаточно специфично для служебных полей капч
- **Все варианты выносим в JSON** — никаких хардкодов в коде

**Пример содержимого:**
```json
"service_field_keywords": [
    "response"
]
```

**⚠️ Важно:** Только добавления в JSON недостаточно — нужно изменить код в `selector_finder.py` для чтения и использования этого ключа.

**Преимущества:**
- ✅ Работает для всех провайдеров капч (reCAPTCHA, hCaptcha, Turnstile, etc.)
- ✅ Легко добавлять новые keywords без изменения кода
- ✅ Соответствует архитектуре проекта (все keywords в `common_fields.json`)
- ✅ Минимальное количество keywords — только универсальные

---

### Пункт 2: Добавить проверку service fields в `selector_finder.py` → `identify_fields`

**Файл:** `src/selector_finder.py`

**Что делаем:**
- Загружаем `service_field_keywords` из `common_fields.json`
- Проверяем 3 условия перед добавлением в `custom_fields`:
  1. `id` элемента содержит любое слово из `service_field_keywords`
  2. `name` элемента содержит любое слово из `service_field_keywords`
  3. `class` элемента содержит любое слово из `service_field_keywords`

**Логика проверки:**
```python
# Загружаем service_field_keywords из common_fields
service_field_keywords = [k.lower() for k in self.common_fields.get("service_field_keywords", [])]

# Перед добавлением в custom_fields (после проверки type="hidden")
combined = f"{name} {el_id} {attrs.get('class', '').lower()} "

is_service_field = any(kw in combined for kw in service_field_keywords)

if is_service_field:
    logger.debug(f"Служебное поле обнаружено: {selector} — пропускаем")
    continue  # Не добавляем в custom_fields
```

**Место вставки:** В `identify_fields()`, после проверки на скрытые поля (`type="hidden"`, `display:none`), перед классификацией в `custom_fields`.

**Преимущества:**
- ✅ Нет хардкодов — все keywords из JSON
- ✅ Работает для всех провайдеров капч (универсальное слово "response")
- ✅ Поле не попадает в `custom_fields` → нет запроса ручного ввода
- ✅ Легко расширять — добавить новые keywords только в JSON

---

## 📁 Затронутые файлы

| Файл | Изменение |
|------|-----------|
| `src/templates/common_fields.json` | Добавить ключ `service_field_keywords` со значением `["response"]` |
| `src/selector_finder.py` | Добавить проверку service fields в `identify_fields` (ПЕРЕД `custom_fields`) + загрузка из JSON |

---

## ⏸️ Запрос подтверждения

**Прежде чем применять изменения:**

1. **Верно ли описана проблема?** Поле `g-recaptcha-response` определяется как `custom_field` → запрашивается ручной ввод, хотя заполняется автоматически.
2. **Согласны ли вы с планом?** 2 пункта: `service_field_keywords` в JSON (только `"response"`) + проверка в `selector_finder.py`.
3. **Достаточно ли ключевого слова `"response"`?** Или нужно добавить дополнительные keywords для других провайдеров?

**После подтверждения** — я предоставлю код изменений в формате diff/patch с контекстом.

## Процесс работы, Требования к исполнению и Формат отчёта

Строго соблюдать указания и требования из основного промта system_prompt, если явно не указано другое.
Если ты забыл правила или не понял, уточни у пользователя.

---

## ✅ Критерии приёмки

соотвествовать критериям приемки из system_prompt
