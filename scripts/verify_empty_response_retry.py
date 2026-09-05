"""Ad-hoc 验证：空 LLM 响应按可重试瞬态故障处理（2s→4s→8s 指数退避重试 3 次）。

对应 brief: docs/modeler-empty-response-retry-brief.md
用 unittest.mock.patch 把 _time.sleep 换成 no-op，避免真实等待。

用法: python scripts/verify_empty_response_retry.py
"""

import sys
import unittest.mock as mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mathmodelingagents.llm_clients import (  # noqa: E402
    EmptyLLMResponseError,
    _invoke_with_retry,
    is_retryable_error,
)

PASS = []


def check(name: str, cond: bool):
    if not cond:
        print(f"❌ {name}")
        raise AssertionError(name)
    print(f"✅ {name}")
    PASS.append(name)


class _Msg:
    def __init__(self, content: str):
        self.content = content


class _StubLLM:
    """按预设脚本依次返回/抛出异常的假 LLM。"""

    def __init__(self, script: list):
        self.script = script
        self.calls = 0

    def invoke(self, messages):
        item = self.script[min(self.calls, len(self.script) - 1)]
        self.calls += 1
        if isinstance(item, Exception):
            raise item
        return _Msg(item)


def main():
    # 1. is_retryable_error(EmptyLLMResponseError(0)) == True
    check(
        "is_retryable_error(EmptyLLMResponseError(0)) == True",
        is_retryable_error(EmptyLLMResponseError(0)) is True,
    )

    # 2. 普通 ValueError 不误伤
    check(
        'is_retryable_error(ValueError("其它错误")) == False',
        is_retryable_error(ValueError("其它错误")) is False,
    )

    # 3. 首回空响应 → 第二次成功返回内容（空包被重试后成功）
    stub = _StubLLM([
        EmptyLLMResponseError(0),
        "这是一段足够长的正常响应内容",
    ])
    with mock.patch("mathmodelingagents.llm_clients._time.sleep", lambda s: None):
        result = _invoke_with_retry(stub, [], "TestAgent", "test")
    check("空包重试后成功返回正常内容", result == "这是一段足够长的正常响应内容")
    check("空包场景共调用 2 次 LLM", stub.calls == 2)

    # 4. 每次都空 → 3 次耗尽后抛 EmptyLLMResponseError
    stub = _StubLLM([EmptyLLMResponseError(0)])
    with mock.patch("mathmodelingagents.llm_clients._time.sleep", lambda s: None):
        try:
            _invoke_with_retry(stub, [], "TestAgent", "test")
            check("全空场景抛出 EmptyLLMResponseError", False)
        except EmptyLLMResponseError as e:
            check("全空场景抛出 EmptyLLMResponseError", True)
            check("异常携带 char_count 属性", e.char_count == 0)
    check("全空场景共调用 3 次 LLM（耗尽 _MAX_RETRIES）", stub.calls == 3)

    # 5. 既有行为不回归：503 仍按原路径重试
    stub = _StubLLM([
        ValueError("Error code: 503 - upstream error"),
        "这是 503 重试后返回的正常长内容",
    ])
    with mock.patch("mathmodelingagents.llm_clients._time.sleep", lambda s: None):
        result = _invoke_with_retry(stub, [], "TestAgent", "test")
    check("503 重试后成功（原路径不回归）", result == "这是 503 重试后返回的正常长内容")
    check("503 场景共调用 2 次 LLM", stub.calls == 2)

    # 6. 不可重试异常仍立即抛出
    stub = _StubLLM([ValueError("invalid api key")])
    with mock.patch("mathmodelingagents.llm_clients._time.sleep", lambda s: None):
        try:
            _invoke_with_retry(stub, [], "TestAgent", "test")
            check("不可重试异常立即抛出", False)
        except ValueError as e:
            check("不可重试异常立即抛出", "invalid api key" in str(e))
    check("不可重试场景只调用 1 次 LLM", stub.calls == 1)

    print(f"\n✅ 全部通过（{len(PASS)} 项断言）")


if __name__ == "__main__":
    main()
