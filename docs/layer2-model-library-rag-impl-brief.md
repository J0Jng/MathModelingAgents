# Implementation Brief: Layer 2 模型知识库 RAG 检索

## Background

Layer 2 的三位建模师（A/B/C）目前完全开放式选题，模型全靠 LLM 自身知识，缺少结构化起点，容易跑偏。本任务为建模层引入「模型知识库 + embedding 向量检索」：在 Layer 1 结束后、Layer 2 辩论前，按题目特点检索出候选模型池（Top-K=5），注入三位建模师第一轮供其选择（可超出池子，风格自然分化）；同时给建模师新增 `model_search` 工具按需补充检索。

设计决策已锁定，见 `docs/adr/0003-model-library-rag.md`。本 brief 是它的可执行落地。

## 已锁定的设计决策（不可更改）

1. **知识库数据**：`mathmodelingagents/knowledge/model_library.json` 已存在（52 条，字段 `name/category/traits/usage`，traits 纯中文，无倾向性标注）。**不要改它的内容**，只读。
2. **embedding**：`BAAI/bge-small-zh-v1.5`，512 维，经 `fastembed` 本地推理。**不引入 torch**。
3. **候选池注入**：主通道——ProblemManager CONCLUDE 后追加一次结构化 LLM 调用提炼「题目特点需求」，编码为 query 向量 → 检索 Top-K=5 → 存 `state["model_candidates"]`，`_build_context` 在 modeling 层**第一轮**注入 A/B/C（后续轮次不注入）。
4. **工具通道**：新增 `model_search` 工具，modeler 节点改成 tool-calling 并绑定 `model_search + web_search + run_code`（复用现有 tool-loop 骨架 `_run_tool_loop`）。
5. **fail-open**：检索/提炼/向量化任一步失败 → 全量谱系降级注入，不阻塞主流程。

## 关键技术事实（Hermes 已实测，直接采用，勿重试踩坑）

- **fastembed 版本** 0.8.0，已装入项目 `.venv`（Python 3.12）。`onnxruntime` 1.29.0 已随装。
- **模型在 fastembed 内部解析为 `Qdrant/bge-small-zh-v1.5`**（不是 BAAI），但调用时仍传 `model_name="BAAI/bge-small-zh-v1.5"` 即可。
- **离线加载正确写法**（已验证可用）：
  ```python
  from fastembed import TextEmbedding
  emb = TextEmbedding(
      model_name="BAAI/bge-small-zh-v1.5",
      cache_dir=<指向已下载模型的缓存目录>,
      local_files_only=True,
  )
  ```
  不要用 `model_name=<snapshot目录路径>`——fastembed 会报 "not supported"。
- **下载需要镜像 + 禁 xet**（国内网络实测）：`HF_ENDPOINT=https://hf-mirror.com` + `HF_HUB_DISABLE_XET=1`。直连 HuggingFace 会 ConnectTimeout；不设 `HF_HUB_DISABLE_XET=1` 会 CAS 401。
- **模型文件大小**：`model_optimized.onnx` = 94.8MB，加 tokenizer/config 共约 95MB，超 GitHub 50MB 警告线但低于 100MB 硬限制。
- **默认 cache 位置**：fastembed 默认把模型下到 `%TEMP%/fastembed_cache/`（Windows），不是 `~/.cache/fastembed`。加载时用 `cache_dir` 显式指定。

## Change List

### 1. `mathmodelingagents/knowledge/__init__.py`（新建）
- 导出模块级 API：`load_model_library()`（读 JSON 返回 list[dict]）、`build_query(...)`、`search_models(...)`。
- 本文件是检索模块的公开入口。

