# Implementation Brief: 候选模型池提炼容错 JSON 解析（对齐敏感性决策）

## Background

Layer 1 候选模型池提炼 `_run_model_candidate_search`（`agents/__init__.py:179-243`）至今仍用
LangChain `with_structured_output(ProblemTraits)` 做结构化输出。在火山方舟（Volcano Ark / custom）通道下，
模型经常不遵守「只输出 JSON」的指令，回中文 markdown 叙述文本（如 `题目特点标签：\n...与结果可靠性。`），
pydantic 拿裸文本当 JSON，在 line1 col1 抛 `json_invalid`：

```
1 validation error for ProblemTraits
Invalid JSON: expected value at line 1 column 1 [type=json_invalid, input_value='题目特点标签：\n...', input_type=str]
```

当前靠 `except Exception` 兜底 fail-open 降级为全量谱系（52 条），流程不崩，但针对性 Top-5 检索完全失效，
选型精度退化。

**对照**：同文件的 `_run_sensitivity_decision`（`agents/__init__.py:60-97`）此前已按同一痛点重构为
「`invoke_with_fallback` 普通文本调用 + `_parse_sensitivity_json` 容错解析」，代码注释里明确记录了
「不用 with_structured_output——火山通道下模型回 markdown 文本非 JSON，pydantic 报 json_invalid」。

本 brief 把候选池提炼对齐到同一模式：普通文本调用 + 容错 JSON 解析，废弃 `with_structured_output`。

## Design

- **照抄 `_run_sensitivity_decision` 的成熟模式**，不复用但严格对齐（不改动已验证的 sensitivity 路径，避免回归）：
  1. 新增模块级 `_parse_traits_json(text) -> tuple[list[str], str]`，跟 `_parse_sensitivity_json` 同款
     容错（先剥 ```json 围栏 → 正则取首个 `{...}` 块 → `json.loads` → 断言 dict），再提取
     `problem_traits`（list[str]）与 `description`（str），字段缺失给空值；解析失败 raise `ValueError`。
  2. `_run_model_candidate_search` 内部：删除 `class ProblemTraits(BaseModel)` 与 pydantic import，
     改用 `invoke_with_fallback(config, "problem", "manager", messages, "model_candidate_search", max_tokens=512)`
     做普通文本调用，结果 `_parse_traits_json` 解析 → `build_query(traits, description)` → `search_models(query, top_k=5)`。
- **prompt 强化**：SystemMessage 追加「请**只输出一个 JSON 对象**，形如
  `{"problem_traits": ["小样本", "多目标优化"], "description": "……"}`，不要输出任何 markdown 代码块或解释文字」，
  HumanMessage 结尾改为「请提炼题目特点并输出 JSON」。这是压低 json_invalid 发生率的根因手段。
- **temperature / max_tokens**：不显式传温度（manager 角色由 `temperature_overrides` 解析为 0.1，与
  sensitivity 决策一致）；`max_tokens=512` 足够输出一个小 JSON 对象。
- **fail-open 语义不变**：`_run_model_candidate_search` 外层 `try/except` 已捕获任何异常并降级
  `full_library` → `empty`，本次改动不触碰这段兜底逻辑。新增的解析失败 `ValueError` 自然落入该 except，
  行为与现状（解析失败也降级全量谱系）完全一致。

## Change List

> ⚠️ 行尾：`mathmodelingagents/agents/__init__.py` 是 **CRLF**，编辑时保持原行尾，不要用跨多行缩进的
> patch（fuzzy matcher 会搞乱缩进）。建议用 Python 脚本做精确 `str.replace` 或对单行文本做最小 patch。
> ⚠️ `create_layer_llm` import 必须保留：文件 1360 行（summary 兜底）仍在用，不要因为它在本函数内不再
> 使用而删掉该 import。

### 1. `mathmodelingagents/agents/__init__.py`

**1a. 新增 `_parse_traits_json`**：放在 `_parse_sensitivity_json`（当前第 42-57 行）之后。与
`_parse_sensitivity_json` 的容错骨架一致，仅字段提取不同。签名：

```python
def _parse_traits_json(text: str) -> tuple[list[str], str]:
    """容错解析候选池提炼返回的 JSON：剥围栏 → 正则取首个 {...} → json.loads → 取 problem_traits/description。

    与 _parse_sensitivity_json 同款容错（火山通道下模型常回 markdown 文本非纯 JSON）。
    字段缺失回退空值；解析失败 raise ValueError（由 _run_model_candidate_search 外层 except 兜底降级全量谱系）。
    """
```

实现要点（照抄 `_parse_sensitivity_json` 的 fence 剥离 + 正则 `re.search(r"\{[\s\S]*\}", cleaned)` +
`json.loads` + `isinstance(dict)` 断言，失败 `raise ValueError`），随后：

```python
    traits_raw = obj.get("problem_traits") or []
    if isinstance(traits_raw, str):
        traits_raw = [traits_raw]
    traits = [str(t).strip() for t in traits_raw if str(t).strip()]
    description = str(obj.get("description") or "").strip()
    return traits, description
