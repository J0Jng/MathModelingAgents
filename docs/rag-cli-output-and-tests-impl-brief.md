# Implementation Brief: RAG 候选池 CLI 输出 + 专项单测

## Background

ADR-0003 引入的模型知识库 RAG 已实现核心链路（`_run_model_candidate_search` → `search_models` → `format_model_entries`），但存在两个缺口：

1. **CLI 不可见**：ProblemManager CONCLUDE 后，调用点只 `print(f"[problem] 候选模型池已生成 ({len} chars)")`，用户看不到「RAG 是否真的命中、检索出了哪些候选模型」，只能看字符长度。
2. **零专项单测**：`tests/` 里没有任何针对 `knowledge` 模块的测试；`test_sensitivity_decision.py` 只断言 LLM 调用次数（`fake.calls == 2`），不验证 `model_candidates` 字段真实生成、不区分「真实命中 vs fail-open 降级」。

本任务：让 CLI 明确输出 RAG 检索结果（含来源标识 + query + 完整候选池），并补齐 `knowledge` 模块的专项单元测试（不依赖 91MB embedding 模型文件，用 mock）。

## Design

### 核心决策：`_run_model_candidate_search` 返回结构化结果

当前函数返回 `str`，失败时静默降级，**调用方无法区分「真实命中」和「fail-open 降级」**。改为返回 `NamedTuple`，携带来源标识，CLI 据此分叉输出。

```python
# mathmodelingagents/agents/__init__.py，模块级（紧跟 imports 之后或函数前）
from typing import NamedTuple

class CandidatePoolResult(NamedTuple):
    source: str   # "rag" | "full_library" | "empty"
    text: str     # markdown 候选池文本（写入 state["model_candidates"]）
    query: str    # 检索 query 文本（CLI 显示依据）
```

三种 source 语义：

| source | 含义 | 触发条件 |
|---|---|---|
| `"rag"` | 真实向量检索命中 | `search_models` 正常返回 Top-5 |
| `"full_library"` | fail-open 降级为全量谱系 | 提炼/检索/向量化任一步异常，但 `load_model_library()` 成功 |
| `"empty"` | 完全失败 | 连全量谱系都加载失败 |

### CLI 输出格式（逐字保留，改动点在 `_make_manager_node`）

真实命中：
```
[problem] 🎯 RAG 候选模型池命中（Top-5）— query: <traits+desc>
<完整 markdown 候选池，5 条，每条 ### 序号. 名称（分类）+ 特点 + 适用>
```

fail-open 降级：
```
[problem] ⚠️ RAG 检索失败，候选池降级为全量谱系（52 条）
```

完全失败：
```
[problem] ⚠️ 候选模型池生成失败，跳过注入
```

## Change List

### 1. `mathmodelingagents/agents/__init__.py`

**（a）新增 `CandidatePoolResult` NamedTuple**：放在 `_run_model_candidate_search` 函数定义之前（约 118 行前）。顶部 `from typing import ...` 若已有 `NamedTuple` 则复用，否则补 import。

**（b）改 `_run_model_candidate_search`（现 118-179 行）**：
- 返回值从 `str` 改为 `CandidatePoolResult`。
- 成功分支（现 172 行 `return format_model_entries(results)`）改为 `return CandidatePoolResult("rag", format_model_entries(results), query)`。
- 第一个 `except`（现 173-179 行）内：`load_model_library()` 成功时返回 `CandidatePoolResult("full_library", format_model_entries(...), "")`；第二个 `except`（连谱系都失败）返回 `CandidatePoolResult("empty", "", "")`。
- 函数 docstring 的 `Returns` 段落同步更新（现在是 `候选模型池 markdown 文本；...返回空字符串`，改为描述 NamedTuple 三字段语义）。
- **query 变量**在成功分支要用 `build_query(traits, description)` 的结果（现 166 行已算出 `query`，直接复用）。

**（c）改调用点（现 908-914 行）**：

