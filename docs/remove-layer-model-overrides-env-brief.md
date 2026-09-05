# 任务书：删除 MATHMODELING_LAYER_MODEL_OVERRIDES env 读取链路

## 背景
`MATHMODELING_LAYER_MODEL_OVERRIDES` 环境变量（`.env` 里用 JSON 覆盖「某层某角色」的模型）
目前已被更高优先级的机制整体压制：
1. CLI 交互式模型选择（`cli/model_picker.py` 的 `_apply_model_selection`）会把 `layer_model_overrides`
   每个角色按 quick/deep 整层改写；
2. provider 级覆盖 `provider_layer_model_overrides` 后合并胜出；
3. provider 别名映射 `provider_model_aliases`（volcengine-plan 下 qwen3.7-max → minimax-m3）。

因此该 env 变量已实际失效。用户要求：删除其读取相关代码，并删除 `.env` / `.env.example` 中的设置与注释部分。

## 改动范围（只允许动这 3 个文件）

### 1. `mathmodelingagents/default_config.py`
- 删除 `import json`（第 8 行）——删除 `_parse_layer_overrides` 后 `json` 不再被使用。
- 删除整个 `_parse_layer_overrides(env_val)` 函数（第 53–69 行，含 docstring）。
- 删除 env merge 块（第 105–112 行）：
  ```python
  # 构建最终 layer_model_overrides（env JSON 覆盖代码默认）
  _base_overrides = _DEFAULT_LAYER_MODEL_OVERRIDES.copy()
  _env_overrides = _parse_layer_overrides(_env("layer_model_overrides"))
  for _layer_name, _layer_roles in _env_overrides.items():
      if _layer_name not in _base_overrides:
          _base_overrides[_layer_name] = {}
      _base_overrides[_layer_name].update(_layer_roles)
  LAYER_MODEL_OVERRIDES: dict = _base_overrides
  ```
  替换为（保留模块级常量，供 DEFAULT_CONFIG 引用）：
  ```python
  # 层模型分配：环境变量 MATHMODELING_LAYER_MODEL_OVERRIDES 已移除，
  # 精细覆盖现由 CLI 交互选择 / provider_layer_model_overrides 控制。
  LAYER_MODEL_OVERRIDES: dict = _DEFAULT_LAYER_MODEL_OVERRIDES
  ```

### 2. `.env.example`
- 删除第 98–100 行注释中的「想覆盖的话可以用 MATHMODELING_LAYER_MODEL_OVERRIDES 这条（见下）。」，
  以及「OpenCode 模式下的模型分配代码里有更精细的按层配置」这句里指向该 env 的部分（整体删掉这一行，保留其余语义）。
- 删除整个「精细覆盖」注释块（约第 108–121 行，从 `# 精细覆盖：` 到
  `# MATHMODELING_LAYER_MODEL_OVERRIDES={"paper":{"writer":"qwen3.7-max"}}` 结束）。

### 3. `.env`
- 删除第 94–95 行注释中的「想覆盖的话可以用 MATHMODELING_LAYER_MODEL_OVERRIDES 这条（见下）。」（保留 DeepSeek 两种模型说明）。
- 删除整个「精细覆盖」注释块（与 `.env.example` 对应，从 `# 精细覆盖：` 到
  `# MATHMODELING_LAYER_MODEL_OVERRIDES={"paper":{"writer":"qwen3.7-max"}}`）。
- 注意：`.env` 是凭证文件，只做上述注释删除，**不得读取/打印/改动其中的任何 API key 行**。

## Do NOT do
- 不改 `_DEFAULT_LAYER_MODEL_OVERRIDES` 的模型分配内容（矩阵保持原样）。
- 不改 `DEFAULT_CONFIG` 里 `"layer_model_overrides"` 这个键名（下游 `get_layer_model()` 仍按此键读取）。
- 不动 `provider_layer_model_overrides`、`provider_model_aliases`、`deep_think_llm` / `quick_think_llm` 的 env 读取。
- 不动 `.env` / `.env.example` 中 DEEP_THINK_LLM / QUICK_THINK_LLM 的配置行。

## 验收（必须全部通过，报告实际输出）
1. 导入无错、默认值不变：
   ```bash
   python -c "from mathmodelingagents.default_config import DEFAULT_CONFIG; print(DEFAULT_CONFIG['layer_model_overrides']['paper']['writer'])"
   ```
   期望输出 `qwen3.7-max`。
2. 源码中不再残留：
   ```bash
   grep -n "MATHMODELING_LAYER_MODEL_OVERRIDES\|_parse_layer_overrides" mathmodelingagents/default_config.py
   ```
   期望无输出。
3. `.env` 与 `.env.example` 中不再出现 `LAYER_MODEL_OVERRIDES`：
   ```bash
   grep -n "LAYER_MODEL_OVERRIDES" .env .env.example
   ```
   期望无输出。
4. 相关测试通过：
   ```bash
   python -m pytest tests/test_main_config_flow.py tests/test_model_picker.py -q
   ```

报告：列出改动的具体行、每条验收命令的实际输出、测试结果。
