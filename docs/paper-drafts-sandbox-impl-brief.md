# Implementation Brief: PaperAgent 草稿下沉 + final_paper 去污染

## Background

B 题实跑（2026-09-07，volcengine-plan + minimax-m3 论文 writer）发现 output_dir 根目录散落 5 份
「论文」文件：`paper.md` / `paper_full.md` / `paper_tail.md` / `draft_paper.md` / `final_paper.md`，
外加 `front_check.md` / `CODE_VERIFICATION.md`。根因：

1. PaperAgent 的 `write_file` 工具（`tools/__init__.py:create_paper_agent_tools` 内）**零路径约束**，
   直接把模型传入的 `path` 原样 `resolve().write_text()`。prompt 从未规定草稿文件名，模型在
   完整论文（~37KB）塞不进一次输出（`max_tokens=16384`）时，**自己临场发明**了拆分策略：
   前半写 `paper_full.md`、后半写 `paper_tail.md`/`draft_paper.md`，最后拼 `paper.md`。全部落在根目录。
2. 框架最终交付 `final_paper.md` 由 `reporting.py:_build_final_paper` 从 `state["final_paper"]` 生成，
   但该字段在「PaperManager 发 REVISE 且轮次耗尽」路径下 = PaperAgent 的原始 `final_output`
   =「自检清单 + `## SELF_CHECK_PASSED` + 论文正文」，`_build_final_paper` 的防御（判 `## 修改项` / 开头 `(`）
   抓不住「自检清单开头」，于是最终论文前 29 行是自检 checklist。

## Design

- **草稿下沉**：PaperAgent 的 `write_file` 一律重定向到 `<output_dir>/drafts/`，只保留文件 basename
  （扁平化、防 `..` 逃逸、防覆盖根目录保留文件）。`drafts/` 不清空、不强制统一文件名（已确认）。
- **读回闭环**：PaperAgent 的 `read_file` 在原路径不存在时，回退尝试 `<output_dir>/drafts/<basename>`，
  覆盖「写进 drafts 却用相对 basename 读不回」的路径一致性漏洞。
- **唯一最终论文**：根目录只保留 `final_paper.md` 一份，由 `_build_final_paper` 生成且**剥离自检清单序言**
  （兜底覆盖正常 CONCLUDE 路径与异常 REVISE 耗尽路径）。`paper.md` 等草稿只存在于 `drafts/`。
- **读盘来源迁移**：`_paper_read_disk` 的候选列表从根目录 `["paper.md","PaperAgent_paper.md","final_paper.md"]`
  改为扫描 `<output_dir>/drafts/*.md`，按文件大小降序取第一份「含 `## 摘要` 或 `## Abstract`」的作论文正文。

## Change List

> ⚠️ 行尾：`tools/__init__.py`、`agents/__init__.py`、`prompt_templates.py` 是 **CRLF**，
> `reporting.py` 是 **LF**。编辑保持各自原行尾（用单行最小 patch 或 `str.replace` 精确替换，
> 每处断言唯一命中）。⚠️ 工作区 `git status --short` 应干净；只改任务书列出的文件，绝不动其他未提交文件。

### 1. `mathmodelingagents/tools/__init__.py`（CRLF）

**1a. `write_file_tool`（`create_paper_agent_tools` 内，约 593-610 行）**：重定向到 drafts。当前：
```python
    @tool
    def write_file_tool(
        content: str,
        path: str,
    ) -> str:
        """Write content to a file. Parent directories are created as needed.

        Use this to save the final paper or intermediate drafts.
        """
        p = Path(path).expanduser().resolve()
        p.parent.mkdir(parents=True, exist_ok=True)
        try:
            p.write_text(content, encoding="utf-8")
            logger.info("write_file_tool: wrote %d chars to %s", len(content), p)
            return f"文件已写入: {p} ({len(content)} 字符)"
        except Exception as e:
            logger.exception("write_file_tool failed for %s", p)
            return f"[错误] 写入失败: {e}"
```
改为（捕获闭包变量 `output_dir`，全部草稿落到 drafts/，只取 basename）：
```python
    @tool
    def write_file_tool(
        content: str,
        path: str,
    ) -> str:
        """Write content to a file. All drafts are stored under the drafts/ subdirectory.

        Use this to save the paper draft or intermediate sections.
        """
        drafts_dir = Path(output_dir).expanduser().resolve() / "drafts"
        drafts_dir.mkdir(parents=True, exist_ok=True)
        target = drafts_dir / Path(path).name  # 只取 basename：扁平化 + 防 .. 逃逸
        try:
            target.write_text(content, encoding="utf-8")
            logger.info("write_file_tool: wrote %d chars to %s", len(content), target)
            return f"文件已写入: {target} ({len(content)} 字符)"
        except Exception as e:
            logger.exception("write_file_tool failed for %s", target)
            return f"[错误] 写入失败: {e}"
```
注意：SolverAgent / VizAgent 有它们自己的 `write_file_tool`（约 406、489 行，写 `code/`、`results/`），
**不要改动**——只改 `create_paper_agent_tools` 里这一份。

