# -*- coding: utf-8 -*-
"""辩论历史滚动窗口（3 轮）测试。

核心原则：落盘全量、喂 prompt 精简——history 字段全量累积，
仅在 _build_context 组装 prompt 时用 _take_last_rounds 裁剪到最近 K 轮。
"""

import pytest

from mathmodelingagents.agents import _build_context, _take_last_rounds


def _make_history(n_rounds: int) -> str:
    """按 _make_modeler_node 的累积格式构造 n 轮历史。"""
    parts = []
    for i in range(1, n_rounds + 1):
        parts.append(f"--- 第 {i} 轮 ---\n\n这是第 {i} 轮的发言正文。")
    return "\n\n".join(parts)


class TestTakeLastRounds:
    def test_five_rounds_k3_keeps_last_three(self):
        history = _make_history(5)
        result = _take_last_rounds(history, 3)
        assert "--- 第 5 轮 ---" in result
        assert "--- 第 4 轮 ---" in result
        assert "--- 第 3 轮 ---" in result
        assert "--- 第 1 轮 ---" not in result
        assert "--- 第 2 轮 ---" not in result

    def test_rounds_le_k_returned_unchanged(self):
        history = _make_history(2)
        assert _take_last_rounds(history, 3) == history

    def test_empty_string_returns_empty(self):
        assert _take_last_rounds("", 3) == ""

    def test_no_marker_text_returned_unchanged(self):
        history = "没有任何轮次分隔符的一段发言。"
        assert _take_last_rounds(history, 3) == history

    def test_inline_round_mention_not_split(self):
        # 发言正文里内联「第 1 轮」字样（非独占一行）不得被误认为分隔符
        history = (
            "--- 第 1 轮 ---\n\n我在第 1 轮提到过这个思路。\n"
            "--- 第 2 轮 ---\n\n回顾第 1 轮的假设，此处文字包含『--- 第 1 轮 ---』字样但不独占一行。"
        )
        result = _take_last_rounds(history, 1)
        # 两个真实轮段 > k=1，只保留第 2 轮段；但正文里的内联字样必须原样保留
        assert result.startswith("--- 第 2 轮 ---")
        assert "『--- 第 1 轮 ---』字样" in result


class TestBuildContextWindow:
    def _state(self, a_history: str) -> dict:
        return {
            "problem_description": "测试题目",
            "model_debate_state": {
                "a_history": a_history,
                "b_history": "",
                "c_history": "",
                "round_count": 5,
            },
        }

    def test_build_context_applies_window(self):
        state = self._state(_make_history(5))
        context = _build_context(state, "modeling", "modeler_b", {})
        assert "--- 第 5 轮 ---" in context
        assert "--- 第 1 轮 ---" not in context

    def test_build_context_keeps_all_when_le_window(self):
        state = self._state(_make_history(3))
        context = _build_context(state, "modeling", "modeler_b", {})
        for i in (1, 2, 3):
            assert f"--- 第 {i} 轮 ---" in context

    def test_build_context_reads_window_from_config(self):
        state = self._state(_make_history(5))
        context = _build_context(state, "modeling", "modeler_b", {"debate_history_window": 2})
        assert "--- 第 5 轮 ---" in context
        assert "--- 第 4 轮 ---" in context
        assert "--- 第 3 轮 ---" not in context
        assert "--- 第 1 轮 ---" not in context
