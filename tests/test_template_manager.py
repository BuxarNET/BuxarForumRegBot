from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from template_manager import TemplateManager


# ---------------------------------------------------------------------------
# Вспомогательные функции
# ---------------------------------------------------------------------------

def run(coro):
    """Запускает корутину синхронно — не требует pytest-asyncio."""
    return asyncio.run(coro)


def make_manager(tmp_path: Path, templates: list[dict] | None = None):
    tdir = tmp_path / "known_forums"
    tdir.mkdir()
    if templates:
        for t in templates:
            name = t.get("name", "tmpl").replace(" ", "_").lower()
            (tdir / f"{name}.json").write_text(json.dumps(t), encoding="utf-8")
    return TemplateManager(str(tdir)), tdir


# ---------------------------------------------------------------------------
# _load_templates
# ---------------------------------------------------------------------------

def test_load_templates_reads_all_json(tmp_path, sample_template):
    manager, _ = make_manager(tmp_path, [sample_template])
    run(manager._load_templates())
    assert len(manager.templates) == 1
    assert manager.templates[0]["name"] == "XenForo"
    assert manager._loaded is True


def test_load_templates_skips_invalid_json(tmp_path, sample_template):
    manager, tdir = make_manager(tmp_path, [sample_template])
    (tdir / "broken.json").write_text("{not valid json", encoding="utf-8")
    run(manager._load_templates())
    assert len(manager.templates) == 1


def test_load_templates_empty_dir(tmp_path):
    manager, _ = make_manager(tmp_path, [])
    run(manager._load_templates())
    assert manager.templates == []
    assert manager._loaded is True


def test_load_templates_missing_dir():
    manager = TemplateManager("/nonexistent/path/xyz")
    run(manager._load_templates())
    assert manager.templates == []
    assert manager._loaded is True


# ---------------------------------------------------------------------------
# detect_template
# ---------------------------------------------------------------------------

def test_detect_template_by_url_pattern(tmp_path, sample_template):
    manager, _ = make_manager(tmp_path, [sample_template])
    html = "<html><head><meta name='generator' content='XenForo'></head><body>xf-register</body></html>"
    result = run(manager.detect_template("https://example.com/register", html))
    assert result is not None
    assert result["name"] == "XenForo"


def test_detect_template_url_pattern_no_match(tmp_path, sample_template):
    manager, _ = make_manager(tmp_path, [sample_template])
    html = "<html><body>xf-register</body></html>"
    result = run(manager.detect_template("https://example.com/login", html))
    assert result is None


def test_detect_template_html_contains_no_match(tmp_path, sample_template):
    manager, _ = make_manager(tmp_path, [sample_template])
    html = "<html><head><meta name='generator' content='XenForo'></head><body>no-marker</body></html>"
    result = run(manager.detect_template("https://example.com/register", html))
    assert result is None


def test_detect_template_meta_tags(tmp_path, xenforo_html):
    template = {
        "name": "MetaTest",
        "detect": {
            "url_pattern": "register",
            "meta_tags": [{"name": "generator", "content": "XenForo"}],
            "html_contains": ["xf-register"],
        },
    }
    manager, _ = make_manager(tmp_path, [template])
    result = run(manager.detect_template("http://site.com/register", xenforo_html))
    assert result is not None


def test_detect_template_returns_none_if_no_templates(tmp_path):
    manager, _ = make_manager(tmp_path, [])
    result = run(manager.detect_template("http://site.com/register", "<html></html>"))
    assert result is None


def test_detect_template_multiple_conditions_and(tmp_path):
    """Все условия AND: если хотя бы одно не совпало — шаблон не найден."""
    template = {
        "name": "StrictTest",
        "detect": {
            "url_pattern": "register",
            "html_contains": ["marker1", "marker2"],
        },
    }
    manager, _ = make_manager(tmp_path, [template])
    # marker2 отсутствует
    result = run(manager.detect_template("http://x.com/register", "<body>marker1</body>"))
    assert result is None
    # оба присутствуют
    result = run(manager.detect_template("http://x.com/register", "<body>marker1 marker2</body>"))
    assert result is not None


