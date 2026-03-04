from __future__ import annotations

import json
import pytest
from pathlib import Path
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).parent.parent / "config"))


# =============================================================================
# Фикстуры
# =============================================================================

@pytest.fixture
def config(tmp_path: Path) -> dict:
    """Минимальная конфигурация для тестов."""
    return {
        "ACCOUNTS_FILE": str(tmp_path / "accounts.json"),
        "PROXIES_FILE": str(tmp_path / "proxies.txt"),
        "FORUMS_SOURCE_FILE": str(tmp_path / "results_new.txt"),
        "PROFILES_DIR": str(tmp_path / "profiles"),
        "RESULTS_DIR": str(tmp_path),
        "RESULTS_FLUSH_IMMEDIATELY": True,
        "INPUT_COMMENT_PREFIX": "#",
        "OUTPUT_TIMESTAMP_FORMAT": "%Y-%m-%dT%H:%M:%S",
        "MAX_CONCURRENT_USERS": 2,
        "EACH_USER_ALL_FORUMS": True,
        "MAX_REGISTRATION_RETRIES": 2,
        "MANUAL_CAPTCHA_TIMEOUT": 300,
        "MANUAL_FIELD_FILL_TIMEOUT": 120,
        "TAB_TIMEOUT_SECONDS": 300,
        "FIND_REGISTRATION_PAGE_TIMEOUT": 60,
        "GRACEFUL_SHUTDOWN_TIMEOUT": 5,
        "REQUIRE_PROXY_PER_USER": False,
        "SHOW_BROWSER_WINDOWS": False,
        "SET_WINDOW_TITLE": False,
        "CLOSE_BROWSER_AFTER_ALL_REGISTRATIONS": True,
        "SAVE_PROFILE_METADATA": False,
        "CLOSE_TAB_AFTER_REGISTRATION": True,
        "SHOW_FINAL_REPORT": False,
        "SHOW_FAILED_DETAILS": True,
        "NO_RETRY_REASONS": {
            "fields_not_found", "no_form_detected", "account_exists",
            "proxy_failed", "proxy_blocked", "manual_fill_timeout", "missing_fields",
        },
        "ERROR_MESSAGES_RU": {
            "captcha_timeout": "Не удалось решить капчу: истекло время ожидания",
            "fields_not_found": "Не найдены поля формы регистрации",
            "proxy_failed": "Прокси не доступен или заблокирован",
            "unknown": "Неизвестная ошибка",
        },
        "WINDOW_TITLE_FORMAT": "{username} | {forum} | {status}",
    }


@pytest.fixture
def orchestrator(config):
    from orchestrator.main_orchestrator import MainOrchestrator
    return MainOrchestrator(config=config)


@pytest.fixture
def sample_accounts() -> list[dict]:
    return [
        {
            "username": "user1",
            "email": "user1@example.com",
            "password": "Pass123",
            "proxy_id": 0,
            "custom_fields": {},
            "status": "pending",
            "attempts": 0,
            "last_attempt": None,
        },
        {
            "username": "user2",
            "email": "user2@example.com",
            "password": "Pass456",
            "proxy_id": 1,
            "custom_fields": {},
            "status": "pending",
            "attempts": 0,
            "last_attempt": None,
        },
    ]


@pytest.fixture
def sample_forums() -> list[str]:
    return [
        "https://forum1.com/",
        "https://forum2.com/",
        "https://forum3.com/",
    ]


def write_forums_file(path: Path, forums: list[str]) -> None:
    """Записывает список форумов в файл."""
    content = "# Список форумов\n" + "\n".join(forums) + "\n"
    path.write_text(content, encoding="utf-8")


def write_accounts_file(path: Path, accounts: list[dict]) -> None:
    """Записывает аккаунты в JSON файл."""
    path.write_text(json.dumps(accounts, ensure_ascii=False, indent=2), encoding="utf-8")


# =============================================================================
# Тесты _sanitize_filename
# =============================================================================

def test_sanitize_filename_replaces_invalid_chars(orchestrator):
    assert orchestrator._sanitize_filename("user<1>") == "user_1_"
    assert orchestrator._sanitize_filename('user"name') == "user_name"
    assert orchestrator._sanitize_filename("user/name") == "user_name"
    assert orchestrator._sanitize_filename("user\\name") == "user_name"
    assert orchestrator._sanitize_filename("user|name") == "user_name"
    assert orchestrator._sanitize_filename("user?name") == "user_name"
    assert orchestrator._sanitize_filename("user*name") == "user_name"


