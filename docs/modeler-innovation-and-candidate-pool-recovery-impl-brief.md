# Implementation Brief: 强化 modeler 创新 + 修复候选池两个脱节点

## Background

三道题验证出一个结论：RAG 候选池对「找不到新模型」的贡献有限，真正决定「新模型」的是 modeler_a 的
「创新与优雅」定位（来自 LLM 预训练知识）。当前机制存在三个脱节点，本 brief 修复其中两个
（第三个「知识库补前沿条目」不在本次范围）：

1. **prompt 未授权 model_search_tool**：`agents/__init__.py:1177` 实际给三个 modeler 绑定了
   `model_search_tool`，但 `prompt_templates.py` 里三个 modeler 的「工具权限」行只写了
   `run_code + web_search`，modeler 从不知道自己有检索能力，从不主动调用。
2. **--from-layer1 丢候选池**：`_run_model_candidate_search` 只在「完整跑含 L1」时执行，结果只写内存
   state，不落盘；`graph/recovery.py` 只恢复 `problem_report`，不回填 `model_candidates`。导致
   `--from-layer1` 模式下 Layer 2 拿不到 L1 预筛的 Top-5 候选池。

本次同时按用户决定做「中等幅度」强化 modeler_a 的创新 prompt。

## Design

- **落盘/回填完全照抄 `sensitivity_decision.json` 既有模式**（fail-open，不改变现网行为）：
  - 落盘：完整跑时在 `_make_manager_node` 的 Layer 1 CONCLUDE 分支，拿到 `pool` 后新增
    `_persist_model_candidates(config, pool)`，写入 `<output_dir>/model_candidates.json`。
  - 回填：`load_layer1_state` 读取 `<from_layer1_dir>/model_candidates.json`，非空则回填
    `model_candidates`。
  - 与 `sensitivity_decision.json` 一致：运行时落盘到 `output_dir`，恢复时从 `--from-layer1` 指向的
    同一目录读取 —— 同一个问题目录下两套机制天然对齐。
- **落盘内容**：`{"text": pool.text, "source": pool.source, "query": pool.query}`（`CandidatePoolResult`
  是 NamedTuple，三字段分别为候选池 markdown、来源 rag/full_library/empty、检索 query）。
  仅当 `pool.text` 非空时落盘（source="empty" 时不落盘）。
- **回填内容**：`recovered["model_candidates"] = data["text"]`（非空才设置；缺失 key 静默跳过，
  与现有 sensitivity_decision 的 json 解析失败处理同风格）。
- **prompt 授权**：三个 modeler 的工具权限行补上 `model_search`，并加一句轻引导
  「可先检索候选模型做选型参考」。这是让 LLM 知晓自己既有能力，不强制它调用。
- **modeler_a 创新强化（中等）**：风格定位补「前沿方法清单」，领地加「第1轮先用 model_search_tool
  检索 + web_search 找近年新方法」的引导。**不加**强制「新颖方法对比」小节（那是激进档，用户已否）。

## Change List

> ⚠️ 行尾格式：`prompt_templates.py` 与 `agents/__init__.py` 是 CRLF，`recovery.py` 是 LF。
> 编辑时保持各文件原有行尾，不要用跨多行缩进的 patch（fuzzy matcher 会搞乱缩进）。
> 建议用 Python 脚本做精确 `str.replace` / 行索引切片，或对单行文本做最小 patch。

### 1. `mathmodelingagents/agents/utils/prompt_templates.py`（CRLF）

**1a. modeler_a 风格定位（第 222-223 行）**：把「倾向：贝叶斯/信息几何/动力系统/拓扑方法等前沿方法」
一句扩为含前沿方法清单：

```text
## 你的风格定位：创新与优雅
你追求理论上漂亮、方法上有新意的方案。倾向贝叶斯/信息几何/动力系统/拓扑方法等前沿方法，追求理论完备性和数学美感。可考虑的前沿方向（按题目相关性选用，勿生搬硬套）：神经 ODE / 物理信息神经网络(PINN)、图神经网络(GNN)、强化学习与多臂老虎机、扩散/生成模型、拓扑数据分析(TDA)、Agent-based 建模、因果推断等。
```

**1b. modeler_a 领地（第 225-232 行「你的领地」清单）**：加一条引导（放在清单内，措辞贴合现有风格）：

```text
✅ 第1轮先用 model_search_tool 检索候选模型知识库、用 web_search 检索近年新方法，再据此提出方案
```

**1c. 三个 modeler 工具权限行** —— 各自补 `model_search` + 轻引导：

- 第 241 行（modeler_a）：`## 工具权限: run_code (sympy/numpy/scipy/sklearn), web_search`
  → `## 工具权限: run_code (sympy/numpy/scipy/sklearn), web_search, model_search（可先检索候选模型做选型参考）`
- 第 280 行（modeler_b）：`## 工具权限: run_code (全部数学/统计/ML 库), web_search`
  → `## 工具权限: run_code (全部数学/统计/ML 库), web_search, model_search（可先检索候选模型做选型参考）`
- 第 320 行（modeler_c）：`## 工具权限: run_code (全部数学/统计库), web_search`
  → `## 工具权限: run_code (全部数学/统计库), web_search, model_search（可先检索候选模型做选型参考）`

