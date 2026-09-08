# 任务书：修复 ModelerC 发言循环「连续工具调用 → 发言失败」

## 背景

2026-09-08 B 题实跑（`~/Desktop/what/MMA_testing/B题-全部转换合并/`），建模师 C（ModelerC）
输出：

```
### 建模师 C [简洁与可解释]
（发言生成失败：模型连续 10 次迭代均未产出文字方案，仅有工具调用。
  请检查模型通道或减少工具依赖。）
```

同轮 A、B 正常产出完整方案。根因不在 provider/网络，而在 `_run_modeler_turn` 的循环终止判据过窄。

## 根因定位

文件 `mathmodelingagents/agents/__init__.py`：

1. `_run_modeler_turn`（约 L1169-1253）唯一的正常返回路径是「LLM 返回无 tool_calls 的纯文本」
   （约 L1208 `if not getattr(response, "tool_calls", None):`）。只要消息带 tool_calls，代码就执行工具
   继续下一轮——**即使这条消息的 content 已写满文字方案，也被直接丢弃**。

2. 兜底 fallback（约 L1247）调 `_extract_final_output`，该函数只会反向找「有 content 且**不含
   tool_calls**」的 AI 消息（L282 `and not has_tools`）。ModelerC 的 10 条回复全带 tool_calls → 返回
   空串 → 落入「发言生成失败」保底文案（L1249-1252）。

ModelerC 的 prompt 要求「运行 A/B 验证代码 + model_search 选型 + 用数据证明精度损失可接受」，
工具调用倾向最强，且最后发言（上下文含 A、B 完整方案+代码），易陷入「边写边验证」的持续
tool-call 循环，从不吐纯文本。A、B 只是碰巧某轮返回纯文本才通过。

## 设计决策

1. 让 Modeler 发言循环在「文字方案已成形」时提前返回，即使该条消息仍带 tool_calls。
2. 兜底放宽：循环耗尽且 `_extract_final_output` 为空时，反向取最后一条「带 tool_calls 但 content
   非空且够长」的 AI 消息文字，而不是直接输出「发言失败」。
3. **不修改共享的 `_extract_final_output`**——它被 Solver/Viz/Paper 的 `_run_tool_loop` 复用，改动
   会波及三层。本次只在 `_run_modeler_turn` 及其专用辅助函数内做。
4. 判定「方案已成形」采用：`len(content) >= 500` 且正则 `###\s*\d+\.` 命中 ≥ 2 处章节标记
   （ModelerA/B/C 输出模板均含 `### 1.`、`### 2.` …章节，通用且安全）。

## 改动清单

全部在 `mathmodelingagents/agents/__init__.py`，新增 2 个模块级辅助函数 + 改 `_run_modeler_turn` 一处。

### 1. 新增辅助函数（放在 `_run_modeler_turn` 定义之前，建议紧随 `_extract_final_output` 之后）

```python
def _looks_like_complete_plan(content: str) -> bool:
    """判定一段模型文字是否已构成完整建模方案（足够长且含章节结构）。

    用于 Modeler 发言循环：当回复仍带 tool_calls、但 content 已写成形时，
    允许提前以该文字作为本轮发言返回，避免持续工具调用耗尽迭代。
    """
    import re
    text = (content or "").strip()
    if len(text) < 500:
        return False
    sections = re.findall(r"###\s*\d+\.", text)
    return len(sections) >= 2


def _extract_last_substantial_text(messages: list, min_chars: int = 500) -> str:
    """循环耗尽兜底：反向取最后一条 content 足够长的 AI 消息文字（不排除带 tool_calls 的）。

    与 _extract_final_output 不同，这里接受「带 tool_calls 但 content 非空」的消息，
    因为 Modeler 常在同一轮既写正文又调用验证工具。
    """
    for msg in reversed(messages):
        if getattr(msg, "type", "") != "ai":
            continue
        content = (getattr(msg, "content", "") or "").strip()
        if len(content) >= min_chars:
            return content
    return ""
```

### 2. 修改 `_run_modeler_turn`（约 L1188-1253 循环体）

在现有「无 tool_calls → 返回」分支之后、执行工具之前，插入提前返回分支；并在 fallback 处放宽兜底。

当前循环结构（L1190-1210 附近）：