**1b. `read_file_tool`（`create_paper_agent_tools` 内，约 561-567 行）**：在原路径不存在时回退 drafts。
当前：
```python
    @tool
    def read_file_tool(path: str) -> str:
        """Read a file at the given path. Returns its contents as a string.

        Use this to read Layer 1/2/3 output files, data files, or your own
        previous drafts to verify numbers, formulas, and facts.
        """
        return read_problem_file(path)
```
改为：
```python
    @tool
    def read_file_tool(path: str) -> str:
        """Read a file at the given path. Returns its contents as a string.

        Use this to read Layer 1/2/3 output files, data files, or your own
        previous drafts to verify numbers, formulas, and facts.
        """
        p = Path(path).expanduser().resolve()
        if not p.exists():
            drafts_dir = Path(output_dir).expanduser().resolve() / "drafts"
            alt = drafts_dir / Path(path).name
            if alt.exists():
                return read_problem_file(str(alt))
        return read_problem_file(path)
```

### 2. `mathmodelingagents/agents/__init__.py`（CRLF）

**2a. `_paper_read_disk`（约 553-592 行）**：候选从根目录固定名单改为扫描 `drafts/`。当前核心：
```python
    paper_candidates = ["paper.md", "PaperAgent_paper.md", "final_paper.md"]
    paper_dir = _Path(output_dir)
    paper_text = ""
    for fname in paper_candidates:
        fpath = paper_dir / fname
        if fpath.is_file():
            try:
                paper_text = fpath.read_text(encoding="utf-8")
                logger.info(
                    f"[Layer4] PaperAgent 从磁盘读取论文: "
                    f"{fpath.name} ({len(paper_text)} 字符)"
                )
                break
            except Exception as e:
                logger.warning(
                    f"[Layer4] PaperAgent 读取论文文件失败 {fpath}: {e}"
                )
```
改为扫描 `output_dir/drafts/*.md`，按文件大小降序，取第一份「含 `## 摘要` 或 `## Abstract`」：
```python
    drafts_dir = _Path(output_dir) / "drafts"
    paper_text = ""
    if drafts_dir.is_dir():
        candidates = sorted(
            drafts_dir.glob("*.md"),
            key=lambda fp: fp.stat().st_size,
            reverse=True,
        )
        for fp in candidates:
            try:
                txt = fp.read_text(encoding="utf-8")
            except Exception as e:
                logger.warning(f"[Layer4] PaperAgent 读取草稿失败 {fp}: {e}")
                continue
            if "## 摘要" in txt or "## Abstract" in txt:
                paper_text = txt
                logger.info(
                    f"[Layer4] PaperAgent 从 drafts 读取论文草稿: "
                    f"{fp.name} ({len(paper_text)} 字符)"
                )
                break
```
保持原「`if paper_text and len(paper_text) > len(final_output)` → 拼接正文」逻辑与 `## 论文正文` 分隔不变；
「未找到草稿回退文本输出」的 logger 文案改为「未在 drafts 找到论文草稿，回退文本输出」，其余措辞尽量保留。

### 3. `mathmodelingagents/reporting.py`（LF）

**3a. `_build_final_paper`（约 299-316 行）**：新增自检清单序言剥离。
在取 `raw` 后、判 `## 修改项` 防御前，插入一个 helper 调用；helper 逻辑如下（可直接内联或写成私有函数）：
```python
def _strip_selfcheck_preamble(raw: str) -> str:
    """剥离 PaperAgent 自检清单序言，只保留论文正文（兜底 REVISE 耗尽路径）。"""
    if "## SELF_CHECK_PASSED" in raw:
        # 取该标记之后的内容，再从第一个 # 级标题开始
        rest = raw.split("## SELF_CHECK_PASSED", 1)[1]
        for marker in ["## 摘要", "## Abstract", "# "]:
            idx = rest.find(marker)
            if idx != -1 and (marker == "# " or rest[idx - 1:idx].isspace() or idx == 0):
                return rest[idx:].strip()
        return rest.strip()
    # 无 SELF_CHECK_PASSED 但以自检清单开头：从论文主标题/摘要起取
    if ("全稿自审" in raw[:800] or "自审" in raw[:400]) and "## 摘要" in raw:
        idx = raw.find("## 摘要")
        # 回退到摘要前最近的 ## 或 # 标题
        head = raw[:idx]
        for m in reversed(range(len(head))):
            if head.startswith("# ", m):
                return raw[m:].strip()
        return raw[idx:].strip()
    return raw
```
`_build_final_paper` 主体改写为：
```python
    raw = state.get("final_paper", "")
    raw = _strip_selfcheck_preamble(raw)
    if not raw or "## 修改项" in raw or raw.strip().startswith("("):
        raw = state.get("visualizations", "") or raw
    for marker in ["**CONCLUDE**", "**CONTINUE**", "**REVISE**", "**ACCEPT**", "**REJECT**"]:
        raw = raw.replace(marker, "")
```
其余（title / 生成时间 / `---` / 正文）不变。注意：擦除 marker 的 `replace` 在剥离序言之后执行，
SELF_CHECK_PASSED 不在 marker 列表里，被 helper 处理，无需改列表。