def test_sanitize_filename_allows_at_sign(orchestrator):
    assert orchestrator._sanitize_filename("test@user") == "test@user"


def test_sanitize_filename_strips_spaces(orchestrator):
    assert orchestrator._sanitize_filename("  user1  ") == "user1"


def test_sanitize_filename_normal(orchestrator):
    assert orchestrator._sanitize_filename("user123") == "user123"


# =============================================================================
# Тесты _parse_file_line
# =============================================================================

def test_parse_file_line_returns_url(orchestrator):
    assert orchestrator._parse_file_line("https://forum1.com/") == "https://forum1.com/"


def test_parse_file_line_extracts_first_field(orchestrator):
    assert orchestrator._parse_file_line(
        "https://forum1.com/ 2026-02-24T15:30:22"
    ) == "https://forum1.com/"


def test_parse_file_line_ignores_comment(orchestrator):
    assert orchestrator._parse_file_line("# https://forum1.com/") is None


def test_parse_file_line_ignores_empty(orchestrator):
    assert orchestrator._parse_file_line("") is None
    assert orchestrator._parse_file_line("   ") is None


def test_parse_file_line_bad_line_with_error_code(orchestrator):
    result = orchestrator._parse_file_line(
        "https://forum3.com/ captcha_timeout 2026-02-24T15:40:12"
    )
    assert result == "https://forum3.com/"


# =============================================================================
# Тесты _write_result
# =============================================================================

def test_write_result_success(orchestrator, tmp_path):
    """Запись успешного результата в файл."""
    filepath = tmp_path / "results_ok_user1.txt"
    orchestrator._write_result(filepath, "https://forum1.com/", ["2026-02-24T15:30:22"])

    content = filepath.read_text(encoding="utf-8")
    assert "https://forum1.com/ 2026-02-24T15:30:22\n" == content


def test_write_result_failed(orchestrator, tmp_path):
    """Запись неудачного результата с кодом ошибки."""
    filepath = tmp_path / "results_bad_user1.txt"
    orchestrator._write_result(
        filepath, "https://forum3.com/", ["captcha_timeout", "2026-02-24T15:40:12"]
    )

    content = filepath.read_text(encoding="utf-8")
    assert "https://forum3.com/ captcha_timeout 2026-02-24T15:40:12\n" == content


def test_write_result_appends(orchestrator, tmp_path):
    """Результаты добавляются в конец файла."""
    filepath = tmp_path / "results_ok_user1.txt"
    orchestrator._write_result(filepath, "https://forum1.com/", ["2026-02-24T15:30:22"])
    orchestrator._write_result(filepath, "https://forum2.com/", ["2026-02-24T15:35:10"])

    lines = filepath.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert "https://forum1.com/" in lines[0]
    assert "https://forum2.com/" in lines[1]


# =============================================================================
# Тесты _load_forums
# =============================================================================

@pytest.mark.asyncio
async def test_load_forums_parses_correctly(orchestrator, config, tmp_path):
    """Загрузка форумов с фильтрацией комментариев."""
    forums_file = Path(config["FORUMS_SOURCE_FILE"])
    forums_file.write_text(
        "# Комментарий\n"
        "https://forum1.com/\n"
        "https://forum2.com/showthread.php?t=12345  # inline comment\n"
        "\n"
        "# https://forum3.com/  закомментировано\n"
        "https://forum4.com/\n",
        encoding="utf-8",
    )

    forums = await orchestrator._load_forums()
    assert len(forums) == 3
    assert "https://forum1.com/" in forums
    assert "https://forum2.com/showthread.php?t=12345" in forums
    assert "https://forum4.com/" in forums
    assert "https://forum3.com/" not in forums


@pytest.mark.asyncio
async def test_load_forums_missing_file(orchestrator):
    """Отсутствие файла форумов → пустой список."""
    forums = await orchestrator._load_forums()
    assert forums == []


# =============================================================================
# Тесты _get_resume_index
# =============================================================================

@pytest.mark.asyncio
async def test_get_resume_index_empty_file(orchestrator, config, tmp_path):
    """Пустой файл results_ok → начинать с индекса 0."""
    ok_file = tmp_path / "results_ok_user1.txt"
    ok_file.write_text("", encoding="utf-8")

    forums = ["https://forum1.com/", "https://forum2.com/", "https://forum3.com/"]
    idx = await orchestrator._get_resume_index("user1", forums)
    assert idx == 0