```python
    messages = list(initial_messages)
    consecutive_tool_only = 0
    for iteration in range(max_iterations):
        ...
        response = invoke_fn(messages)
        messages.append(response)

        if getattr(response, "tool_calls", None) and not (response.content or "").strip():
            consecutive_tool_only += 1
        else:
            consecutive_tool_only = 0
        if consecutive_tool_only >= 5:
            ...  # 注入纯文本提醒

        if not getattr(response, "tool_calls", None):
            content = response.content or ""
            return messages, content

        for tc in response.tool_calls:
            ...
```

改为：

```python
    messages = list(initial_messages)
    consecutive_tool_only = 0
    for iteration in range(max_iterations):
        ...
        response = invoke_fn(messages)
        messages.append(response)

        content = (getattr(response, "content", "") or "").strip()
        has_tools = bool(getattr(response, "tool_calls", None))

        if has_tools and not content:
            consecutive_tool_only += 1
        else:
            consecutive_tool_only = 0
        if consecutive_tool_only >= 5:
            messages.append(HumanMessage(content=(
                "请停止调用工具，基于已获得的工具结果直接给出你的文字建模方案。"
            )))
            consecutive_tool_only = 0
            logger.warning(f"[{layer_tag}] {agent_tag} 连续 5 次仅工具调用，已注入纯文本提醒")

        if not has_tools:
            return messages, getattr(response, "content", "") or ""

        # 新增：带 tool_calls 但文字方案已成形 → 提前返回，不再执行剩余工具
        if _looks_like_complete_plan(content):
            logger.info(
                f"[{layer_tag}] {agent_tag} 文字方案已成形（{len(content)} 字符），"
                f"提前结束工具调用"
            )
            return messages, getattr(response, "content", "")

        for tc in response.tool_calls:
            ...
```

fallback 兜底（L1246-1253）改为：

```python
    # 保底：耗尽迭代仍无纯文本
    fallback = _extract_final_output(messages)
    if not fallback.strip():
        fallback = _extract_last_substantial_text(messages)
    if not fallback.strip():
        fallback = (
            f"（发言生成失败：模型连续 {max_iterations} 次迭代均未产出文字方案，仅有工具调用。"
            "请检查模型通道或减少工具依赖。）"
        )
    return messages, fallback
```

> 注意：上面 `HumanMessage` 需确认已在函数作用域可用（`_run_modeler_turn` 当前已用
> `HumanMessage`，见原 L1202 提醒注入；若没有 import 需补齐）。原代码里该提醒注入用的正是
> `HumanMessage`，保持原样。

## Do NOT 清单

- **不要**改动 `_extract_final_output`（Solver/Viz/Paper 复用）。
- **不要**改动 `_run_tool_loop`、`_sanitize_tool_pairing`。
- **不要**改动 ModelerC 的 prompt（`prompt_templates.py` get_modeler_c_prompt）。
- **不要**改 graph 拓扑、AgentState、状态键名。
- **不要**改 `_run_modeler_turn` 的 `max_iterations=10` 传参语义。
- **不要**动 `llm_clients/`、`tools/`。
- 日志/既有文案（除上述明确改动外）逐字保留，不得删除或改写。

## 可运行验收标准（AC）

新建 `scripts/verify_modeler_c_tool_loop.py`（仿 `scripts/verify_model_candidates_recovery.py` 风格），
至少覆盖：

1. `py_compile` `mathmodelingagents/agents/__init__.py` 无错误。
2. `import mathmodelingagents.agents` 冒烟成功。
3. `_looks_like_complete_plan` 单元断言：
   - 完整方案（≥500 字 + ≥2 处 `### N.`）→ True；
   - 短文字（<500 字）→ False；
   - 长文字但无章节标记 → False。
4. `_extract_last_substantial_text` 单元断言：
   - 消息列表含「带 tool_calls 且 content ≥500 字」的 AI 消息 + 后续短过渡消息 → 返回该长文字；
   - 全部消息 content <500 字 → 返回 ""。
5. `_run_modeler_turn` 集成断言（mock invoke_fn，用 `langchain_core.messages.AIMessage` 构造）：
   - 场景 A（提前退出）：invoke_fn 第一次即返回「带 tool_calls + content 为完整方案」→
     断言返回 result == 该 content，且 invoke_fn 只被调用 1 次（未执行工具、未耗尽循环）。

运行方式（仓库根目录）：

```bash
export PYTHONPATH=
.venv/Scripts/python.exe scripts/verify_modeler_c_tool_loop.py
```

全部 PASS 并打印 `ALL CHECKS PASSED` 才算通过。