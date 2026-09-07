# Implementation Brief: 输入超长溢出 + 降级链止血 + 结构化决策容错

> 关联故障：`main.py B题-全部转换合并.md --provider volcengine --max-rounds 1`
> L2 全部节点报 `400 InvalidParameter: Input length 1255282 exceeds maximum length 1048566`。
> 由 Hermes 诊断定案，Claude Code 执行，Hermes 验收复核。

## Background

一次完整流程在 Layer 2 全体建模师/Manager 崩掉。根因链条（Hermes 已逐条对照源码复核）：

1. **题目文件是「数据堆」**：`B题-全部转换合并.md` 总 772,484 字符，其中 4 个附件（附件1~4.xlsx）被整体转成 markdown 表格塞进正文，占 96% 字符（每附件约 19.2 万字符、~7471 行表格）。
2. **`Propagator.create_initial_state`（`graph/propagation.py:57`）把整个 md 读入 `problem_description`**，L2 的 `_build_context` 又叠加 `problem_report`（Layer1 复述了 444 行表格）等，L2 输入膨胀到 ~1.25M 字符，超火山硬上限 1,048,566。
3. 该 `400 InvalidParameter / Input length exceeds` 是**确定性硬错误**：`is_retryable_error` 不认识它（不重试，对），但降级链仍空转 4 步换 provider——换 provider 无意义（不是通道故障，是输入太大）。
4. 雪上加霜：`.env` 的 `FALLBACK_PROVIDER=opencode`，其账号本月额度已耗尽（`429 GoUsageLimitError`，6 天后重置），故降级链全部失败。
5. 真实 bug：CLI 里 quick/deep 引擎都选 `deepseek-v4-flash`，`_build_fallback_steps` 的 4 步里 step1==step3、step2==step4，白跑两遍。
6. 次要：`_run_sensitivity_decision` 用 `with_structured_output`，火山通道下模型回 markdown 文本非 JSON，pydantic 报 `json_invalid`，靠 fail-open 兜底（能跑但结构化输出不可靠）。

数据已验证（Hermes 实测，`B题-全部转换合并.md`）：
- 切分后**正文段 = 1082 字符 / 53 行**（题目文字 + 附件说明完全在正文段内）
- 附件1~4 各 ~19.2 万字符、7471 行表格
- 剥离后上下文可缩到 ~4K 字符（缩 99.5%）

## Design

### Ticket A — 附件数据剥离（根因，唯一能让整流程跑通的正解）

附件表格数据**不进 LLM prompt**，改为「摘要 + 文件按需读取」。数据可达性靠工具（read_file / run_code）保证，与项目既有设计意图一致（见 `_build_context` docstring：敏感层传摘要，Agent 用工具取完整数据）。

新增模块 `mathmodelingagents/problem_loader.py`，接口如下（签名必须一致）：

```python
SPLIT_MARKER_RE = re.compile(r"<!-- ========== (.+?) ========== -->")

def split_problem_document(md_text: str) -> tuple[str, list[tuple[str, str]]]:
    """把题 md 切成 (正文段 body, [(附件名, 附件表格md), ...])。body 为第一个分隔符之前的全部内容。"""

def build_attachment_summary(attachments: list[tuple[str, str]], source_dir: str | None = None) -> str:
    """为每个附件生成摘要：附件名、表格行数、列说明（表头）、前 2 条数据行示例、可选源 xlsx 绝对路径。逐个附件用函数内统计，不要口算。"""

def extract_attachments(md_text: str, output_dir: str, source_dir: str | None = None) -> tuple[str, str]:
    """高层入口：split → 把每个附件表格写成 output_dir/attachments/{i:02d}_{name}.md → 返回 (body, summary)。summary 含每个附件落盘后的相对路径。"""
```

摘要里每个附件必须含：`名称`、`数据行数`、`列结构（表头）`、`前 2 条数据行示例`、`落盘文件相对路径（output_dir 相对）`、可选的 `源 xlsx 绝对路径`（若 `source_dir/附件/<name>.xlsx` 存在）。

