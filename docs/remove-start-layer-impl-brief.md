# 删除 `--start-layer` CLI 参数

> **状态：已完成（2026-09-03）。** Claude Code 代理当时不可连（本机 15721 代理无监听），经向用户说明后由 Hermes 直接执行（机械低风险改动）。103/103 测试通过。

## 背景与目标

`--start-layer`（从第 N 层开始）经实测与 SKILL 判定无法正确工作：`create_initial_state()` 空初始化，跳过 L1/L2 时下游拿不到问题分析与数学模型，不能用于正常调试。已有更好的替代 `--from-layer1 DIR`（从既有 Layer 1 输出恢复，保持上下文完整）。

用户决定：**完整删除 `--start-layer` 参数及其相关代码与全部文档引用**。

## 关键约束（"Do NOT do"）

- **不要改 `selected_layers` 机制蓝图**。`config['selected_layers']` 是敏感性闸门 `conditional_logic.py:_sensitivity_active` 与 `graph/setup.py` 层间路由的底层依赖。删除后它稳定取各处的 `or [1,2,3,4]` 默认值（全层运行），行为不变。**不得**新增 DEFAULT_CONFIG 该键、不得重写 ConditionalLogic/setup 的选层逻辑。
- **不要动 `--from-layer1`**（独立功能，保留）。
- **不要动** `mathmodelingagents/` 包内任何 .py（`graph/`、`agents/`、`reporting.py` 等均不涉及）。

## 具体改动

### 1. `main.py`
- 删 `parser.add_argument("--start-layer", ...)` 块（约 main.py:206-211，含 `choices=[1,2,3,4,5]`）。
- 删 `build_config_from_args()` 中 `config["selected_layers"] = list(range(args.start_layer, 5))`（main.py:152）。
- 删 `main()` 中互斥校验：
  ```python
  # 互斥校验：--from-layer1 与 --start-layer 不能同时显式指定
  if args.from_layer1 and args.start_layer != 1:
      parser.error("--from-layer1 与 --start-layer 互斥，不能同时指定")
  ```
- `--from-layer1` 的 help 文案去掉"（与 --start-layer 互斥）"字样。
- `build_config_from_args()`：`selected_layers` 不再由本函数显式写入 config（由 DEFAULT_CONFIG / 各处默认值承担）。`--from-layer1` 分支仍保留 `config["selected_layers"] = [2, 3]`。

### 2. `tests/test_main_config_flow.py`
- 从 `_args()` 的 base dict 中删除 `start_layer=1`。

### 3. 文档引用清理
- `README.md`：152 / 218 / 330-331 / 336 处引用 —— 改为只讲 `--from-layer1`，删除 `--start-layer N` 的运行示例与"从第 N 层开始"说明。
- `MODEL_CONFIG_PROPOSAL.md`：22 / 201。
- **保留** `docs/explain-from-layer1-impl-brief.md`（历史决策记录，git 跟踪中），**仅删**其中提到 `--start-layer` 的互斥校验句（8-9、19、107、150）与说明句，改为 `--from-layer1`。**不要删除整个文件**。

> **skills 文档**（`C:\Users\joeji\AppData\Local\hermes\skills\autonomous-ai-agents\math-modeling-agents\SKILL.md` 与其 `references/start-layer-and-model-validation.md`）由 **Hermes 在收尾阶段直接编辑**，Claude Code 不负责编辑 skills 目录外文件（见下方分工与收尾）。

## 验收标准

1. `grep -rn "start-layer\|start_layer" ` 在工作区 **仅剩** `docs/` 与 `tests/` 中合理保留项（若 brief 删除则为 0）。不允许在 `main.py` / `README` / `MODEL_CONFIG_PROPOSAL` 残留。
2. `python -c "from main import build_config_from_args"` 可导入（语法通过）。
3. `pytest tests/test_main_config_flow.py -q` 全绿。
4. `python main.py --help` 不再列出 `--start-layer`。
5. `python main.py dummy_problem.md --start-layer 3` 应报 argparse 错误（Unknown argument）。

## 分工

- **Claude Code**：执行上述 `main.py`、`tests/`、repo 内文档（README/MODEL_CONFIG_PROPOSAL/impl-brief）改动并自测。
- **Hermes**：收尾时清理 skills 文档（SKILL.md 中该参数说明）、跑最终验证、在 Obsidian 归档决策。

## 收尾（Hermes 负责）

- 删除 skills 文档中对 `--start-layer` 的说明段落，改为突出 `--from-layer1`。
- `git diff --stat` 确认改动集中。
- `pytest tests/` 全绿。
- Obsidian 项目作品集记录此项决策（如需）。