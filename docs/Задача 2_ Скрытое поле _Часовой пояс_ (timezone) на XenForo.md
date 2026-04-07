# Задача 2: Скрытое поле "Часовой пояс" (timezone) на XenForo

## 📋 Описание проблемы

**Симптом:** Запрос ручного ввода для поля `timezone` с селектором `#ctrl_bba1ce9cf69acf46cec9b2ce030a63df`, хотя поле не видно на странице.

**Пример из лога:**
```
ТРЕБУЕТСЯ РУЧНОЙ ВВОД
Поле:     timezone — «Часовой пояс:»
Селектор: #ctrl_bba1ce9cf69acf46cec9b2ce030a63df
Задание:  Выберите значение для поля 'timezone'
Нажмите Enter без ввода чтобы пропустить поле.
```

**Корневая причина:** Поле "Часовой пояс" скрыто через `style="display: none"` у родительского элемента `<dl>`. `SelectorFinder.identify_fields` добавляет поле в `custom_fields`, а JS-проверка видимости в `_try_fill_element` возвращает `zero_size` — но для `custom_fields` всё равно запрашивается ручной ввод.

**Текущее поведение:**
- Поле определяется как `custom_fields` → `timezone`
- При заполнении `_try_fill_element` возвращает `not_visible (zero_size)`
- Для `custom_fields` это триггерит `_ask_manual_input` → запрос оператору
- Оператор пропускает поле → регистрация продолжается

**Риски:**
- Лишний запрос оператора на каждом форуме с авто-timezone
- На других форумах поле может быть видимым → заполнится некорректно
- Код полагается на ручной пропуск вместо явной логики

---

## 📄 Примеры из HTML (XenForo)

**Скрытое поле Timezone:**
```html
<dl class="ctrlUnit" style="display: none">
    <dt><label for="ctrl_f68c3b1d9d1a94fdcda53af27ffed6e9">Часовой пояс:</label></dt>
    <dd>
        <select name="f68c3b1d9d1a94fdcda53af27ffed6e9" 
                class="textCtrl AutoTimeZone OptOut" 
                id="ctrl_f68c3b1d9d1a94fdcda53af27ffed6e9">
            <option value="Europe/Moscow" selected="selected">
                (UTC+03:00) Москва, Санкт-Петербург, Волгоград
            </option>
            ...
        </select>
    </dd>
</dl>
```

**Общий признак авто-полей XenForo:**
- Родительский `<dl>` имеет `style="display: none"` **ИЛИ** класс `OptOut`
- Элемент имеет класс `AutoTimeZone` и/или `OptOut`
- Нет атрибута `required`
- Есть значение по умолчанию (`selected="selected"`)

---

## 📊 Лог ошибки (фрагмент)

```log
2026-03-24 15:53:24 | DEBUG | controllers.registration_controller:1732 | 
    Поле 'timezone' недоступно (zero_size) — пропускаем: #ctrl_bba1ce9cf69acf46cec9b2ce030a63df

2026-03-24 15:53:24 | WARNING | controllers.registration_controller:1445 | 
    Все варианты профиля не подошли для 'timezone' — запрашиваем ручной ввод

2026-03-24 15:53:42 | DEBUG | controllers.registration_controller:1732 | 
    Поле 'timezone' недоступно (zero_size) — пропускаем: #ctrl_bba1ce9cf69acf46cec9b2ce030a63df
```

---

## ✅ Согласованный план решений (2 пункта)

### Пункт 1: Добавить `skip_field_classes` в `common_fields.json` + изменение кода

**Файл:** `src/templates/common_fields.json` + `src/selector_finder.py`

**Что делаем:**
1. **Добавляем новый ключ `skip_field_classes` в JSON** с массивом классов
2. **⚠️ Важно:** Только добавления в JSON недостаточно — нужно изменить код в `selector_finder.py` для чтения и использования этого ключа
3. **Все варианты классов выносим в JSON** — никаких хардкодов в коде
4. **Учитываем XenForo-специфичные классы** для авто-полей

**Пример содержимого JSON:**
```json
"skip_field_classes": [
    "OptOut",
    "AutoTimeZone",
    "AutoValidator",
    "Disabler",
    "AutoComplete"
]
```

**Необходимые изменения в коде (`selector_finder.py` → `identify_fields`):**
```python
# Загружаем skip_field_classes из common_fields (НОВОЕ)
skip_field_classes = [k.lower() for k in self.common_fields.get("skip_field_classes", [])]

# Проверка перед добавлением в custom_fields (НОВОЕ)
parent_dl = await self._get_parent_dl(element)
if parent_dl:
    parent_style = (parent_dl.get_attribute("style") or "").lower()
    parent_classes = (parent_dl.get_attribute("class") or "").lower()
    element_classes = (attrs.get("class") or "").lower()
    
    # Проверка style="display: none" у родителя
    if "display: none" in parent_style:
        logger.debug(f"Пропускаем скрытое поле (родитель): {selector}")
        continue
    
    # Проверка skip-классов у родителя или элемента
    if any(cls in parent_classes or cls in element_classes for cls in skip_field_classes):
        logger.debug(f"Пропускаем поле со skip-классом: {selector}")
        continue
```

