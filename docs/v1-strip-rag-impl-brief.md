# Implementation Brief: v1 分支剥离 RAG（保留 modeler 循环修复）

## Background

master 主线定为 **v1pro（含 RAG）**，另建 **v1（无 RAG）** 分支。两条线都已指向同一 git 基线
（当前 v1 分支 HEAD = master HEAD = `af63f66`）。

本任务在 **v1 分支**上，把「纯 RAG 功能」从代码中剥离，但**保留**与 RAG 无关的通用鲁棒性修复
（modeler 发言循环修复、tool-loop 降级链、空响应重试、180s 超时等）。

```
v1pro (master)  = 完整功能（含 RAG）
v1              = 无 RAG：无 model_search 工具、无 model_candidates 注入、无 knowledge 库
```

RAG 的四个构件：`knowledge/` 库、`model_search_tool`、`_run_model_candidate_search`、
`model_candidates` 候选池注入（Layer 1 生成 → Layer 2 首轮消费）。这四者要一并摘除。

`_run_modeler_turn`（modeler 发言循环修复）**不是 RAG**，是 modeler 语义 bug 修复，v1 必须保留；
v1 的 modeler 仍然绑定 `web_search + run_code` 两个工具，继续用 `_run_modeler_turn` 循环。

## Change List

### 1. 删除整个文件 / 目录（RAG 专属）

```
git rm -r mathmodelingagents/knowledge/
git rm scripts/build_model_library.py
git rm tests/test_knowledge_retrieval.py
git rm docs/adr/0003-model-library-rag.md
git rm docs/layer2-model-library-rag-impl-brief.md
git rm docs/rag-cli-output-and-tests-impl-brief.md
```

### 2. `mathmodelingagents/agents/__init__.py`

**删除（RAG）：**
- `from typing import Any, Callable, NamedTuple` → 改回 `from typing import Any, Callable`
  （`NamedTuple` 仅被 `CandidatePoolResult` 使用）。
- 删除 `CandidatePoolResult` 类定义（`class CandidatePoolResult(NamedTuple): ...`，约 34-45 行）。
- 删除 `_run_model_candidate_search` 函数（`def _run_model_candidate_search(...) -> CandidatePoolResult: ...`
  到其 `except` 块结束，约 48-112 行）。
- `_build_context` 内删除 `model_candidates` 注入块（约 187-192 行）：
  ```python
  # ── 候选模型池（ADR-0003）：仅第一轮注入，后续轮次不重复 ──
  # modeler 节点不递增 round_count（由 Manager 管理），首轮 debate.round_count 为 0
  candidates = state.get("model_candidates", "")
  ...
  if candidates and debate.get("round_count", 0) <= 1:
      parts.append(f"## 候选模型池（参考起点，可超越）\n\n{candidates}")
  ```
- `_make_manager_node` 内删除候选池调用块（约 200-212 行）：
  ```python
  # ── Layer 1 候选模型池（ADR-0003）：CONCLUDE 后结构化提炼 + RAG 检索 ──
  if layer == "problem":
      pool = _run_model_candidate_search(config, result)
      ...
  ```

**保留（不要动）：**
- `_run_modeler_turn` 函数整体（约 221-287 行区域）。
- `_run_tool_loop` 的 `invoke_fn` 参数、软失败检测、降级链调用改动。
- Solver/Viz/Paper 的 `_invoke_fn` 定义与 `invoke_fn=...` 传参改动。

**修改（部分）：**
- `_make_modeler_node` 的 docstring 删掉「绑定 model_search（模型知识库 RAG）+」及 model_search 相关描述，
  改为只提 web_search + run_code。
- 工具白名单：`wanted = {"model_search_tool", "web_search_tool", "run_code_tool"}`
  → 改为 `wanted = {"web_search_tool", "run_code_tool"}`。

### 3. `mathmodelingagents/tools/__init__.py`

**删除（RAG）：**
- 删除 `model_search_tool` 定义（`@tool` 装饰器整块，含 `from mathmodelingagents.knowledge import ...`）。
- 在 `create_langchain_tools` 返回列表里删除 `model_search_tool,` 一行。

