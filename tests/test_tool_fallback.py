"""llm_clients 工具路径降级链测试（monkeypatch，不发起真实网络）。

替换 mathmodelingagents.llm_clients.create_llm_client 返回 fake 客户端，
验证 invoke_with_tools_with_fallback 的 4 步降级行为：
- 第 1 步（主 provider+角色模型）失败 → 降级第 2 步（fallback provider+同模型）成功
- 4 步全部失败 → 抛 RuntimeError
"""

import logging

import pytest
from langchain_core.messages import AIMessage

import mathmodelingagents.llm_clients as llm_clients
from mathmodelingagents.llm_clients import (
    EmptyLLMResponseError,
    invoke_with_tools_with_fallback,
)

CONFIG = {
    "llm_provider": "opencode",
    "fallback_provider": "deepseek",
    "quick_think_llm": "deepseek-v4-flash",
    "layer_timeouts": {},
    "temperature_overrides": {},
    "default_temperature": 0.0,
    "default_max_tokens": 1024,
}

TOOL_CALL = {"name": "noop", "args": {}, "id": "call_1"}


class FakeClient:
    """替身客户端：bind_tools 返回自身，invoke 按脚本返回 AIMessage 或抛错。"""

    def __init__(self, script):
        self.script = list(script)

    def bind_tools(self, tools):
        return self

    def invoke(self, messages):
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def test_fallback_succeeds_on_step2(monkeypatch, caplog):
    monkeypatch.setattr("time.sleep", lambda *a, **k: None)

    # 第 1 步：每次 invoke 都返回空响应 → 3 次重试耗尽后降级
    step1_client = FakeClient([EmptyLLMResponseError(0)] * 3)
    # 第 2 步：返回带 tool_calls 的 AIMessage
    step2_client = FakeClient([AIMessage(content="ok", tool_calls=[TOOL_CALL])])
    created = []

    def fake_create_llm_client(**kwargs):
        created.append(kwargs)
        return step1_client if len(created) == 1 else step2_client

    monkeypatch.setattr(llm_clients, "create_llm_client", fake_create_llm_client)

    with caplog.at_level(logging.INFO, logger="mathmodelingagents.llm_clients"):
        response = invoke_with_tools_with_fallback(
            CONFIG, "implementation", "coder",
            tools=[], messages=[], agent_name="SolverAgent",
        )

    # 返回第 2 步的完整 response 对象（含 tool_calls，不被修改）
    assert len(response.tool_calls) == 1
    assert response.tool_calls[0]["name"] == "noop"
    assert response.tool_calls[0]["id"] == "call_1"
    assert response.content == "ok"
    # create_llm_client 被调 2 次（step1 失败 + step2 成功）
    assert len(created) == 2
    # 日志含"降级"
    assert any("降级" in r.message for r in caplog.records)


def test_fallback_all_steps_fail_raises_runtime_error(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda *a, **k: None)
    calls = []

    def fake_create_llm_client(**kwargs):
        calls.append(kwargs)
        return FakeClient([RuntimeError("provider down")])

    monkeypatch.setattr(llm_clients, "create_llm_client", fake_create_llm_client)

    with pytest.raises(RuntimeError, match="降级链全部失败"):
        invoke_with_tools_with_fallback(
            CONFIG, "implementation", "coder",
            tools=[], messages=[], agent_name="SolverAgent",
        )

    # 4 步降级链全部尝试
    assert len(calls) == 4