### 2. `mathmodelingagents/agents/__init__.py`（CRLF）

**2a. 新增 `_persist_model_candidates` 函数**：放在 `_persist_sensitivity_decision`（第 85-115 行）附近，
照抄其 fail-open 模式。函数签名大致：

```python
def _persist_model_candidates(config: dict, pool) -> None:
    """把 Layer 1 候选模型池落盘到 `<output_dir>/model_candidates.json`（ADR-0003）。"""
    output_dir = config.get("output_dir", "")
    if not output_dir:
        logger.warning("[problem] 候选模型池未落盘: config 未设置 output_dir")
        return
    if not getattr(pool, "text", ""):
        return
    payload = {
        "text": getattr(pool, "text", ""),
        "source": getattr(pool, "source", ""),
        "query": getattr(pool, "query", ""),
    }
    try:
        path = Path(output_dir) / "model_candidates.json"
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info(f"[problem] 候选模型池已落盘: {path}")
    except OSError as e:
        logger.warning(f"[problem] 候选模型池落盘失败（不影响主流程）: {e}")
```

> `json`、`Path`、`logger` 在该文件已 import（`_persist_sensitivity_decision` 正在用），无需新增 import。

**2b. 调用落盘**：在第 937-946 行的 Layer 1 候选模型池分支里，`pool = _run_model_candidate_search(config, result)`
之后、处理 `pool.source` 之前（或紧接其后）加一句 `_persist_model_candidates(config, pool)`。
保证无论 source 是 rag/full_library/empty 都尝试落盘（函数内部已对空 text 短路）。

### 3. `mathmodelingagents/graph/recovery.py`（LF）

**3a. `load_layer1_state` 回填 `model_candidates`**：在现有 `decision_path` 读取逻辑（第 51-58 行）之后，
新增一段读取 `model_candidates.json` 的对称逻辑：

```python
    candidates_path = Path(output_dir) / "model_candidates.json"
    if candidates_path.exists():
        try:
            data = json.loads(candidates_path.read_text(encoding="utf-8"))
            text = str(data.get("text", "") or "").strip()
            if text:
                recovered["model_candidates"] = text
        except (json.JSONDecodeError, OSError, AttributeError) as e:
            logger.warning(f"[recovery] 候选模型池 json 解析失败，跳过回填: {e}")
```

`json`、`Path`、`logger` 已在该文件 import。**只回填 text，不回填 source/query**（state 字段
`model_candidates` 是 str）。

## Acceptance Criteria（必须逐条跑通）

1. **语法检查**：`.venv/Scripts/python.exe -m py_compile` 三个被改文件，无输出 = 通过。
2. **import 冒烟**：`export PYTHONPATH= && .venv/Scripts/python.exe -c "import mathmodelingagents.agents, mathmodelingagents.graph.recovery"` 成功。
3. **prompt 授权验证**：加载 modeler_a/b/c 的 prompt，断言三处都含 `model_search`；断言 modeler_a
   prompt 含「前沿方向」与「model_search_tool 检索候选模型知识库」关键词。
4. **落盘验证**：构造一个 `config={"output_dir": <临时目录>}`，调用 `_persist_model_candidates`（用一个
   CandidatePoolResult 或等价 NamedTuple 对象），断言 `<临时目录>/model_candidates.json` 生成且包含
   `text`/`source`/`query` 三键。
5. **回填验证**：在临时目录放一个 `Layer1_问题分析.md`（含 `### 问题分析经理 [裁决]` 段落）和
   `model_candidates.json`（含非空 text），调用 `load_layer1_state(临时目录)`，断言返回 dict 含
   非空 `model_candidates`，且等于 json 里的 text；再删掉 model_candidates.json 重调用，断言返回 dict
   **不含** `model_candidates` 键（缺省不错误）。
6. **工作区干净**：`git status --short` 只出现预期改动的 3 个源文件 + 本 brief + 新增的验证脚本，无无关文件。

> 验证脚本建议写成 `scripts/verify_model_candidates_recovery.py`（可复用、入库）；临时目录用
> `tempfile.mkdtemp`，脚本结束时清理。禁止用 heredoc 传含 `\r\n`/`"""` 的 Python 代码。

## Explicitly Do NOT Do

- **不要**改动 `model_library.json` / `retrieval.py` / 检索算法（第三脱节点「补库」不在本次范围）。
- **不要**给 modeler_a 加「新颖方法对比」强制输出小节（激进档，用户已否）。
- **不要**改 graph 拓扑、`AgentState` 的字段定义、`_build_context` 注入条件（`round_count <= 1` 已正确）。
- **不要**改 `model_search_tool` 实现，也不要改 `agents/__init__.py:1177` 的工具绑定套装。
- **不要**动 `.env`、`pyproject.toml`、依赖、`uv.lock`。
- **不要**改 `sensitivity_decision.json` 的既有逻辑。
- **不要**顺手清理或重构无关代码、不要改 logging 文案的既有措辞（新增的 warning 文案可新写）。
- 所有测试/验证用 `.venv/Scripts/python.exe`，运行前先 `export PYTHONPATH=`。