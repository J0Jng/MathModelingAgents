# Implementation Brief: 从 Layer1 恢复，跑 L2→L3(+L5) 并生成模型解释文档

## 背景（Background）

当前 `main.py` 只能从题目文件完整跑 L1→L4。用户需要一种新启动方式：**依据一份已经存在的 Layer 1 数据**，只跑 Layer 2 建模 → Layer 3 求解，最后产出**模型解释文档**（面向自己/团队的技术说明，非竞赛论文），不跑 Layer 4 论文。若 Layer 1 判定本题需要敏感性分析，则在 L3 之后插 Layer 5，再产出解释文档。

当前框架的两个结构性缺口（已读源码确认）：
1. **Layer 1 的敏感性决策不落盘**：`_run_sensitivity_decision()`（`agents/__init__.py:41`）结果只写内存 state 的 `sensitivity_enabled` / `sensitivity_reason`（`agents/__init__.py:797-803`），不写任何文件。所以「已存在的 Layer1 数据」里目前没有这个决策。
2. **`--start-layer 2` 无法继承 Layer 1 产出**：`create_initial_state()`（`propagation.py:32`）把 `problem_report` 初始化为空串，跳过 L1 时 L2 拿不到问题分析。

## 设计决策（Design）

### D1. 新 CLI 入口 `--from-layer1 <dir>`
```
python main.py <题目.md> --from-layer1 <已有输出目录> [--output <新输出名>] [--provider ...] [--max-rounds ...]
```
- `<题目.md>` 仍是必填 positional 参数：提供 `problem_description`（题目原文），因为 `Layer1_问题分析.md` 文件里不保存题目原文。
- `--from-layer1`：读 `<dir>/Layer1_问题分析.md` 提取 Layer 1 分析结果，注入 `problem_report`。
- 与 `--start-layer` 互斥（argparse 校验：两者同时给则报错退出）。
- 隐含语义：`selected_layers = [2, 3]`（不含 4），`explain_mode = True`，`sensitivity_mode` 保持 `auto`（尊重恢复的决策）。
- 默认 `output_name`：`<题目stem>_explain`（若用户未给 `--output`），避免与已有目录重名。

### D2. 敏感性决策持久化 + 恢复（方案 B+A）
- **落盘（B）**：Layer 1 敏感性决策调用成功后，把 `{"enabled": bool, "reason": str, "mode": str, "generated_at": iso}` 写入 `<output_dir>/sensitivity_decision.json`。写入失败只 warn 不 crash。
- **恢复（A）**：`--from-layer1` 时优先读 `<dir>/sensitivity_decision.json`；读不到（旧目录）则调用 `_run_sensitivity_decision(config, problem_report)` 重新判定一次，并把结果写进新输出目录。
- 旧目录完全兼容：读不到 json 自动重新判定。

### D3. 新增 explainer 节点（新「层」`explanation`）
- 定位：**技术说明文档**，面向自己/团队，解释建模思路、假设、公式推导、求解方法、结果，以及（若有）敏感性结论。**不套竞赛论文格式**，无摘要/优缺点/改进方向等论文节。
- 实现：单 agent、无工具、单次 LLM 调用，复用 `_make_llm_node(config, "explainer", "explanation", "agent", "explanation_doc")`。
- 输出：state 字段 `explanation_doc`，并 `_record(..., layer="explanation")` 进入 `layer_outputs`（增量写盘自动产出对应 md 文件）。
- 模型路由：`layer="explanation"`, `role="agent"`，在 `_DEFAULT_LAYER_MODEL_OVERRIDES` 新增 `"explanation": {"agent": "deepseek-v4-pro"}`。

