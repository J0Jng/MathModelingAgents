# Implementation Brief: 修复 modeler 发言回显 bug（输入被当最终发言返回）

## Background

commit `1b46eb4` 给 modeler 绑定工具并引入 `_run_modeler_turn` 循环后，实跑（B题，volcengine-plan + deepseek-v4-pro）
出现：三位建模师的「发言」= **输入上下文逐字回显**（`请根据以下上下文执行你的任务...## 题目内容...`），
Layer 2 无任何实质产出，经理裁决原文「三位建模师历史发言仅见题目上下文重复」。

诊断已确认（探针 + 机制复现，2026-09-07）：

1. 火山 Agent Plan 通道下 `deepseek-v4-pro` 在长上下文 + 多工具（model_search/web_search/run_code）场景，
   持续返回**空 content + tool_calls**，连续 10 次迭代不产出纯文本（`scripts/probe_modeler_echo2.py` 实测：迭代 1、2 均空 content + tool_calls）。
2. `_run_modeler_turn` 耗尽 10 次迭代后走保底 `return messages, _extract_final_output(messages)`
   （`agents/__init__.py:1225` 附近）。
3. `_extract_final_output`（`agents/__init__.py:246-264`）反向遍历只排除「tool 类型消息」和「带 tool_calls 的
   AIMessage」——**HumanMessage 全部条件通过** → 输入 user_msg 被原样当最终发言返回。
4. 机制级复现已验证：全 tool_calls 消息序列 → `_extract_final_output(msgs) == user_msg.content`。

次要 bug：`_build_context` 元信息显示 `辩论轮次: 1/1`——用了 deprecated 的 `max_debate_rounds`
（`agents/__init__.py:838`），Layer 2 实际生效的是 `max_modeling_rounds`（conditional_logic.py 用它控制循环）。

## Design

四条修复，1+2 根治回显（哪怕模型空转也不会把输入当发言），3 提升发言成功率，4 修显示：

1. **`_extract_final_output` 只认 AIMessage**：遍历条件加 `getattr(msg, "type", "") == "ai"`，
   HumanMessage/SystemMessage 永不被选为「最终输出」。找不到 → 返回空串（现状语义）。
2. **`_run_modeler_turn` 保底返回明确占位**：耗尽迭代后若 `_extract_final_output` 挖到空串，
   返回明确失败占位 `（发言生成失败：模型连续 {max_iterations} 次迭代均未产出文字方案，仅有工具调用。请检查模型通道或减少工具依赖。）`
   ——让 Manager 与报告能看见真实状态，而不是垃圾回显。若挖到非空（理论上 1 修复后只可能是 AIMessage 纯文本）则照旧返回。
3. **连续工具空转软干预**：`_run_modeler_turn` 循环内计数「连续返回 tool_calls 且 content 为空」的迭代；
   连续达到 5 次时，在消息列表追加一条 HumanMessage（内容：`请停止调用工具，基于已获得的工具结果直接给出你的文字建模方案。`）
   再继续循环，给模型一次「被点名要纯文本」的机会。不改动 `_invoke_tools_with_retry` / `invoke_with_tools_with_fallback`
   的重试语义（那是 transport 层，本次不动）。
4. **轮次显示修正**：`_build_context` 中 `max_rounds` 改为按层取值——`modeling` 层用
   `config.get("max_modeling_rounds", 5)`，其余层维持 `config.get("max_debate_rounds", 10)` 不动
   （problem 层实际用 max_problem_rounds 控制，但本次只修 modeling 层的已知错显，不扩 scope）。

## Change List

> ⚠️ 行尾：`mathmodelingagents/agents/__init__.py` 是 **CRLF**，编辑保持原行尾，用 Python 脚本精确
> `str.replace`（每处断言唯一命中）或单行最小 patch。⚠️ 工作区有大量任务前遗留的未提交改动
> （agent_states.py、default_config.py、graph/modeling_graph.py、llm_clients/__init__.py 等），
> **绝对不要触碰、还原、格式化它们**。

### 1. `mathmodelingagents/agents/__init__.py`（CRLF）

**1a. `_extract_final_output`（第 246-264 行）**：循环体加 AIMessage 类型过滤。

现在的判断：
```python
        if content and not has_tools and not tool_msg:
            return content
```
改为（在函数内取一次 `msg_type`）：
```python
        msg_type = getattr(msg, "type", "")
        is_ai = msg_type == "ai"
        if is_ai and content and not has_tools:
            return content
```
`tool_msg` 检查可保留也可并入（`type=="tool"` 恒非 `"ai"`），保留原变量但条件以 `is_ai` 为准。
同步更新 docstring：把「不是 tool 类型消息」改为「必须是 AIMessage（type=='ai'），排除把输入
HumanMessage 误当输出的回显 bug（2026-09-07 B题实跑发现）」。

**1b. `_run_modeler_turn`（第 1159-1225 行）两处**：

- **软干预**：循环内新增连续空转计数（在 `for iteration in range(max_iterations):` 体内，
  `response = invoke_fn(messages)` 与 `messages.append(response)` 之后）：
