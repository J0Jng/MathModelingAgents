# MathModelingAgents

[![Python](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](https://www.python.org/)
[![LangGraph](https://img.shields.io/badge/LangGraph-0.4%2B-green.svg)](https://github.com/langchain-ai/langgraph)
[![License](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

> 多智能体协作的数学建模竞赛全流程框架。输入题目，自动产出完整论文。

**5 层架构 · 19 个专职 Agent · 辩论循环 · 代码自动验证 · 增量写盘容灾**

```
题目文件 ──→ [Layer 1: 问题分析] ──→ [Layer 2: 数学建模] ──→ [Layer 3: 代码实现] ──→ [Layer 4: 论文写作] ──→ 完整论文
                                     ↑ 辩论循环               ↑ 重试循环             ↑ 修改循环
                              (可选) [Layer 5: 敏感性分析]
```

## 快速开始

```bash
# 1. 安装
git clone https://github.com/J0Jng/MathModelingAgents.git
cd MathModelingAgents
pip install -e .

# 2. 配置
cp .env.example .env
# 编辑 .env，填入你的 API Key（至少配一个）

# 3. 运行
python main.py problem_2024a.md
```

运行结束后，桌面上会出现一个文件夹，里面是完整的数学建模论文和所有中间结果。

## 架构

框架分为 5 层，每层由多个专职 Agent 组成，层与层之间只传递精华摘要而非原始输出。

```
Layer 1: 问题分析
  Decomposer → DataAnalyst → ConstraintAnalyst → ProblemManager
  产出：理解题目边界、挖掘数据特征、明确约束假设

Layer 2: 数学建模（辩论循环）
  ModelerA → ModelerB → ModelerC → ModelingManager ──→ 继续辩论 / 通过
  产出：数学模型定义、公式推导、求解方案

Layer 3: 代码实现（重试循环）
  AlgorithmDesigner → Coder → Visualizer → ImplManager ──→ 重试 / 通过
  产出：可执行的 Python 代码、图表、代码验证报告

Layer 4: 论文写作（修改循环）
  PaperArchitect → SectionWriter → ChartDesigner → PaperManager ──→ 修改 / 通过
  产出：完整的中文学术论文（Markdown）

Layer 5: 敏感性分析（可选）
  ParamPerturber → RobustnessAnalyst → SensitivityManager
  产出：参数稳定性评估
```

### 每个 Agent 的工作方式

```
Agent 接收 ──→ System Prompt ──→ LLM 调用 ──→ 输出写入 State
                (42KB 模板，           │            │
                含领地/禁区约束)        │            │
                                  4 步降级链     逐 Agent 写盘
                                  + 3 次重试     (崩溃不丢已完成层)
```

## 特性

### 可靠的 LLM 调用链路

```
主通道 (OpenCode Go) + 深度推理模型
  ──→ 失败 ──→ 备用通道 (DeepSeek 官方 API) + 同模型
    ──→ 失败 ──→ 主通道 + 快速模型
      ──→ 失败 ──→ 备用通道 + 快速模型
```

每步内部有 3 次指数退避重试（2s → 4s → 8s），覆盖 503、超时、限流等瞬态故障。空内容返回（某些模型在长中文 prompt 下的已知缺陷）会被识别为故障并自动重试。

### 增量输出 · 崩溃不丢数据

每个 Agent 完成工作后**立即将输出写入磁盘**。即使后续层崩溃，已完成层的内容不会丢失。重新运行 `--start-layer` 可以从中断的层继续。

### 代码自动验证

Layer 3 的 Python 代码块会被**实际执行**（沙盒限制 30 秒 / 512MB 内存），验证通过后才算完成。杜绝"看起来对的假代码"。

### 全配置化

所有设置通过 `.env` 管理（API Key、模型名、超时、辩论轮数等），每个选项都有中文注释。`.env.example` 可直接复制使用。

## 支持模型

| 模型 | 适用角色 | 特点 |
|---|---|---|
| `deepseek-v4-pro` | Manager、建模师、Coder | 深度推理，复杂逻辑 |
| `deepseek-v4-flash` | 数据分析、可视化、论文大纲 | 快速响应，高性价比 |
| `qwen3.7-max` | 论文正文撰写 | 中文写作质量高 |

### 已知不可用模型

以下模型在长中文数学建模 prompt 下会返回空内容，已被框架排除：

- `kimi-k2.7-code` · `glm-5.2` · `glm-5.1` · `qwen3.7-max`（仅建模场景不可用，论文写作仍可用）

## 配置

所有配置通过 `.env` 文件管理，完整列表见 `.env.example`。核心配置项：

| 变量 | 默认值 | 说明 |
|---|---|---|
| `MATHMODELING_LLM_PROVIDER` | `opencode` | LLM 通道：`opencode` 或 `deepseek` |
| `DEEPSEEK_API_KEY` | (必填) | DeepSeek 官方 API 密钥（降级通道用） |
| `MATHMODELING_MAX_DEBATE_ROUNDS` | `10` | 建模辩论最大轮数 |
| `MATHMODELING_MAX_IMPL_RETRIES` | `3` | 代码实现最大重试次数 |
| `MATHMODELING_DEFAULT_MAX_TOKENS` | `16384` | 单次 LLM 调用最大输出 |
| `MATHMODELING_SELECTED_LAYERS` | `1,2,3,4` | 要执行的层（调试时可用 `3,4` 跳层） |

详细说明和完整配置项见 `.env.example`。

## 命令行

```bash
python main.py <题目文件> [选项]

参数:
  problem_path          题目 Markdown 文件路径

选项:
  --output, -o NAME     输出文件夹名（默认自动生成）
  --sensitivity, -s     启用 Layer 5 敏感性分析
  --max-rounds, -r N    每层最大辩论轮次（默认 10）
  --provider, -p        指定 LLM provider（opencode / deepseek）
  --start-layer N       从第 N 层开始（1-5，调试用）

示例:
  python main.py problem_2024a.md
  python main.py problem_2024a.md -s -o my_solution
  python main.py problem_2024a.md --start-layer 4    # 只重跑论文
```

## 项目结构

```
MathModelingAgents/
├── main.py                          # 入口 + 代码验证
├── pyproject.toml
├── .env.example                     # 配置模板（复制为 .env）
├── .gitignore
│
└── mathmodelingagents/
    ├── default_config.py            # 全局配置（所有值可通过 .env 覆盖）
    │
    ├── agents/
    │   ├── __init__.py              # Agent 工厂函数（585 行）
    │   └── utils/
    │       ├── prompt_templates.py  # 全部 System Prompt 模板（971 行）
    │       └── agent_states.py      # AgentState 类型定义
    │
    ├── llm_clients/
    │   └── __init__.py              # LLM 客户端 + 统一降级链（303 行）
    │
    ├── graph/
    │   ├── setup.py                 # LangGraph StateGraph 构建
    │   ├── modeling_graph.py        # 主入口类 MathModelingGraph
    │   ├── conditional_logic.py     # 辩论/重试/循环路由
    │   └── propagation.py           # 初始状态 + 图执行参数
    │
    └── reporting.py                 # 增量写盘 + 最终报告汇总
```

## 技术栈

- **编排引擎**：[LangGraph](https://github.com/langchain-ai/langgraph) — 构建有状态的多 Agent 工作流图
- **LLM 接口**：`langchain-openai` (ChatOpenAI) — 兼容 OpenAI API 协议
- **模型**：DeepSeek V4 Pro / Flash、Qwen3.7-Max（通过 OpenCode Go 或官方 API）
- **计算沙盒**：`numpy` · `scipy` · `sympy` · `pandas` · `matplotlib` · `seaborn` · `scikit-learn`

## License

MIT