现有：
```python
if layer == "problem":
    candidates_text = _run_model_candidate_search(config, result)
    if candidates_text:
        updates["model_candidates"] = candidates_text
        print(f"[problem] 候选模型池已生成 ({len(candidates_text)} chars)", flush=True)
```

改为：
```python
if layer == "problem":
    pool = _run_model_candidate_search(config, result)
    if pool.source == "rag" and pool.text:
        updates["model_candidates"] = pool.text
        print(f"[problem] 🎯 RAG 候选模型池命中（Top-5）— query: {pool.query}", flush=True)
        print(pool.text, flush=True)
    elif pool.source == "full_library" and pool.text:
        updates["model_candidates"] = pool.text
        print("[problem] ⚠️ RAG 检索失败，候选池降级为全量谱系（52 条）", flush=True)
    else:
        print("[problem] ⚠️ 候选模型池生成失败，跳过注入", flush=True)
```

> 注意：`pool.text` 是 markdown 多行文本，`print(pool.text)` 会自然换行输出；不要加额外缩进破坏格式。`source == "rag"` 和 `"full_library"` 都写 `model_candidates`（两者都有价值），只有 `"empty"` 跳过写入。

### 2. `tests/test_knowledge_retrieval.py`（新建）

覆盖 `knowledge` 模块纯逻辑 + `_run_model_candidate_search` + `_build_context` 注入边界。**不依赖 embedding 模型文件**——`search_models` 用 mock，真实检索用 `skipif` 保护。

具体用例：

| 测试函数 | 覆盖点 | mock 方式 |
|---|---|---|
| `test_load_model_library_has_52_entries` | 52 条 + 字段 `name/category/traits/usage` 完整 | 无 mock，直接读 json |
| `test_build_query_joins_traits_and_description` | 拼装正确（标签+描述用空格连接、空项过滤） | 无 |
| `test_entry_text_includes_name_traits_usage` | 检索文本含 name/traits/usage | 无 |
| `test_format_model_entries_markdown` | 格式化含 `### 1.` / `- 特点：` / `- 适用：` | 无 |
| `test_search_models_returns_top_k_sorted` | 命中路径：mock `_encode` 返回可控向量，验证返回 top_k 条且首条正确 | monkeypatch `mathmodelingagents.knowledge.retrieval._encode` |
| `test_search_models_fail_open_on_encode_error` | fail-open：mock `_encode` 抛异常 → 返回全量 52 条（不 crash） | monkeypatch `_encode` raise |
| `test_build_context_injects_pool_first_round_only` | round_count=0 注入、=2 不注入、无 candidates 不注入 | 直接构造 state dict |
| `test_run_model_candidate_search_rag_source` | `_run_model_candidate_search` 返回 `source=="rag"` + `text` 非空 | monkeypatch `create_layer_llm`（假 structured LLM）+ monkeypatch `mathmodelingagents.knowledge.search_models` 返回固定 [dict] |
| `test_run_model_candidate_search_fail_open_full_library` | `search_models` 抛异常 → 返回 `source=="full_library"` + 52 条 | monkeypatch `search_models` raise |
| `test_run_model_candidate_search_empty_source` | `search_models` 抛异常 + `load_model_library` 也抛异常 → `source=="empty"` + 空 text | monkeypatch 两个都 raise |
| `test_real_retrieval_smoke` | 真实检索冒烟（模型文件存在时）：`search_models("小样本 指数增长 预测")` 首条接近灰色预测 | `@pytest.mark.skipif(not Path(".../models").is_dir(), reason="embedding 模型未下载")` |

**mock 目标路径提示**（关键，避免 mock 错层）：

