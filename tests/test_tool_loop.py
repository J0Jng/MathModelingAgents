"""Ticket D — `_run_tool_loop` 循环可测行为（纯内存 stub，不启真 agent/网络/子进程）。

注入一个可控假 llm：连续 2 轮带 tool_calls + 1 轮 SELF_CHECK_PASSED。
断言循环退出、final_output 含自检、messages 结构与现状一致、logging 前缀。
"""

import logging

import pytest
from langchain_core.messages import AIMessage

from mathmodelingagents.agents import _run_tool_loop
from mathmodelingagents.llm_clients import EmptyLLMResponseError

TOOL_CALL = {"name": "noop", "args": {}, "id": "call_1"}


def scripted_responses():
    return [
        AIMessage(content="", tool_calls=[TOOL_CALL]),
        AIMessage(content="", tool_calls=[TOOL_CALL]),
        AIMessage(content="SELF_CHECK_PASSED 求解完成"),
    ]


class FakeToolLLM:
    """替身 LLM：按顺序吐出脚本化响应，记录每次 invoke 的入参。"""

    def __init__(self, responses):
        self.responses = list(responses)
        self.invoke_calls = []

    def invoke(self, messages):
        self.invoke_calls.append(list(messages))
        return self.responses.pop(0)

    def bind_tools(self, tools):
        return self


class TestRunToolLoop:
    def test_selfcheck_loop_exits_with_output(self, caplog):
        caplog.set_level(logging.INFO)
        fake = FakeToolLLM(scripted_responses())

        messages, final_output = _run_tool_loop(
            llm=fake,
            llm_with_tools=fake,
            tools=[],
            layer_tag="Layer3",
            agent_tag="SolverAgent",
            max_iterations=30,
            initial_messages=[],
        )

        # 循环正常退出，final_output 含自检标记
        assert "SELF_CHECK_PASSED" in final_output
        # 恰好发生在 3 轮后（第 3 轮自检退出，未耗尽 30）
        assert len(fake.invoke_calls) == 3

        # messages 结构：3 个 AIMessage + 2 个 ToolMessage（前两轮 tool_calls 各一个）
        assert len(messages) == 5
        assert sum(1 for m in messages if getattr(m, "type", "") == "ai") == 3
        assert sum(1 for m in messages if getattr(m, "type", "") == "tool") == 2

        # logging 前缀保留（不破坏依赖日志的测试/运维）
        assert any(
            "[Layer3] SolverAgent iteration" in r.message for r in caplog.records
        )

    def test_empty_response_retries_then_succeeds(self, monkeypatch):
        monkeypatch.setattr("time.sleep", lambda *a, **k: None)
        fake = FakeToolLLM([
            AIMessage(content=""),                          # 空响应 → 软失败重试
            AIMessage(content=""),                          # 空响应 → 软失败重试
            AIMessage(content="SELF_CHECK_PASSED 求解完成"),
        ])
        messages, final_output = _run_tool_loop(
            llm=fake, llm_with_tools=fake, tools=[],
            layer_tag="Layer3", agent_tag="SolverAgent",
            max_iterations=30, initial_messages=[],
        )
        assert "SELF_CHECK_PASSED" in final_output
        assert len(fake.invoke_calls) == 3  # 2 次空响应重试 + 1 次成功

    def test_all_empty_raises(self, monkeypatch):
        monkeypatch.setattr("time.sleep", lambda *a, **k: None)
        fake = FakeToolLLM([AIMessage(content="") for _ in range(3)])
        with pytest.raises(EmptyLLMResponseError):
            _run_tool_loop(
                llm=fake, llm_with_tools=fake, tools=[],
                layer_tag="Layer3", agent_tag="SolverAgent",
                max_iterations=30, initial_messages=[],
            )


class TestInvokeFnInjection:
    def test_invoke_fn_is_used_when_provided(self):
        fake = FakeToolLLM(scripted_responses())
        responses = scripted_responses()
        invoke_fn_calls = []

        def fake_invoke_fn(messages):
            invoke_fn_calls.append(list(messages))
            return responses.pop(0)

        messages, final_output = _run_tool_loop(
            llm=None,
            llm_with_tools=None,
            tools=[],
            layer_tag="Layer2",
            agent_tag="modeler_a",
            max_iterations=10,
            initial_messages=[],
            invoke_fn=fake_invoke_fn,
        )

        # invoke_fn 被调用 3 次（2 次带 tool_calls + 1 次 SELF_CHECK_PASSED）
        assert len(invoke_fn_calls) == 3
        # llm_with_tools 完全未被使用
        assert fake.invoke_calls == []
        assert "SELF_CHECK_PASSED" in final_output

    def test_invoke_fn_exhausted_propagates(self):
        def failing_invoke_fn(messages):
            raise RuntimeError("降级链全部失败")

        with pytest.raises(RuntimeError, match="降级链全部失败"):
            _run_tool_loop(
                llm=None,
                llm_with_tools=None,
                tools=[],
                layer_tag="Layer2",
                agent_tag="modeler_a",
                max_iterations=10,
                initial_messages=[],
                invoke_fn=failing_invoke_fn,
            )