```

`re`、`json` 该文件顶部已 import（`_parse_sensitivity_json` 正在用），无需新增。

**1b. 重写 `_run_model_candidate_search` 的 try 主体**（当前第 216-236 行）：

- 删除函数内 `from pydantic import BaseModel, Field`（第 198 行）与 `class ProblemTraits(BaseModel): ...`
  定义（第 207-214 行）。内层 `from mathmodelingagents.knowledge import (...)` 保留。
- 把 `llm = create_layer_llm(...)` + `structured = llm.with_structured_output(ProblemTraits)` +
  `parsed = structured.invoke([...])` 整段替换为：

```python
    messages = [
        SystemMessage(content=(
            "你是数学建模竞赛的方法选型专家。根据题目分析报告，提炼本题的"
            "「题目特点需求」：3-8 个纯中文特点标签（数据形态/目标类型/约束特征，"
            "如：小样本、非线性、多目标、时序预测），加一两句特点描述。"
            "标签用于检索候选数学模型知识库，请贴题目实际特征，不要罗列模型名。"
            "请**只输出一个 JSON 对象**，形如 "
            '{"problem_traits": ["小样本", "多目标优化"], "description": "……"}，'
            "不要输出任何 markdown 代码块或解释文字。"
        )),
        HumanMessage(content=f"## 题目分析报告\n\n{problem_report}\n\n请提炼题目特点并输出 JSON。"),
    ]
    text = invoke_with_fallback(config, "problem", "manager", messages, "model_candidate_search", max_tokens=512)
    traits, description = _parse_traits_json(text)
    query = build_query(traits, description)
    results = search_models(query, top_k=5)
```

- 保留原有 `logger.info("[problem] 候选模型池检索完成: traits=%s, top1=%s", ...)` 与
  `return CandidatePoolResult("rag", format_model_entries(results), query)`（当前第 232-236 行）。
- 原 `traits = [t.strip() for t in (parsed.problem_traits or []) ...]` 与 `description = str(...)` 两行
  （第 228-229 行）删除，由 `_parse_traits_json` 承担。

**1c. 文档同步（非阻塞）**：函数 docstring 第 179-186 行里「用 problem 层 manager 档模型 +
with_structured_output 提炼」一句，改为「用 problem 层 manager 档模型 + 普通文本调用 + 容错 JSON 解析
提炼」，措辞对齐 `_run_sensitivity_decision` 的注释风格。只改这一句，不动 docstring 其余部分。

## Acceptance Criteria（必须逐条跑通）

1. **语法检查**：`export PYTHONPATH= && .venv/Scripts/python.exe -m py_compile mathmodelingagents/agents/__init__.py` 无输出 = 通过。
2. **import 冒烟**：`export PYTHONPATH= && .venv/Scripts/python.exe -c "import mathmodelingagents.agents"` 成功。
3. **解析单测**：新增 `tests/test_candidate_search_parse.py`，覆盖 `_parse_traits_json`：
   - 纯 JSON 对象 `{"problem_traits": ["小样本","多目标"], "description": "……"}` → 返回正确 (list, str)；
   - markdown 围栏包裹（```json ... ```）→ 正确解析；
   - JSON 前后夹中文叙述文字（含换行，如 `题目特点标签：\n...{"problem_traits": [...]}...`）→ 正则仍能挖出并解析；
   - `problem_traits` 缺省 → 返回 `([], "")` 不抛错；
   - 无 `{` 的纯中文文本 → `raise ValueError`。
   用 `.venv/Scripts/python.exe -m pytest tests/test_candidate_search_parse.py -v` 通过。
4. **回归**：`.venv/Scripts/python.exe -m pytest tests/ -v` 全绿（含既有两个测试文件）。
5. **静态确认**：`search_files` 确认 `mathmodelingagents/agents/__init__.py` 内已无 `ProblemTraits` 与
   `with_structured_output` 残留。
6. **工作区干净**：`git status --short` 只出现 `mathmodelingagents/agents/__init__.py` + 新增测试 +
   本 brief，无其他无关文件。

> 测试用 `.venv/Scripts/python.exe`，运行前先 `export PYTHONPATH=`。禁止用 heredoc 传含 `\r\n`/`"""` 的 Python 代码。

## Explicitly Do NOT Do

- **不要**改动 `_parse_sensitivity_json` / `_run_sensitivity_decision` / `_persist_sensitivity_decision`
  及任何敏感性决策既有逻辑（已验证可用，本 brief 只对齐模式、不复用也不触碰它们）。
- **不要**改动 `knowledge/retrieval.py`、`model_library.json`、检索/向量化算法、`build_query` / `search_models` / `format_model_entries`。
- **不要**改动 `_run_model_candidate_search` 的外层 `try/except` 兜底（fail-open 降级全量谱系）语义。
- **不要**改动 `CandidatePoolResult` 结构、`_persist_model_candidates`、`main.py`、graph 拓扑。
- **不要**删 `create_layer_llm` import（1360 行 summary 兜底仍用）。
- **不要**动 `.env`、`pyproject.toml`、依赖、`uv.lock`。
- **不要**顺手清理或重构无关代码、不要改 logging 文案的既有措辞（新增 warning 文案可新写）。