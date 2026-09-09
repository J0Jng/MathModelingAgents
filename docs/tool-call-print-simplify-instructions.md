# 改动指令：工具调用 CLI 打印改简洁形式

## 目标
在 `mathmodelingagents/agents/__init__.py` 两个函数里，把每次工具调用的 CLI 打印改成一行简洁形式：
`[layer] agent 🔧 tool_name`
即去掉「调用工具」三个字，并删掉展示工具返回结果内容的那行（避免刷屏）。

## 共 4 处（2 改 2 删），精确定位

### 函数 `_run_tool_loop`（约 475 行与 497 行）

1. 把这一行：
   `print(f"[{layer_tag}] {agent_tag} 🔧 调用工具 {tool_name}", flush=True)`
   改为：
   `print(f"[{layer_tag}] {agent_tag} 🔧 {tool_name}", flush=True)`
   （即去掉「调用工具」三个字）

2. 删除这一整行：
   `print(f"[{layer_tag}] {agent_tag} 🔧 {tool_name} → {result_str[:120]}", flush=True)`

### 函数 `_run_modeler_turn`（约 1230 行与 1244 行）

3. 同第 1 步：把 `print(f"[{layer_tag}] {agent_tag} 🔧 调用工具 {tool_name}", flush=True)` 改为 `print(f"[{layer_tag}] {agent_tag} 🔧 {tool_name}", flush=True)`

4. 同第 2 步：删除 `print(f"[{layer_tag}] {agent_tag} 🔧 {tool_name} → {result_str[:120]}", flush=True)` 这一整行

## 约束
- 只做这 4 处。其他所有代码一字不动。
- logger.info/warning/error 全部保留（尤其包含 `logger.info(...工具 {tool_name}: {result_str[:120]}...)` 的那行保留）。
- 迭代轮次 print、自检/交卷/强断 print 都保留，不要动。
- result_str 变量仍在 logger 和 ToolMessage 中使用，不要动它。
- 完成后用 `.venv/Scripts/python.exe` 跑 `python -m py_compile mathmodelingagents/agents/__init__.py` 自测，报告 exit 0 结果。