```python
        if getattr(response, "tool_calls", None) and not (response.content or "").strip():
            consecutive_tool_only += 1
        else:
            consecutive_tool_only = 0
        if consecutive_tool_only >= 5:
            messages.append(HumanMessage(content=(
                "请停止调用工具，基于已获得的工具结果直接给出你的文字建模方案。"
            )))
            consecutive_tool_only = 0
            logger.warning(f"[{layer_tag}] {agent_tag} 连续 5 次仅工具调用，已注入纯文本提醒")
```
  计数变量在循环前初始化 `consecutive_tool_only = 0`。
- **保底占位**：函数末尾（现第 1224-1225 行）
  `return messages, _extract_final_output(messages)` 改为：
```python
    fallback = _extract_final_output(messages)
    if not fallback.strip():
        fallback = (
            f"（发言生成失败：模型连续 {max_iterations} 次迭代均未产出文字方案，仅有工具调用。"
            "请检查模型通道或减少工具依赖。）"
        )
    return messages, fallback
```

**1c. `_build_context` 轮次显示（第 836-847 行）**：
```python
    max_rounds = config.get("max_debate_rounds", 10)
```
改为：
```python
    if layer == "modeling":
        max_rounds = config.get("max_modeling_rounds", 5)
    else:
        max_rounds = config.get("max_debate_rounds", 10)
```

### 2. 新增 `tests/test_modeler_echo_fix.py`

覆盖（全部 mock，不发真实 LLM 请求）：

- `_extract_final_output`：
  - 全 tool_calls 序列（AIMessage(content="", tool_calls=[...]) × N + ToolMessage 交替，前有 System/Human）
    → 返回**空串**（修复前是 user_msg 原文，即回显 bug 基线）；
  - 末尾有 AIMessage 纯文本 → 正常返回该文本；
  - System/Human 之后直接是带 tool_calls 的 AIMessage → 空串。
- `_run_modeler_turn`：用 stub `invoke_fn`（第 1 次调用起恒返回
  `AIMessage(content="", tool_calls=[{"name": "t", "args": {}, "id": "c1", "type": "tool_call"}])`，
  tools 传一个 `@tool` 装饰的假工具）：
  - `max_iterations=3` → 返回 result 为「发言生成失败」占位文本；
  - stub 在第 2 次调用改为返回纯文本 `AIMessage(content="我的建模方案是……")` → result 为该文本。
- `_build_context` 轮次显示：构造 `config={"max_modeling_rounds": 5, "max_debate_rounds": 10}` 与
  含 `model_debate_state={"round_count": 1}` 的 state，调 `_build_context(state, "modeling", "modeler_a", config)`
  → 返回串含 `辩论轮次: 1/5`；同 config 调 layer="problem" → 含 `辩论轮次: 1/10`。
  （`_build_context` 对 modeling 层需要 state 里有题目等字段，最小构造即可，参考函数体实际读取的字段。）

## Acceptance Criteria（必须逐条跑通）

1. **语法检查**：`export PYTHONPATH= && .venv/Scripts/python.exe -m py_compile mathmodelingagents/agents/__init__.py` 无输出。
2. **新增测试**：`.venv/Scripts/python.exe -m pytest tests/test_modeler_echo_fix.py -v` 全绿。
3. **回归**：`.venv/Scripts/python.exe -m pytest tests/ -q` 全绿（含 test_candidate_search_parse / test_debate_history_window / test_problem_loader）。
4. **回显基线翻转验证**：跑一段 ad-hoc（可写在验证脚本里）——全 tool_calls 序列 +
   `_extract_final_output` → 必须返回空串（修复前复现脚本返回 user_msg 原文）。
5. **工作区干净**：`git status --short` 相比任务开始前**只新增** `tests/test_modeler_echo_fix.py`
   和 `mathmodelingagents/agents/__init__.py` 的修改；任务前遗留的未提交文件一个都不能动。

> 测试用 `.venv/Scripts/python.exe`，运行前先 `export PYTHONPATH=`。禁止 heredoc 传含 `\r\n`/`"""` 的代码。

## Explicitly Do NOT Do

- **不要**触碰工作区里任务前遗留的未提交改动（agent_states.py、default_config.py、graph/modeling_graph.py、llm_clients/__init__.py、problem_loader.py、未跟踪 brief/脚本等）——不还原、不格式化、不"顺手修复"。
- **不要**改 `_invoke_tools_with_retry` / `invoke_with_tools_with_fallback` / `invoke_with_fallback` 的重试与降级语义。
- **不要**改 `_run_tool_loop`（Solver/Viz/Paper 共用循环）——它们的 SELF_CHECK 语义依赖 `_extract_final_output` 现有返回，1a 的 AIMessage 过滤对它们只会更严格、更正确，但循环本身不动。
- **不要**改 conditional_logic.py 的路由逻辑、graph 拓扑、`AgentState` 定义。
- **不要**动 `.env`、`pyproject.toml`、依赖、`uv.lock`、`scripts/probe_modeler_echo*.py`（诊断探针保留）。
- **不要**改 logging 文案既有措辞（新增 warning 可新写）。不要提交 git。
