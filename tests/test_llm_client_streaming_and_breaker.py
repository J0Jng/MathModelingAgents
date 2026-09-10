"""LLM 调用流式化 + 同通道连续超时熔断 的单元测试（impl-brief D1/D4）。"""

from types import SimpleNamespace

import pytest
from openai import APIConnectionError, APITimeoutError

from mathmodelingagents import llm_clients as lc
from mathmodelingagents.llm_clients import create_llm_client, invoke_with_fallback


class _TimeoutLLM:
    """每次 invoke 都抛 APITimeoutError 的假 LLM。"""

    def __init__(self):
        self.call_count = 0

    def invoke(self, messages):
        self.call_count += 1
        raise APITimeoutError(request=None)


class _ErrorSequenceLLM:
    """按给定异常序列依次抛出的假 LLM。"""

    def __init__(self, errors):
        self.errors = list(errors)
        self.call_count = 0

    def invoke(self, messages):
        idx = self.call_count
        self.call_count += 1
        if idx < len(self.errors):
            raise self.errors[idx]
        # 序列耗尽后默认继续超时
        raise APITimeoutError(request=None)


class _OkLLM:
    """返回合法文本的假 LLM。"""

    def __init__(self, content="this is a valid answer body"):
        self.content = content
        self.call_count = 0

    def invoke(self, messages):
        self.call_count += 1
        return SimpleNamespace(content=self.content)


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    """禁用退避 sleep，避免拖慢用例。"""
    monkeypatch.setattr("time.sleep", lambda s: None)


# ── 测试 1：create_llm_client 返回 streaming=True ──
def test_create_llm_client_streaming_true(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    client = create_llm_client("deepseek", "deepseek-v4-flash")
    assert client.streaming is True


# ── 测试 2：连续超时熔断，第 5 次超时后停止（consecutive_timeout_limit=5）──
def test_timeout_breaker_stops_after_limit():
    llm = _TimeoutLLM()
    with pytest.raises(APITimeoutError):
        lc._invoke_with_retry(llm, [{"role": "user", "content": "hi"}], "agent", "modeling")
    assert llm.call_count == 5  # consecutive_timeout_limit=5：连续超时熔断于第 5 次


# ── 测试 3：timeout 后跟非 timeout 可重试错误 → streak 重置，跑满 3 次 ──
def test_streak_resets_on_non_timeout_error():
    llm = _ErrorSequenceLLM(
        [
            APITimeoutError(request=None),
            APIConnectionError(message="connection reset", request=None),
            APITimeoutError(request=None),
        ]
    )
    with pytest.raises(APITimeoutError):
        lc._invoke_with_retry(llm, [{"role": "user", "content": "hi"}], "agent", "modeling")
    assert llm.call_count == lc._MAX_RETRIES


# ── 测试 4：fallback 循环对 timeout-only 失败的 step 跳到下一步 ──
def test_fallback_skips_stuck_channel(monkeypatch):
    seen_providers = []

    def fake_create_llm_client(provider=None, model=None, base_url=None, **kwargs):
        seen_providers.append(provider)
        if provider == "P":
            return _TimeoutLLM()
        return _OkLLM()

    monkeypatch.setattr(lc, "create_llm_client", fake_create_llm_client)

    config = {
        "llm_provider": "P",
        "fallback_provider": "F",
        "quick_think_llm": "m1",
        "layer_model_overrides": {"modeling": {"agent": "m1"}},
        "layer_timeouts": {"modeling": 90},
        "max_tokens_overrides": {},
        "temperature_overrides": {},
        "default_max_tokens": 1024,
        "default_temperature": 0.0,
        "provider_model_aliases": {},
        "provider_layer_model_overrides": {},
    }

    result = invoke_with_fallback(
        config, "modeling", "agent", [{"role": "user", "content": "hi"}], "test_agent"
    )

    assert seen_providers == ["P", "F"]
    assert result.startswith("[降级 F/m1]")
    assert "this is a valid answer body" in result