@pytest.mark.asyncio
async def test_get_resume_index_no_file(orchestrator, config, tmp_path):
    """Отсутствие файла results_ok → начинать с индекса 0."""
    forums = ["https://forum1.com/", "https://forum2.com/"]
    idx = await orchestrator._get_resume_index("user1", forums)
    assert idx == 0


@pytest.mark.asyncio
async def test_get_resume_index_resumes_correctly(orchestrator, config, tmp_path):
    """Resume с правильного форума после сбоя."""
    ok_file = tmp_path / "results_ok_user1.txt"
    ok_file.write_text(
        "https://forum1.com/ 2026-02-24T15:30:22\n"
        "https://forum2.com/ 2026-02-24T15:35:10\n",
        encoding="utf-8",
    )

    forums = [
        "https://forum1.com/",
        "https://forum2.com/",
        "https://forum3.com/",
        "https://forum4.com/",
    ]
    idx = await orchestrator._get_resume_index("user1", forums)
    assert idx == 2  # продолжаем с forum3


@pytest.mark.asyncio
async def test_get_resume_index_all_done(orchestrator, config, tmp_path):
    """Все форумы обработаны → индекс за пределами списка."""
    ok_file = tmp_path / "results_ok_user1.txt"
    forums = ["https://forum1.com/", "https://forum2.com/"]
    ok_file.write_text(
        "https://forum1.com/ 2026-02-24T15:30:22\n"
        "https://forum2.com/ 2026-02-24T15:35:10\n",
        encoding="utf-8",
    )

    idx = await orchestrator._get_resume_index("user1", forums)
    assert idx == 2  # за пределами → очередь будет пустой


# =============================================================================
# Тесты _get_proxy_for_user
# =============================================================================

def test_get_proxy_for_user_returns_correct_proxy(orchestrator):
    proxy_manager = MagicMock()
    proxy_manager.proxies = ["http://1.1.1.1:8080", "http://2.2.2.2:8080"]

    user = {"username": "user1", "proxy_id": 1}
    proxy = orchestrator._get_proxy_for_user(user, proxy_manager)
    assert proxy == "http://2.2.2.2:8080"


def test_get_proxy_for_user_out_of_range(orchestrator):
    proxy_manager = MagicMock()
    proxy_manager.proxies = ["http://1.1.1.1:8080"]

    user = {"username": "user1", "proxy_id": 5}
    proxy = orchestrator._get_proxy_for_user(user, proxy_manager)
    assert proxy is None


def test_get_proxy_for_user_empty_list(orchestrator):
    proxy_manager = MagicMock()
    proxy_manager.proxies = []

    user = {"username": "user1", "proxy_id": 0}
    proxy = orchestrator._get_proxy_for_user(user, proxy_manager)
    assert proxy is None


def test_get_proxy_for_user_no_proxy_id(orchestrator):
    proxy_manager = MagicMock()
    proxy_manager.proxies = ["http://1.1.1.1:8080"]

    user = {"username": "user1"}
    proxy = orchestrator._get_proxy_for_user(user, proxy_manager)
    assert proxy is None


# =============================================================================
# Тесты _generate_final_report
# =============================================================================

def test_generate_final_report_structure(orchestrator, tmp_path, sample_accounts, sample_forums, config):
    """Отчёт содержит правильные секции."""
    # Создаём результирующие файлы
    ok_file = tmp_path / "results_ok_user1.txt"
    bad_file = tmp_path / "results_bad_user1.txt"
    ok_file.write_text(
        "https://forum1.com/ 2026-02-24T15:30:22\n"
        "https://forum2.com/ 2026-02-24T15:35:10\n",
        encoding="utf-8",
    )
    bad_file.write_text(
        "https://forum3.com/ captcha_timeout 2026-02-24T15:40:12\n",
        encoding="utf-8",
    )

    report = orchestrator._generate_final_report(sample_forums, sample_accounts[:1])

    assert "REGISTRATION REPORT" in report
    assert "Успешно:" in report
    assert "Неудачи:" in report
    assert "FAILED REGISTRATIONS" in report


def test_generate_final_report_counts(orchestrator, tmp_path, sample_accounts, config):
    """Отчёт корректно подсчитывает результаты из файлов."""
    ok_file = tmp_path / "results_ok_user1.txt"
    ok_file.write_text(
        "https://forum1.com/ 2026-02-24T15:30:22\n"
        "https://forum2.com/ 2026-02-24T15:35:10\n"
        "https://forum3.com/ 2026-02-24T15:36:10\n",
        encoding="utf-8",
    )

    report = orchestrator._generate_final_report(
        ["https://forum1.com/", "https://forum2.com/", "https://forum3.com/"],
        sample_accounts[:1],
    )

    assert "Успешно:                     3" in report