### D4. 路由拓扑
```
START → modeler_a → ... → modeling_manager → solver_agent → impl_manager → viz_agent → clear_impl
   clear_impl → [敏感性启用?] → param_perturber → robustness_analyst → sensitivity_manager → explainer → END
   clear_impl → [否则]        → explainer → END
```
- `ConditionalLogic` 新增构造参数 `explain_mode: bool = False`。
- `_route_after_impl`（`conditional_logic.py:256`）：敏感性启用 → `"param_perturber"`（不变）；否则若 `explain_mode` → `"explainer"`；否则若 `4 in selected_layers` → `"paper_agent"`；否则 END。
- `should_continue_sensitivity`（`conditional_logic.py:241`）：若 `explain_mode` → `"explainer"`；否则若 `4 in selected_layers` → `"paper_agent"`；否则 END。
- `GraphSetup.__init__`：从 `config.get("explain_mode", False)` 读取并传给 `ConditionalLogic`。
- `GraphSetup._create_agent_nodes`：`self.explainer = create_explainer(config)`。
- `GraphSetup._add_layer4_nodes` 之后新增 `workflow.add_node("explainer", self.explainer)`（无条件构建，跟 Layer 5 同理）。
- 新增 `_add_explainer_nodes(workflow)` 或在 `setup_graph` 里 `add_node("explainer", ...)` + `add_edge("explainer", END)`。
- **conditional_edges 的 destinations 映射必须与对应返回函数的值集合严格一致**（langgraph 运行时返回值不在 mapping 会抛错）：
  - `_get_layer3_destinations()`：解释模式下返回 `{ "param_perturber": "param_perturber", "explainer": "explainer", END: END }`（不含 paper_agent）；非解释模式维持现状（但也要保证与 `_route_after_impl` 返回值一致，现状已一致）。
  - `l5_dests`（`setup.py:283`）：解释模式下 `{ "explainer": "explainer", "__end__": END }`；非解释模式维持 `{ "paper_agent": "paper_agent", "__end__": END }`（若 4 被选中）。
- `first_entry` 映射已含 `2: "modeler_a"`（`setup.py:206-211`），无需改；`selected_layers=[2,3]` 时 `START → modeler_a` 已正确。

### D5. 报告输出适配
- `reporting.py` 的 `AGENT_DISPLAY` 加 `"explainer": ("模型解释 Agent", "撰写")`；`LAYER_NAMES` 加 `"explanation": "模型解释文档"`；`LAYER_TITLES` 加 `"explanation": "模型解释文档"`；`LAYER_ORDER` 追加 `"explanation"`。
- `finalize_reports()`：若 `state.get("explanation_doc")` 非空 → 写 `model_explanation.md`（内容 = explanation_doc，去掉裁决标记，加标题 `# <problem_name> 模型解释文档`），**跳过写 `final_paper.md`**；否则维持现状（写 final_paper.md）。
- `_build_summary()`：Layer 4 行改为「若 explanation_doc 非空显示『模型解释文档』状态，否则显示论文状态」。

## 改动清单（Change List）

### 1. `mathmodelingagents/agents/utils/agent_states.py`
- `AgentState` 新增字段：`explanation_doc: str`（放 Layer 4 产出区之后或独立一行，注释「模型解释文档（explain 模式）」。

### 2. `mathmodelingagents/agents/utils/prompt_templates.py`
- 新增静态 prompt 函数 `get_explainer_prompt()`（**纯静态字符串，无 f-string**，与现有 16 个 prompt 风格一致；中文）。
- 注册进 `_PROMPT_REGISTRY`：`"explainer": get_explainer_prompt`（加在 Layer 5 之后）。
- prompt 要点：你是数学建模团队的技术文档撰写者；基于 Layer 2 模型方案、Layer 3 求解结果（及可选 Layer 5 敏感性报告），写一份给团队自用的模型解释文档，结构含：建模思路与假设、符号与公式推导、求解方法、结果呈现、敏感性结论（若提供）；**不要**写竞赛论文格式的摘要/优缺点/改进方向；必须忠实引用上下文给出的公式与数值，不得编造；图表若在结果清单中则引用其路径。

### 3. `mathmodelingagents/default_config.py`
- `_DEFAULT_LAYER_MODEL_OVERRIDES` 新增 `"explanation": {"agent": "deepseek-v4-pro"}`。

### 4. `mathmodelingagents/agents/__init__.py`
- 新增模块级函数 `_persist_sensitivity_decision(config, enabled, reason)`：写 `<output_dir>/sensitivity_decision.json`（UTF-8），内容 `{"enabled": bool, "reason": str, "mode": str, "generated_at": datetime.now().isoformat()}`；`output_dir` 为空或写入失败只 `logger.warning`。
- 在 `_make_manager_node` 的 Layer 1 决策分支（`agents/__init__.py:797-803`）成功拿到 `enabled, reason` 后调用 `_persist_sensitivity_decision(config, enabled, reason)`。
- 新增 `create_explainer(config)`：
  ```python
  def create_explainer(config: dict) -> Callable[[AgentState], dict[str, Any]]:
      return _make_llm_node(config, "explainer", "explanation", "agent", "explanation_doc")
  ```
