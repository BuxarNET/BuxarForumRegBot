from __future__ import annotations

import asyncio
import json
import re
import time
from pathlib import Path
from urllib.parse import urlparse

import aiofiles
from bs4 import BeautifulSoup
from loguru import logger
import tldextract

class TemplateManager:
    """Менеджер шаблонов для известных форумных движков.

    Загружает JSON-шаблоны из директории и предоставляет методы
    для поиска подходящего шаблона по URL и содержимому страницы.
    """

    def __init__(
        self,
        templates_dir: str = "templates/known_forums",
        accounts_file: str = "data/accounts.json"
    ) -> None:
        self.templates_dir = Path(templates_dir)
        self.templates: list[dict] = []
        self._loaded: bool = False
        self._platforms: list[str] = []
        self._platform_link_triggers: list[str] = []
        self._platforms_loaded: bool = False
        self.platforms_file = self.templates_dir.parent / "forum_platforms.json"
        self._engines: list[dict] = []
        self._engines_loaded: bool = False
        self.engines_file = self.templates_dir.parent / "forum_engines.json"
        self.accounts_file = Path(accounts_file)
        self._common_fields: dict = {}
        self._common_fields_loaded: bool = False
        self.common_fields_file = self.templates_dir.parent / "common_fields.json"

    async def _load_templates(self) -> None:
        """Асинхронно загружает все JSON-шаблоны из директории.

        Сканирует `templates_dir`, читает все `.json` файлы через aiofiles.
        При ошибке парсинга конкретного файла логирует предупреждение и продолжает.
        """
        self.templates = []

        if not self.templates_dir.exists():
            logger.warning(f"Директория шаблонов не найдена: {self.templates_dir}")
            self._loaded = True
            return

        json_files = list(self.templates_dir.glob("*.json"))
        if not json_files:
            logger.warning(f"JSON-шаблоны не найдены в: {self.templates_dir}")
            self._loaded = True
            return

        for json_path in json_files:
            try:
                async with aiofiles.open(json_path, encoding="utf-8") as f:
                    content = await f.read()
                template = await asyncio.to_thread(json.loads, content)
                self.templates.append(template)
                logger.debug(f"Загружен шаблон: {json_path.name}")
            except json.JSONDecodeError as e:
                logger.warning(f"Ошибка парсинга шаблона {json_path.name}: {e}")
            except OSError as e:
                logger.warning(f"Ошибка чтения файла {json_path.name}: {e}")

        logger.info(f"Загружено шаблонов: {len(self.templates)}")
        self._loaded = True
        
    async def _load_platforms(self) -> None:
        """Загружает список платформ для форумов из forum_platforms.json."""
        if not self.platforms_file.exists():
            logger.warning(f"Файл платформ не найден: {self.platforms_file}")
            self._platforms_loaded = True
            return
        try:
            async with aiofiles.open(self.platforms_file, encoding="utf-8") as f:
                content = await f.read()
            data = await asyncio.to_thread(json.loads, content)
            self._platforms = data.get("platforms", [])
            self._platform_link_triggers = data.get("platform_link_triggers", [])
            logger.debug(f"Загружено платформ: {len(self._platforms)}, тригеров: {len(self._platform_link_triggers)}")
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"Ошибка загрузки платформ: {e}")
        self._platforms_loaded = True
    
    async def _load_engines(self) -> None:
        """Загружает список движков из forum_engines.json."""
        if not self.engines_file.exists():
            logger.warning(f"Файл движков не найден: {self.engines_file}")
            self._engines_loaded = True
            return
        try:
            async with aiofiles.open(self.engines_file, encoding="utf-8") as f:
                content = await f.read()
            data = await asyncio.to_thread(json.loads, content)
            self._engines = data.get("engines", [])
            logger.debug(f"Загружено движков: {len(self._engines)}")
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"Ошибка загрузки движков: {e}")
        self._engines_loaded = True
        
    async def _load_common_fields(self) -> None:
        """Загружает общие ключевые слова полей из common_fields.json."""
        if not self.common_fields_file.exists():
            logger.warning(f"Файл общих полей не найден: {self.common_fields_file}")
            self._common_fields_loaded = True
            return
        try:
            async with aiofiles.open(self.common_fields_file, encoding="utf-8") as f:
                content = await f.read()
            self._common_fields = await asyncio.to_thread(json.loads, content)
            logger.debug(f"Загружены общие ключевые слова полей: {list(self._common_fields.keys())}")
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"Ошибка загрузки common_fields.json: {e}")
        self._common_fields_loaded = True

    async def get_common_fields(self) -> dict:
        """Возвращает словарь общих ключевых слов полей.
        
        Returns:
            Словарь с ключевыми словами для каждого типа поля.
        """
        if not self._common_fields_loaded:
            await self._load_common_fields()
        return self._common_fields
    
    async def _load_template_from_file(self, engine_name: str) -> dict | None:
        """Читает шаблон из файла {engine_name}.json напрямую.

        Если файл найден и валиден — добавляет шаблон в кэш (без полной перезагрузки).
        Проверяет соответствие поля engine имени файла — логирует warning при расхождении.

        Args:
            engine_name: Название движка = имя файла без расширения.

        Returns:
            Словарь шаблона или None если файл не найден или невалиден.
        """
        file_path = self.templates_dir / f"{engine_name}.json"
        if not file_path.exists():
            return None

        try:
            async with aiofiles.open(file_path, encoding="utf-8") as f:
                content = await f.read()
            template = await asyncio.to_thread(json.loads, content)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"Ошибка чтения шаблона {file_path}: {e}")
            return None

        # Проверяем соответствие engine имени файла
        engine_in_file = template.get("engine", "")
        engine_lower = engine_name.lower()
        if engine_in_file.lower() != engine_lower:
            logger.warning(
                f"Несоответствие engine в файле {file_path}: "
                f"ожидалось '{engine_name}', найдено '{engine_in_file}'"
            )

        # Добавляем в кэш только если engine совпадает
        if self._loaded and engine_in_file.lower() == engine_lower:
            if not any(
                t.get("engine", "").lower() == engine_lower
                for t in self.templates
            ):
                self.templates.append(template)

        return template
    
    async def detect_engine(
        self, url: str, page_source: str
    ) -> tuple[str, dict | None]:
        """Определяет движок/платформу форума и возвращает подходящий шаблон.

        Порядок:
        1. Извлекаем domain2 через tldextract
        2. Ищем файл {domain2}.json напрямую
        3. Ищем платформу в forum_platforms.json + триггер-ссылки
           → если найдена: создаём шаблон через add_template и возвращаем
        4. Определяем движок через forum_engines.json по мета-тегам / HTML
           → ищем файл {engine_name}.json напрямую
           → если не найден: создаём шаблон через add_template и возвращаем
        5. Fallback — возвращаем domain2 без шаблона

        Args:
            url: URL текущей страницы.
            page_source: HTML страницы.

        Returns:
            Кортеж (engine_name, шаблон или None).
        """
        # Этап 1: извлекаем domain2
        try:
            ext = tldextract.extract(url)
            domain2 = f"{ext.domain}.{ext.suffix}" if ext.domain and ext.suffix else ""
        except Exception as e:
            logger.warning(f"Ошибка извлечения домена из {url}: {e}")
            domain2 = ""

        logger.debug(f"Определение движка: url={url}, домен={domain2}")

        # Этап 2: прямой доступ к файлу {domain2}.json
        if domain2:
            template = await self._load_template_from_file(domain2)
            if template is not None:
                logger.info(f"Шаблон найден по домену: {domain2}")
                return domain2, template

        # Этап 3: загружаем платформы, ищем domain2 + триггер-ссылки
        if not self._platforms_loaded:
            await self._load_platforms()

        platform: str | None = None

        if domain2:
            for p in self._platforms:
                if p.lower() == domain2.lower():
                    platform = p
                    logger.info(f"Определена платформа по домену: {platform}")
                    break

        if platform is None and self._platform_link_triggers:
            platform = await asyncio.to_thread(
                self._check_platform_links,
                page_source,
                self._platform_link_triggers,
            )
            if platform:
                logger.info(f"Определена платформа по триггер-ссылке: {platform}")

        # Платформа найдена — создаём шаблон и возвращаем сразу
        if platform:
            try:
                _, template = await self.add_template(platform)
                logger.info(f"Создан шаблон для платформы: {platform}")
                return platform, template
            except OSError as e:
                logger.error(f"Ошибка создания шаблона для платформы {platform}: {e}")
                return platform, None

        # Этап 4: определяем движок по мета-тегам / HTML
        if not self._engines_loaded:
            await self._load_engines()

        engine_name = await asyncio.to_thread(self._check_engine_meta, page_source)
        if engine_name:
            # Прямой доступ к файлу {engine_name}.json
            template = await self._load_template_from_file(engine_name)
            if template is not None:
                logger.info(f"Шаблон найден по движку: {engine_name}")
                return engine_name, template

            # Файл не найден — создаём новый шаблон
            try:
                _, template = await self.add_template(engine_name)
                logger.info(f"Создан шаблон для движка: {engine_name}")
                return engine_name, template
            except OSError as e:
                logger.error(f"Ошибка создания шаблона для движка {engine_name}: {e}")
                return engine_name, None

        # Этап 5: fallback — движок не определён
        fallback = domain2 or tldextract.extract(url).registered_domain or url
        logger.debug(f"Движок не определён — fallback: {fallback}")
        return fallback, None

    @staticmethod
    def _check_platform_links(
        page_source: str, triggers: list[str]
    ) -> str | None:
        """Ищет ссылки с триггерным текстом и возвращает домен из href.

        Не сравнивает с текущим доменом — берёт домен прямо из найденной ссылки.
        Это позволяет определить платформу даже если форум использует собственный домен.

        Args:
            page_source: HTML страницы.
            triggers: Список триггерных фраз из platform_link_triggers.

        Returns:
            Домен платформы из найденной ссылки или None.
        """
        try:
            soup = BeautifulSoup(page_source, "html.parser")
            for a in soup.find_all("a", href=True):
                text = (a.get_text() or "").strip().lower()
                href = (a["href"] or "").strip()
                if not href or href.startswith("#"):
                    continue
                if any(trigger.lower() in text for trigger in triggers):
                    # Извлекаем домен второго уровня из href
                    try:
                        ext = tldextract.extract(href)
                        if ext.domain and ext.suffix:
                            platform_domain = f"{ext.domain}.{ext.suffix}"
                            logger.debug(
                                f"Найдена триггер-ссылка: текст='{text}', "
                                f"href='{href}', платформа='{platform_domain}'"
                            )
                            return platform_domain
                    except Exception:
                        continue
        except Exception as e:
            logger.warning(f"Ошибка проверки триггер-ссылок: {e}")
        return None

    def _check_engine_meta(self, page_source: str) -> str | None:
        """Определяет движок по мета тегам и характерным признакам HTML.

        Читает список движков из self._engines загруженных из forum_engines.json.

        Args:
            page_source: HTML страницы.

        Returns:
            Название движка или None.
        """
        try:
            soup = BeautifulSoup(page_source, "html.parser")
            page_lower = page_source.lower()
            meta_generator = (
                soup.find("meta", attrs={"name": "generator"}) or {}
            )
            meta_content = (
                meta_generator.get("content") or ""
            ).lower() if isinstance(meta_generator, dict) else (
                meta_generator.get("content") or ""
            ).lower()

            for engine in self._engines:
                name = engine.get("name", "")
                meta_gen = (engine.get("meta_generator") or "").lower()
                html_signs = [s.lower() for s in engine.get("html_signs", [])]

                # Проверка мета тега generator
                if meta_gen and meta_gen in meta_content:
                    return name

                # Проверка HTML признаков
                if any(sign in page_lower for sign in html_signs):
                    return name

        except Exception as e:
            logger.warning(f"Ошибка определения движка по мета тегам: {e}")
        return None

    async def detect_template(self, url: str, page_source: str) -> dict | None:
        """Возвращает первый подходящий шаблон (для обратной совместимости)."""
        templates = await self.detect_templates(url, page_source)
        return templates[0] if templates else None
    
    async def detect_templates(self, url: str, page_source: str) -> list[dict]:
        """Возвращает все подходящие шаблоны."""
        if not self._loaded:
            await self._load_templates()
        matched = []
        for template in self.templates:
            detect = template.get("detect", {})
            if await self._matches_detect_rules(url, page_source, detect):
                matched.append(template)
        if matched:
            names = [t.get("name") for t in matched]
            logger.info(f"Совпавшие шаблоны: {names}")
        else:
            logger.debug("Подходящий шаблон не найден.")
        return matched

    async def _matches_detect_rules(
        self, url: str, page_source: str, detect: dict
    ) -> bool:
        """Проверяет, соответствует ли страница правилам обнаружения шаблона.

        Args:
            url: URL страницы.
            page_source: HTML-содержимое страницы.
            detect: Словарь с правилами из секции `detect` шаблона.

        Returns:
            True если все условия выполнены, иначе False.
        """
        # Проверка url_pattern
        url_pattern = detect.get("url_pattern")
        if url_pattern and url_pattern not in url:
            return False

        # Проверка html_contains
        html_contains = detect.get("html_contains", [])
        for fragment in html_contains:
            if fragment not in page_source:
                return False

        # Проверка meta_tags (BS4 — блокирующая, запускаем в thread)
        meta_tags = detect.get("meta_tags", [])
        if meta_tags:
            matched = await asyncio.to_thread(
                self._check_meta_tags, page_source, meta_tags
            )
            if not matched:
                return False

        return True

    @staticmethod
    def _check_meta_tags(page_source: str, meta_tags: list[dict]) -> bool:
        """Проверяет наличие meta-тегов в HTML (синхронная, для to_thread).

        Args:
            page_source: HTML-содержимое страницы.
            meta_tags: Список словарей {"name": ..., "content": ...}.

        Returns:
            True если все meta-теги найдены.
        """
        soup = BeautifulSoup(page_source, "html.parser")
        for tag_spec in meta_tags:
            found = soup.find("meta", attrs=tag_spec)
            if not found:
                return False
        return True

    async def get_template_by_name(self, name: str) -> dict | None:
        """Возвращает шаблон по имени (без учёта регистра).

        Args:
            name: Имя шаблона (поле `name` в JSON).

        Returns:
            Найденный шаблон или None.
        """
        if not self._loaded:
            await self._load_templates()

        name_lower = name.lower()
        for template in self.templates:
            if template.get("name", "").lower() == name_lower:
                return template
        return None

    async def get_all_templates(self) -> list[dict]:
        """Возвращает список всех загруженных шаблонов.

        Returns:
            Список шаблонов (словарей).
        """
        if not self._loaded:
            await self._load_templates()
        return self.templates

    async def add_template(self, engine_name: str) -> tuple[str, dict]:
        """Создаёт новый шаблон на основе default.json и сохраняет в файл.

        Загружает default.json как основу, заполняет поля name и engine,
        сохраняет в templates/known_forums/{engine_name}.json.

        Args:
            engine_name: Название движка, платформы или домена сайта.

        Returns:
            Кортеж (путь к файлу, словарь шаблона).

        Raises:
            OSError: При ошибке записи файла.
        """
        # Загружаем шаблон по умолчанию
        default_file = self.templates_dir.parent / "default.json"
        template_data: dict = {}

        if default_file.exists():
            try:
                async with aiofiles.open(default_file, encoding="utf-8") as f:
                    content = await f.read()
                template_data = await asyncio.to_thread(json.loads, content)
                logger.debug(f"Загружен шаблон по умолчанию: {default_file}")
            except (json.JSONDecodeError, OSError) as e:
                logger.warning(f"Ошибка загрузки default.json: {e} — используем пустой шаблон")
        else:
            logger.warning(f"Файл default.json не найден: {default_file} — используем пустой шаблон")

        # Заполняем базовые поля
        template_data["name"] = engine_name
        template_data["engine"] = engine_name

        # Сохраняем
        self.templates_dir.mkdir(parents=True, exist_ok=True)
        filename = self._generate_filename(template_data)
        if not filename.endswith(".json"):
            filename = f"{filename}.json"

        file_path = self.templates_dir / filename
        content = json.dumps(template_data, ensure_ascii=False, indent=2)

        async with aiofiles.open(file_path, "w", encoding="utf-8") as f:
            await f.write(content)

        logger.info(f"Создан новый шаблон: {file_path}")

        # Точечно добавляем в кэш — без перезагрузки всей директории
        engine_lower = engine_name.lower()
        if not any(t.get("engine", "").lower() == engine_lower for t in self.templates):
            self.templates.append(template_data)

        return str(file_path), template_data
    
    def _generate_filename(self, template_data: dict) -> str:
        """Генерирует имя файла из данных шаблона.

        Приоритет: поле `name` → поле `domain` → timestamp.
        Очищает имя от символов: / \\ : * ? " < > |

        Args:
            template_data: Словарь с данными шаблона.

        Returns:
            Имя файла без расширения.
        """
        raw_name = (
            template_data.get("name")
            or template_data.get("domain")
            or str(int(time.time()))
        )
        # Очищаем недопустимые символы
        clean = re.sub(r'[/\\:*?"<>|]', "_", raw_name)
        # Убираем лишние пробелы и заменяем на _
        clean = re.sub(r"\s+", "_", clean.strip())
        return clean or str(int(time.time()))
    
    async def update_template(
        self,
        engine_name: str,
        new_data: dict,
        username: str | None = None,
        profile_fields: dict | None = None,
    ) -> dict | None:
        """Обновляет шаблон новыми данными без перезаписи существующих.

        Открывает файл напрямую по engine_name — без перебора всех шаблонов.
        После записи обновляет только этот элемент в кэше — без полной перезагрузки.

        Гарантия: engine_name всегда совпадает с именем файла без расширения.
        Это обеспечивается на уровне add_template и detect_engine.

        Args:
            engine_name: Название движка = имя файла без расширения.
            new_data: Новые данные для слияния в шаблон.
            username: Имя пользователя для обновления профиля (опционально).
            profile_fields: Новые поля профиля (опционально).

        Returns:
            Обновлённый шаблон или None при ошибке.
        """
        # Прямой доступ к файлу — поиск по всем шаблонам не нужен
        file_path = self.templates_dir / f"{engine_name}.json"

        if not file_path.exists():
            logger.warning(f"Файл шаблона не найден: {file_path}")
            return None

        # Читаем только нужный файл
        try:
            async with aiofiles.open(file_path, encoding="utf-8") as f:
                content = await f.read()
            target = await asyncio.to_thread(json.loads, content)
        except (json.JSONDecodeError, OSError) as e:
            logger.error(f"Ошибка чтения шаблона {file_path}: {e}")
            return None

        # Сливаем новые данные
        target = self._merge_template(target, new_data)

        # Записываем в тот же файл — путь уже известен, _generate_filename не нужен
        try:
            content = json.dumps(target, ensure_ascii=False, indent=2)
            async with aiofiles.open(file_path, "w", encoding="utf-8") as f:
                await f.write(content)
            logger.info(f"Шаблон обновлён: {file_path}")
        except OSError as e:
            logger.error(f"Ошибка сохранения шаблона {file_path}: {e}")
            return None

        # Точечное обновление кэша — без полной перезагрузки всех шаблонов
        if self._loaded:
            engine_lower = engine_name.lower()
            for i, t in enumerate(self.templates):
                if t.get("engine", "").lower() == engine_lower:
                    self.templates[i] = target
                    break

        # Обновляем профиль пользователя если нужно
        if username and profile_fields:
            await self._update_account_profile(username, profile_fields)

        return target

    def _merge_template(self, target: dict, new_data: dict) -> dict:
        """Сливает new_data в target по правилам дополнения.

        Args:
            target: Текущий шаблон.
            new_data: Новые данные для слияния.

        Returns:
            Обновлённый шаблон.
        """
        for key, new_val in new_data.items():
            if key == "fields":
                # Каждое поле — список вариантов селекторов
                target_fields = target.get("fields", {}) or {}
                for field_name, new_selector in (new_val or {}).items():
                    if not new_selector:
                        continue
                    existing = target_fields.get(field_name)
                    if existing is None:
                        # Поля не было — создаём список
                        target_fields[field_name] = [new_selector]
                    elif isinstance(existing, list):
                        # Добавляем если нет в списке
                        if new_selector not in existing:
                            existing.append(new_selector)
                    elif isinstance(existing, str):
                        # Конвертируем строку в список
                        if existing != new_selector:
                            target_fields[field_name] = [existing, new_selector]
                        else:
                            target_fields[field_name] = [existing]
                target["fields"] = target_fields

            elif key in ("success_indicators", "error_indicators", "custom_fields"):
                # Списки — дополняем уникальными значениями
                existing_list = target.get(key) or []
                if not isinstance(existing_list, list):
                    existing_list = [existing_list]
                new_list = new_val if isinstance(new_val, list) else [new_val]
                for item in new_list:
                    if item and item not in existing_list:
                        existing_list.append(item)
                target[key] = existing_list

            elif key == "agree_step":
                # Вложенный словарь agree_step
                target_agree = target.get("agree_step") or {}
                new_agree = new_val or {}
                # Чекбоксы — список, дополняем
                new_cbs = new_agree.get("checkboxes") or []
                existing_cbs = target_agree.get("checkboxes") or []
                for cb in new_cbs:
                    if cb and cb not in existing_cbs:
                        existing_cbs.append(cb)
                target_agree["checkboxes"] = existing_cbs
                # submit_button — только если null
                if not target_agree.get("submit_button") and new_agree.get("submit_button"):
                    target_agree["submit_button"] = new_agree["submit_button"]
                target["agree_step"] = target_agree

            elif key == "registration_page":
                # url и form_selector — списки вариантов
                target_rp = target.get("registration_page") or {}
                new_rp = new_val or {}
                for rp_key in ("url", "form_selector"):
                    new_rp_val = new_rp.get(rp_key)
                    if not new_rp_val:
                        continue
                    existing_rp = target_rp.get(rp_key)
                    if existing_rp is None:
                        target_rp[rp_key] = [new_rp_val]
                    elif isinstance(existing_rp, list):
                        if new_rp_val not in existing_rp:
                            existing_rp.append(new_rp_val)
                    elif isinstance(existing_rp, str):
                        if existing_rp != new_rp_val:
                            target_rp[rp_key] = [existing_rp, new_rp_val]
                        else:
                            target_rp[rp_key] = [existing_rp]
                target["registration_page"] = target_rp

            else:
                # Остальные скалярные поля — только если null
                if not target.get(key) and new_val:
                    target[key] = new_val

        return target

    async def _update_account_profile(
        self, username: str, profile_fields: dict
    ) -> None:
        """Обновляет custom_fields профиля пользователя в accounts.json.

        Добавляет новые поля не перезаписывая существующие.

        Args:
            username: Имя пользователя.
            profile_fields: Новые поля для добавления в custom_fields.
        """
        if not self.accounts_file.exists():
            logger.warning(f"Файл аккаунтов не найден: {self.accounts_file}")
            return

        try:
            async with aiofiles.open(self.accounts_file, encoding="utf-8") as f:
                content = await f.read()
            accounts: list[dict] = await asyncio.to_thread(json.loads, content)
        except (json.JSONDecodeError, OSError) as e:
            logger.error(f"Ошибка загрузки аккаунтов для обновления: {e}")
            return

        updated = False
        for account in accounts:
            if account.get("username") == username:
                custom = account.get("custom_fields") or {}
                for field, value in profile_fields.items():
                    existing = custom.get(field)
                    # Оператор намеренно пропускал — не трогаем
                    if isinstance(existing, list) and len(existing) == 0:
                        logger.debug(f"Поле профиля [{username}]: {field} — пропускалось, не трогаем")
                        continue
                    # Поля нет или null — инициализируем списком
                    if field not in custom or existing is None:
                        new_val = value if isinstance(value, list) else [value]
                        custom[field] = new_val
                        logger.debug(f"Инициализировано поле профиля [{username}]: {field}={new_val!r}")
                    # Поле есть — дописываем новые варианты
                    elif isinstance(existing, list):
                        new_items = value if isinstance(value, list) else [value]
                        for v in new_items:
                            if v and v not in existing:
                                existing.append(v)
                                logger.debug(f"Добавлен вариант профиля [{username}]: {field}+={v!r}")
                account["custom_fields"] = custom
                updated = True
                break

        if not updated:
            logger.warning(f"Пользователь не найден в accounts.json: {username}")
            return

        try:
            content = json.dumps(accounts, ensure_ascii=False, indent=2)
            async with aiofiles.open(self.accounts_file, "w", encoding="utf-8") as f:
                await f.write(content)
            logger.info(f"Профиль обновлён в accounts.json: {username}")
        except OSError as e:
            logger.error(f"Ошибка сохранения accounts.json: {e}")
            
