# 实施任务书：web_search 真实搜索接入

> 本文档由 Hermes 编写（架构），由 Claude Code 执行源码编辑。改动完成后**不要**删除本文档。

## 背景

`mathmodelingagents/tools/__init__.py` 中的 `web_search()` 目前是占位符（返回"尚未实现"）。
目标：接入真实搜索 API，挂载到 **Layer 3 SolverAgent（真工具）** 与 **Layer 1 Decomposer（预搜索注入）**。

## 搜索后端设计（Provider 抽象）

Provider 选择逻辑（环境变量 `MATHMODELING_WEB_SEARCH_PROVIDER`）：
- `auto`（默认）：存在 `TAVILY_API_KEY` 或 `MATHMODELING_TAVILY_API_KEY` → 用 tavily；否则 → ddgs
- `tavily` / `ddgs` / `off`：强制指定（`off` = 禁用搜索）
- 未知值 → 记 warning，按 `off` 处理

行为铁律：
- 超时 10 秒
- **任何异常不得抛出**——返回 `[搜索失败] <provider>: <错误摘要>` 文本
- 摘要截断 ≤200 字符（控制 token）
- 默认 `max_results=5`
- 结果格式（LLM 友好）：
  ```
  ### 搜索结果: <query>（provider: tavily, 3 条）
  1. [标题](URL)
     摘要…
  2. [标题](URL)
     摘要…
  ```
- tavily 失败时**自动降级**尝试 ddgs；都失败才返回失败文本

## 改动清单

### 1. 新建 `mathmodelingagents/tools/web_search.py`

核心模块，包含：
- `_get_provider() -> str` — provider 选择（见上）
- `_search_tavily(query, max_results) -> list[dict]` — POST `https://api.tavily.com/search`，body `{"api_key": key, "query": q, "max_results": n, "search_depth": "basic"}`，返回 `[{"title","url","content"}]`
- `_search_ddgs(query, max_results) -> list[dict]` — `from ddgs import DDGS`，`DDGS().text(query, max_results=n, region="wt-t", safesearch="off")`，返回 `[{"title": r["title"], "url": r["href"], "content": r["body"]}]`。**lazy import**：ddgs 未安装时返回空列表（由 `web_search()` 提示 `pip install duckduckgo-search`）
- `_format_results(query, provider, results) -> str` — 格式化输出（见上）
- `web_search(query: str, max_results: int = 5) -> str` — 总入口：选 provider → 搜索 → 失败降级 → 格式化；`off` 时返回 `[搜索未启用] …`

HTTP 实现：优先 `httpx`（环境中已有），`ImportError` 时 fallback 标准库 `urllib.request`。**不要新增硬依赖**（ddgs 也是 lazy import，不写入 pyproject dependencies）。

### 2. 修改 `mathmodelingagents/tools/__init__.py`

- 删除占位函数 `web_search()`（约 261-275 行），改为：
  `from mathmodelingagents.tools.web_search import web_search`（模块顶部统一导入；`__all__` 中已有 `"web_search"`，保持不变）
- `create_coding_agent_tools()`（SolverAgent/VizAgent 用）工具列表追加 `web_search_tool`：
  ```python
  @tool
  def web_search_tool(query: str) -> str:
      """Search the web for background knowledge. Returns formatted results (title, url, snippet)."""
      return web_search(query, max_results=5)
  ```
  追加到返回列表 `read_file_tool, run_code_tool, write_file_tool, list_dir_tool, web_search_tool`
- `create_langchain_tools()` 里已有的 `web_search_tool`（占位包装）自动生效，无需改动

### 3. 修改 `mathmodelingagents/default_config.py`

`DEFAULT_CONFIG` 中新增两个 key（放在"计算沙盒配置"区块前）：
```python
# ═══════════════════════════════════════════════
# Web 搜索配置（auto = 有 TAVILY_API_KEY 用 tavily，否则 ddgs）
# ═══════════════════════════════════════════════
"web_search_provider": _env("web_search_provider", "auto"),
"tavily_api_key": _env("tavily_api_key") or os.getenv("TAVILY_API_KEY"),
```

### 4. 修改 `mathmodelingagents/agents/__init__.py`

**a. `create_decomposer`（约 560-561 行）**：从 `_make_llm_node` 改为专用 node_fn（参照 `create_constraint_analyst` 的写法，约 568-598 行），核心差异是加**预搜索注入**：

```python
def create_decomposer(config: dict) -> Callable[[AgentState], dict[str, Any]]:
    def node_fn(state: AgentState) -> dict[str, Any]:
        logger.info("[Layer1] Decomposer 执行中...")
        max_tok = resolve_max_tokens(config, "agent", "decomposer")
        system_prompt = get_prompt("decomposer") + "\n\n" + get_global_constraints()
        context = _build_context(state, "problem", "decomposer", config)
        user_msg = f"请根据以下上下文执行你的任务：\n\n{context}"

        # ── 预搜索注入：题目背景知识（失败静默，不影响主流程）──
        try:
            problem_text = (state.get("problem_description") or "").strip()
            if problem_text:
                from mathmodelingagents.tools.web_search import web_search
                search_result = web_search(problem_text[:150], max_results=3)
                if not search_result.startswith("[搜索失败]") and not search_result.startswith("[搜索未启用]"):
                    user_msg += "\n\n## 题目背景资料（自动搜索）\n\n" + search_result
        except Exception as e:
            logger.info(f"[Layer1] Decomposer 背景搜索跳过: {e}")

        messages = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_msg),
        ]
        try:
            result = invoke_with_fallback(config, "problem", "agent", messages, "decomposer", max_tokens=max_tok)
        except Exception as e:
            logger.error(f"[Layer1] decomposer 全部降级耗尽: {e}")
            result = f"LLM 调用失败（全部降级耗尽）: {e}"
        return {
            "problem_report": result,
            "layer_outputs": _record(state, "decomposer", "problem", "agent", 1, result),
        }

    node_fn.__name__ = "decomposer"
    return node_fn
```

