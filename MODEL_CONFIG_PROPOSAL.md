# MathModelingAgents 模型配置方案 v4 ✅ 已实施

> **更新时间**: 2026-07-21 · **状态**: 已部署到代码
>
> **v4 变更（2026-07-21）**:
> - Layer 3: AlgorithmDesigner + Coder + Visualizer → **CodingAgent**（有工具、内部 Agentic 循环，2 Agent）
> - Layer 4: PaperArchitect + SectionWriter + ChartDesigner → **PaperAgent**（有工具、分节迭代，2 Agent）
> - 全部 System Prompt 静态化（prompt caching 优化）
> - 15 agents（从 19 减少）
> - 模型配置简化：Layer 3 只需 `coder` + `manager`，Layer 4 只需 `writer` + `manager`
>
> **v3 变更（2026-07-17）**: 见下方历史记录

---

## 使用方式

```bash
# OpenCode Go 接口（推荐，模型池丰富，18+ 模型）
python main.py 题目.md --provider opencode --max-rounds 2

# DeepSeek API 接口（简洁，仅 flash/pro 两个模型）
python main.py C:\Users\joeji\Desktop\1.绿色物流配送\绿色物流配送_完整文档.md --provider deepseek --max-rounds 3

# 从中间层开始（调试用）
python main.py 题目.md --provider opencode --max-rounds 3 --start-layer 2
```

---

## 一、核心配置参数

### 全局参数

| 参数 | 值 | 说明 |
|------|-----|------|
| `default_max_tokens` | **16384** | 所有 Agent 统一上限，不再按角色差异化 |
| `default_temperature` | **0.0** | 默认温度 |
| `temperature_overrides` | `coder: 1.0` | kimi-k2.7-code 要求 temperature=1，否则 400 |
| `layer_timeouts` | 全部 **10800s（3h）** | 不限时间，确保推理模型完整跑完 |
| `max_debate_rounds` | 3-10（可配置） | L2 辩论最大轮数 |
| `max_impl_retries` | 3 | L3 代码实现最大重试次数 |

### 韧性机制

所有 Agent 的 LLM 调用统一走 `_invoke_llm_with_retry()`：
- **重试**：3 次指数退避（2s → 4s → 8s），顺序执行
- **降级**：主模型 3 次耗尽后，自动降级到 `deepseek-v4-flash` 再试一次
- **不可重试错误**：纯 400（非 transient）立即抛出
- **代码验证**：L3 生成的 Python 代码会被实际执行验证

---

## 二、方案 A：OpenCode Go 接口（`--provider opencode`）

### 2.1 全部 Agent 模型分配（逐 Agent 精确表）

```
╔══════════════════════════════════════════════════════════════════╗
║  Layer 1 — 问题分析                                              ║
╠══════════════════════════════════════════════════════════════════╣
║  decomposer           → deepseek-v4-flash    role=agent          ║
║  data_analyst         → deepseek-v4-flash    role=agent          ║
║  constraint_analyst   → deepseek-v4-flash    role=agent          ║
║  problem_manager      → deepseek-v4-pro      role=manager  ⭐    ║
╠══════════════════════════════════════════════════════════════════╣
║  Layer 2 — 数学建模 ⭐ 最耗推理的层                              ║
╠══════════════════════════════════════════════════════════════════╣
║  modeler_a            → deepseek-v4-pro      role=agent    ⭐    ║
║  modeler_b            → deepseek-v4-pro      role=agent    ⭐    ║
║  modeler_c            → deepseek-v4-pro      role=agent    ⭐    ║
║  modeling_manager     → deepseek-v4-pro      role=manager  ⭐    ║
╠══════════════════════════════════════════════════════════════════╣
║  Layer 3 — 代码实现                                              ║
╠══════════════════════════════════════════════════════════════════╣
║  algorithm_designer   → deepseek-v4-flash    role=algorithm      ║
║  coder                → kimi-k2.7-code       role=coder    🆕    ║
║  visualizer           → deepseek-v4-flash    role=visualizer     ║
║  impl_manager         → deepseek-v4-pro      role=manager  ⭐    ║
╠══════════════════════════════════════════════════════════════════╣
║  Layer 4 — 论文写作                                              ║
╠══════════════════════════════════════════════════════════════════╣
║  paper_architect      → deepseek-v4-flash    role=architect      ║
║  section_writer       → qwen3.7-max          role=writer    🆕    ║
║  chart_designer       → deepseek-v4-flash    role=visualizer     ║
║  paper_manager        → deepseek-v4-pro      role=manager  ⭐    ║
╠══════════════════════════════════════════════════════════════════╣
║  Layer 5 — 敏感性分析（可选）                                    ║
╠══════════════════════════════════════════════════════════════════╣
║  param_perturber      → deepseek-v4-flash    role=agent          ║
║  robustness_analyst   → deepseek-v4-flash    role=agent          ║
║  sensitivity_manager  → deepseek-v4-pro      role=manager  ⭐    ║
╚══════════════════════════════════════════════════════════════════╝
```