**Преимущества:**
- ✅ Работает на любых форумах (не только XenForo)
- ✅ Легко добавлять новые классы без изменения кода (только JSON)
- ✅ Соответствует архитектуре проекта (все keywords в `common_fields.json`)
- ✅ Код читает настройки из JSON — гибкая конфигурация

---

### Пункт 2: Комбинированный подход — проверка в 2 уровнях

**Файл:** `src/selector_finder.py` + `src/controllers/registration_controller.py`

**Уровень 1: Ранняя фильтрация в `identify_fields` (ПЕРЕД добавлением в `custom_fields`)**

**Что делаем:**
- Загружаем `skip_field_classes` из `common_fields.json`
- Проверяем 3 условия перед классификацией поля:
  1. `style="display: none"` у родительского `<dl>`
  2. Класс родителя содержит `"limited"` (honeypot)
  3. Класс элемента содержит любое слово из `skip_field_classes`

**Логика проверки:**
```python
# Загружаем skip_field_classes из common_fields
skip_field_classes = [k.lower() for k in self.common_fields.get("skip_field_classes", [])]

# Для каждого поля — проверяем родителя и классы
parent_dl = await self._get_parent_dl(element)
if parent_dl:
    parent_style = (parent_dl.get_attribute("style") or "").lower()
    parent_classes = (parent_dl.get_attribute("class") or "").lower()
    element_classes = (attrs.get("class") or "").lower()
    
    # Проверка style="display: none" у родителя
    if "display: none" in parent_style:
        logger.debug(f"Пропускаем скрытое поле (родитель): {selector}")
        continue
    
    # Проверка skip-классов у родителя или элемента
    if any(cls in parent_classes or cls in element_classes for cls in skip_field_classes):
        logger.debug(f"Пропускаем поле со skip-классом: {selector}")
        continue
```

**Уровень 2: Страховка в `_try_fill_element` (в `registration_controller.py`)**

**Что делаем:**
- Расширяем JS-проверку видимости для проверки родителя на `display: none`
- Добавляем проверку классов элемента на `skip_field_classes`
- Возвращаем `not_visible` если родитель скрыт или элемент имеет skip-класс

**Логика проверки (JS):**
```javascript
// В существующей проверке видимости добавляем:
var parent = el.parentElement;
while (parent) {
    var parentStyle = window.getComputedStyle(parent);
    if (parentStyle.display === 'none') {
        return JSON.stringify({visible: false, reason: 'parent_display_none'});
    }
    parent = parent.parentElement;
}

// Проверка классов элемента
var skipClasses = ['OptOut', 'AutoTimeZone', 'AutoValidator', 'Disabler'];
var elClasses = el.className || '';
if (skipClasses.some(function(cls) { return elClasses.indexOf(cls) > -1; })) {
    return JSON.stringify({visible: false, reason: 'skip_class'});
}
```

**Преимущества комбинированного подхода:**
- ✅ **Уровень 1:** Поле не попадает в `custom_fields` → нет запроса ручного ввода
- ✅ **Уровень 2:** Страховка от динамических изменений DOM между анализом и заполнением
- ✅ **Защита в глубину:** Если один уровень не сработал — второй перехватит
- ✅ **Нет хардкодов:** Все классы из `common_fields.json`

---

## 📁 Затронутые файлы

| Файл | Изменение |
|------|-----------|
| `src/templates/common_fields.json` | Добавить ключ `skip_field_classes` |
| `src/selector_finder.py` | Добавить проверку в `identify_fields` (ПЕРЕД `custom_fields`) + загрузка из JSON |
| `src/controllers/registration_controller.py` | Расширить JS-проверку в `_try_fill_element` (страховка) |

---

## ⏸️ Запрос подтверждения

**Прежде чем применять изменения:**

1. **Верно ли описана проблема?** Поле timezone скрыто → определяется как custom_fields → запрашивается ручной ввод.
2. **Согласны ли вы с планом?** 2 пункта: JSON + код (чтение skip_field_classes) + комбинированный подход (2 уровня проверки).
3. **Нужно ли добавить другие классы?** Например для других форумов или движков.

**После подтверждения** — я предоставлю код изменений в формате diff/patch с контекстом.

## Процесс работы, Требования к исполнению и Формат отчёта

Строго соблюдать указания и требования из основного промта system_prompt, если явно не указано другое.
Если ты забыл правила или не понял, уточни у пользователя.

---

## ✅ Критерии приёмки

соотвествовать критериям приемки из system_prompt