### 4. `mathmodelingagents/agents/utils/prompt_templates.py`（CRLF）

**4a. `get_paper_agent_prompt`（约 685 行）**工具说明行，把
`| \`write_file(content, path)\` | 保存文件。用于保存论文草稿 |`
改为
`| \`write_file(content, path)\` | 保存草稿（自动落入 drafts/ 子目录，文件名可自拟） |`。
**4b.** 工作流程 Phase 2（约 805-816 行）「分节撰写」段，在「写」步骤后补一句：
`（草稿统一写入 drafts/ 子目录，框架会自动读取最完整的一份作为最终论文，无需自行合并到根目录。）`
——放在 Phase 2 的 6.「写」条目内或紧随其后，措辞贴合上下文即可，不新增小节。

## Acceptance Criteria（必须逐条跑通）

1. **语法检查**：`export PYTHONPATH= && .venv/Scripts/python.exe -m py_compile mathmodelingagents/tools/__init__.py mathmodelingagents/agents/__init__.py mathmodelingagents/reporting.py mathmodelingagents/agents/utils/prompt_templates.py` 无输出。
2. **新增验收脚本** `scripts/verify_paper_drafts.py`（仿 `scripts/verify_problem_strip.py` 风格：
   头部 `sys.path.insert(0, repo_root)` + `os.environ.pop("PYTHONPATH", None)`，`main() -> int` + `sys.exit(main())`，
   数值结论全部来自实际执行，不得硬编码预期数字）覆盖并断言：
   - `create_paper_agent_tools(tmp_output_dir)` 的 `write_file`：传 `path="paper.md"` 写字符串 → 断言
     `tmp_output_dir/drafts/paper.md` 存在 且 `tmp_output_dir/paper.md` **不存在**；传 `path="../escape.md"` →
     断言写到 `tmp_output_dir/drafts/escape.md`（无逃逸）。
   - 同工具的 `read_file`：写 `paper.md` 后 `invoke({"path":"paper.md"})` → 断言返回内容等于写入内容（走 drafts 回退）。
   - `_paper_read_disk(tmp_output_dir, "xx\n## SELF_CHECK_PASSED\n", [])`：在 `tmp_output_dir/drafts/` 预置
     `paper_full.md`（短、含 `## 摘要`）与 `paper.md`（长、含 `## 摘要`）→ 断言返回值含长文件正文且挑的是最长的。
   - `_build_final_paper({"final_paper": "全稿自审…✅…\n## SELF_CHECK_PASSED\n\n# 论文标题\n\n## 摘要\n…正文…"})`：
     断言以 `# 论文标题` 开头、不含 `SELF_CHECK_PASSED` 也不含 `全稿自审`。
3. **验收脚本跑通**：`export PYTHONPATH= && .venv/Scripts/python.exe scripts/verify_paper_drafts.py` 输出 `✅ 验收通过`，退出码 0。
4. **工作区范围**：`git status --short` 相比任务前**只新增** `scripts/verify_paper_drafts.py`、
   `docs/paper-drafts-sandbox-impl-brief.md` 与上述 4 个源文件的修改；无任何无关文件被格式化/改动。

## Explicitly Do NOT Do

- **不要**改 SolverAgent / VizAgent 的 `write_file_tool` / `read_file_tool`（`tools/__init__.py` 约 369-530 行区间，写 code/、results/）。
- **不要**改动 `_run_modeler_turn` / `_run_tool_loop` / `_extract_final_output` / conditional_logic.py / graph 拓扑 / `AgentState` 定义。
- **不要**动 `default_config.py`、`llm_clients/`、`.env`、`pyproject.toml`、`uv.lock`、依赖；不要 touch git。
- **不要**引入新的第三方依赖。不要改变 `final_paper.md` 之外任何现有文件名/目录名（LayerN、summary、results、code 保持不变）。
- **不要**修改 logging 既有措辞（新增/微调 `_paper_read_disk` 未找到草稿的那一条可改，其余逐字保留）。
- **不要**在 `docs/` 删除或改动其他既有 impl-brief / ADR 文件。
- **不要**提交 git；不要跑真实 LLM 请求（验收脚本全 mock / 临时目录，不发网络）。