- `__all__` 追加 `"create_explainer"`。

### 5. `mathmodelingagents/graph/recovery.py`（新文件）
- 函数 `load_layer1_state(output_dir: str) -> dict`：
  - 读 `<output_dir>/Layer1_问题分析.md`（不存在则 raise FileNotFoundError）。
  - 提取 ProblemManager 的裁决段落作为 `problem_report`：用正则匹配 `### 问题分析经理 \[裁决\]\n(.*?)(?=\n---\n### |\Z)`（DOTALL）。提取失败（无该段落）则回退为整个文件内容。
  - 读 `<output_dir>/sensitivity_decision.json`（若存在）：返回 `{"problem_report": ..., "sensitivity_enabled": bool, "sensitivity_reason": str}`；json 不存在或解析失败则**只返回** `{"problem_report": ...}`（不含 sensitivity 键，由上层判定未决并重新判定）。
- 注意：读取用 `Path.read_text(encoding="utf-8")`，json 用 `json.loads` 包 try/except。

### 6. `mathmodelingagents/graph/propagation.py`
- `create_initial_state(problem_path, output_name=None, overrides: dict | None = None)` 新增可选参数 `overrides`，在返回前用 `overrides` 里的键覆盖 state 对应字段（只覆盖存在的键，如 `problem_report` / `sensitivity_enabled` / `sensitivity_reason`）。

### 7. `mathmodelingagents/graph/modeling_graph.py`
- `propagate(problem_path, output_name=None, initial_state_overrides=None)` 新增可选参数，透传给 `self.propagator.create_initial_state(..., overrides=initial_state_overrides)`。

### 8. `mathmodelingagents/graph/conditional_logic.py`
- `__init__` 新增参数 `explain_mode: bool = False`，`self.explain_mode = explain_mode`。
- `_route_after_impl`：按 D4 调整。
- `should_continue_sensitivity`：按 D4 调整。

### 9. `mathmodelingagents/graph/setup.py`
- import `create_explainer`。
- `__init__`：`self.explain_mode = config.get("explain_mode", False)`，传给 `ConditionalLogic(..., explain_mode=self.explain_mode)`。
- `_create_agent_nodes`：`self.explainer = create_explainer(config)`。
- `setup_graph`：`workflow.add_node("explainer", self.explainer)`（无条件构建）；`workflow.add_edge("explainer", END)`。
- `_get_layer3_destinations`、`l5_dests`：按 D4 补 explainer 映射（保证与返回函数值集合一致）。

### 10. `main.py`
- 新增 argparse 参数 `--from-layer1`（`type=str, default=None, metavar="DIR"`）。
- 校验：`--from-layer1` 与 `--start-layer` 同时给则 `parser.error(...)` 退出。
- 当 `--from-layer1` 给定时：
  - `from mathmodelingagents.graph.recovery import load_layer1_state`，`recovered = load_layer1_state(args.from_layer1)`。
  - 若 recovered 无 `sensitivity_enabled`：调 `from mathmodelingagents.agents import _run_sensitivity_decision` 重新判定（传入 `config` 与 `recovered["problem_report"]`），把 enabled/reason 写入 recovered（并可用 `_persist_sensitivity_decision` 落盘到新目录；若落盘目标目录尚未确定，可在 output_dir 确定后再落盘，或直接略过落盘——重新判定结果也随本流程内存使用即可）。
  - `config["selected_layers"] = [2, 3]`；`config["explain_mode"] = True`。
  - 默认 `output_name` = `f"{problem_path.stem}_explain"`（若未给 `--output`）。
  - 调用 `mm.propagate(..., initial_state_overrides=recovered)`。
  - 结尾文案：把「论文已输出到」改为「模型解释文档已输出到」（或按 finalize 实际产物提示）。
- 非 `--from-layer1` 路径行为完全不变。

### 11. `mathmodelingagents/reporting.py`
- 按 D5 适配。

## 验收标准（Acceptance Criteria，必须全部真实执行通过）