- `_run_model_candidate_search` 内部用 `from mathmodelingagents.knowledge import search_models, load_model_library, ...`（函数内局部 import，每次调用重新读取）。因此 monkeypatch 目标是 **`mathmodelingagents.knowledge.search_models`** / **`mathmodelingagents.knowledge.load_model_library`**（`knowledge/__init__.py` 的模块属性），patch 后会生效。
- `search_models` 内部调用 `retrieval._encode` / `retrieval._get_vectors`，patch 目标是 **`mathmodelingagents.knowledge.retrieval._encode`**（`search_models` 里的 `_encode` 是 `retrieval` 模块内直接引用的名字）。
- `_run_model_candidate_search` 里 `create_layer_llm` 是 `agents` 模块级引用，patch 目标 `mathmodelingagents.agents.create_layer_llm`（参考 `test_sensitivity_decision.py` 的 `FakeStructuredLLM` 写法，`with_structured_output` 返回带 `invoke` 的对象，`invoke` 返回 `SimpleNamespace(problem_traits=[...], description="...")`）。

### 3. `docs/rag-cli-output-and-tests-impl-brief.md`（本任务书，已存在，不改）

## Acceptance Criteria（必须逐条跑通并报告实际输出）

1. `export PYTHONPATH= && .venv/Scripts/python.exe -m pytest tests/test_knowledge_retrieval.py -v` 全部通过（真实检索冒烟在模型未下载时显示 skip 也视为通过，但报告 skip 数量）。
2. `export PYTHONPATH= && .venv/Scripts/python.exe -m pytest tests/ -q` 全绿（现有 103 个 + 新增，不回归）。
3. CLI 输出验证：运行下面探针，确认打印「🎯 RAG 候选模型池命中」+ 完整 markdown（5 条 `### N.`）：

```bash
export PYTHONPATH= && .venv/Scripts/python.exe -c "
import sys; sys.path.insert(0,'.')
from types import SimpleNamespace
import mathmodelingagents.agents as A
class FakeLLM:
    def with_structured_output(self, schema):
        class R:
            def invoke(self, messages):
                return SimpleNamespace(problem_traits=['小样本','指数增长','时序预测'], description='数据量少且呈指数增长的时间序列')
        return R()
A.create_layer_llm = lambda *a, **kw: FakeLLM()
pool = A._run_model_candidate_search({'llm_provider':'opencode'}, '## 问题分析结论\n\n**CONCLUDE**\n\n')
print('SOURCE:', pool.source)
print('QUERY:', pool.query)
print(pool.text)
"
```

   预期 `SOURCE: rag`，`pool.text` 含 5 条 markdown，首条接近「灰色预测 GM(1,1)」。

4. Import 冒烟：`.venv/Scripts/python.exe -c "import mathmodelingagents.agents, mathmodelingagents.knowledge, mathmodelingagents.tools"` 成功。

## Explicitly Do NOT Do

- **不要改 `model_library.json`**（52 条、traits 纯中文、无倾向性标注，已定稿）。
- **不要改 `_run_model_candidate_search` 以外的函数签名**（`_build_context`、`_run_tool_loop`、`search_models` 等保持原样）。
- **不要引入 torch / sentence-transformers / chroma / faiss**。
- **不要动 Layer 2 辩论拓扑、modeler system prompt 的风格定位**。
- **不要把候选池注入 system prompt**（保持 system prompt 静态可缓存）。
- **不要改 config 的层模型分配 / max_tokens / timeout**。
- **不要动 `pyproject.toml` / `uv.lock` / 依赖**（fastembed/onnxruntime 已装，无需新增）。
- **不要提交、不要 push**——只编辑文件，git 操作由 Hermes 处理。
- **不要改动 `_run_model_candidate_search` 的 fail-open 语义**（任何失败仍要降级，不抛异常出模块边界）。

## 环境注意（Windows 陷阱，务必遵守）

- 所有命令用 `.venv/Scripts/python.exe`（项目 venv，Python 3.12），**不要用系统 `python`**（会解析到 Hermes 自己的 venv，缺 fastembed）。
- 跑任何测试/脚本前先 `export PYTHONPATH=`（清除 Hermes 注入的 PYTHONPATH，它会 shadow 项目里的 `cli` 模块）。
- `print` 中文若遇 `UnicodeEncodeError`，终端已是 UTF-8（现有代码 print 中文正常），不需要额外处理。
