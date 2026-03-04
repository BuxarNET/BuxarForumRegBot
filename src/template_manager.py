from __future__ import annotations

import asyncio
import json
import re
import time
from pathlib import Path

import aiofiles
from bs4 import BeautifulSoup
from loguru import logger


class TemplateManager:
    """Менеджер шаблонов для известных форумных движков.

    Загружает JSON-шаблоны из директории и предоставляет методы
    для поиска подходящего шаблона по URL и содержимому страницы.
    """

    def __init__(self, templates_dir: str = "templates/known_forums") -> None:
        """Инициализация менеджера шаблонов.

        Args:
            templates_dir: Путь к директории с JSON-шаблонами.
        """
        self.templates_dir = Path(templates_dir)
        self.templates: list[dict] = []
        self._loaded: bool = False

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

    async def detect_template(self, url: str, page_source: str) -> dict | None:
        """Определяет подходящий шаблон по URL и HTML-содержимому страницы.

        Проверяет правила из секции `detect` каждого шаблона.
        Все условия внутри одного шаблона соединяются по AND.

        Args:
            url: URL текущей страницы.
            page_source: HTML-содержимое страницы.

        Returns:
            Первый подходящий шаблон или None.
        """
        if not self._loaded:
            await self._load_templates()

        for template in self.templates:
            detect = template.get("detect", {})
            if await self._matches_detect_rules(url, page_source, detect):
                logger.info(f"Определён шаблон: {template.get('name', 'unknown')}")
                return template

        logger.debug("Подходящий шаблон не найден.")
        return None

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

    async def add_template(
        self, template_data: dict, filename: str | None = None
    ) -> str:
        """Сохраняет шаблон в JSON-файл и обновляет кэш.

        Args:
            template_data: Словарь с данными шаблона.
            filename: Имя файла (без расширения). Если не указан — генерируется автоматически.

        Returns:
            Путь к сохранённому файлу.

        Raises:
            OSError: При ошибке записи файла.
        """
        self.templates_dir.mkdir(parents=True, exist_ok=True)

        if filename is None:
            filename = self._generate_filename(template_data)

        if not filename.endswith(".json"):
            filename = f"{filename}.json"

        file_path = self.templates_dir / filename
        content = json.dumps(template_data, ensure_ascii=False, indent=2)

        async with aiofiles.open(file_path, "w", encoding="utf-8") as f:
            await f.write(content)

        logger.info(f"Шаблон сохранён: {file_path}")

        # Обновляем кэш
        self._loaded = False
        await self._load_templates()

        return str(file_path)

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
