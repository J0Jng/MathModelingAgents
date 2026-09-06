# Implementation Brief: `_run_tool_loop` 空响应软失败检测

## Background

Layer2 模型竞合循环（`_make_modeler_node` → `_run_tool_loop`）实际故障现象：

```
[Layer2] modeler_a LLM 调用重试 1/3, 2s: Connection error.
[Layer2] modeler_a 3 轮无工具调用，强制中断
```

链路：modeler_a 的 LLM 接口偶发 `Connection error` → 循环内 invoke 重试后「成功返回」，但返回的是 **HTTP 200、无 `tool_calls`、`content` 为空** 的软失败响应 → `_run_tool_loop` 把空响应当作「正常执行了一轮」，计入 `consecutive_no_tool` → 连续 3 轮 → 强制中断，输出空壳，辩论结果失效。

根因是两套调用路径行为不对等：

| | 无工具 agent（`invoke_with_fallback`） | 工具型 agent（`_run_tool_loop`） |
|---|---|---|
| 空响应检测 | ✅ `_invoke_with_retry` 里 `<10 字符` → `EmptyLLMResponseError` → 重试 | ❌ 无 |
| 降级链 | ✅ 4 步 provider/model 切换 | ❌ 无 |

`invoke_with_fallback` 那条链路已经通过 `docs/modeler-empty-response-retry-brief.md`（已实现，`EmptyLLMResponseError` 定义在 `llm_clients/__init__.py:46`）修好了，但 `_run_tool_loop` 的 invoke 重试（`agents/__init__.py:364-379`）**只 catch 网络/超时异常**，没有软失败判定。

本 brief 只补这一处：给 `_run_tool_loop` 的 invoke 重试补上「空响应 = 瞬态故障」判定，复用已存在的 `EmptyLLMResponseError`。

## Design

- **复用而非重造**：`EmptyLLMResponseError` 已在 `llm_clients/__init__.py` 定义且 `is_retryable_error()` 已识别它（返回 True）。直接 import 复用，不新增异常类型。
- **软失败判定条件**：成功拿到 `response` 后，若 **无 `tool_calls` 且 `content` 为空或过短（`len(content.strip()) < 10`）**，`raise EmptyLLMResponseError(len(content))`，让它落入已有的 `except → is_retryable_error → sleep 2s/4s/8s → 重试` 分支。
- **阈值 10 与 `llm_clients` 一致**（`_MIN_CONTENT_CHARS = 10`）。正常响应（带 `SELF_CHECK_PASSED` 的文本、或带 `tool_calls` 的空 content）不会被误判：带 `tool_calls` 时 `getattr(response, "tool_calls", None)` 为真，直接跳过软失败检测。
- **耗尽后的行为**：`max_retries`（3 次）重试后仍为空 → `raise`，由上层 `_make_modeler_node` 的 `try/except`（`agents/__init__.py:1114-1116`）捕获，`result` 变成清晰的 `"LLM 调用失败（全部降级耗尽）: ..."`，比「强制中断空壳」更可诊断。**不把空响应 append 进 messages 继续跑。**

## Change List

### 1. `mathmodelingagents/agents/__init__.py`

**(a) import 行（第 16 行）** 追加 `EmptyLLMResponseError`：

```python
from mathmodelingagents.llm_clients import invoke_with_fallback, resolve_max_tokens, is_retryable_error, create_layer_llm, EmptyLLMResponseError
```

**(b) `_run_tool_loop` 的 invoke 重试块（约 364-379 行）**，在 `response = llm_with_tools.invoke(messages)` 之后、`break` 之前插入软失败检测：

当前代码：

```python
        response = None
        for attempt in range(1, max_retries + 1):
            try:
                response = llm_with_tools.invoke(messages)
                break
            except Exception as e:
                if attempt < max_retries and is_retryable_error(e):
                    delay = 2 ** attempt
                    logger.warning(
                        f"[{layer_tag}] {agent_tag} LLM 调用重试 {attempt}/{max_retries}, "
                        f"{delay}s: {e}"
                    )
                    _time.sleep(delay)
                else:
                    raise
```