# =============================================================================
# Тесты _on_captcha_stats
# =============================================================================

def test_on_captcha_stats_auto(orchestrator):
    orchestrator._on_captcha_stats({"provider": "2captcha", "success": True})
    assert orchestrator._stats["captcha_auto"] == 1
    assert orchestrator._stats["captcha_manual"] == 0


def test_on_captcha_stats_manual(orchestrator):
    orchestrator._on_captcha_stats({"provider": "manual", "success": True})
    assert orchestrator._stats["captcha_manual"] == 1
    assert orchestrator._stats["captcha_auto"] == 0


def test_on_captcha_stats_failed(orchestrator):
    orchestrator._on_captcha_stats({"provider": "2captcha", "success": False})
    assert orchestrator._stats["captcha_failed"] == 1
    assert orchestrator._stats["captcha_auto"] == 0


# =============================================================================
# Тест run() с моками
# =============================================================================

@pytest.mark.asyncio
async def test_run_processes_all_forums(config, tmp_path, sample_accounts, sample_forums):
    """run() обрабатывает все форумы для всех пользователей."""
    from orchestrator.main_orchestrator import MainOrchestrator

    # Создаём входные файлы
    forums_file = Path(config["FORUMS_SOURCE_FILE"])
    write_forums_file(forums_file, sample_forums)

    accounts_file = Path(config["ACCOUNTS_FILE"])
    write_accounts_file(accounts_file, sample_accounts[:1])

    proxies_file = Path(config["PROXIES_FILE"])
    proxies_file.write_text("http://1.1.1.1:8080\n", encoding="utf-8")

    orch = MainOrchestrator(config=config)

    # Мокаем _process_user чтобы не запускать реальный браузер
    async def mock_process_user(username, user_data, forum_queue, proxy_manager):
        for url in forum_queue:
            safe = orch._sanitize_filename(username)
            orch._write_result(
                tmp_path / f"results_ok_{safe}.txt",
                url,
                [datetime.now().strftime(config["OUTPUT_TIMESTAMP_FORMAT"])],
            )
            orch._stats["success"] += 1
            orch._stats["processed"] += 1
        return {"username": username, "success": len(forum_queue), "failed": 0}

    orch._process_user = mock_process_user

    stats = await orch.run()

    assert stats["total_forums"] == len(sample_forums)
    assert stats["total_users"] == 1
    assert stats["success"] == len(sample_forums)


@pytest.mark.asyncio
async def test_run_empty_forums(config, tmp_path, sample_accounts):
    """run() с пустым файлом форумов завершается без ошибок."""
    from orchestrator.main_orchestrator import MainOrchestrator

    forums_file = Path(config["FORUMS_SOURCE_FILE"])
    forums_file.write_text("# Только комментарии\n", encoding="utf-8")

    accounts_file = Path(config["ACCOUNTS_FILE"])
    write_accounts_file(accounts_file, sample_accounts[:1])

    orch = MainOrchestrator(config=config)
    stats = await orch.run()

    assert stats["total_forums"] == 0


@pytest.mark.asyncio
async def test_run_resume_skips_processed_forums(config, tmp_path, sample_accounts):
    """run() с resume пропускает уже обработанные форумы."""
    from orchestrator.main_orchestrator import MainOrchestrator

    forums = ["https://f1.com/", "https://f2.com/", "https://f3.com/"]
    forums_file = Path(config["FORUMS_SOURCE_FILE"])
    write_forums_file(forums_file, forums)

    accounts_file = Path(config["ACCOUNTS_FILE"])
    write_accounts_file(accounts_file, sample_accounts[:1])

    # Симулируем что f1 и f2 уже обработаны
    ok_file = tmp_path / "results_ok_user1.txt"
    ok_file.write_text(
        "https://f1.com/ 2026-02-24T10:00:00\n"
        "https://f2.com/ 2026-02-24T10:05:00\n",
        encoding="utf-8",
    )

    orch = MainOrchestrator(config=config)
    processed_forums = []

    async def mock_process_user(username, user_data, forum_queue, proxy_manager):
        processed_forums.extend(forum_queue)
        return {"username": username, "success": 0, "failed": 0}

    orch._process_user = mock_process_user
    await orch.run()

    # Должен обработать только f3
    assert processed_forums == ["https://f3.com/"]
