# MathModelingAgents 模型配置

> 本文件说明框架中每个 Agent 在四种 LLM provider 下的模型分配，以及路由解析、降级链、生成参数。
> 配置实现在 `mathmodelingagents/default_config.py` 与 `mathmodelingagents/llm_clients/__init__.py`，本文档与代码保持同步。

## 使用方式

```bash
# OpenCode Go 网关（模型池丰富）
python main.py 题目.md --provider opencode --max-rounds 3

# DeepSeek 官方 API（仅 flash/pro 两个模型）
python main.py 题目.md --provider deepseek --max-rounds 3

# 火山方舟 Coding Plan（普通方舟 Key）
python main.py 题目.md --provider volcengine --max-rounds 2

# 火山方舟 Agent Plan（订阅套餐 Key）
python main.py 题目.md --provider volcengine-plan --max-rounds 2

# 从中间层开始（调试用）
python main.py 题目.md --provider opencode --start-layer 2
```

---

## 统一模型配置表

> 四列 provider 为每个 Agent 实际解析出的最终模型。`deep_think_llm = deepseek-v4-pro`，`quick_think_llm = deepseek-v4-flash`。

| 层 | Agent | role | opencode | deepseek | volcengine (Coding) | volcengine-plan (Agent) |
|----|-------|------|----------|----------|---------------------|-------------------------|
| L1 问题分析 | decomposer | agent | deepseek-v4-flash | deepseek-v4-flash | deepseek-v4-flash | deepseek-v4-flash |
| | data_analyst | agent | deepseek-v4-flash | deepseek-v4-flash | deepseek-v4-flash | deepseek-v4-flash |
| | constraint_analyst | agent | deepseek-v4-flash | deepseek-v4-flash | deepseek-v4-flash | deepseek-v4-flash |
| | problem_manager | manager | deepseek-v4-pro | deepseek-v4-pro | deepseek-v4-pro | deepseek-v4-pro |
| L2 数学建模 | modeler_a | agent | deepseek-v4-pro | deepseek-v4-flash ⚠️ | deepseek-v4-pro | deepseek-v4-pro |
| | modeler_b | agent | deepseek-v4-pro | deepseek-v4-flash ⚠️ | deepseek-v4-pro | deepseek-v4-pro |
| | modeler_c | agent | deepseek-v4-pro | deepseek-v4-flash ⚠️ | deepseek-v4-pro | deepseek-v4-pro |
| | modeling_manager | manager | deepseek-v4-pro | deepseek-v4-pro | deepseek-v4-pro | deepseek-v4-pro |
| L3 代码实现 | solver_agent | coder | deepseek-v4-pro | deepseek-v4-flash | deepseek-v4-pro | kimi-k2.7-code |
| | viz_agent | coder | deepseek-v4-pro | deepseek-v4-flash | deepseek-v4-pro | kimi-k2.7-code |
| | impl_manager | manager | deepseek-v4-pro | deepseek-v4-pro | deepseek-v4-pro | deepseek-v4-pro |
| L4 论文写作 | paper_agent | writer | qwen3.7-max | deepseek-v4-flash | deepseek-v4-pro * | minimax-m3 |
| | paper_manager | manager | deepseek-v4-pro | deepseek-v4-pro | deepseek-v4-pro | deepseek-v4-pro |
| L5 敏感性分析 | param_perturber | agent | deepseek-v4-flash | deepseek-v4-flash | deepseek-v4-flash | deepseek-v4-flash |
| | robustness_analyst | agent | deepseek-v4-flash | deepseek-v4-flash | deepseek-v4-flash | deepseek-v4-flash |
| | sensitivity_manager | manager | deepseek-v4-pro | deepseek-v4-pro | deepseek-v4-pro | deepseek-v4-pro |
| 解释文档 | explainer | agent | deepseek-v4-pro | deepseek-v4-flash | deepseek-v4-pro | deepseek-v4-pro |

> `*` volcengine 下 `qwen3.7-max` 经别名映射为 `deepseek-v4-pro`（`provider_model_aliases`）。
> `⚠️` deepseek 官方 API 无 pro 给建模师，L2 建模质量会低于其他 provider，可通过增大 `--max-rounds` 用辩论轮次弥补。

### 分配逻辑（非 deepseek）

```
所有 Manager                 → deepseek-v4-pro   （推理裁决，不可省）
L2 建模师（modeler_*）        → deepseek-v4-pro   （深度数学推理）
L3 coder（solver/viz）       → deepseek-v4-pro   （默认）/ kimi-k2.7-code（volcengine-plan）
L4 writer（paper_agent）     → qwen3.7-max       （默认，中文论文）/ minimax-m3（volcengine-plan）
L1/L5 agent 与 explainer     → deepseek-v4-flash 或 deepseek-v4-pro（见上表）
```

---

## 路由解析规则（`get_layer_model`）

```python
provider = config["llm_provider"]

if provider == "deepseek":
    # 官方 API 极简规则
    model = deep_think_llm if role == "manager" else quick_think_llm
else:
    # opencode / volcengine / volcengine-plan：查 layer_model_overrides
    layer_config = layer_model_overrides[layer]                          # 全局
    layer_config = {**layer_config,
                    **provider_layer_model_overrides[provider][layer]}   # provider 级覆盖优先
    if role in layer_config:
        model = layer_config[role]
    elif "agent" in layer_config:
        model = layer_config["agent"]                                    # 兜底到 agent key
    else:
        model = quick_think_llm

model = provider_model_aliases[provider].get(model, model)               # 别名映射
```

