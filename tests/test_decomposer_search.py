"""Tests for create_decomposer pre-search enhancement — no real network/LLM calls.

Covers:
- Successful dual-query injection → background_research populated
- Search failure (returned error text) → background_research empty, LLM still called
- Search exception → silently swallowed, background_research empty
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from mathmodelingagents.agents import create_decomposer


# ═══════════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════════

def _make_state(problem_description: str) -> dict:
    """Build a minimal state dict with just the fields create_decomposer uses via .get()."""
    return {
        "problem_description": problem_description,
        "model_debate_state": {},
        "layer_outputs": [],
        "impl_retry_count": 0,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# Test: successful dual-query injection
# ═══════════════════════════════════════════════════════════════════════════════

def test_background_research_success():
    """Both queries succeed → background_research is non-empty and contains both sections."""
    state = _make_state(
        "2024 MCM Problem A: Resource Availability and Sex Ratios\n\n"
        "Paragraph two about details of the problem. More text here."
        " " * 200  # padding to ensure first_line >= 10 chars
    )

    def fake_web_search(query: str, max_results: int = 5) -> str:
        if "建模" in query:
            return "### 搜索结果: ... 数学建模（provider: ddgs, 1 条）\n1. [Modeling](https://x.com)\n   content"
        else:
            return "### 搜索结果: Resource Availability（provider: ddgs, 1 条）\n1. [BG](https://x.com)\n   bg content"

    with patch(
        "mathmodelingagents.agents.invoke_with_fallback",
        return_value="## 问题拆解报告 — Decomposer\n\n...fake response...",
    ), patch(
        "mathmodelingagents.tools.web_search.web_search",
        side_effect=fake_web_search,
    ):
        node = create_decomposer({})
        result = node(state)

    assert "background_research" in result
    assert result["background_research"] != ""
    assert "查询 1（题目背景）" in result["background_research"]
    assert "查询 2（建模方法参考）" in result["background_research"]
    assert result["problem_report"]  # LLM was still called


def test_background_research_single_query():
    """When first_line is too short (< 10 chars), only query 1 runs."""
    state = _make_state("Short.")

    def fake_web_search(query: str, max_results: int = 5) -> str:
        return "### 搜索结果: ...（provider: ddgs, 1 条）\n1. [X](https://x.com)\n   content"

    with patch(
        "mathmodelingagents.agents.invoke_with_fallback",
        return_value="## 问题拆解报告 — Decomposer\n\n...fake response...",
    ), patch(
        "mathmodelingagents.tools.web_search.web_search",
        side_effect=fake_web_search,
    ):
        node = create_decomposer({})
        result = node(state)

    assert result["background_research"] != ""
    assert "查询 1（题目背景）" in result["background_research"]
    assert "查询 2" not in result["background_research"]  # too short → skipped


# ═══════════════════════════════════════════════════════════════════════════════
# Test: search returns error text → silently skipped
# ═══════════════════════════════════════════════════════════════════════════════

def test_search_failure_text_returns_empty_background():
    """When web_search returns [搜索失败] text, background_research is empty, LLM still called."""
    state = _make_state(
        "2024 MCM Problem A: Resource Availability and Sex Ratios\n\n"
        "Paragraph two about details. " * 5
    )

    with patch(
        "mathmodelingagents.agents.invoke_with_fallback",
        return_value="## 问题拆解报告 — Decomposer\n\n...fake response...",
    ) as mock_llm, patch(
        "mathmodelingagents.tools.web_search.web_search",
        return_value="[搜索失败] tavily: timeout",
    ):
        node = create_decomposer({})
        result = node(state)

    assert result["background_research"] == ""
    mock_llm.assert_called_once()  # LLM still invoked


def test_search_disabled_text_returns_empty_background():
    """When web_search returns [搜索未启用] text, background_research is empty."""
    state = _make_state(
        "2024 MCM Problem A: Resource Availability and Sex Ratios\n\n"
        "Paragraph two about details. " * 5
    )

    with patch(
        "mathmodelingagents.agents.invoke_with_fallback",
        return_value="## 问题拆解报告 — Decomposer\n\n...fake response...",
    ) as mock_llm, patch(
        "mathmodelingagents.tools.web_search.web_search",
        return_value="[搜索未启用] 搜索功能当前已关闭。",
    ):
        node = create_decomposer({})
        result = node(state)

    assert result["background_research"] == ""
    mock_llm.assert_called_once()


# ═══════════════════════════════════════════════════════════════════════════════
# Test: search raises exception → silently swallowed
# ═══════════════════════════════════════════════════════════════════════════════

def test_search_exception_silently_swallowed():
    """When web_search raises, exception is not propagated; background_research is empty."""
    state = _make_state(
        "2024 MCM Problem A: Resource Availability and Sex Ratios\n\n"
        "Paragraph two about details. " * 5
    )

    with patch(
        "mathmodelingagents.agents.invoke_with_fallback",
        return_value="## 问题拆解报告 — Decomposer\n\n...fake response...",
    ) as mock_llm, patch(
        "mathmodelingagents.tools.web_search.web_search",
        side_effect=ConnectionError("network unreachable"),
    ):
        node = create_decomposer({})
        result = node(state)  # must not raise

    assert result["background_research"] == ""
    mock_llm.assert_called_once()


def test_search_exception_no_problem_description():
    """When problem_description is empty, search is skipped entirely, no exception."""
    state = _make_state("")

    with patch(
        "mathmodelingagents.agents.invoke_with_fallback",
        return_value="## 问题拆解报告 — Decomposer\n\n...fake response...",
    ) as mock_llm:
        node = create_decomposer({})
        result = node(state)

    assert result["background_research"] == ""
    mock_llm.assert_called_once()