**保留（不要动）：**
- `_exec_script` 里的 `encoding="utf-8", errors="replace"` 两行（沙盒读取修复，非 RAG）。

### 4. `mathmodelingagents/agents/utils/agent_states.py`

- 删除 `model_candidates: str  # Layer 1 候选模型池文本（ADR-0003，RAG 检索 Top-5；注入 Layer 2 第一轮）`
  这一行。

### 5. `pyproject.toml`

- 删除以下三行（含注释）：
  ```toml
  # ── 模型知识库 RAG（ADR-0003）：本地 embedding，不引入 torch ──
  fastembed>=0.8.0,
  onnxruntime>=1.17,
  ```

### 6. `README.md`

- 删除「## 模型知识库 RAG」整章（约 300-353 行，从该标题到 `fail-open` 段落结束，到下一个 `## 配置` 之前）。

### 7. `CONTEXT.md`

- 删除「### 模型知识库」整节（约 39-50 行，含「模型知识库 / 候选模型池 / 特点标签」三个条目）。

### 8. 保留不动（非 RAG，明确不要碰）

- `mathmodelingagents/llm_clients/__init__.py`（降级链 + 空响应重试 + 180s 超时，全部保留）
- `mathmodelingagents/default_config.py`（180s 超时，保留）
- `.env.example`（fallback 注释清理，保留）
- `main.py`（mode_label 重构，保留）
- `mathmodelingagents/agents/utils/prompt_templates.py`（沙盒 hacks 禁令第 6 条，保留）
- `tests/test_modeler_turn.py`、`tests/test_tool_fallback.py`、`tests/test_tool_loop.py`、
  `tests/test_sensitivity_decision.py`（保留，它们测试的是 modeler 循环 / tool-loop，非 RAG）
- `scripts/probe_bind_tools_volcengine.py`、`scripts/probe_volcengine_pro_longprompt.py`（纯 volcengine 探测）
- `scripts/probe_two_volcengine_providers.py`、`scripts/probe_long_context_bind_tools.py`（诊断脚本，
  仅本地存根 model_search_tool，不 import knowledge 库，无运行时依赖，保留）
- `docs/modeler-turn-loop-impl-brief.md`、其余非 RAG brief（保留）

## Acceptance Criteria

在 v1 分支上运行（工作目录 `F:\code\projects\MathModelingAgents`）：

1. **无 RAG 引用残留**：
   ```bash
   export PYTHONPATH=
   grep -rn "model_search\|model_candidates\|CandidatePoolResult\|_run_model_candidate_search\|mathmodelingagents.knowledge\|fastembed\|onnxruntime" mathmodelingagents/ pyproject.toml
   ```
   应**零输出**（或仅剩 probe 脚本里本地定义的 `model_search_tool` 存根名，属预期）。
2. **导入冒烟**：
   ```bash
   export PYTHONPATH=
   .venv/Scripts/python.exe -c "from mathmodelingagents.agents import create_modeler_agent, _run_modeler_turn; print('import OK')"
   ```
   成功打印 `import OK`。
3. **全量测试**：
   ```bash
   export PYTHONPATH=
   .venv/Scripts/python.exe -m pytest tests/ -q
   ```
   全绿（`test_knowledge_retrieval.py` 已删除，其余应全部通过，无因剥离导致的失败）。

## Explicitly Do NOT Do

- 不要修改 `_run_modeler_turn` 函数本体（这是 v1 要保留的核心修复）。
- 不要动 tool-loop 降级链 / 空响应重试 / 超时相关代码（llm_clients、default_config、agents 里的 invoke_fn 部分）。
- 不要删除 `tests/test_modeler_turn.py`（它测 `_run_modeler_turn`，非 RAG）。
- 不要提交（我会亲自验收后再提交）；工作区保持已编辑未提交状态即可。
- 不要改动 `.env`（含真实凭证）——它本就被 gitignore。
- 所有 python/pytest 命令用 `.venv/Scripts/python.exe`，运行前 `export PYTHONPATH=`。