> 注意：node_fn 返回的 state key 与 `_make_llm_node` 调用时的 `state_key="problem_report"` 保持一致。decomposer 在原 `_make_llm_node` 调用中 layer="problem"、role="agent"。

**b. Layer 3 SolverAgent**：无需改代码——`create_solver_agent` 用 `create_coding_agent_tools`，工具自动带上 web_search。

### 5. 修改 `mathmodelingagents/agents/utils/prompt_templates.py`

**a. `get_decomposer_prompt()`**：
- 第 17 行 `✅ 用 read_file 读取题目 Markdown 文件（路径将在用户消息中提供）` → `✅ 通读上下文中的题目全文（已注入），提取核心目标`
- 第 21 行 `✅ 读取题目附件：如有数据文件，用 run_code 做初步读取（head/info/describe）` → `✅ 题目附件：如有数据文件，引用上下文中的数据描述（深度分析由 DataAnalyst 负责）`
- "## 工具权限"（31-34 行）整体替换为：
  ```
  ## 工具权限
  - 题目全文已包含在上下文中
  - 题目背景资料由系统自动联网搜索并注入（如已提供请直接使用，无需自行调用工具）
  ```
- 输出模板第 51 行 `[如有附件，贴 run_code 读取结果（head + info + describe 前5行）]` → `[如有附件，引用上下文中的数据描述]`

**b. `get_data_analyst_prompt()`**：仅删工具权限中的 `- web_search: 查询数据字段含义、单位解释` 一行，替换为 `- 无网络搜索：数据字段含义请基于题目上下文与数据规律推断`

**c. `get_solver_agent_prompt()`**：
- 第 403 行 `你有四个工具` → `你有五个工具`
- 工具表（406-413 行）追加一行：
  `| web_search(query) | 联网搜索：查询题目背景、数据字段含义、算法/模型资料（辅助参考，所有数值必须来自 run_code 实际执行） |`

### 6. 新建 `tests/test_web_search.py`（新建 tests/ 目录）

pytest 单元测试（用 `monkeypatch`/`unittest.mock`，**不发起真实网络请求**）：
- `_get_provider`：auto+有 key→tavily；auto+无 key→ddgs；off→off；未知→off
- `_format_results`：格式含 `### 搜索结果`、`1. [标题](URL)`、摘要截断 ≤200 字符
- `web_search` 异常兜底：mock `_search_tavily` 抛异常且 `_search_ddgs` 抛异常 → 返回 `[搜索失败]` 前缀，不抛异常
- `web_search` 降级：mock tavily 失败、ddgs 成功 → 返回 ddgs 结果
- `web_search` 关闭：provider=off → 返回 `[搜索未启用]`

### 7. 新建 `scripts/verify_web_search.py`

真实调用演示（不走 mock）：
- 打印当前生效 provider（可加 `MATHMODELING_WEB_SEARCH_PROVIDER`/key 提示）
- 调用 `web_search("2026 华为杯 数学建模", max_results=3)`，打印返回文本
- 退出码：搜索成功 0，全部失败 1

### 8. 修改 `.env.example`

追加（带注释）：
```
# ── Web 搜索（Layer 1 题目背景 / Layer 3 求解参考）──
# TAVILY_API_KEY=                 # Tavily 免费 key：https://tavily.com （每月 1000 次，无需信用卡）
# MATHMODELING_WEB_SEARCH_PROVIDER=auto   # auto | tavily | ddgs | off
```

### 9. 修改 `CLAUDE.md`（项目根）

在"关键文件"或架构说明处追加一行 web_search 说明（新增 `mathmodelingagents/tools/web_search.py` 文件条目 + 一行特性描述）。低优先，简单即可。

## 验收标准（完成后必须自测并报告）

1. `python -m pytest tests/ -v` 全绿（新测试通过；项目原本无测试，不影响）
2. `python scripts/verify_web_search.py` 正常输出搜索结果（无 key 时应走 ddgs；若 ddgs 未安装，输出友好提示且退出码为 1 不算失败——报告即可）
3. `python -c "from mathmodelingagents.tools import web_search; from mathmodelingagents.agents import create_decomposer, create_solver_agent; print('imports OK')"` 无错误
4. `ruff check mathmodelingagents/tools/web_search.py mathmodelingagents/tools/__init__.py mathmodelingagents/agents/__init__.py mathmodelingagents/default_config.py mathmodelingagents/agents/utils/prompt_templates.py` 通过（如 ruff 可用；有格式问题用 `ruff check --fix` 修复）
5. 报告每个改动文件的 diff 摘要

## 明确不做

- 不改 Layer 2 / Layer 4 / Layer 5 任何代码
- 不给 Layer 1 引入工具循环（保持纯 LLM + 预搜索注入）
- 不新增硬依赖到 pyproject（ddgs 保持 lazy import）
- 不提交 git、不推送、不删除本文档