### 2. `mathmodelingagents/knowledge/retrieval.py`（新建）
核心检索模块。包含：
- `load_model_library() -> list[dict]`：读 `model_library.json`（相对本文件路径 `Path(__file__).parent / "model_library.json"`），返回 `data["models"]`。
- `_get_embedder()`：单例加载 fastembed `TextEmbedding`。`model_name="BAAI/bge-small-zh-v1.5"`，`cache_dir` 指向 repo 内模型目录（见产物清单 `mathmodelingagents/knowledge/models/`），`local_files_only=True`。首次加载失败（模型目录不存在/未安装 fastembed）时 raise 或返回 None，由调用方 fail-open。
- `build_query(problem_traits: list[str], description: str = "") -> str`：把特点标签 + 描述拼成检索 query 文本（纯中文）。
- `search_models(query: str, top_k: int = 5) -> list[dict]`：编码 query → 与预计算向量算余弦相似度 → 返回 top_k 条模型条目（含 `name/category/traits/usage`）。向量缓存缺失时**现场计算**（fallback，不依赖 .npz）。
- `_encode(texts: list[str]) -> numpy.ndarray`：内部辅助，L2 归一化后的向量。
- 所有异常都要能被上层捕获以触发 fail-open；**不在本模块内 print 大段日志**，用 `logging`。

### 3. `mathmodelingagents/knowledge/model_library_vectors.npz`（数据文件，脚本生成）
- 52 条模型的预计算向量（512 维 × 52），由建库脚本生成。
- **不手写**，见 Change List #7。

### 4. `mathmodelingagents/knowledge/models/`（模型目录，Git LFS / 镜像）
- 存放 bge-small-zh-v1.5 的 ONNX + tokenizer 文件。
- **实现阶段**：由建库脚本或手动命令下载（用镜像环境变量），不是 Claude 手写二进制。

### 5. `scripts/build_model_library.py`（新建）
离线建库脚本，职责：
- 加载 `model_library.json`，对每条模型构造检索文本（`name + " " + " ".join(traits) + " " + usage`）。
- 用 fastembed 编码全部条目 → 存 `model_library_vectors.npz`（keys: `vectors` 矩阵 + `names` 数组）。
- 支持 `--download-model` 参数：设置镜像环境变量下载模型到 `models/` 目录。
- 脚本顶部 docstring 写清：镜像下载命令、`HF_HUB_DISABLE_XET=1` 原因。

### 6. `mathmodelingagents/agents/utils/agent_states.py`
- `AgentState` 新增字段 `model_candidates: str`（候选模型池文本，供 Layer 2 注入）。

### 7. `mathmodelingagents/agents/__init__.py`
- **ProblemManager 节点**（`create_problem_manager` → `_make_manager_node` 的 layer=="problem" 分支）：在 CONCLUDE 后（紧跟现有的 `_run_sensitivity_decision` 调用之后）追加一次结构化提炼调用 `_run_model_candidate_search(config, result)`，产出 `model_candidates` 文本写入 updates。
  - 实现 `_run_model_candidate_search`：用 `create_layer_llm(config, "problem", "manager", max_tokens=1024)` + `with_structured_output`，schema 含 `problem_traits: list[str]`（3-8 个纯中文特点标签）和 `description: str`（一两句题目特点描述）。拿到 traits 后调 `retrieval.build_query` + `search_models(top_k=5)`，把结果格式化成 markdown 文本。
  - fail-open：任何异常 → 返回全量谱系文本（`load_model_library()` 全部条目格式化）。
- **`_build_context`**（modeling 分支）：新增注入逻辑——当 `state.get("model_candidates")` 非空**且当前是第一轮**时，追加 `## 候选模型池（参考起点，可超越）\n\n{model_candidates}` 到 parts。第一轮判断：`debate.get("round_count", 0) <= 1`（注意 modeler 节点的 round_count 语义，见下）。
- **`_make_modeler_node`**：从纯文本调用改为 tool-calling。改用 `_run_tool_loop` 骨架（参考 `create_solver_agent` 的写法），绑定 `tools = [model_search_tool, web_search_tool, run_code_tool]`。`max_iterations` 建议 10（建模师不需要 Solver 的 30 轮）。输出提取仍用 `_extract_final_output`。
  - 注意：modeler 的 `run_code` 用 `create_langchain_tools()` 里的 `run_code_tool`（临时目录版）即可，不需要 `create_coding_agent_tools` 的持久 cwd 版。
- **`model_search` 工具**：在 `tools/__init__.py` 新增 `model_search_tool(query: str) -> str`，内部调 `retrieval.search_models(query, top_k=5)`，格式化返回。加入 `create_langchain_tools()` 的返回列表，同时也要能被 modeler 节点单独引用。

