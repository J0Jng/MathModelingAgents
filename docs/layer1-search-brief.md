# 实施任务书：Layer 1 多角度预搜索增强（建模背景注入 Layer 2）

> Hermes 架构，Claude Code 执行。完成后不要删除本文档。

## 背景与目标

当前 Layer 1 Decomposer 的预搜索只做**一次**宽泛查询（题目前 150 字符，max_results=3），背景资料只注入 Decomposer 自己的 user_msg——如果 Decomposer 没把资料写进拆解报告，Layer 2 建模师就拿不到。
目标：**增强为多角度搜索（题目背景 + 建模方法），并把原始搜索资料写入 state 字段 `background_research`，由 `_build_context` 注入 Layer 2 建模上下文**。Layer 2 本身零改动。

## 改动清单

### 1. `mathmodelingagents/agents/__init__.py` — create_decomposer 预搜索增强

将现有预搜索块（约 568-577 行，`# ── 预搜索注入：题目背景知识（失败静默，不影响主流程）──`）替换为**双查询**逻辑：

```python
# ── 预搜索注入：题目背景知识（失败静默，不影响主流程）──
search_combined = ""
try:
    from mathmodelingagents.tools.web_search import web_search
    problem_text = (state.get("problem_description") or "").strip()
    if problem_text:
        parts_search = []

        # 查询 1: 题目背景（题目前 150 字符）
        r1 = web_search(problem_text[:150], max_results=5)
        if not r1.startswith("[搜索失败]") and not r1.startswith("[搜索未启用]"):
            parts_search.append(f"### 查询 1（题目背景）\n{r1}")

        # 查询 2: 建模方法参考（题目首行 + " 数学建模"）
        first_line = problem_text.split("\n")[0].strip()
        if len(first_line) >= 10:
            q2 = (first_line[:80] + " 数学建模").strip()
            r2 = web_search(q2, max_results=3)
            if not r2.startswith("[搜索失败]") and not r2.startswith("[搜索未启用]"):
                parts_search.append(f"### 查询 2（建模方法参考）\n{r2}")

        if parts_search:
            search_combined = "\n\n".join(parts_search)
            user_msg += "\n\n## 题目背景资料（自动搜索）\n\n" + search_combined
except Exception as e:
    logger.info("[Layer1] Decomposer 背景搜索跳过: %s", e)
```

并在 node_fn 的 return 中**新增** `"background_research": search_combined`：

```python
return {
    "problem_report": result,
    "background_research": search_combined,  # 原始搜索资料（可能为空字符串），供 Layer 2 直接使用
    "layer_outputs": _record(state, "decomposer", "problem", "agent", 1, result),
}
```

### 2. `mathmodelingagents/agents/utils/agent_states.py` — AgentState 加字段

在 "Layer 1 产出" 区块（`data_insights: str` 之后）新增：

```python
    background_research: str        # Layer 1 自动搜索的题目背景资料（原始文本，供 Layer 2 注入）
```

### 3. `mathmodelingagents/graph/propagation.py` — 初始 state 默认值

在初始 state dict 中（`problem_description` 附近）加 `"background_research": ""`。

### 4. `mathmodelingagents/agents/__init__.py` — _build_context modeling 分支注入

在 `elif layer == "modeling":` 分支内、`problem_report` 注入之后（约 312 行后）新增：

```python
        if state.get("background_research"):
            parts.append(f"## 题目背景资料（Layer 1 自动搜索）\n\n{state['background_research']}")
```

### 5. `mathmodelingagents/agents/utils/prompt_templates.py` — Decomposer prompt 更新

工具权限部分（当前为"题目背景资料由系统自动联网搜索并注入（如已提供请直接使用，无需自行调用工具）"）改为：

```
## 工具权限
- 题目全文已包含在上下文中
- 系统已自动进行多角度联网搜索（题目背景 + 建模方法）并将资料注入上下文；请在问题拆解中**引用**背景资料，重要领域信息（专有名词、行业背景、数据口径）应写入拆解报告
```

### 6. 新增 `tests/test_decomposer_search.py`

用 `unittest.mock.patch` 测 create_decomposer 的 node_fn（不发起真实网络/LLM 请求）：

- **成功注入**：patch `mathmodelingagents.agents.invoke_with_fallback`（或 agents 模块内实际引用的名字，需先确认 import 方式）返回固定文本；patch `mathmodelingagents.tools.web_search.web_search` 返回固定"### 搜索结果"文本 → 断言 return 中 `background_research` 非空、包含两个查询段
- **搜索失败静默**：patch web_search 返回 `[搜索失败] ...` → 断言 `background_research == ""`、LLM 调用仍正常执行（invoke_with_fallback 被调用）
- **搜索抛异常静默**：patch web_search 抛异常 → 断言不传播异常、background_research 为空
- 注意：node_fn 内是 `from mathmodelingagents.tools.web_search import web_search` 局部导入，mock 目标应为 `mathmodelingagents.tools.web_search.web_search`。若 mock 无效，可用 `unittest.mock.patch` 的 `create=True` 或改 patch 路径，以实现等价断言为准。
- 若测试需要 `state` 最小结构：`{"problem_description": "…"}` 即可（node_fn 只用 get()）

### 7. `README.md` — Web 搜索特性区块更新（低优先）

"Layer 1 的 Decomposer 会自动对题目关键词联网搜索背景资料并注入上下文" 更新为：
"Layer 1 的 Decomposer 自动进行**多角度联网搜索**（题目背景 + 建模方法），背景资料注入问题拆解与 Layer 2 建模上下文；Layer 3 的 SolverAgent 可自主调用 `web_search` 查询数据字段含义、算法资料。"

## 验收标准（完成后自测并报告）

1. `python -m pytest tests/ -v` 全绿（原 14 + 新增用例）
2. `python -c "from mathmodelingagents.agents import create_decomposer; from mathmodelingagents.agents.utils.agent_states import AgentState; from mathmodelingagents.graph.propagation import build_initial_state; print('imports OK')"` 无错误（propagation 函数名以实际为准）
3. `ruff check mathmodelingagents/agents/__init__.py mathmodelingagents/agents/utils/agent_states.py mathmodelingagents/graph/propagation.py mathmodelingagents/agents/utils/prompt_templates.py` 无**新增**警告
4. 报告每个文件的 diff 摘要

## 明确不做

- 不改 Layer 2 任何节点逻辑（只通过 _build_context 注入字段）
- 不改 web_search.py 本身
- 不提交 git、不推送
