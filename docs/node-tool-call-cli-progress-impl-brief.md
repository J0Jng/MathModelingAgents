# Implementation Brief: 节点工具调用 CLI 进度打印

## Background

带工具调用的 Agent 节点（SolverAgent / VizAgent / PaperAgent 走 `_run_tool_loop`；
Modeler A/B/C 走 `_run_modeler_turn`）的工具调用与迭代进度目前只通过
`logger.info` 记录。项目没有为 `mathmodelingagents` logger 配置 console handler
（`debug=True` 只在 `modeling_graph.py` 里 `setLevel(DEBUG)`，root logger 仍无
StreamHandler），因此这些日志不会出现在 CLI 上。

用户要求在 CLI 上实时看到「每个节点调用了哪个工具、跑了几轮、是否自检通过」。

对比：`_make_llm_node` / `_make_manager_node`（无工具节点）已经用
`print(..., flush=True)` 输出 `⏳ calling ...` / `✅ done`。本项目已有
`stream-progress-pattern` 惯例：CLI 进度用 `print(flush=True)`，logger 仅供日志。

本次改动让带工具的节点补齐同样的 CLI 可见性。

## Design

- 在共享函数 `_run_tool_loop` 与 `_run_modeler_turn` 的「迭代开始 / 工具调用 /
  交卷 / 自检 / 强制中断」关键点，补 `print(..., flush=True)`。
- 打印前缀沿用 `[{layer_tag}] {agent_tag} ...`，与现有 logger 文案及
  `_make_llm_node`/`_make_manager_node` 的 `[layer] agent` 风格一致。
- **保留所有现有 `logger.info/warning/error` 行，逐字不动**（依赖日志的测试/运维不受影响）。
- **不打印工具参数**：`run_code` 的 `args["code"]` 是完整源码，打印会刷屏。
  只打印工具名 + 结果首 120 字符（与现有 logger 的 `result_str[:120]` 裁切对齐）。
- 工具名用 `🔧` 前缀、自检/交卷/强断用既有语义前缀，视觉可辨识。

## Change List

全部改动仅在 `mathmodelingagents/agents/__init__.py`。

### 1. `_run_tool_loop`（约 389–520 行）

- 迭代开始处（现 `logger.info(f"[{layer_tag}] {agent_tag} iteration {iteration + 1}/{max_iterations}")`，
  约 424 行）：在其下方新增：
  ```python
  print(f"[{layer_tag}] {agent_tag} 第 {iteration + 1}/{max_iterations} 轮", flush=True)
  ```
- 工具执行处（约 460–494 行）：
  - 在 `tool_fn = ...` 匹配到工具、真正调用前（约 473 行 `try:` 之前），新增：
    ```python
    print(f"[{layer_tag}] {agent_tag} 🔧 调用工具 {tool_name}", flush=True)
    ```
  - 现有工具结果 `logger.info(f"[{layer_tag}] {agent_tag} 工具 {tool_name}: {result_str[:120]}...")`
    （约 491–494 行）下方新增：
    ```python
    print(f"[{layer_tag}] {agent_tag} 🔧 {tool_name} → {result_str[:120]}", flush=True)
    ```
- 自检通过处（约 499–504 行，`if "SELF_CHECK_PASSED" in content:` 分支内
  `logger.info(...)` 之后）新增：
  ```python
  print(f"[{layer_tag}] {agent_tag} ✅ 自检通过（第 {iteration + 1} 轮）", flush=True)
  ```
- 强制中断处（约 506–511 行，`if consecutive_no_tool >= consecutive_no_tool_limit:` 分支内
  `logger.warning(...)` 之后）新增：
  ```python
  print(f"[{layer_tag}] {agent_tag} ⚠️ {consecutive_no_tool} 轮无工具调用，强制中断", flush=True)
  ```

### 2. `_run_modeler_turn`（约 1192–1244 行）

- 迭代开始处（现 `logger.info(f"[{layer_tag}] {agent_tag} iteration {iteration + 1}/{max_iterations}")`，
  约 1205 行）下方新增：
  ```python
  print(f"[{layer_tag}] {agent_tag} 第 {iteration + 1}/{max_iterations} 轮", flush=True)
  ```
- 交卷处（约 1212–1214 行，`if plan:` 分支内 `logger.info(...)` 之后）新增：
  ```python
  print(f"[{layer_tag}] {agent_tag} ✅ 交卷（{len(plan)} 字符）", flush=True)
  ```
- 工具调用结果处（现有 `logger.info(f"[{layer_tag}] {agent_tag} 工具 {tool_name}: {result_str[:120]}...")`，
  约 1235 行）：
  - 在其上方（执行工具 `tool_fn.invoke(...)` 前）新增：
    ```python
    print(f"[{layer_tag}] {agent_tag} 🔧 调用工具 {tool_name}", flush=True)
    ```
  - 在其下方新增：
    ```python
    print(f"[{layer_tag}] {agent_tag} 🔧 {tool_name} → {result_str[:120]}", flush=True)
    ```

## Acceptance Criteria

1. 语法/导入冒烟：
   ```bash
   export PYTHONPATH=
   .venv/Scripts/python.exe -m py_compile mathmodelingagents/agents/__init__.py
   .venv/Scripts/python.exe -c "import mathmodelingagents.agents"
   ```
   均通过（exit 0，无输出错误）。

2. 真实 stdout 验证：写一个 stub 脚本（放 `%TEMP%`，跑完删除），用假工具 +
   monkeypatch/invoke_fn 跑一次 `_run_tool_loop` 与一次 `_run_modeler_turn`，
   断言捕获的 stdout 同时包含 `🔧`、工具名、`→`、以及 `第 1/` 等迭代标记。
   具体：给 `_run_tool_loop` 传 `invoke_fn` 返回一个带 `tool_calls` 的假 AIMessage，
   tools 里放一个返回 `{"ok": True}` 的假工具，捕获 stdout 验证 `🔧 调用工具` 出现。

3. `print` 全部带 `flush=True`；`logger.*` 文案与改动前完全一致（diff 里 logger 行零改动）。

## Explicitly Do NOT Do

- 不删除、不修改任何 `logger.info/warning/error` 文案（逐字保留）。
- 不打印工具完整参数（尤其 `run_code` 的 `code` 源码）。
- 不改 `mathmodelingagents/tools/__init__.py`、graph/、config、prompt、AgentState。
- 不改 `_extract_final_output` / `_sanitize_tool_pairing` 等其它函数。
- 不引入新的第三方依赖。
- 除本文件列出的两处函数外，不动 `agents/__init__.py` 的其它任何代码。