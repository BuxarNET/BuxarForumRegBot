from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import TypedDict

import aiofiles
from loguru import logger

from controllers.registration_controller import AccountData, RegistrationResult

_REQUIRED_FIELDS = ("username", "email", "password", "proxy_id")


class StoredAccount(TypedDict):
    """Аккаунт с полным набором служебных полей для хранения в accounts.json."""

    username: str
    email: str
    password: str
    proxy_id: int
    custom_fields: dict[str, str]
    status: str
    attempts: int
    last_attempt: str | None


class AccountManager:
    """Менеджер аккаунтов для хранения, обновления и логирования регистраций.

    Работает с единым файлом data/accounts.json — источником аккаунтов
    и хранилищем их статусов. Пользователь редактирует файл вручную
    когда скрипт остановлен.
    """

    def __init__(
        self,
        accounts_file: str | Path = "data/accounts.json",
        log_file: str | Path = "data/registration_log.json",
    ) -> None:
        """Инициализация менеджера аккаунтов.

        Args:
            accounts_file: Путь к JSON-файлу аккаунтов.
            log_file: Путь к JSON-файлу лога регистраций.
        """
        self.accounts_file = Path(accounts_file)
        self.log_file = Path(log_file)
        self._accounts_cache: list[StoredAccount] | None = None

    async def _load_json(self, filepath: Path) -> list:
        """Асинхронно загружает JSON-файл.

        Args:
            filepath: Путь к файлу.

        Returns:
            Список из файла или пустой список при отсутствии/ошибке файла.
        """
        try:
            async with aiofiles.open(filepath, encoding="utf-8") as f:
                content = await f.read()
            return json.loads(content)
        except FileNotFoundError:
            logger.debug(f"Файл не найден, возвращается пустой список: {filepath}")
            return []
        except json.JSONDecodeError as e:
            logger.warning(f"Ошибка парсинга JSON {filepath}: {e}")
            return []

    async def _save_json(self, filepath: Path, data: list) -> None:
        """Асинхронно сохраняет данные в JSON-файл.

        Args:
            filepath: Путь к файлу.
            data: Список для сохранения.
        """
        filepath.parent.mkdir(parents=True, exist_ok=True)
        content = json.dumps(data, ensure_ascii=False, indent=2)
        async with aiofiles.open(filepath, "w", encoding="utf-8") as f:
            await f.write(content)

    async def log_registration(
        self,
        result: RegistrationResult,
        account_data: AccountData | None = None,
    ) -> None:
        """Сохраняет результат регистрации в лог-файл.

        Args:
            result: Результат регистрации из RegistrationController.register().
            account_data: Данные аккаунта. Если не передан — извлекается
                          из result["form_data"].
        """
        source = account_data if account_data is not None else result.get("form_data", {})

        entry = {
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "username": source.get("username"),
            "email": source.get("email"),
            "success": result.get("success"),
            "reason": result.get("reason"),
            "template_used": result.get("template_used"),
            "screenshot_path": result.get("screenshot"),
            "form_data": result.get("form_data", {}),
        }

        log = await self._load_json(self.log_file)
        log.append(entry)
        await self._save_json(self.log_file, log)
        logger.info(f"Результат регистрации записан: {entry['username']} — success={entry['success']}")

    async def get_registration_log(
        self,
        username: str | None = None,
        success: bool | None = None,
    ) -> list[dict]:
        """Возвращает лог регистраций с опциональной фильтрацией.

        Args:
            username: Фильтровать по имени пользователя.
            success: Фильтровать по статусу (True/False).

        Returns:
            Отсортированный список записей (новые первыми).
        """
        log = await self._load_json(self.log_file)

        if username is not None:
            log = [e for e in log if e.get("username") == username]
        if success is not None:
            log = [e for e in log if e.get("success") == success]

        log.sort(key=lambda e: e.get("timestamp", ""), reverse=True)
        return log

    async def get_pending_accounts(self, limit: int = 100) -> list[StoredAccount]:
        """Возвращает аккаунты со статусом pending, готовые к регистрации.

        Проверяет наличие обязательных полей. Аккаунты без обязательных
        полей пропускаются с логированием warning.

        Args:
            limit: Максимальное количество возвращаемых аккаунтов.

        Returns:
            Список аккаунтов, отсортированных: сначала без попыток,
            затем по возрастанию last_attempt.
        """
        accounts = await self._load_json(self.accounts_file)
        self._accounts_cache = accounts  # type: ignore[assignment]

        pending = []
        for account in accounts:
            # Проверка обязательных полей
            missing = [f for f in _REQUIRED_FIELDS if not account.get(f) and account.get(f) != 0]
            if missing:
                logger.warning(
                    f"Аккаунт пропущен — отсутствуют поля {missing}: "
                    f"{account.get('username', 'unknown')}"
                )
                continue

            if account.get("status", "pending") == "pending":
                pending.append(account)

        # Сортировка: сначала attempts==0, затем по last_attempt
        pending.sort(key=lambda a: (
            a.get("attempts", 0) > 0,
            a.get("last_attempt") or "",
        ))

        return pending[:limit]  # type: ignore[return-value]

    async def update_account_status(
        self,
        username: str,
        status: str,
        reason: str | None = None,
        proxy: str | None = None,
    ) -> None:
        """Обновляет статус аккаунта в accounts.json.

        Увеличивает счётчик attempts, обновляет last_attempt.
        Опционально сохраняет причину ошибки и использованный прокси.

        Args:
            username: Имя пользователя для поиска.
            status: Новый статус (pending/registered/failed/banned).
            reason: Причина ошибки (опционально).
            proxy: Использованный прокси (опционально).
        """
        accounts = await self._load_json(self.accounts_file)
        updated = False

        for account in accounts:
            if account.get("username") == username:
                account["status"] = status
                account["attempts"] = account.get("attempts", 0) + 1
                account["last_attempt"] = datetime.now().isoformat(timespec="seconds")

                if reason or proxy:
                    account["last_error"] = {
                        "reason": reason,
                        "proxy_used": proxy,
                    }

                updated = True
                logger.info(
                    f"Статус аккаунта обновлён: {username} → {status} "
                    f"(попытка #{account['attempts']})"
                )
                break

        if not updated:
            logger.warning(f"Аккаунт не найден для обновления статуса: {username}")
            return

        await self._save_json(self.accounts_file, accounts)
        self._accounts_cache = None  # инвалидируем кэш

    async def export_failed_accounts(self, output_path: str | Path) -> int:
        """Экспортирует аккаунты со статусом failed или banned в JSON-файл.

        Args:
            output_path: Путь к выходному файлу.

        Returns:
            Количество экспортированных аккаунтов.
        """
        accounts = await self._load_json(self.accounts_file)
        failed = [
            a for a in accounts
            if a.get("status") in ("failed", "banned")
        ]

        await self._save_json(Path(output_path), failed)
        logger.info(f"Экспортировано аккаунтов с ошибками: {len(failed)} → {output_path}")
        return len(failed)
