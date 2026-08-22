"""Ticket D — `_run_tool_loop` 循环可测行为（纯内存 stub，不启真 agent/网络/子进程）。

注入一个可控假 llm：连续 2 轮带 tool_calls + 1 轮 SELF_CHECK_PASSED。
断言循环退出、final_output 含自检、messages 结构与现状一致、logging 前缀。
"""

import logging

from langchain_core.messages import AIMessage

from mathmodelingagents.agents import _run_tool_loop

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