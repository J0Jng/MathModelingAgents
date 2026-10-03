"""_build_context modeling 层 previous_model_spec 注入测试（--diverge）。

- 非空时注入「禁止重复」文案 + 旧方案文本；
- 空串时不注入；
- 每轮注入（round_count=1 的第 2 轮仍注入，回归锚点）。
"""

from mathmodelingagents.agents import _build_context


def _state(extra=None):
    state = {"problem_description": "测试题目"}
    state.update(extra or {})
    return state


def test_previous_model_spec_injected_when_non_empty():
    state = _state({"previous_model_spec": "旧方案：线性规划模型"})
    ctx = _build_context(state, "modeling", "modeler_a", {})
    assert "禁止重复" in ctx
    assert "旧方案：线性规划模型" in ctx


def test_empty_previous_model_spec_not_injected():
    state = _state({"previous_model_spec": ""})
    ctx = _build_context(state, "modeling", "modeler_a", {})
    assert "禁止重复" not in ctx


def test_injected_on_second_round():
    """每轮注入：round_count=1（第 2 轮）仍包含禁止重复文案。"""
    state = _state({
        "previous_model_spec": "旧方案：线性规划模型",
        "model_debate_state": {"round_count": 1},
    })
    ctx = _build_context(state, "modeling", "modeler_a", {})
    assert "禁止重复" in ctx
    assert "旧方案：线性规划模型" in ctx
