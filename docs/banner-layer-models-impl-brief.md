# Banner 打印每层 agent/manager 实际模型 —— 实施任务书

## 背景

CLI 交互式模型选择会把用户选中的模型写入 `layer_model_overrides`（非 deepseek provider），但
`main.py` 结尾的 Banner 仍然只读 `quick_think_llm` / `deep_think_llm` 两个过时键，导致：

- 用户菜单里选了 `ark-code-latest`（默认项），Banner 却显示 `Quick: deepseek-v4-flash / Deep: deepseek-v4-pro`。
- Banner 与实际各层 agent 用到的模型（`get_layer_model(...)` 解析结果）不一致，误导用户。

用户要求：**Banner 改为打印每个 layer 的 agent/manager 实际解析出的模型**（即使命 `get_layer_model` 的真实结果）。

父级需求文档：无（本次为独立 CLI 展示修复）。本 brief 由 Hermes 架构师编写，交 Claude Code 执行。

## 改动范围（尽量收窄）

- 仅改 1 个文件：`main.py` 的 Banner 打印段（约 `main.py:269-282`）。
- **不动**：自由改 graph 拓扑、`AgentState`、`llm_clients/*`、`default_config.py`、`cli/model_picker.py`、`cli/model_catalog.py`。这些逻辑正确，只是 Banner 没展示正确数据源。

## 目标行为

Banner 不再显示 `Quick:` / `Deep:` 两行读取 `quick_think_llm`/`deep_think_llm`，
改为显示每一层 agent 与 manager 角色**实际会调用到的模型**（即 `get_layer_model(config, layer, role)` 的结果，
含 provider 级覆盖、别名映射，与运行期完全一致）。

展示要求：
1. 逐层显示。层与 `get_layer_model` 的 layer 参数一致（`problem` / `modeling` / `implementation` / `paper`）。
   若 `config` 的 `selected_layers` 不含某层，仍可照常列出全部四层（保守：全部列出，注释说明由 selected_layers 控制执行）。
2. 每层显示其 agent 角色与 manager 角色。角色取值遵循 `get_layer_model` 的签名与现有 layer 里实际使用的角色：
   - `problem` / `modeling` / `paper`：`agent` + `manager`
   - `implementation`：`coder` + `manager`（该层没有 agent 角色，用 coder 表示"模型求解/代码实现主体"；若你确认该层运行期用 agent，则用 agent）
   > 用 `grep` 查看各 layer 节点 `create_layer_llm(config, layer, role)` 的调用以确认实际角色名，不要臆测。
3. Banner 样式与现状一致（box-drawing 边框、右对齐 `:<N>` padding 以对齐 `║`、`Print:...` f-string），
   `:<33`/`:<34` padding 可调整为容纳"层名 + 角色模型"的更长内容，保证最右 `║` 依旧对齐。
4. 若某 layer 的上层 provider（如 `deepseek`）走的是 `quick/deep_think_llm` 分支，`get_layer_model` 已正确处理，Banner 无需特判，直接复用结果。

## 验收标准（Claude Code 自测 + Hermes 复核）

1. 用现有入口模拟一次配置（如 `build_config_from_args` 或构造一个含
   `layer_model_overrides={'problem': {'agent': 'ark-code-latest', 'manager': 'ark-code-latest'}, ...}` 的 config），
   打印 Banner，确认：
   - `problem agent`、`problem manager`（或对应实际角色）显示 `ark-code-latest`；
   - 不再出现 `Quick:` 与 `Deep:` 两行；
   - 每条右边缘 `║` 对齐。
2. `python -m py_compile main.py` 通过。
3. 无其他改动（`git diff --stat` 仅 `main.py` 一行）。
4. 若 `tests/` 中存在对 Banner 输出断言的测试（如 `test_main`），同步跑相关测试确认不挂；
   若无相关测试则说明已核对该文件没有 Banner 断言。

## Do NOT

- 不要改动 `get_layer_model` / `_apply_model_alias` / `create_layer_llm` / `prompt_model_selection` / `_apply_model_selection` 的逻辑。
- 不要移除 `quick_think_llm`/`deep_think_llm` 这两个键（deepseek provider 仍用它们，Banner 只是不再单独展示）。
- 不要动 Banner 的 box 风格或标题 `MathModelingAgents v0.1.0`。
- 不要重构其他无关代码或做 formatting 改动。

完成后报告：改动 diff（简短）、你确认的各 layer 实际角色名、自测输出片段。