1. **测试全绿**：`.venv/Scripts/python.exe -m pytest tests/ -q`（现有 + 新增测试全部通过）。
2. **新增测试（TDD，先写失败测试再实现）**：
   - `tests/test_recovery.py`：
     - `load_layer1_state` 能从 fixture 目录提取 `problem_report`（含 manager 裁决文本）。
     - 有 `sensitivity_decision.json` 时返回 `sensitivity_enabled`/`sensitivity_reason`。
     - 缺 json 时返回的 dict 不含 `sensitivity_enabled` 键。
     - 目录缺 `Layer1_问题分析.md` 时 raise `FileNotFoundError`。
   - `tests/test_explain_routing.py`：
     - `explain_mode=True` 且 `sensitivity_enabled=True` → `_route_after_impl({...}) == "param_perturber"`。
     - `explain_mode=True` 且 `sensitivity_enabled=False` → `_route_after_impl({...}) == "explainer"`。
     - `explain_mode=True` → `should_continue_sensitivity({}) == "explainer"`。
     - `explain_mode=False`（默认）→ 行为与现有 `test_sensitivity_routing.py` 完全一致（回归）。
     - `GraphSetup(config with explain_mode=True, selected_layers=[2,3]).setup_graph().compile()`：节点集含 `explainer`；`START` 的入边指向 `modeler_a`。
   - `tests/test_sensitivity_persist.py`（可选，若写落盘函数则必写）：
     - `_persist_sensitivity_decision` 写入 json 后文件存在且 `json.loads` 内容 enabled/reason 正确。
3. **导入冒烟**：`.venv/Scripts/python.exe -c "from mathmodelingagents.graph.modeling_graph import MathModelingGraph; from mathmodelingagents.graph.recovery import load_layer1_state; from mathmodelingagents.agents import create_explainer; print('OK')"` 成功。
4. **图编译冒烟（explain 模式）**：`.venv/Scripts/python.exe -c "from mathmodelingagents.graph.setup import GraphSetup; g=GraphSetup({'llm_provider':'opencode','selected_layers':[2,3],'explain_mode':True,'sensitivity_mode':'auto','max_debate_rounds':2,'max_problem_rounds':2,'max_modeling_rounds':2,'max_revision_rounds':2,'max_impl_retries':2}).setup_graph(); c=g.compile(); print('nodes:', sorted(c.get_graph().nodes.keys()))"` 打印的节点含 `explainer`，且不报错。
5. **既有敏感性回归**：`test_sensitivity_routing.py`、`test_sensitivity_decision.py`、`test_sensitivity_mode.py` 全部保持通过（不得破坏现有真值表）。

## 明确不要做（Explicitly Do NOT Do）

- **不改 graph 拓扑之外的层职责**：不碰 Layer 1/2/3/5 的 agent prompt、manager prompt、工具列表、循环逻辑（除 D2 落盘这一处插入）。
- **不改 `AgentState` 现有字段语义**，只新增 `explanation_doc`。
- **不删/不改 `finalize_reports` 的非 explain 路径**：非解释模式写 `final_paper.md` 的行为必须原样保留。
- **不引入新依赖**：只用 stdlib + 已安装的 langgraph/langchain/langchain-openai。
- **不修改 `.env` / `.env.example` / `default_config.py` 的 provider 配置块**（除 D3 的 `_DEFAULT_LAYER_MODEL_OVERRIDES` 新增一条）。
- **不写 README 之外的文档**（本任务书就是唯一新增设计文档）。
- **不重构 `_run_tool_loop` / `_make_llm_node` / `_make_manager_node`**：explainer 直接复用 `_make_llm_node`。
- **不改变 `--start-layer` 现有行为**。
- **不提交、不 push**：只编辑文件，git 操作由 Hermes 后续处理。

## 环境注意（Windows 陷阱，务必遵守）

- 运行测试/命令一律用项目解释器 `.venv/Scripts/python.exe`，不要用 `python` / `python3` / `uv`（会解析到 Hermes 环境）。
- 每次跑命令前先 `export PYTHONPATH=` 清除 Hermes 注入的 PYTHONPATH（会 shadow 项目包）。
- 用 `cd "/f/code/projects/MathModelingAgents"` 进入项目（中文路径需在命令串内 cd，不要依赖 workdir 参数）。
