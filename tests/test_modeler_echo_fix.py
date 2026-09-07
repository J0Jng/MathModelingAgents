# -*- coding: utf-8 -*-
"""modeler 发言回显 bug 修复测试（docs/modeler-echo-fix-impl-brief.md）。

背景：火山 Agent Plan 通道下 deepseek-v4-pro 长上下文 + 多工具场景持续返回
空 content + tool_calls，_run_modeler_turn 耗尽迭代后 _extract_final_output
反向遍历只排除 tool 消息，导致输入 HumanMessage 被原样当「最终发言」返回。

修复语义：
1. _extract_final_output 只认 AIMessage（type=='ai'），找不到返回空串；
2. _run_modeler_turn 保底挖到空串时返回明确失败占位；
3. 连续 5 次空转（tool_calls + 空 content）注入纯文本提醒 HumanMessage；
4. _build_context 辩论轮次显示 modeling 层用 max_modeling_rounds。

全部 mock，不发真实 LLM 请求。
"""

from langchain_core.messages import (
    AIMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_core.tools import tool

from mathmodelingagents.agents import (
    _build_context,
    _extract_final_output,
    _run_modeler_turn,
)

USER_TEXT = "请根据以下上下文执行你的任务...## 题目内容..."  # 修复前被回显的输入


def _tool_call_msg(i: int, name: str = "fake_tool") -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[{
            "name": name,
            "args": {"query": "x"},
            "id": f"c{i}",
            "type": "tool_call",
        }],
    )


def _all_tool_calls_messages() -> list:
    """全 tool_calls 序列：System/Human 开头，之后 AIMessage(tool_calls) 与 ToolMessage 交替。"""
    msgs = [
        SystemMessage(content="系统提示"),
        HumanMessage(content=USER_TEXT),
    ]
    for i in range(3):
        msgs.append(_tool_call_msg(i))
        msgs.append(ToolMessage(content="tool-result", tool_call_id=f"c{i}"))
    return msgs


@tool
def fake_tool(query: str) -> str:
    """假工具：返回固定字符串，不发真实请求。"""
    return "fake-result"


class TestExtractFinalOutput:
    def test_all_tool_calls_returns_empty(self):
        """回显 bug 基线翻转：全 tool_calls 序列必须返回空串（修复前返回 USER_TEXT 原文）。"""
        assert _extract_final_output(_all_tool_calls_messages()) == ""

    def test_trailing_ai_text_returned(self):
        msgs = _all_tool_calls_messages()
        msgs.append(AIMessage(content="我的建模方案是……"))
        assert _extract_final_output(msgs) == "我的建模方案是……"

    def test_human_input_never_selected(self):
        """System/Human 之后直接是带 tool_calls 的 AIMessage → 空串（输入不是发言）。"""
        msgs = [
            SystemMessage(content="系统提示"),
            HumanMessage(content=USER_TEXT),
            _tool_call_msg(0),
        ]
        assert _extract_final_output(msgs) == ""

    def test_empty_messages_returns_empty(self):
        assert _extract_final_output([]) == ""


class TestRunModelerTurn:
    def _stub(self, responses: list):
        """按调用次序返回 responses，耗尽后重复最后一个。"""
        state = {"calls": 0}

        def invoke_fn(messages):
            idx = min(state["calls"], len(responses) - 1)
            state["calls"] += 1
            return responses[idx]

        return invoke_fn, state

    def test_exhausted_iterations_yields_failure_placeholder(self):
        """耗尽 max_iterations 仍全 tool_calls → 返回明确失败占位（修复前是输入回显）。"""
        invoke_fn, state = self._stub([_tool_call_msg(0)])
        _, result = _run_modeler_turn(
            tools=[fake_tool],
            layer_tag="modeling",
            agent_tag="modeler_a",
            max_iterations=3,
            initial_messages=[
                SystemMessage(content="系统提示"),
                HumanMessage(content=USER_TEXT),
            ],
            invoke_fn=invoke_fn,
        )
        assert state["calls"] == 3
        assert "发言生成失败" in result
        assert "模型连续 3 次" in result
        assert USER_TEXT not in result  # 输入不得被当发言回显

    def test_pure_text_on_second_call_wins(self):
        """第 2 次调用返回纯文本 → 以该文本为发言返回。"""
        invoke_fn, state = self._stub([
            _tool_call_msg(0),
            AIMessage(content="我的建模方案是……"),
        ])
        _, result = _run_modeler_turn(
            tools=[fake_tool],
            layer_tag="modeling",
            agent_tag="modeler_a",
            max_iterations=10,
            initial_messages=[HumanMessage(content=USER_TEXT)],
            invoke_fn=invoke_fn,
        )
        assert state["calls"] == 2
        assert result == "我的建模方案是……"

    def test_soft_intervention_injected_after_five_tool_only_turns(self):
        """连续 5 次空转（tool_calls + 空 content）→ 注入纯文本提醒 HumanMessage。"""
        invoke_fn, state = self._stub([_tool_call_msg(0)])
        messages, _ = _run_modeler_turn(
            tools=[fake_tool],
            layer_tag="modeling",
            agent_tag="modeler_a",
            max_iterations=6,
            initial_messages=[HumanMessage(content=USER_TEXT)],
            invoke_fn=invoke_fn,
        )
        assert state["calls"] == 6
        nudges = [
            m for m in messages
            if getattr(m, "type", "") == "human" and "请停止调用工具" in (m.content or "")
        ]
        assert len(nudges) == 1


class TestBuildContextRounds:
    CONFIG = {"max_modeling_rounds": 5, "max_debate_rounds": 10}

    def test_modeling_layer_uses_max_modeling_rounds(self):
        state = {
            "problem_description": "测试题目",
            "model_debate_state": {"round_count": 1},
        }
        context = _build_context(state, "modeling", "modeler_a", self.CONFIG)
        assert "辩论轮次: 1/5" in context
        assert "辩论轮次: 1/10" not in context

    def test_other_layers_keep_max_debate_rounds(self):
        state = {
            "problem_description": "测试题目",
            "model_debate_state": {"round_count": 1},
        }
        context = _build_context(state, "problem", "decomposer", self.CONFIG)
        assert "辩论轮次: 1/10" in context
        assert "辩论轮次: 1/5" not in context
