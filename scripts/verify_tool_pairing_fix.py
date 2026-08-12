"""验证 _sanitize_tool_pairing 函数的正确性。

测试场景：
1. 孤立 ToolMessage（前一条消息无 tool_calls）→ 被丢弃
2. 末尾未完成的 AIMessage(tool_calls)（无 ToolMessage 跟随）→ 被丢弃
3. 空列表 → 安全返回
4. 全正常列表 → 原样保留
5. 多 tool_calls 单 AIMessage + 多 ToolMessage → 全部保留
6. 列表以孤立 ToolMessage 开头 → 被丢弃
7. tool_calls AIMessage content="" 正常配对 → 保留
"""

import sys
sys.path.insert(0, ".")

from langchain_core.messages import (
    SystemMessage, HumanMessage, AIMessage, ToolMessage,
)
from mathmodelingagents.agents import _sanitize_tool_pairing


def test_orphan_tool_message():
    """孤立 ToolMessage 应被丢弃，正常配对保留。

    构造: Sys → Human → AI(tool_calls=[tc1]) → Tool(tc1) → Tool(orphan) → AI(text)
    预期: Tool(orphan) 被丢弃，其余保留。
    """
    messages = [
        SystemMessage(content="system prompt"),
        HumanMessage(content="user question"),
        AIMessage(content="calling tool...", tool_calls=[
            {"name": "run_code", "args": {"code": "print(1)"}, "id": "call_1"}
        ]),
        ToolMessage(content="result: 1", tool_call_id="call_1"),
        # ── 截断切点：此 ToolMessage 的 AIMessage(tool_calls) 被切掉了 ──
        ToolMessage(content="orphan result", tool_call_id="call_2"),
        AIMessage(content="all done"),
    ]
    result = _sanitize_tool_pairing(messages)

    # 6条 → 5条（多余 ToolMessage 被丢弃）
    assert len(result) == 5, f"expected 5, got {len(result)}"

    assert result[0].type == "system"
    assert result[1].type == "human"
    assert getattr(result[2], "tool_calls", None) is not None  # AI(tool_calls)
    assert result[3].type == "tool"
    assert result[3].content == "result: 1"  # 正常配对保留
    assert result[4].type == "ai"  # 末尾文本 AIMessage 保留
    print("✅ test_orphan_tool_message: PASSED")


def test_trailing_unfinished_tool_calls():
    """末尾 AIMessage(tool_calls) 无 ToolMessage 跟随 → 被丢弃。"""
    messages = [
        SystemMessage(content="system"),
        HumanMessage(content="user"),
        AIMessage(content="normal response"),
        AIMessage(content="unfinished tool call", tool_calls=[
            {"name": "write_file", "args": {"path": "x.py"}, "id": "call_x"}
        ]),
    ]
    result = _sanitize_tool_pairing(messages)

    # 4条 → 3条（末尾未完成的 tool_calls AIMessage 被丢弃）
    assert len(result) == 3, f"expected 3, got {len(result)}"
    assert result[-1].type == "ai"
    # LangChain AIMessage 默认 tool_calls=[]（非 None），用 bool 检查
    assert not bool(getattr(result[-1], "tool_calls", None))
    print("✅ test_trailing_unfinished_tool_calls: PASSED")


def test_mid_list_unfinished_tool_calls():
    """中间 AIMessage(tool_calls) 无 ToolMessage 跟随 → 被丢弃。"""
    messages = [
        SystemMessage(content="system"),
        HumanMessage(content="user"),
        AIMessage(content="broken tool call", tool_calls=[
            {"name": "run_code", "args": {"code": "x"}, "id": "c1"}
        ]),
        # 缺少 ToolMessage — 直接跳到下一个 AIMessage
        AIMessage(content="next response"),
    ]
    result = _sanitize_tool_pairing(messages)

    assert len(result) == 3, f"expected 3, got {len(result)}"
    assert result[-1].type == "ai"
    assert result[-1].content == "next response"
    print("✅ test_mid_list_unfinished_tool_calls: PASSED")


def test_empty_list():
    """空列表安全返回。"""
    assert _sanitize_tool_pairing([]) == []
    print("✅ test_empty_list: PASSED")