优先级：**provider 级覆盖 > 全局覆盖 > agent key > quick_think_llm**，最后套别名映射。

### provider 级覆盖（`provider_layer_model_overrides`）

| provider | 覆盖 |
|----------|------|
| `volcengine-plan` | L3 `coder` → `kimi-k2.7-code`；L4 `writer` → `minimax-m3` |
| 其余 | 无 |

### 模型别名映射（`provider_model_aliases`）

| provider | 映射 |
|----------|------|
| `volcengine` | `qwen3.7-max` → `deepseek-v4-pro` |
| `volcengine-plan` | `qwen3.7-max` → `minimax-m3` |

---

## 降级链与重试

所有 Agent 的 LLM 调用统一走 `invoke_with_fallback()`，共 4 步：

```
1) 主 provider + 角色模型          （如 opencode + deepseek-v4-pro）
2) fallback provider + 同模型名    （如 deepseek 官方 + deepseek-v4-pro）
3) 主 provider + flash 模型        （opencode + deepseek-v4-flash）
4) fallback provider + flash 模型  （deepseek 官方 + deepseek-v4-flash）
```

- 每步内部 3 次指数退避重试（2s → 4s → 8s），仅对瞬态故障（429/5xx/超时等）重试。
- 纯 400 等不可重试错误立即进入下一步降级。
- 输出 < 10 字符视为模型故障，触发重试/降级。
- 4 步全部失败抛出 `RuntimeError`；命中降级路径时输出会附加 `[降级 provider/model]` 标记。
- `fallback_provider` 默认 `deepseek`，可用 `fallback_base_url` 自定义。

---

## 生成参数

| 参数 | 值 | 说明 |
|------|-----|------|
| `default_max_tokens` | **16384** | 所有 Agent 统一上限（`max_tokens_overrides` 当前为空） |
| `default_temperature` | **0.2** | 默认温度 |
| `temperature_overrides` | `manager: 0.1` `coder: 0.0` `writer: 0.5` | 温度解析优先级：`agent_name > role > default` |

`volcengine-plan` 端点额外设置 `reasoning_effort = "medium"`：Agent Plan 的 `max_completion_tokens` 是推理+正文总预算，显式压制推理暴走，避免正文返空（已实测）。

---

## Provider 明细

| provider | Base URL | API Key 环境变量 | 备注 |
|----------|----------|------------------|------|
| `opencode` | `https://opencode.ai/zen/go/v1` | `OPENCODE_GO_API_KEY` | 模型池最丰富 |
| `deepseek` | `https://api.deepseek.com/v1` | `DEEPSEEK_API_KEY` | 仅 flash/pro 两模型 |
| `volcengine` | `https://ark.cn-beijing.volces.com/api/coding/v3` | `VOLCENGINE_API_KEY` | 火山方舟 Coding Plan；`qwen3.7-max` 自动别名到 `deepseek-v4-pro` |
| `volcengine-plan` | `https://ark.cn-beijing.volces.com/api/plan/v3` | `VOLCENGINE_PLAN_API_KEY`（fallback `VOLCENGINE_API_KEY`） | 火山方舟 Agent Plan；L3 coder→kimi、L4 writer→minimax-m3 |

> 超时：所有层 `layer_timeouts` 统一 **10800s（3h）**，确保推理模型完整跑完。

---

## 环境变量

```bash
# provider（可选，默认 opencode）
export MATHMODELING_LLM_PROVIDER=volcengine-plan   # opencode / deepseek / volcengine / volcengine-plan

# API Key（必须，写入 ~/.hermes/.env 或项目 .env）
OPENCODE_GO_API_KEY=sk-...          # opencode
DEEPSEEK_API_KEY=sk-...             # deepseek / 降级兜底
VOLCENGINE_API_KEY=...              # volcengine (Coding Plan)
VOLCENGINE_PLAN_API_KEY=...         # volcengine-plan (Agent Plan，订阅后从控制台换取)

# 覆盖各层模型（JSON，深合并到代码默认值）
MATHMODELING_LAYER_MODEL_OVERRIDES={"paper":{"writer":"qwen3.7-max"}}

# 其他常用
MATHMODELING_MAX_MODELING_ROUNDS=5
MATHMODELING_MAX_REVISION_ROUNDS=8
MATHMODELING_SENSITIVITY_MODE=auto     # auto / always / never
```

---

## 配置来源（代码位置）

| 文件 | 配置项 |
|------|--------|
| `mathmodelingagents/default_config.py` | `layer_model_overrides`、`provider_layer_model_overrides`、`provider_model_aliases`、`deep_think_llm`/`quick_think_llm`、`temperature_overrides`、`default_max_tokens`、`layer_timeouts` |
| `mathmodelingagents/llm_clients/__init__.py` | `get_layer_model()`（路由解析）、`_apply_model_alias()`（别名）、`create_layer_llm()`（客户端创建）、`invoke_with_fallback()`（降级链）、`_invoke_with_retry()`（重试） |

---

## 适用场景速查

| 场景 | 命令 | 预计耗时 |
|------|------|---------|
| 快速验证 | `--provider deepseek --max-rounds 1` | ~20 min |
| 标准运行 | `--provider opencode --max-rounds 2` | ~60-90 min |
| 正式比赛（高质量） | `--provider opencode --max-rounds 3` | ~90-120 min |
| 火山方舟 Agent Plan | `--provider volcengine-plan --max-rounds 2` | ~60-90 min |
| 火山方舟 Coding Plan | `--provider volcengine --max-rounds 2` | ~60-90 min |
| 调试某层 | `--provider opencode --start-layer N` | 仅该层耗时 |