### 2.2 分配逻辑

```
所有 Manager 角色        → deepseek-v4-pro     （推理裁决，不可省）
L2 建模师（modeler_*）   → deepseek-v4-pro     （深度数学推理）
L3 Coder                 → kimi-k2.7-code      （代码专精，失败降级 flash）
L4 SectionWriter         → qwen3.7-max         （中文论文，失败降级 flash）
其他所有 Agent            → deepseek-v4-flash   （省 token 首选）
```

### 2.3 特殊说明

#### kimi-k2.7-code（L3 Coder）
- **温度要求**：`temperature=1.0`（否则 OpenCode Go 后端返回 400）
- **已知问题**：在长中文数学 prompt 下可能返回空内容
- **兜底机制**：框架 3 次重试后自动降级 `deepseek-v4-flash`
- 代码质量会因降级而降低，但不影响可运行性

#### qwen3.7-max（L4 SectionWriter）
- **已知问题**：在长中文数学 prompt 下可能返回空内容
- **兜底机制**：框架 3 次重试后自动降级 `deepseek-v4-flash`
- 论文质量会因降级而降低，可通过多轮 REVISE 弥补

#### 不可用模型（已排除）
| 模型 | 问题 | 状态 |
|------|------|------|
| `kimi-k2.7-code` | 长中文数学 prompt 返空 | ⚠️ 保留但配降级兜底 |
| `qwen3.7-max` | 长中文数学 prompt 返空 | ⚠️ 保留但配降级兜底 |
| `glm-5.2` / `glm-5.1` / `glm-5` | 长中文数学 prompt 返空 | ❌ 已移除 |
| `deepseek-v4-flash` | 复杂推理质量较低 | ✅ 用于轻量任务 |
| `deepseek-v4-pro` | 延迟高但推理质量好 | ✅ 最佳选择 |

---

## 三、方案 B：DeepSeek API 接口（`--provider deepseek`）

DeepSeek 官方 API 只有 2 个模型，分配规则极简：

```
所有 Agent    → deepseek-v4-flash   （快速分析/写作/画图）
所有 Manager  → deepseek-v4-pro    （推理裁决）
```

### 3.1 全部 Agent 模型分配

```
╔══════════════════════════════════════════════════════════════╗
║  Layer 1 — 问题分析                                          ║
╠══════════════════════════════════════════════════════════════╣
║  decomposer           → deepseek-v4-flash                    ║
║  data_analyst         → deepseek-v4-flash                    ║
║  constraint_analyst   → deepseek-v4-flash                    ║
║  problem_manager      → deepseek-v4-pro                      ║
╠══════════════════════════════════════════════════════════════╣
║  Layer 2 — 数学建模                                          ║
╠══════════════════════════════════════════════════════════════╣
║  modeler_a            → deepseek-v4-flash   ⚠️ 注意          ║
║  modeler_b            → deepseek-v4-flash   ⚠️ 注意          ║
║  modeler_c            → deepseek-v4-flash   ⚠️ 注意          ║
║  modeling_manager     → deepseek-v4-pro                      ║
╠══════════════════════════════════════════════════════════════╣
║  Layer 3 — 代码实现                                          ║
╠══════════════════════════════════════════════════════════════╣
║  algorithm_designer   → deepseek-v4-flash                    ║
║  coder                → deepseek-v4-flash                    ║
║  visualizer           → deepseek-v4-flash                    ║
║  impl_manager         → deepseek-v4-pro                      ║
╠══════════════════════════════════════════════════════════════╣
║  Layer 4 — 论文写作                                          ║
╠══════════════════════════════════════════════════════════════╣
║  paper_architect      → deepseek-v4-flash                    ║
║  section_writer       → deepseek-v4-flash                    ║
║  chart_designer       → deepseek-v4-flash                    ║
║  paper_manager        → deepseek-v4-pro                      ║
╠══════════════════════════════════════════════════════════════╣
║  Layer 5 — 敏感性分析                                        ║
╠══════════════════════════════════════════════════════════════╣
║  param_perturber      → deepseek-v4-flash                    ║
║  robustness_analyst   → deepseek-v4-flash                    ║
║  sensitivity_manager  → deepseek-v4-pro                      ║
╚══════════════════════════════════════════════════════════════╝
```