改为：

```python
        response = None
        for attempt in range(1, max_retries + 1):
            try:
                response = llm_with_tools.invoke(messages)
                # 软失败检测：无 tool_calls 且正文空/过短（HTTP 200 但空包），
                # 与 _invoke_with_retry 的空响应判定对齐，视为瞬态故障走重试。
                _content = getattr(response, "content", "") or ""
                if not getattr(response, "tool_calls", None) and len(_content.strip()) < 10:
                    raise EmptyLLMResponseError(len(_content))
                break
            except Exception as e:
                if attempt < max_retries and is_retryable_error(e):
                    delay = 2 ** attempt
                    logger.warning(
                        f"[{layer_tag}] {agent_tag} LLM 调用重试 {attempt}/{max_retries}, "
                        f"{delay}s: {e}"
                    )
                    _time.sleep(delay)
                else:
                    raise
```

注意：
- `EmptyLLMResponseError` 已 `is_retryable_error=True`，无需改 `is_retryable_error`。
- 日志文案沿用现有格式，从 `logger.warning(f"... LLM 调用重试 ...")` 可以自然看到空响应的重试记录（异常消息是「模型返回空/极短内容 (N 字符)，视为模型故障」）。
- 不要改 `consecutive_no_tool` 的判定逻辑（405-435 行）——那是「模型真的连续 N 轮只说话不调工具」的设计意图，与空响应是两码事，本 brief 不碰。

### 2. `tests/test_tool_loop.py`（新增测试，不改现有测试）

新增两个测试用例，复用现有 `FakeToolLLM` / `AIMessage` 写法，并在空响应重试场景下 patch 掉 `time.sleep`——**注意**：`agents/__init__.py` 里 `_run_tool_loop` 用 `import time as _time`（函数体内局部 import），`_time` 不是模块级属性，所以 patch 目标必须是标准库 `time.sleep`（`_time` 就是指代 `time` 模块对象，二者是同一个函数）：

```python
import pytest
from mathmodelingagents.llm_clients import EmptyLLMResponseError


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
```

（`test_all_empty_raises` 断言 `max_retries`（默认 3）耗尽后向外抛 `EmptyLLMResponseError`；`pytest` 和 `EmptyLLMResponseError` 的 import 加在文件顶部，与现有第 9-11 行的 import 区合并。）

## Acceptance Criteria（必须逐条跑通并报告实际输出）

1. `py_compile` 通过（或直接靠 pytest 的 import 证明无语法/import 错误）。
2. 新增两个测试通过：
   ```
   .venv/Scripts/python.exe -m pytest tests/test_tool_loop.py -v
   ```
   预期 3 passed（原 `test_selfcheck_loop_exits_with_output` + 2 个新增）。
3. 全量测试无回归：
   ```
   .venv/Scripts/python.exe -m pytest tests/ -q
   ```
4. 空响应重试行为真实生效（可用 ad-hoc 脚本验证或直接由 test 1 覆盖；测试 1 已断言 invoke 被调用 3 次即可）。

## Explicitly Do NOT Do

- ❌ 不要改 `consecutive_no_tool` 或「强制中断」的判定逻辑（405-435 行）。
- ❌ 不要改 `llm_clients/__init__.py`（它已有 `EmptyLLMResponseError`，无需动）。
- ❌ 不要把空响应 append 进 messages 或改成「静默继续」——耗尽必须 raise。
- ❌ 不要给 `_run_tool_loop` 加 provider 降级链（那是另一个 ticket，超出本 brief 范围）。
- ❌ 不要引入新依赖、不要动 pyproject.toml / lock 文件。
- ❌ 不要改现有 `test_selfcheck_loop_exits_with_output` 的行为。
- ❌ 不要碰其他 agent 节点 / graph 拓扑 / AgentState。

## 分责

- **Claude Code**：执行本 brief 的改动清单并自测。
- **Hermes**：最终验收——自己跑 `git diff --stat` 核对越界、跑 pytest、做空响应场景的 smoke 复核。