### 8. `mathmodelingagents/tools/__init__.py`
- 新增 `model_search_tool`（LangChain @tool），见上。
- 在 `create_langchain_tools()` 返回列表加入 `model_search_tool`。

### 9. `pyproject.toml`
- `[project].dependencies` 加入 `"fastembed>=0.8.0"`（onnxruntime 是其传递依赖，不必显式写，但可加 `onnxruntime>=1.17` 保险）。
- **不要**加 torch。

### 10. `README.md`
- 新增「模型知识库 RAG」小节，写清：embedding 模型来源（Git LFS 提交 / `HF_ENDPOINT=https://hf-mirror.com` + `HF_HUB_DISABLE_XET=1` 镜像下载两条路径）、`scripts/build_model_library.py` 用法、一致性铁律（换模型必须重建向量库）。

## 产物清单（对应 reference 设计）

| 产物 | 路径 | 说明 |
|---|---|---|
| 知识库数据 | `mathmodelingagents/knowledge/model_library.json` | 已存在，勿改 |
| 检索模块 | `mathmodelingagents/knowledge/retrieval.py` | 新建 |
| 公开入口 | `mathmodelingagents/knowledge/__init__.py` | 新建 |
| 向量缓存 | `mathmodelingagents/knowledge/model_library_vectors.npz` | 脚本生成 |
| 模型目录 | `mathmodelingagents/knowledge/models/` | 下载/提交 |
| 建库脚本 | `scripts/build_model_library.py` | 新建 |

## Acceptance Criteria（必须逐条跑通）

1. **数据完整性**：`python -c "from mathmodelingagents.knowledge import load_model_library; assert len(load_model_library()) == 52"` 通过。
2. **检索冒烟**（用 mock 或已下载模型）：`search_models("小样本 指数增长 预测", top_k=5)` 返回 5 条，每条含 `name/traits/usage`，且首条语义上应接近「灰色预测 GM(1,1)」或时序模型。若模型未下载，则 `search_models` 应走 fail-open 返回全量（不 crash）。
3. **向量库生成**：`python scripts/build_model_library.py` 能生成 `model_library_vectors.npz`（模型已下载时）。
4. **导入冒烟**：`python -c "import mathmodelingagents.knowledge, mathmodelingagents.agents, mathmodelingagents.tools"` 成功。
5. **现有测试不回归**：`python -m pytest tests/ -q` 全绿（先 `export PYTHONPATH=`，用 `.venv/Scripts/python.exe`）。
6. **state 字段**：`AgentState` 含 `model_candidates`，`_build_context` 在 modeling 第一轮注入候选池、第二轮不注入（可写单测或用脚本验证）。

## Explicitly Do NOT Do

- **不要改 `model_library.json` 的内容**（52 条、traits 纯中文、无倾向性标注，已定稿）。
- **不要引入 torch / sentence-transformers / chroma / faiss** 等重依赖——只用 fastembed + numpy。
- **不要改 Layer 2 的辩论拓扑**（modeler_a→b→c→manager 的边和路由不变）。
- **不要改 modeler 的 system prompt 里的风格定位**（创新/实用/简洁三定位必须保留）。候选池注入是「参考起点」，prompt 里若需提示，用「可超越池子」的口径。
- **不要把候选池注入到 system prompt**——必须走用户消息（`_build_context` 返回的 context 里），保持 system prompt 静态可缓存。
- **不要动 Layer 1/3/4/5 其他节点**，除非是 `_make_manager_node` 的 problem 分支里与敏感性决策同处一个函数体、需要同一处加调用的自然改动。
- **不要手写 .npz 二进制**，由建库脚本生成。
- **不要删除或重构** `_run_tool_loop`、`_sanitize_tool_pairing`、`_extract_final_output` 等共享函数——复用即可。
- **不要改 config 的层模型分配 / max_tokens / timeout**。

## 交付要求

完成全部改动后：
1. `git diff --stat` 列出改动文件，确认只动了 brief 清单里的文件。
2. 逐条跑 Acceptance Criteria，报告每条的实际输出（不要只说"通过"，给出命令 + 关键输出）。
3. 若某条无法通过（如模型文件未下载导致无法真实检索），如实报告失败原因和已实现的 fail-open 行为，不要伪造通过。
