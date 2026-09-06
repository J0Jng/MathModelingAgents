# Implementation Brief: modeler 发言专用循环（方案 B）

## Background

Layer 2 建模师（modeler_a/b/c）是**辩论参与者**，一轮「发言」的产物是**文字方案**（含生成公式 + 验证代码 + 结果）。它绑定 run_code / web_search / model_search 三个工具，其中 run_code 只是辅助验证核心公式，不是最终目的。

但当前 `_make_modeler_node` 复用了 Solver 的 `_run_tool_loop` 骨架（`agents/__init__.py:1117`），而 Solver 骨架内置了「`consecutive_no_tool` 连续 3 轮无工具调用 → 强制中断」的终止逻辑。这个逻辑对 Solver（必须每轮写码/跑码）合理，对 modeler 却是错误的：modeler 输出文字方案是常态，一旦连续 3 轮没调工具就被误杀，且中断后 `_extract_final_output` 反向取「最后一条非工具文本」，拿到的是 modeler 空转的「确认/归档」废话而非完整方案。

实测复现（volcengine + ark-code-latest，真实 modeler prompt）：modeler 第 1/3/6 轮输出了 5k~7k 字高质量方案，第 4/7/8 轮空转废话后连空 3 次被「强制中断」，final_output 只剩 686 字「归档完毕」废话。辩论内容实际失效。

**根因**：用错了循环骨架——modeler 的终止语义应与 Solver 相反：**无工具调用 = 发言完成（正常返回），而非「卡死」**。

## Design

新增 modeler 专属循环 `_run_modeler_turn`，**不复用、不修改** `_run_tool_loop`（Solver/Viz/Paper 仍依赖它，测试锁定了其日志文案）。

语义：
- `invoke_fn` 返回带 `tool_calls` → 执行工具、喂回结果、继续（允许 modeler 继续 run_code 验证或 model_search/web_search 检索）。
- `invoke_fn` 返回纯文本（无 `tool_calls`）→ **本轮发言完成，立即以该文本为 result 返回**。
- 不再有 `consecutive_no_tool` 计数，不再有「强制中断」，不再反向取最后一条文本。
- `max_iterations` 仅作「连续疯狂调工具停不下来」的保底上限。

工具执行逻辑（`工具执行异常` / `未知工具` 分支、ToolMessage 构造、`logger.info("[Layer2] {agent} 工具 {name}: ...")` 文案）**逐字照搬** `_run_tool_loop` 内 393–427 行的实现，保持日志文案一致。

`invoke_fn`（即 `invoke_with_tools_with_fallback`）内部已做「空响应软失败重试 + 4 步降级链」，所以 `_run_modeler_turn` 不需要自己处理重试；它拿到的 `response` 一定是有 tool_calls 或有内容的有效响应。

## Change List

> ⚠️ 重要：**当前工作区是未提交状态**。`_make_modeler_node` 已从 HEAD 重构为「`invoke_fn` + `_run_tool_loop`」版本。请基于**当前工作区文件内容**修改，**不要** `git checkout` / `git reset` 到 HEAD，也不要动其他未提交文件。

### 1. `mathmodelingagents/agents/__init__.py`

**(a) 新增模块级函数 `_run_modeler_turn`**，放在 `_make_modeler_node`（当前约 1076 行）的正上方，Layer 2 区域。签名与实现如下（精确照抄，勿改逻辑）：