def test_all_normal():
    """全正常列表原样保留。"""
    messages = [
        SystemMessage(content="system"),
        HumanMessage(content="user"),
        AIMessage(content="tool call 1", tool_calls=[
            {"name": "run_code", "args": {"code": "1+1"}, "id": "c1"}
        ]),
        ToolMessage(content="result: 2", tool_call_id="c1"),
        AIMessage(content="tool call 2", tool_calls=[
            {"name": "write_file", "args": {"path": "r.py"}, "id": "c2"}
        ]),
        ToolMessage(content="saved", tool_call_id="c2"),
        AIMessage(content="SELF_CHECK_PASSED"),
    ]
    result = _sanitize_tool_pairing(messages)
    assert len(result) == len(messages), f"expected {len(messages)}, got {len(result)}"
    print("✅ test_all_normal: PASSED")


def test_multi_tool_calls():
    """单个 AIMessage 含多个 tool_calls → 多个 ToolMessage 全部保留。"""
    messages = [
        SystemMessage(content="system"),
        HumanMessage(content="user"),
        AIMessage(content="multi call", tool_calls=[
            {"name": "run_code", "args": {"code": "1"}, "id": "c1"},
            {"name": "write_file", "args": {"path": "x.py"}, "id": "c2"},
        ]),
        ToolMessage(content="result: 1", tool_call_id="c1"),
        ToolMessage(content="saved", tool_call_id="c2"),
        AIMessage(content="done"),
    ]
    result = _sanitize_tool_pairing(messages)
    assert len(result) == 6, f"expected 6, got {len(result)}"
    assert result[3].type == "tool"
    assert result[4].type == "tool"
    print("✅ test_multi_tool_calls: PASSED")


def test_orphan_at_start():
    """列表以孤立 ToolMessage 开头 → 被丢弃。"""
    messages = [
        ToolMessage(content="orphan at start", tool_call_id="orphan"),
        SystemMessage(content="system"),
        HumanMessage(content="user"),
    ]
    result = _sanitize_tool_pairing(messages)
    assert len(result) == 2, f"expected 2, got {len(result)}"
    assert result[0].type == "system"
    print("✅ test_orphan_at_start: PASSED")


def test_normal_tool_no_text_ai():
    """tool_calls AIMessage 无文本内容（content=""），配对正常 → 保留。"""
    messages = [
        SystemMessage(content="system"),
        HumanMessage(content="user"),
        AIMessage(content="", tool_calls=[
            {"name": "run_code", "args": {"code": "1"}, "id": "c1"}
        ]),
        ToolMessage(content="result", tool_call_id="c1"),
        AIMessage(content="SELF_CHECK_PASSED"),
    ]
    result = _sanitize_tool_pairing(messages)
    assert len(result) == 5, f"expected 5, got {len(result)}"
    print("✅ test_normal_tool_no_text_ai: PASSED")


def test_partial_tool_messages():
    """AIMessage 含 2 个 tool_calls 但仅 1 个 ToolMessage → AIMessage 被丢弃。"""
    messages = [
        SystemMessage(content="system"),
        HumanMessage(content="user"),
        AIMessage(content="multi-call broken", tool_calls=[
            {"name": "run_code", "args": {"code": "1"}, "id": "c1"},
            {"name": "write_file", "args": {"path": "x.py"}, "id": "c2"},
        ]),
        ToolMessage(content="only one result", tool_call_id="c1"),
        # 缺少第二个 ToolMessage → 直接跳到 AIMessage
        AIMessage(content="after broken"),
    ]
    result = _sanitize_tool_pairing(messages)
    # 3条保留：System + Human + AIMessage("after broken")
    assert len(result) == 3, f"expected 3, got {len(result)}"
    # 不应该有 tool_calls AIMessage 残留
    for msg in result:
        assert not getattr(msg, "tool_calls", None), f"unexpected tool_calls in {msg}"
    print("✅ test_partial_tool_messages: PASSED")


if __name__ == "__main__":
    test_orphan_tool_message()
    test_trailing_unfinished_tool_calls()
    test_mid_list_unfinished_tool_calls()
    test_empty_list()
    test_all_normal()
    test_multi_tool_calls()
    test_orphan_at_start()
    test_normal_tool_no_text_ai()
    test_partial_tool_messages()
    print("\n🎉 全部 9 个断言通过！")
