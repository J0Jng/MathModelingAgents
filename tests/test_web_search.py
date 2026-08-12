"""Unit tests for web_search module — no real network calls."""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest

# Import under test
from mathmodelingagents.tools.web_search import (
    _get_provider,
    _format_results,
    _search_ddgs,
    _search_tavily,
    web_search,
)


# ═══════════════════════════════════════════════════════════════════════════════
# _get_provider
# ═══════════════════════════════════════════════════════════════════════════════


class TestGetProvider:
    def test_auto_with_tavily_key(self, monkeypatch):
        monkeypatch.setenv("TAVILY_API_KEY", "tvly-test123")
        monkeypatch.delenv("MATHMODELING_WEB_SEARCH_PROVIDER", raising=False)
        monkeypatch.delenv("MATHMODELING_TAVILY_API_KEY", raising=False)
        assert _get_provider() == "tavily"

    def test_auto_with_mathmodeling_tavily_key(self, monkeypatch):
        monkeypatch.delenv("TAVILY_API_KEY", raising=False)
        monkeypatch.setenv("MATHMODELING_TAVILY_API_KEY", "tvly-test456")
        monkeypatch.delenv("MATHMODELING_WEB_SEARCH_PROVIDER", raising=False)
        assert _get_provider() == "tavily"

    def test_auto_no_key(self, monkeypatch):
        monkeypatch.delenv("TAVILY_API_KEY", raising=False)
        monkeypatch.delenv("MATHMODELING_TAVILY_API_KEY", raising=False)
        monkeypatch.delenv("MATHMODELING_WEB_SEARCH_PROVIDER", raising=False)
        assert _get_provider() == "ddgs"

    def test_explicit_tavily(self, monkeypatch):
        monkeypatch.setenv("MATHMODELING_WEB_SEARCH_PROVIDER", "tavily")
        assert _get_provider() == "tavily"

    def test_explicit_ddgs(self, monkeypatch):
        monkeypatch.setenv("MATHMODELING_WEB_SEARCH_PROVIDER", "ddgs")
        assert _get_provider() == "ddgs"

    def test_explicit_off(self, monkeypatch):
        monkeypatch.setenv("MATHMODELING_WEB_SEARCH_PROVIDER", "off")
        assert _get_provider() == "off"

    def test_unknown_provider(self, monkeypatch):
        monkeypatch.setenv("MATHMODELING_WEB_SEARCH_PROVIDER", "unknown-provider")
        assert _get_provider() == "off"


# ═══════════════════════════════════════════════════════════════════════════════
# _format_results
# ═══════════════════════════════════════════════════════════════════════════════


class TestFormatResults:
    def test_basic_format(self):
        results = [
            {"title": "Test Title", "url": "https://example.com", "content": "Some content here"},
        ]
        out = _format_results("test query", "tavily", results)
        assert "### 搜索结果: test query（provider: tavily, 1 条）" in out
        assert "1. [Test Title](https://example.com)" in out
        assert "   Some content here" in out

    def test_content_truncation(self):
        long_content = "x" * 300
        results = [{"title": "T", "url": "https://a.com", "content": long_content}]
        out = _format_results("q", "ddgs", results)
        # Content should be truncated to 200 chars + "…"
        assert "…" in out
        # The line with content should not exceed 200 + "…"
        content_line = out.split("\n")[-1]
        # Strip leading spaces and check
        assert len(long_content[:200] + "…") <= 201

    def test_multiple_results(self):
        results = [
            {"title": "A", "url": "https://a.com", "content": "aaa"},
            {"title": "B", "url": "https://b.com", "content": "bbb"},
        ]
        out = _format_results("q", "tavily", results)
        assert "2 条" in out
        assert "1. [A](https://a.com)" in out
        assert "2. [B](https://b.com)" in out


# ═══════════════════════════════════════════════════════════════════════════════
# web_search error handling / fallback / off
# ═══════════════════════════════════════════════════════════════════════════════


class TestWebSearchOff:
    def test_provider_off_returns_disabled(self, monkeypatch):
        monkeypatch.setenv("MATHMODELING_WEB_SEARCH_PROVIDER", "off")
        result = web_search("anything")
        assert result.startswith("[搜索未启用]")


class TestWebSearchExceptionSwallowing:
    def test_both_providers_fail(self, monkeypatch):
        """When both tavily and ddgs throw, web_search returns failure text without raising."""
        monkeypatch.setenv("MATHMODELING_WEB_SEARCH_PROVIDER", "tavily")
        monkeypatch.setenv("TAVILY_API_KEY", "tvly-fake")

        with patch(
            "mathmodelingagents.tools.web_search._search_tavily",
            side_effect=RuntimeError("tavily boom"),
        ), patch(
            "mathmodelingagents.tools.web_search._search_ddgs",
            side_effect=RuntimeError("ddgs boom"),
        ):
            result = web_search("test")
            assert result.startswith("[搜索失败]")
            # Should not raise


class TestWebSearchFallback:
    def test_tavily_fails_ddgs_succeeds(self, monkeypatch):
        """When tavily fails but ddgs succeeds, return ddgs results."""
        monkeypatch.setenv("MATHMODELING_WEB_SEARCH_PROVIDER", "tavily")
        monkeypatch.setenv("TAVILY_API_KEY", "tvly-fake")

        fallback_results = [
            {"title": "DDGS Result", "url": "https://ddg.com", "content": "from ddgs"},
        ]

        with patch(
            "mathmodelingagents.tools.web_search._search_tavily",
            side_effect=RuntimeError("tavily down"),
        ), patch(
            "mathmodelingagents.tools.web_search._search_ddgs",
            return_value=fallback_results,
        ):
            result = web_search("test")
            assert "DDGS Result" in result
            assert "ddgs" in result  # relabeled provider in output


# ═══════════════════════════════════════════════════════════════════════════════
# _search_ddgs lazy import
# ═══════════════════════════════════════════════════════════════════════════════


class TestDDGSLazyImport:
    def test_ddgs_not_installed_returns_empty(self, monkeypatch):
        """When ddgs is not importable, _search_ddgs returns empty list."""
        # Simulate import error by patching __import__ in the module
        import builtins
        original_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
            if name == "ddgs":
                raise ImportError("No module named 'ddgs'")
            return original_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=mock_import):
            result = _search_ddgs("test")
            assert result == []
