"""_run_modeler_turn 行为测试（纯内存 stub，不发网络）。

验证 modeler 专用循环的三条语义（与 Solver 骨架相反）：
1. 有 tool_calls → 执行工具并继续，直到纯文本发言为止（result = 最后一次纯文本）
2. 第一轮就是纯文本 → 立即返回该文本（不空转、不「强制中断」）
3. 连续 tool_calls 耗尽 → 保底返回最后一条非工具文本
"""

from langchain_core.messages import AIMessage

from mathmodelingagents.agents import _run_modeler_turn


def _resp(content="", tool_calls=None):
    return AIMessage(content=content, tool_calls=tool_calls or [])


CALL = {"name": "noop", "args": {}, "id": "call_1"}


class FakeTool:
    def __init__(self, name):
        self.name = name
        self.invoked = []

    def invoke(self, args):
        self.invoked.append(args)
        return {"ok": True}


def test_tool_then_text_returns_last_text():
    tool = FakeTool("noop")
    script = [_resp(tool_calls=[CALL]), _resp(content="完整方案，已完成验证")]

    def invoke_fn(msgs):
        return script.pop(0)

    messages, result = _run_modeler_turn(
        tools=[tool], layer_tag="Layer2", agent_tag="modeler_a",
        max_iterations=10, initial_messages=[], invoke_fn=invoke_fn,
    )
    assert result == "完整方案，已完成验证"
    assert tool.invoked == [{}]          # 工具确实被执行
    assert len(messages) == 3            # AIMessage(tool) + ToolMessage + AIMessage(text)


def test_first_text_exits_immediately():
    def invoke_fn(msgs):
        return _resp(content="直接给出方案")

    messages, result = _run_modeler_turn(
        tools=[], layer_tag="Layer2", agent_tag="modeler_b",
        max_iterations=10, initial_messages=[], invoke_fn=invoke_fn,
    )
    assert result == "直接给出方案"
    assert len(messages) == 1


def test_exhausted_tool_calls_returns_last_text():
    tool = FakeTool("noop")

    def invoke_fn(msgs):
        return _resp(tool_calls=[CALL])   # 永远只调工具，不给方案

    messages, result = _run_modeler_turn(
        tools=[tool], layer_tag="Layer2", agent_tag="modeler_c",
        max_iterations=3, initial_messages=[], invoke_fn=invoke_fn,
    )
    assert result == ""                  # 无纯文本 → 空字符串（异常态保底）
    assert len(messages) == 6            # 3 轮 × (AIMessage + ToolMessage)