附件名含 `.xlsx` 与中文，落盘文件名需 sanitize（`附件1.xlsx` → `附件1.xlsx.md`，非法字符替换为 `_`）。

### Ticket B — 输入超长硬错误 fail-fast

新增 `llm_clients/__init__.py`：

```python
_INPUT_TOO_LONG_SUBSTRINGS = (
    "input length", "maximum length", "maximum context",
    "context length", "invalidparameter", "too many tokens",
    "context window", "exceeds the maximum",
)

def is_input_too_long_error(error: Exception) -> bool:
    """判断是否为「输入超长」确定性错误（不可重试、换 provider 无效）。"""
```

在 `invoke_with_fallback`（现 356-373 的 except 块）与 `invoke_with_tools_with_fallback`（现 451-470 的 except 块）里，**进入 "unavailable → trying next fallback" 打印之前**先判断：若 `is_input_too_long_error(e)`，立即 `raise RuntimeError(f"[{layer}] {agent_name} 输入超长（超过模型上下文上限），请剥离附件数据/精简输入后重试: {e}")`，不再尝试剩余 steps。

行为语义：输入超长 = 确定性错误，fail-fast，绝不再烧 3 步降级调用。

### Ticket C — 降级链去重

`_build_fallback_steps`（`llm_clients/__init__.py:297-309`）构造 4 步后按 `(provider, model)` 去重（保留首次出现）。primary==flash 时 4 步自动缩为 2 步。保持返回类型 `list[tuple[str, str, str | None]]`。不改变降级链的顺序语义。

### Ticket D — 敏感性决策结构化输出容错

`_run_sensitivity_decision`（`agents/__init__.py:41-82`）弃用 `with_structured_output`，改为「走统一降级链的普通文本调用 + 容错 JSON 解析」：