```python
def _run_modeler_turn(
    *,
    tools: list,
    layer_tag: str,
    agent_tag: str,
    max_iterations: int,
    initial_messages: list,
    invoke_fn: Callable[[list], Any],
) -> tuple[list, str]:
    """运行一轮建模师发言（辩论参与者专用，不复用 Solver 的 _run_tool_loop）。

    语义与 Solver 相反：建模师一轮发言以文字方案为产物，调用工具只是辅助验证。
    - 返回 tool_calls → 执行工具、喂回结果、继续（允许继续验证/检索）
    - 返回纯文本（无 tool_calls）→ 本轮发言完成，立即以该文本为 result 返回
    不再有「连续 N 轮无工具调用 → 强制中断」，也不再反向取最后一条文本。
    """
    import json as _json
    from langchain_core.messages import ToolMessage

    messages = list(initial_messages)
    for iteration in range(max_iterations):
        logger.info(
            f"[{layer_tag}] {agent_tag} iteration {iteration + 1}/{max_iterations}"
        )
        response = invoke_fn(messages)  # 内含降级链 + 空响应重试
        messages.append(response)

        if not getattr(response, "tool_calls", None):
            content = response.content or ""
            return messages, content

        for tc in response.tool_calls:
            tool_name = tc.get("name", "")
            tool_args = tc.get("args", {})
            tool_id = tc.get("id", "")

            tool_fn = None
            for t in tools:
                if t.name == tool_name:
                    tool_fn = t
                    break

            if tool_fn is not None:
                try:
                    result = tool_fn.invoke(tool_args)
                except Exception as e:
                    result = f"[工具执行异常] {tool_name}: {e}"
                    logger.error(
                        f"[{layer_tag}] 工具 {tool_name} 执行失败: {e}"
                    )
            else:
                result = f"[未知工具] {tool_name}"

            result_str = (
                _json.dumps(result, ensure_ascii=False)
                if isinstance(result, dict) else str(result)
            )
            messages.append(ToolMessage(
                content=result_str, tool_call_id=tool_id,
            ))
            logger.info(
                f"[{layer_tag}] {agent_tag} 工具 {tool_name}: "
                f"{result_str[:120]}..."
            )

    # 保底：耗尽迭代仍无纯文本（连续 tool_calls），取最后一条非工具文本
    return messages, _extract_final_output(messages)
```

**(b) 修改 `_make_modeler_node`**（当前 1116–1129 行的 try 块）：把 `_run_tool_loop(...)` 调用替换为 `_run_modeler_turn(...)`。新代码：

```python
        try:
            messages, result = _run_modeler_turn(
                tools=tools,
                layer_tag="Layer2",
                agent_tag=agent_name,
                max_iterations=10,
                initial_messages=messages,
                invoke_fn=_invoke_fn,
            )
        except Exception as e:
            logger.error(f"[Layer2] {agent_name} 发言失败: {e}")
            result = f"LLM 调用失败（全部降级耗尽）: {e}"
```

> 注意：异常日志文案从 `tool loop 失败` 改为 `发言失败`（modeler 已不再走 tool loop，旧文案误导），这是刻意更新。

**(c) 更新 `_make_modeler_node` 的 docstring**（当前 1082–1088 行）：删除「复用共享骨架 _run_tool_loop」的表述，改为说明「使用 modeler 专用循环 _run_modeler_turn，一次发言以纯文本方案为终止条件」。

### 2. `tests/test_modeler_turn.py`（新增）

```python
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
```

## Acceptance Criteria

运行（先 `export PYTHONPATH=`，用项目解释器 `.venv/Scripts/python.exe`）：

1. `python -m pytest tests/test_modeler_turn.py -v` → 3 passed。
2. `python -m pytest tests/ -v` → 全绿（尤其 `tests/test_tool_loop.py`、`tests/test_tool_fallback.py` 必须仍绿，证明 Solver/Viz/Paper 的 `_run_tool_loop` 未受影响）。
3. 导入冒烟：`python -c "from mathmodelingagents.agents import _run_modeler_turn, _make_modeler_node; print('OK')"`。
4. 自查：`_make_modeler_node` 中已无 `_run_tool_loop(` 调用；`_run_modeler_turn` 中无 `consecutive_no_tool`、无「强制中断」字样。

## Explicitly Do NOT Do

- ❌ 不要修改 `_run_tool_loop` 本体（Solver/Viz/Paper 依赖，测试锁定其日志文案）。
- ❌ 不要修改任何 prompt（`get_modeler_a_prompt` / `get_modeler_b_prompt` / `get_modeler_c_prompt` 及 prompt_templates.py 内一切内容）。
- ❌ 不要改 `_extract_final_output`、`_sanitize_tool_pairing`、graph 拓扑（`graph/setup.py`、`graph/conditional_logic.py`）、`DebateState`/`AgentState` 字段、`default_config.py`、`llm_clients/__init__.py`。
- ❌ 不要 `git checkout` / `git reset` —— 工作区是未提交状态，必须在其上继续改。
- ❌ 不要改动本改动之外的任何未提交文件（如其他正在编辑的 brief、tests、knowledge/）。
- ❌ 不要新增依赖、不要动 `pyproject.toml` / lock 文件。