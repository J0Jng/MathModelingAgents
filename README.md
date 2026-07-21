# MathModelingAgents

[![Python](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](https://www.python.org/)
[![LangGraph](https://img.shields.io/badge/LangGraph-0.4%2B-green.svg)](https://github.com/langchain-ai/langgraph)
[![License](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

> 多智能体协作的数学建模竞赛全流程框架。输入题目，自动产出完整论文。

**5 层架构 · 15 个专职 Agent · 辩论循环 · Agentic Tool Calling · Prompt 缓存优化 · 增量写盘容灾**

```
题目文件 ──→ [L1: 问题分析] ──→ [L2: 数学建模] ──→ [L3: 代码实现] ──→ [L4: 论文写作] ──→ 完整论文
              顺序流水线          辩论循环             Agentic 循环         Agentic 循环
                                                        (写→跑→修)          (写→查→改)
                                    (可选) [L5: 敏感性分析]
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

框架分为 5 层，层与层之间只传递精华摘要而非原始输出。

```
Layer 1: 问题分析（4 Agent，顺序协作）
  Decomposer → DataAnalyst → ConstraintAnalyst → ProblemManager
  产出：理解题目边界、挖掘数据特征、明确约束假设

Layer 2: 数学建模（4 Agent，辩论循环）
  ModelerA → ModelerB → ModelerC → ModelingManager ──→ 继续辩论 / 通过
  产出：数学模型定义、公式推导、求解方案

Layer 3: 代码实现（2 Agent，Agentic Tool Calling）
  CodingAgent（有工具，内部循环：写→跑→修→再跑）→ ImplManager ──→ 重试 / 通过
  产出：可运行代码、验证结果、论文级图表

Layer 4: 论文写作（2 Agent，Agentic Tool Calling）
  PaperAgent（有工具，分节迭代：写→读数据核实→修改）→ PaperManager ──→ 修改 / 通过
  产出：完整的中文学术论文（Markdown）

Layer 5: 敏感性分析（3 Agent，可选）
  ParamPerturber → RobustnessAnalyst → SensitivityManager
  产出：参数稳定性评估
```

### Layer 3 & 4 的核心创新：Agentic Tool Calling

Layer 3 的 CodingAgent 和 Layer 4 的 PaperAgent 不再是单次 LLM 调用，而是
**有真实工具的自主循环 Agent**：

```
┌─ CodingAgent（30 轮 max）──────────────┐
│  工具: run_code / read_file /          │
│        write_file / list_dir           │
│                                        │
│  写代码 → 执行 → 看报错 → 修复          │
│  → 再执行 → 结果正确 → 生成图表         │
│  → 自检 → SELF_CHECK_PASSED            │
└────────────────────────────────────────┘

┌─ PaperAgent（30 轮 max）───────────────┐
│  工具: read_file / list_dir /          │
│        write_file（只读为主）           │
│                                        │
│  写 §1 → read_file 核实数据 → 改        │
│  → 写 §2 → read_file 核实公式 → 改      │
│  → ... → 全稿自审 → SELF_CHECK_PASSED  │
└────────────────────────────────────────┘
```

### 每个 Agent 的工作方式

```
Agent 接收 ──→ 静态 System Prompt ──→ LLM 调用 ──→ 输出写入 State
               (可被 API 缓存)            │            │
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

每步内部有 3 次指数退避重试（2s → 4s → 8s），覆盖 503、超时、限流等瞬态故障。

### Prompt 缓存优化

所有 Agent 的 System Prompt 均为**纯静态字符串**（无变量注入），确保 LLM API 的
prefix caching 可以命中。动态值（路径、轮次、重试次数等）全部移至 User Message。

### 增量输出 · 崩溃不丢数据

每个 Agent 完成工作后**立即将输出写入磁盘**。即使后续层崩溃，已完成层的内容不会丢失。
重新运行 `--start-layer` 可以从中断的层继续。

### 中文图表渲染保护

沙盒执行环境自动检测中文字体（SimHei / Microsoft YaHei / STSong 等），
若系统无中文字体则输出警告。CodingAgent 的 prompt 要求根据字体可用性
决定使用中文或英文标签，避免出现方块乱码。

### 全配置化

所有设置通过 `.env` 管理（API Key、模型名、超时、辩论轮数等），每个选项都有中文注释。
`.env.example` 可直接复制使用。

## AI 编码 Agent 使用指南

本项目设计时考虑了 AI 编码助手（如 Claude Code、GitHub Copilot、Cursor 等）的使用场景。
以下是指引 AI Agent 快速上手此项目的说明。

### 项目定位

这是一个 **LangGraph 多智能体数学建模框架**。输入一道数学建模竞赛题目（Markdown 文件），
自动产出完整的解答论文（含代码、图表、分析）。

### 架构速览

```
5 层流水线: L1 问题分析 → L2 数学建模(辩论) → L3 代码实现(Agentic) → L4 论文写作(Agentic) → L5 敏感性分析(可选)
```

- **L1/L2/L5**：传统 LLM 节点链（System Prompt → 单次调用 → 输出）
- **L3 CodingAgent**：有 `run_code` / `read_file` / `write_file` / `list_dir` 工具的 Agentic 循环（写→跑→修，最多 30 轮）
- **L4 PaperAgent**：有 `read_file` / `list_dir` / `write_file` 工具的 Agentic 循环（逐节写→核实→改，最多 30 轮）
- **总 Agent 数**：15（L1: 4, L2: 4, L3: 2, L4: 2, L5: 3）

### 关键文件地图

| 文件 | 作用 | 什么时候看 |
|------|------|-----------|
| `mathmodelingagents/graph/setup.py` | 图拓扑定义（节点、边、路由） | 修改 Agent 编排流程 |
| `mathmodelingagents/graph/conditional_logic.py` | 条件路由（辩论继续/结束、重试/通过） | 修改裁决逻辑 |
| `mathmodelingagents/agents/__init__.py` | Agent 工厂函数（含 Tool Calling 循环） | 修改 Agent 行为、添加工具 |
| `mathmodelingagents/agents/utils/prompt_templates.py` | 全部 System Prompt（纯静态，可缓存） | 修改 Agent 指令 |
| `mathmodelingagents/tools/__init__.py` | 沙盒执行 + LangChain Tool 封装 | 修改/添加工具 |
| `mathmodelingagents/llm_clients/__init__.py` | LLM 客户端 + 降级链 | 修改 API 调用逻辑 |
| `mathmodelingagents/default_config.py` | 全局配置 + 模型路由 | 修改默认值、模型分配 |
| `.env.example` | 环境变量说明 | 了解/修改用户配置项 |
| `main.py` | CLI 入口 | 了解启动流程 |

### 常用操作

```bash
# 运行完整流程
python main.py problem_2024a.md

# 只跑代码实现 + 论文（跳过前面的分析，调试用）
python main.py problem_2024a.md --start-layer 3

# 只用 DeepSeek 官方 API
python main.py problem_2024a.md --provider deepseek

# 启用敏感性分析
python main.py problem_2024a.md -s
```

### 修改 Prompt 的正确方式

1. 找到 `prompt_templates.py` 中对应的 `get_XXX_prompt()` 函数
2. 所有 prompt 函数是**无参数纯静态字符串**（为了 API 缓存）。不要在 prompt 中注入变量，动态值通过 `_build_context()` 在 user message 中提供
3. 修改后运行 `pytest tests/test_layer3_layer4.py -v` 验证

### 添加新 Agent 的步骤

1. `prompt_templates.py`：添加 `get_new_agent_prompt()` 函数
2. `_PROMPT_REGISTRY`：注册新 prompt
3. `agents/__init__.py`：添加 `create_new_agent()` 工厂函数
4. `graph/setup.py`：`_create_agent_nodes` 创建节点、`_add_layerN_nodes` 添加节点、`_connect_layers` 连接边
5. `reporting.py`：`AGENT_DISPLAY` 添加显示名称
6. `default_config.py`：如需要，在 `layer_model_overrides` 中配置模型

### 沙盒工具的关键约束

- **网络模块**（socket, requests, urllib 等）被 import hook 阻断
- **子进程和线程**（subprocess, threading）放行（matplotlib 内部需要）
- **每次 run_code 是独立进程**，变量不跨调用保留。跨调用数据通过 write_file → read_file 传递
- **中文字体**：沙盒自动检测 SimHei/Microsoft YaHei，无字体时 Agent 应改用英文标签

### 测试

```bash
# 单元测试（无需 API key）
pytest tests/test_layer3_layer4.py tests/test_font_detection.py -v

# API 连通性测试（需要 API key）
python tests/test_api_connectivity.py
```

## 支持模型

| 模型 | 适用角色 | 特点 |
|---|---|---|
| `deepseek-v4-pro` | Manager、建模师、CodingAgent | 深度推理，复杂逻辑，工具调用 |
| `deepseek-v4-flash` | 数据分析、敏感性分析 | 快速响应，高性价比 |
| `qwen3.7-max` | PaperAgent 论文正文撰写 | 中文写作质量高 |

### 已知不可用模型

以下模型在长中文数学建模 prompt 下会返回空内容，已被框架排除：

- `kimi-k2.7-code` · `glm-5.2` · `glm-5.1`

## 配置

所有配置通过 `.env` 文件管理，完整列表见 `.env.example`。核心配置项：

| 变量 | 默认值 | 说明 |
|---|---|---|
| `MATHMODELING_LLM_PROVIDER` | `opencode` | LLM 通道：`opencode` 或 `deepseek` |
| `OPENCODE_GO_API_KEY` | (必填) | OpenCode Go API 密钥（主通道） |
| `DEEPSEEK_API_KEY` | (必填) | DeepSeek 官方 API 密钥（降级通道） |
| `MATHMODELING_MAX_DEBATE_ROUNDS` | `10` | 建模辩论最大轮数 |
| `MATHMODELING_MAX_IMPL_RETRIES` | `3` | 代码实现最大重试次数 |
| `MATHMODELING_DEFAULT_MAX_TOKENS` | `16384` | 单次 LLM 调用最大输出 |
| `MATHMODELING_SELECTED_LAYERS` | `1,2,3,4` | 要执行的层（调试时可用 `3,4` 跳层） |

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
    │   ├── __init__.py              # Agent 工厂函数（含 Tool Calling 循环）
    │   └── utils/
    │       ├── prompt_templates.py  # 全部 System Prompt（静态，可缓存）
    │       └── agent_states.py      # AgentState 类型定义
    │
    ├── llm_clients/
    │   └── __init__.py              # LLM 客户端 + 统一降级链
    │
    ├── tools/
    │   └── __init__.py              # 沙盒代码执行 + LangChain Tool 封装
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
- **Tool Calling**：`langchain-core` — AIMessage / ToolMessage 工具调用协议
- **模型**：DeepSeek V4 Pro / Flash、Qwen3.7-Max（通过 OpenCode Go 或官方 API）
- **计算沙盒**：`numpy` · `scipy` · `sympy` · `pandas` · `matplotlib` · `seaborn` · `scikit-learn` · `statsmodels`

## License

MIT