```python
def _parse_sensitivity_json(text: str) -> dict:
    """容错解析模型返回的 JSON：剥离 ```json 围栏 → 正则取首个 {...} 块 → json.loads。失败 raise ValueError。"""
```

`_run_sensitivity_decision` 内：
- `messages = [SystemMessage("你是…请**只输出一个 JSON 对象**，形如 {\"enabled\": true/false, \"reason\": \"…\"}，不要输出任何 markdown 或解释文字。"), HumanMessage(…)]`
- `text = invoke_with_fallback(config, "problem", "manager", messages, "sensitivity_decision", max_tokens=512)`
- `obj = _parse_sensitivity_json(text)`；`enabled = bool(obj.get("enabled"))`；`reason = str(obj.get("reason") or "").strip() or "（未给出理由）"`
- 现有 `except Exception` fail-open 兜底结构**原样保留**（解析失败 → `return True, f"决策调用失败（{e}），fail-open 默认执行"`）。

## Change List

### 1. `mathmodelingagents/problem_loader.py`（新增，Ticket A）
按上面 Design 实现三个函数 + `SPLIT_MARKER_RE`。纯函数为主，`extract_attachments` 负责落盘（用 `Path.write_text(encoding="utf-8")`，落盘前 `mkdir(parents=True, exist_ok=True)`）。

### 2. `mathmodelingagents/agents/utils/agent_states.py`
`AgentState`（TypedDict, total=False）新增字段 `attachment_summary: str`（放 `model_candidates` 附近，约 53 行后）。注释：`# Layer 1 附件数据摘要（附件名/行数/示例/落盘路径，供各层按需读取；全量表格不进 prompt）`。

### 3. `mathmodelingagents/graph/modeling_graph.py`
`propagate()`（约 99-175）：在 `setup_incremental(output_dir)`（~123）之后、`problem_name = …`（~124）之前，插入：

```python
from mathmodelingagents.problem_loader import extract_attachments
from pathlib import Path as _Path
_md_text = _Path(initial_state["problem_path"]).read_text(encoding="utf-8")
_body, _summary = extract_attachments(
    _md_text, output_dir,
    source_dir=str(_Path(initial_state["problem_path"]).parent),
)
initial_state["problem_description"] = _body
initial_state["attachment_summary"] = _summary
```

（正文段覆盖 `problem_description`，附件摘要写入 `attachment_summary`。）

### 4. `mathmodelingagents/agents/__init__.py`
- **`_build_context`（~678-802）**：题目信息段（~688-692，`if agent != "viz_agent"` 分支内）在 `## 题目内容` 之后、`problem` 之前追加：

```python
if state.get("attachment_summary"):
    parts.append(f"## 附件数据摘要（全量数据请用 read_file/run_code 按文件读取，勿把表格塞进回答）\n\n{state['attachment_summary']}")
```

- **`create_solver_agent`（~1268-1365）**：无需额外改动——first-run 走 `_build_context(..., "implementation", "solver_agent", ...)`，会自动看到 attachment_summary。**不要**单独加硬编码注入。
- **`_run_sensitivity_decision`（~41-82）**：按 Ticket D 重写（新增 `_parse_sensitivity_json` helper，放本文件内、函数之前）。

### 5. `mathmodelingagents/llm_clients/__init__.py`
- 新增 `_INPUT_TOO_LONG_SUBSTRINGS` + `is_input_too_long_error`（放 `is_retryable_error` 之后，~75 行附近）。
- `_build_fallback_steps`（~297-309）：返回值去重。
- `invoke_with_fallback`（~356-373）与 `invoke_with_tools_with_fallback`（~451-470）：except 块内 fail-fast 拦截（Ticket B）。

### 6. `tests/test_problem_loader.py`（新增，TDD）
用内存构造的 md 样本（`<!-- ========== 附件A.xlsx ========== -->` 分隔 + 若干表格行），断言：
- `split_problem_document` 正确切出 body 与附件列表；
- `build_attachment_summary` 含附件名、数据行数、示例行；
- `_build_fallback_steps`（`test` 用临时 config：`layer_model_overrides` 使 primary==flash）去重后返回 2 步；
- `is_input_too_long_error` 对 `Input length ... exceeds the maximum length` 类消息返回 True、对 `429 rate limit` 返回 False。

## Acceptance Criteria（全部用项目虚拟环境，先 `export PYTHONPATH=`）

```bash
export PYTHONPATH=
cd /f/code/projects/MathModelingAgents

# 1. 新增单测全绿
.venv/Scripts/python.exe -m pytest tests/ -v

# 2. 真实题目剥离验收脚本（需提交此脚本 scripts/verify_problem_strip.py）
.venv/Scripts/python.exe scripts/verify_problem_strip.py
#   → 打印 body 字符数、attachment_summary 长度、落盘附件文件清单，并断言 body < 15000 且落盘 4 个附件文件

# 3. import smoke
.venv/Scripts/python.exe -c "import mathmodelingagents.problem_loader; print('ok')"
```

验收脚本 `scripts/verify_problem_strip.py` 内容要求：读 `C:/Users/joeji/Desktop/what/math.25b/B题-全部转换合并.md`，调 `extract_attachments` 落到临时目录（`tempfile.mkdtemp`，不污染桌面），打印并断言。**脚本里数值结论必须来自函数实际执行，不得硬编码预期数字。**

## Explicitly Do NOT Do

- **不得改 `.env`、`pyproject.toml`、任何 lock 文件**；不新增依赖。
- **不得动 RAG/候选模型池（`knowledge/`、`modeler_a` 创新 prompt、`_persist_model_candidates`）**——本次剥离与候选池无关，若落盘路径与之有冲突，就地记录到 docstring 并报告，不要擅自改候选池逻辑。
- **不得改图拓扑（`graph/setup.py`、`conditional_logic.py`）、`recovery.py`**。
- **不清理用户桌面上的任何文件**，验收脚本用 `tempfile`，不写 `C:/Users/joeji/Desktop/`。
- **日志/提示文案保持现有风格与措辞**；新增的报错文案除外（Ticket B 的 fail-fast 文案按 brief 原文写）。
- 不新增 `--max-rounds`/CLI 改动。
- 用 Claude Code 执行，先写失败测试（red）再实现（green），每 ticket 独立后自审 `git diff --stat` 确认越界。