### 3.2 局限性

- **L2 建模师用 flash**（DeepSeek 模式没有 pro 给 agent），建模质量低于 OpenCode 模式
  - 建议：增加 `max_debate_rounds` 到 2-3，让辩论弥补单次建模质量
  - 或改用 OpenCode 接口让建模师享受 pro
- **L3 Coder 用 flash**（DeepSeek 无代码专精模型），代码质量低于 kimi-k2.7-code
- **L4 Writer 用 flash**（DeepSeek 无中文写作专精模型），论文质量低于 qwen3.7-max

---

## 四、配置来源（代码位置）

| 文件 | 配置项 |
|------|--------|
| `mathmodelingagents/default_config.py` | `layer_model_overrides`（模型路由）、`layer_timeouts`、`max_tokens_overrides`、`temperature_overrides` |
| `mathmodelingagents/llm_clients/__init__.py` | `get_layer_model()`（路由解析）、`create_layer_llm()`（客户端创建） |
| `mathmodelingagents/agents/__init__.py` | `_invoke_llm_with_retry()`（重试+降级逻辑） |

### 路由解析规则（`get_layer_model`）

```python
# OpenCode 模式
overrides = config["layer_model_overrides"]
layer_config = overrides[layer]          # 如 {"agent": "pro", "manager": "pro"}
model = layer_config[role]               # 精确匹配 role key
# 降级：role 不在 layer_config 中 → 尝试 "agent" key → 否则 quick_think_llm

# DeepSeek 模式
if role == "manager":
    model = config["deep_think_llm"]      # deepseek-v4-pro
else:
    model = config["quick_think_llm"]     # deepseek-v4-flash
```

---

## 五、适用场景速查

| 场景 | 命令 | 预计耗时 |
|------|------|---------|
| 快速验证（<30min） | `--provider deepseek --max-rounds 1` | ~20 min |
| 标准运行 | `--provider opencode --max-rounds 2` | ~60-90 min |
| 正式比赛（高质量） | `--provider opencode --max-rounds 3` | ~90-120 min |
| 调试某层 | `--provider opencode --start-layer N` | 仅该层耗时 |

---

## 六、环境变量

```bash
# 可选（有默认值）
export MATHMODELING_LLM_PROVIDER=opencode    # opencode 或 deepseek
export MATHMODELING_MAX_DEBATE_ROUNDS=3
export MATHMODELING_ENABLE_SENSITIVITY=false

# API Key（必须）
# ~/.hermes/.env:
OPENCODE_GO_API_KEY=sk-...
DEEPSEEK_API_KEY=sk-...
```

---

## 七、版本历史

| 版本 | 日期 | 主要变化 |
|------|------|---------|
| v1 | 2026-07-15 | 初始版本：双 provider 方案、timeout 600-1800s、kimi/qwen 用于 L3+L4 |
| v2 | 2026-07-15 | timeout 统一 10800s、**但 L3 仍用 kimi 给 algorithm+coder、L4 仍用 qwen 给 agent** |
| **v3** | **2026-07-17** | **L2 agent 切 pro、L3 拆分角色（algorithm→flash / coder→kimi / visualizer→flash）、L4 拆分角色（architect→flash / writer→qwen / visualizer→flash）、temperature_overrides（coder=1.0）、重试+降级机制、max_tokens 统一 16384、清理输出长度约束提示词** |

---

## 八、相关文档

- `math-modeling-agents` skill（SKILL.md）— 运行流程、验证命令、关键陷阱
- `references/model-routing-and-prompt-quality-fix.md` — 模型路由切换与提示词增强
- `references/provider-temperature-constraints.md` — provider 温度约束
- `references/llm-retry-and-code-verification-fix.md` — 重试降级 + 代码验证
- `references/paper-retry-and-final-paper-guard.md` — Paper 层 REVISE 重试
- `scripts/probe_model_quality.py` — 切模型前的中文长文本探针
