# MathModelingAgents

[![Python](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](https://www.python.org/)
[![LangGraph](https://img.shields.io/badge/LangGraph-0.4%2B-green.svg)](https://github.com/langchain-ai/langgraph)
[![License](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

> 多智能体协作的数学建模竞赛全流程框架。输入题目，自动产出完整论文。

**5 层架构 · 17 个专职 Agent · 辩论循环 · Agentic Tool Calling · Web 搜索 · Prompt 缓存优化 · 增量写盘容灾**

```
题目文件 ──→ [L1: 问题分析] ──→ [L2: 数学建模] ──→ [L3: 代码实现] ──→ [L4: 论文写作] ──→ 完整论文
              顺序流水线          辩论循环             Agentic 循环         Agentic 循环
                                                        (写→跑→修)          (写→查→改)
                                    (可选) [L5: 敏感性分析]
```

另有一种**恢复模式**：从一份已完成的 Layer 1 数据出发，只跑 L2→L3（L1 判需敏感性时再插 L5），
产出**模型解释文档**（面向团队的技术说明）而非竞赛论文：

```
已有 Layer1 数据 ──→ [L2: 数学建模] ──→ [L3: 代码实现] ──→ [模型解释文档]
                        辩论循环           Agentic 循环          (技术说明, 非论文)
                                             │ (L1 判需敏感性时)
                                             ▼
                                        [L5: 敏感性分析]
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

### 交互式模型选择

`python main.py 题目.md` 交互模式下，配置组装后会先弹出两个模型选择菜单（菜单项严格来自 `cli/model_catalog.py` 的官方静态清单；清单中的逻辑别名如 `glm-5.3`、`kimi-k2.7-code`、`minimax-m3` 虽不在 `/models` 部署列表，chat 端点可直接使用）：

```
Select Your [Quick-Thinking] LLM Engine (volcengine-plan):   # 快速思考模型
Select Your [Deep-Thinking] LLM Engine (volcengine-plan):    # 深度思考模型
```

选中的 quick/deep 模型作为**最高优先级**完全覆盖 provider 级角色硬编码（如 volcengine-plan 的 L3 coder / L4 writer），写回 config 后再启动建模流程。

| 环境变量 | 作用 |
|---------|------|
| `MATHMODELING_QUICK_THINK_LLM` | 快速思考模型的默认值（仅作默认模型，**不**关闭交互） |
| `MATHMODELING_DEEP_THINK_LLM` | 深度思考模型的默认值（仅作默认模型，**不**关闭交互） |
| `MATHMODELING_SKIP_MODEL_PROMPT` | 显式设为 `1`/`true`/`yes`/`on` 时**跳过全部交互菜单**（适合 CI / 脚本 / 无 TTY 环境） |

> 设置 `MATHMODELING_SKIP_MODEL_PROMPT=1` 可完全跳过交互，直接使用默认模型。未显式设置该变量时，交互菜单照常弹出——即使你已在 `.env` 里用了 `QUICK/DEEP_THINK_LLM` 指定默认模型。provider 未知或交互异常时自动走默认模型，绝不阻塞。

## 架构

框架分为 5 层，层与层之间只传递精华摘要而非原始输出。

```
Layer 1: 问题分析（4 Agent，顺序协作）
  Decomposer → DataAnalyst → ConstraintAnalyst → ProblemManager
  产出：理解题目边界、挖掘数据特征、明确约束假设

Layer 2: 数学建模（4 Agent，辩论循环）
  ModelerA → ModelerB → ModelerC → ModelingManager ──→ 继续辩论 / 通过
  产出：数学模型定义、公式推导、求解方案
  （建模师绑定 model_search + web_search + submit_plan：检索选型后以 submit_plan 交卷，本层不做数值验证）

Layer 3: 代码实现（3 Agent，Agentic Tool Calling）
  SolverAgent（有工具，内部循环：写→跑→修→再跑）→ ImplManager → VizAgent
  产出：可运行代码、results.json、论文级图表

Layer 4: 论文写作（2 Agent，Agentic Tool Calling）
  PaperAgent（有工具，分节迭代：写→读数据核实→修改）→ PaperManager ──→ 修改 / 通过
  产出：完整的中文学术论文（Markdown）

Layer 5: 敏感性分析（3 Agent，可选）
  ParamPerturber → RobustnessAnalyst → SensitivityManager
  产出：参数稳定性评估

模型解释层（explanation，1 Agent，恢复模式 --from-layer1 专用）
  Explainer：面向团队的技术说明文档（建模思路/公式/求解/结果/敏感性结论）
  产出：model_explanation.md（非竞赛论文格式）
```

### Layer 3 & 4 的核心创新：Agentic Tool Calling

Layer 3 的 SolverAgent 和 Layer 4 的 PaperAgent 不再是单次 LLM 调用，而是
**有真实工具的自主循环 Agent**：

```
┌─ SolverAgent（30 轮 max）─────────────┐
│  工具: run_code / read_file /          │
│        write_file / list_dir /         │
│        web_search                      │
│                                        │
│  写代码 → 执行 → 看报错 → 修复          │
│  → 再执行 → 结果正确 → 生成图表         │
│  → 自检 → SELF_CHECK_PASSED            │
└────────────────────────────────────────┘

┌─ PaperAgent（30 轮 max）───────────────┐
│  工具: read_file / list_dir /          │
│        write_file / check_url          │
│  （草稿统一写入 drafts/，根目录仅保留  │
│    最终稿 final_paper.md）             │
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
崩溃后可用 `--from-layer1 <dir>` 从既有 Layer 1 输出的恢复模式继续。

### 中文图表渲染保护

沙盒执行环境自动检测中文字体（SimHei / Microsoft YaHei / STSong 等），
若系统无中文字体则输出警告。VizAgent 的 prompt 要求根据字体可用性
决定使用中文或英文标签，避免出现方块乱码。

### Web 搜索（题目背景知识）

Layer 1 的 Decomposer 自动进行**多角度联网搜索**（题目背景 + 建模方法），背景资料注入问题拆解与 Layer 2 建模上下文；
Layer 3 的 SolverAgent 可自主调用 `web_search` 查询数据字段含义、算法资料。Layer 4 的 PaperAgent 写参考文献前会用 `check_url` 验证每条 URL 的真实可达性，失效引用自动剔除。

- **后端**：Tavily（免费 1000 次/月，推荐）或 ddgs（DuckDuckGo，免费无 key）
- **配置**：`MATHMODELING_WEB_SEARCH_PROVIDER=auto|tavily|ddgs|off`（默认 `auto`：有 key 用 Tavily，否则 ddgs）
- **容错**：10 秒超时、异常吞噬、tavily 失败自动降级 ddgs；搜索失败静默跳过，不影响主流程

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

- **L1/L5**：传统 LLM 节点链（System Prompt → 单次调用 → 输出）
- **L2 建模师**：Agentic 循环，绑定 `model_search`（模型知识库 RAG）+ `web_search` + `submit_plan` 三个工具；以 `submit_plan` 交卷为一次发言的终止条件（`max_iterations=15` 仅作检索类调用的保底上限）。本层不做数值验证——方案中的数值结论一律标注「待 Layer 3 数值验证」
- **L3 SolverAgent**：有 `run_code` / `read_file` / `write_file` / `list_dir` / `web_search` 工具的 Agentic 循环（写→跑→修，最多 30 轮），VizAgent 负责图表生成（最多 10 轮）
- **L4 PaperAgent**：有 `read_file` / `list_dir` / `write_file` / `check_url` 工具的 Agentic 循环（逐节写→核实→改，最多 30 轮）
- **总 Agent 数**：17（L1: 4, L2: 4, L3: 3, L4: 2, L5: 3, 模型解释: 1）

### 关键文件地图

| 文件 | 作用 | 什么时候看 |
|------|------|-----------|
| `mathmodelingagents/graph/setup.py` | 图拓扑定义（节点、边、路由） | 修改 Agent 编排流程 |
| `mathmodelingagents/graph/conditional_logic.py` | 条件路由（辩论继续/结束、重试/通过、恢复模式路由） | 修改裁决逻辑 |
| `mathmodelingagents/graph/recovery.py` | Layer 1 状态恢复（--from-layer1） | 修改恢复模式 |
| `mathmodelingagents/agents/__init__.py` | Agent 工厂函数（含 Tool Calling 循环） | 修改 Agent 行为、添加工具 |
| `mathmodelingagents/agents/utils/prompt_templates.py` | 全部 System Prompt（纯静态，可缓存） | 修改 Agent 指令 |
| `mathmodelingagents/tools/__init__.py` | 沙盒执行 + LangChain Tool 封装 | 修改/添加工具 |
| `mathmodelingagents/tools/web_search.py` | Web 搜索（Tavily / ddgs） | 修改搜索后端 |
| `mathmodelingagents/llm_clients/__init__.py` | LLM 客户端 + 降级链 | 修改 API 调用逻辑 |
| `mathmodelingagents/default_config.py` | 全局配置 + 模型路由 | 修改默认值、模型分配 |
| `.env.example` | 环境变量说明 | 了解/修改用户配置项 |
| `main.py` | CLI 入口 | 了解启动流程 |

### 常用操作

```bash
# 运行完整流程
python main.py problem_2024a.md

# 从已完成的 Layer 1 输出恢复，只跑 L2→L3(+L5)，产出模型解释文档（调试/迭代推荐）
python main.py problem_2024a.md --from-layer1 <已有Layer1目录>

# 只用 DeepSeek 官方 API
python main.py problem_2024a.md --provider deepseek

# 用火山方舟 Agent Plan（订阅套餐，L3 coder→kimi-k2.7-code，L4 writer→minimax-m3）
python main.py problem_2024a.md --provider volcengine-plan

# 用火山方舟 Coding Plan（订阅套餐，qwen3.7-max 自动映射为 deepseek-v4-pro）
python main.py problem_2024a.md --provider volcengine

# 启用/控制敏感性分析（裸 -s = always 强制启用；auto 交给 Layer 1 决策；never 强制跳过）
python main.py problem_2024a.md -s
python main.py problem_2024a.md -s never
```

### 修改 Prompt 的正确方式

1. 找到 `prompt_templates.py` 中对应的 `get_XXX_prompt()` 函数
2. 所有 prompt 函数是**无参数纯静态字符串**（为了 API 缓存）。不要在 prompt 中注入变量，动态值通过 `_build_context()` 在 user message 中提供
3. 修改后用 `--max-rounds 1 --provider deepseek` 小规模跑一轮冒烟验证（单元测试套件已移除，见下文「验证」）

### 添加新 Agent 的步骤

1. `prompt_templates.py`：添加 `get_new_agent_prompt()` 函数
2. `_PROMPT_REGISTRY`：注册新 prompt
3. `agents/__init__.py`：添加 `create_new_agent()` 工厂函数
4. `graph/setup.py`：`_create_agent_nodes` 创建节点、`_add_layerN_nodes` 添加节点、`_connect_layers` 连接边
5. `reporting.py`：`AGENT_DISPLAY` 添加显示名称
6. `default_config.py`：如需要，在 `layer_model_overrides` 中配置模型

### 沙盒工具的关键约束

- **网络模块**（socket, requests, urllib 等）被 import hook 阻断；`urllib.parse` 作为安全子模块精确豁免（纯解析无网络，matplotlib 字体配置内部依赖）
- **子进程和线程**（subprocess, threading）放行（matplotlib 内部需要）
- **每次 run_code 是独立进程**，变量不跨调用保留。跨调用数据通过 write_file → read_file 传递
- **单次 run_code 硬上限 300s**（`MAX_RUN_CODE_TIMEOUT`），Agent 传入更大的 timeout 也会被钳制；复杂求解需拆分为多个小执行单元
- **L3 工具路径作用域**：`create_coding_agent_tools` 的 read_file/write_file/list_dir 相对路径统一 scope 到输出目录（`output_dir`），与 run_code 的 cwd=`output_dir/code` 对齐，规避「写 A 处/跑 B 处/读 C 处」的路径迷路
- **L4 草稿下沉**：PaperAgent 的 write_file 一律扁平化写入 `output_dir/drafts/`（只取 basename + 防 `..` 逃逸），最终论文定稿为根目录 `final_paper.md`
- **matplotlib/socket 冲突**：构建沙盒 preamble 时注入 `_BlockedSocketStub` 到 `sys.modules`，让 matplotlib 加载链的顶层 `import socket` 成功、真实网络能力在属性访问时抛 `RuntimeError`——解决图表脚本验证阶段必被 socket 阻断失败的问题
- **中文字体**：沙盒自动检测 SimHei/Microsoft YaHei，无字体时 Agent 应改用英文标签

### 验证

单元测试套件（`tests/`）已于 2026-09 移除，当前验证方式：

```bash
# 小规模冒烟：一轮辩论完整流程（最快路径，需 API key）
python main.py problem_2024a.md --max-rounds 1 --provider deepseek

# 从已有 Layer 1 恢复，只跑后续层（调试某层推荐）
python main.py problem_2024a.md --from-layer1 <已有输出目录> --max-rounds 1

# RAG 检索冒烟（无需 API key）
.venv/Scripts/python.exe -c "from mathmodelingagents.knowledge import search_models; print(search_models('小样本 指数增长 预测', top_k=1)[0]['name'])"
```

## 支持模型

### opencode / deepseek 通道（默认）

| 模型 | 适用角色 | 特点 |
|---|---|---|
| `deepseek-v4-pro` | Manager、建模师、SolverAgent | 深度推理，复杂逻辑，工具调用 |
| `deepseek-v4-flash` | 数据分析、敏感性分析 | 快速响应，高性价比 |
| `qwen3.7-max` | PaperAgent 论文正文撰写（仅 opencode 通道） | 中文写作质量高 |

### 火山方舟 Agent Plan 通道（`--provider volcengine-plan`）

Provider 级角色覆盖（`provider_layer_model_overrides`），优先于全局分配：

| 角色 | 模型 | 说明 |
|---|---|---|
| L3 coder | `kimi-k2.7-code` | 火山主打编程模型 |
| L4 writer | `minimax-m3` | 1M 上下文，长文写作 |
| 其余角色 | `deepseek-v4-pro` / `deepseek-v4-flash` | 沿用默认矩阵 |

Agent Plan 模型池（11 个）：`ark-code-latest`、`doubao-seed-2.1-turbo`、`doubao-seed-evolving`、`glm-5.3/latest`、`deepseek-v4-pro/flash`、`doubao-seed-2.0-lite/mini`、`minimax-m3`、`kimi-k2.7-code`、`kimi-k3`。

### 火山方舟 Coding Plan 通道（`--provider volcengine`）

- Base URL 为 `https://ark.cn-beijing.volces.com/api/coding/v3`（与 Agent Plan 的 `/api/plan/v3` 不同端点）。
- 火山两个套餐均不支持 `qwen3.7-max`，自动映射（Coding Plan → `deepseek-v4-pro`，Agent Plan → `minimax-m3`）。

### 已知不可用 / 风险提示

- `glm-5.2` · `glm-5.1` 在 opencode 通道的长中文数学建模 prompt 下会返回空内容，已被排除。
- `kimi-k2.7-code` 在 **OpenCode Go 后端**曾因长 prompt 返空被移除，且只接受 `temperature=1`；本次在 **volcengine-plan 通道**重新启用为 L3 coder（火山原生端点行为不同），正式跑题前请先做小 prompt 实测输出质量（探针脚本 `scripts/probe_model_quality.py` 已随测试清理移除，可直接用任一题目 md + `--max-rounds 1` 冒烟）。

## 模型知识库 RAG

Layer 2 辩论前会按题目特点从内置模型知识库（`mathmodelingagents/knowledge/model_library.json`，52 条）中检索候选模型池（Top-5），注入三位建模师第一轮作为参考起点（可超越池子）；建模师还可用 `model_search` 工具按需补充检索（ADR-0003）。

**Embedding 模型**：`BAAI/bge-small-zh-v1.5`（512 维，fastembed 本地 ONNX 推理，不引入 torch）。

模型文件（约 91MB：`model_optimized.onnx` + tokenizer/config）是二进制，已在 `.gitignore` 中忽略、**不随 git 提交**；而预计算的向量库 `knowledge/model_library_vectors.npz`（52×512）随仓库提交。因此 clone 到新环境后需自行补下载模型：

**第一步 · 判断是否需要下载**

检查模型目录是否存在：

```bash
ls mathmodelingagents/knowledge/models/
```

- 已有 `models--Qdrant--bge-small-zh-v1.5/`（含 snapshot）→ 已就绪，跳过下载，直接可用。
- 目录为空或不存在 → 执行下一步。

**第二步 · 下载模型**（国内网络实测：直连 HuggingFace 会 ConnectTimeout，不禁 xet 会 CAS 401，两个环境变量缺一不可）

```bash
export HF_ENDPOINT=https://hf-mirror.com
export HF_HUB_DISABLE_XET=1
.venv/Scripts/python.exe scripts/build_model_library.py --download-model
```

**第三步 · 验证下载成功**

```bash
.venv/Scripts/python.exe -c "from mathmodelingagents.knowledge import search_models; print(search_models('小样本 指数增长 预测', top_k=1)[0]['name'])"
```

首条应接近「灰色预测 GM(1,1)」，表示真实 embedding 检索已可用。

模型已下载后，`search_models` 走纯离线推理（`local_files_only=True`），运行期无需联网。

**建库脚本**：`scripts/build_model_library.py` 对 52 条模型条目（name + traits + usage）预计算向量并生成 `knowledge/model_library_vectors.npz`。向量库已随 git 提交，仅在**更换 embedding 模型**或**改动 `model_library.json` 条目**时才需重建：

```bash
.venv/Scripts/python.exe scripts/build_model_library.py            # 模型已存在时，仅重建向量库
```

**一致性铁律**：更换 embedding 模型后**必须重跑建库脚本重建向量库**，否则检索会因 names/维度不一致而退化为现场计算（模型也不可用时进一步 fail-open 为全量谱系注入）。

fail-open 行为：模型未下载 / 向量化失败 / 结构化提炼失败时，候选池自动降级为全量模型谱系注入，不阻塞主流程。

## 配置

所有配置通过 `.env` 文件管理，完整列表见 `.env.example`。核心配置项：

| 变量 | 默认值 | 说明 |
|---|---|---|
| `MATHMODELING_LLM_PROVIDER` | `opencode` | LLM 通道：`opencode` / `deepseek` / `volcengine` / `volcengine-plan` |
| `OPENCODE_GO_API_KEY` | (必填) | OpenCode Go API 密钥（主通道） |
| `DEEPSEEK_API_KEY` | (必填) | DeepSeek 官方 API 密钥（降级通道） |
| `VOLCENGINE_PLAN_API_KEY` | (选填) | 火山方舟 Agent Plan 专属密钥（订阅后从控制台换取） |
| `MATHMODELING_MAX_DEBATE_ROUNDS` | `10` | 建模辩论最大轮数 |
| `MATHMODELING_MAX_IMPL_RETRIES` | `3` | 代码实现最大重试次数 |
| `MATHMODELING_DEFAULT_MAX_TOKENS` | `16384` | 单次 LLM 调用最大输出 |
| `MATHMODELING_SELECTED_LAYERS` | `1,2,3,4` | 要执行的层（调试时可用 `3,4` 跳层） |
| `MATHMODELING_WEB_SEARCH_PROVIDER` | `auto` | Web 搜索后端：`auto`/`tavily`/`ddgs`/`off` |
| `TAVILY_API_KEY` | (可选) | Tavily 搜索 key（不填则自动用 ddgs 免费后端） |

## 命令行

```bash
python main.py <题目文件> [选项]

参数:
  problem_path          题目 Markdown 文件路径

选项:
  --output, -o NAME     输出文件夹名（默认自动生成）
  --sensitivity, -s [auto|always|never]
                        敏感性模式（默认 auto，由 Layer 1 分析题目后决策；
                        裸 -s 等价 always 强制启用，never 强制跳过）
  --max-rounds, -r N    每层最大辩论轮次（默认 10）
  --provider, -p        指定 LLM provider（opencode / deepseek / volcengine / volcengine-plan）
  --from-layer1 DIR     从已完成的 Layer 1 输出目录恢复，只跑 L2→L3(+L5) 产出模型解释文档

  示例:
    python main.py problem_2024a.md
    python main.py problem_2024a.md -s -o my_solution
    python main.py problem_2024a.md -s never          # 强制跳过敏感性分析
    python main.py problem_2024a.md --from-layer1 results/problem_2024a   # 恢复模式，产出模型解释文档
```

## 项目结构

```
MathModelingAgents/
├── main.py                          # 入口 + 代码验证
├── cli/
│   ├── model_catalog.py             # 各 provider 官方静态模型清单（交互菜单数据源）
│   └── model_picker.py              # 交互式 Quick/Deep 模型选择（questionary）
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
    ├── knowledge/                   # 模型知识库 RAG（Layer 2 候选模型池）
    │   ├── model_library.json       # 52 条数学模型条目
    │   ├── model_library_vectors.npz# 预计算向量库（随 git 提交）
    │   └── retrieval.py             # 语义检索（bge-small-zh-v1.5, 本地 ONNX）
    │
    ├── llm_clients/
    │   └── __init__.py              # LLM 客户端 + 统一降级链
    │
    ├── tools/
    │   ├── __init__.py              # 沙盒代码执行 + LangChain Tool 封装
    │   └── web_search.py            # Web 搜索（Tavily / ddgs）+ check_url
    │
    ├── graph/
    │   ├── setup.py                 # LangGraph StateGraph 构建
    │   ├── modeling_graph.py        # 主入口类 MathModelingGraph
    │   ├── conditional_logic.py     # 辩论/重试/循环路由
    │   ├── propagation.py           # 初始状态 + 图执行参数
    │   ├── checkpointer.py          # 检查点（可选）
    │   └── recovery.py              # Layer 1 状态恢复（--from-layer1）
    │
    └── reporting.py                 # 增量写盘 + 最终报告汇总
```

## 技术栈

- **编排引擎**：[LangGraph](https://github.com/langchain-ai/langgraph) — 构建有状态的多 Agent 工作流图
- **LLM 接口**：`langchain-openai` (ChatOpenAI) — 兼容 OpenAI API 协议
- **Tool Calling**：`langchain-core` — AIMessage / ToolMessage 工具调用协议
- **模型**：DeepSeek V4 Pro / Flash、Qwen3.7-Max、Kimi K2.7-Code、MiniMax M3（通过 OpenCode Go / 火山方舟 Agent & Coding Plan / DeepSeek 官方 API）
- **计算沙盒**：`numpy` · `scipy` · `sympy` · `pandas` · `matplotlib` · `seaborn` · `scikit-learn` · `statsmodels`
- **Web 搜索**：Tavily（推荐） / ddgs（DuckDuckGo 免费后端）

## License

MIT