# ---------------------------------------------------------------------------
# get_template_by_name
# ---------------------------------------------------------------------------

def test_get_template_by_name_found(tmp_path, sample_template):
    manager, _ = make_manager(tmp_path, [sample_template])
    result = run(manager.get_template_by_name("xenforo"))
    assert result is not None
    assert result["name"] == "XenForo"


def test_get_template_by_name_case_insensitive(tmp_path, sample_template):
    manager, _ = make_manager(tmp_path, [sample_template])
    result = run(manager.get_template_by_name("XENFORO"))
    assert result is not None


def test_get_template_by_name_not_found(tmp_path, sample_template):
    manager, _ = make_manager(tmp_path, [sample_template])
    result = run(manager.get_template_by_name("phpbb"))
    assert result is None


def test_get_template_by_name_loads_if_needed(tmp_path, sample_template):
    manager, _ = make_manager(tmp_path, [sample_template])
    assert manager._loaded is False
    result = run(manager.get_template_by_name("XenForo"))
    assert manager._loaded is True
    assert result is not None


# ---------------------------------------------------------------------------
# get_all_templates
# ---------------------------------------------------------------------------

def test_get_all_templates(tmp_path, sample_template):
    manager, _ = make_manager(tmp_path, [sample_template])
    result = run(manager.get_all_templates())
    assert len(result) == 1


def test_get_all_templates_auto_loads(tmp_path, sample_template):
    manager, _ = make_manager(tmp_path, [sample_template])
    assert manager._loaded is False
    run(manager.get_all_templates())
    assert manager._loaded is True


# ---------------------------------------------------------------------------
# add_template и _generate_filename
# ---------------------------------------------------------------------------

def test_add_template_creates_file(tmp_path, sample_template):
    manager, _ = make_manager(tmp_path)
    path = run(manager.add_template(sample_template))
    assert Path(path).exists()
    content = json.loads(Path(path).read_text(encoding="utf-8"))
    assert content["name"] == "XenForo"
    assert len(manager.templates) == 1


def test_add_template_custom_filename(tmp_path, sample_template):
    manager, _ = make_manager(tmp_path)
    path = run(manager.add_template(sample_template, filename="my_forum"))
    assert Path(path).name == "my_forum.json"


def test_add_template_updates_cache(tmp_path, sample_template):
    """После добавления шаблон доступен через get_template_by_name."""
    manager, _ = make_manager(tmp_path)
    run(manager.add_template(sample_template))
    result = run(manager.get_template_by_name("XenForo"))
    assert result is not None


# ---------------------------------------------------------------------------
# _generate_filename
# ---------------------------------------------------------------------------

def test_generate_filename_from_name():
    manager = TemplateManager()
    assert manager._generate_filename({"name": "My Forum"}) == "My_Forum"


def test_generate_filename_from_domain():
    manager = TemplateManager()
    assert manager._generate_filename({"domain": "example.com"}) == "example.com"


def test_generate_filename_name_priority_over_domain():
    manager = TemplateManager()
    result = manager._generate_filename({"name": "TestForum", "domain": "test.com"})
    assert result == "TestForum"


def test_generate_filename_cleans_special_chars():
    manager = TemplateManager()
    result = manager._generate_filename({"name": 'Forum/Name:With*Chars"<>|'})
    for bad in r'/\:*?"<>|':
        assert bad not in result


def test_generate_filename_fallback_to_timestamp():
    manager = TemplateManager()
    result = manager._generate_filename({})
    assert result.isdigit()


# ---------------------------------------------------------------------------
# Интеграционные тесты
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_detect_xenforo_live():
    """Интеграционный: тест на реальном сайте."""
    import aiohttp

    async def _run():
        manager = TemplateManager("templates/known_forums")
        async with aiohttp.ClientSession() as session:
            async with session.get("https://xenforo.com/community/register/") as resp:
                html = await resp.text()
        return await manager.detect_template("https://xenforo.com/community/register/", html)

    result = run(_run())
    assert result is not None
    assert result["name"] == "XenForo"
