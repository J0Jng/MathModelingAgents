# Implementation Brief: 修复 _strip_selfcheck_preamble 在无主标题场景的截断 bug

## Background

上一轮（`paper-drafts-sandbox-impl-brief.md`）验收复核发现：`reporting.py:_strip_selfcheck_preamble`
第一分支用 `marker.find` 顺序匹配 `["# ", "## 摘要", "## Abstract"]`，其中 `# ` 会**误命中 `## 摘要` 内部的
`# `**（`## 摘要` = `#`+`#`+` `，第二个 `#` 后紧跟空格）。在「论文正文无主标题、直接以 `## 摘要` 开头」时
（这正是 `prompt_templates.py` 规定的标准输出格式 `## SELF_CHECK_PASSED` → `## 摘要`），返回结果把
`## 摘要` 截断成 `# 摘要`（丢一个 `#`）。

实测复现（已跑）：
```
输入  "## SELF_CHECK_PASSED\n\n## 摘要\n…正文…"
输出  首行 = "# 摘要"   ← 应为 "## 摘要"
```

上一版验收脚本测试输入恰好是「有主标题」场景（`# 论文标题` 在 `## 摘要` 前），未覆盖无标题场景，
故未暴露此 bug。

## Design

第一分支改用**按行切片**：把 `## SELF_CHECK_PASSED` 之后的内容按 `\n` 切行，取「第一个非空且以 `#` 开头
的行」作为论文正文起点（`#` 一级标题、`## 摘要`、`### 子节`都匹配），从该行到结尾拼回并 strip。
此方案对「有主标题」「无主标题」两种形态都健壮，不存在 `# ` 误命中 `##` 的问题。

第二分支（无 `SELF_CHECK_PASSED`、自检清单开头）保持上一版逐字不变——它用 `head.startswith("# ", m)`
从后往前找 `# `，且 `## 摘要` 内的 `# ` 不会出现「前面的 `# ` 被 reverse 遍历先选中」的问题（reverse 遍历
的是 `head = raw[:idx]`，`idx` 是 `## 摘要` 位置，`head` 不含 `## 摘要`），无需改动。

## Change List

> ⚠️ `reporting.py` 保持 LF 行尾，编辑用最小 patch（唯一命中断言）。

### 1. `mathmodelingagents/reporting.py`（LF）

替换 `_strip_selfcheck_preamble` 的第一分支（当前是 `for marker in ["# ", "## 摘要", "## Abstract"]` 那段循环）：

当前（上一版落盘后）：
```python
    if "## SELF_CHECK_PASSED" in raw:
        # 取该标记之后的内容，再从第一个 # 级标题开始
        rest = raw.split("## SELF_CHECK_PASSED", 1)[1]
        for marker in ["# ", "## 摘要", "## Abstract"]:
            idx = rest.find(marker)
            if idx != -1 and (marker == "# " or rest[idx - 1:idx].isspace() or idx == 0):
                return rest[idx:].strip()
        return rest.strip()
```
改为：
```python
    if "## SELF_CHECK_PASSED" in raw:
        # 取该标记之后的内容，再定位论文正文起点（第一个非空且以 # 开头的行）。
        # 按行切片，健壮处理「有主标题 # xxx」与「无主标题直接 ## 摘要」两种形态，
        # 避免用 find 匹配 "# " 误命中 "## 摘要" 导致截断。
        rest = raw.split("## SELF_CHECK_PASSED", 1)[1]
        lines = rest.split("\n")
        for i, ln in enumerate(lines):
            if ln.strip() and ln.strip().startswith("#"):
                return "\n".join(lines[i:]).strip()
        return rest.strip()
```
第二分支及函数其余部分不变。

### 2. `scripts/verify_paper_drafts.py`

追加一条「无主标题」用例（加在现有 `_build_final_paper` 去污染断言之后）：
```python
    # 无主标题：正文直接以 ## 摘要 开头（prompt 规定的标准输出格式），不得截断成 "# 摘要"
    raw_no_title = (
        "全稿自审…✅ 检查项通过\n\n## SELF_CHECK_PASSED\n\n"
        "## 摘要\n\n这是论文摘要正文\n\n## 1. 问题重述\n\n正文……\n"
    )
    final_no_title = _build_final_paper({"final_paper": raw_no_title}, "")
    head_no_title = final_no_title.lstrip("# \n")  # 略
```
断言（写清楚，数值来自实际执行）：
- `final_no_title` 以 `## 摘要` 开头（首行 == `## 摘要`），**不**以 `# 摘要` 开头；
- 不含 `SELF_CHECK_PASSED`、不含 `全稿自审`。

（具体断言方式由实现者自拟，但必须覆盖「首行为 `## 摘要` 而非 `# 摘要`」这一点。）

## Acceptance Criteria（必须逐条跑通）

1. 语法检查：`export PYTHONPATH= && .venv/Scripts/python.exe -m py_compile mathmodelingagents/reporting.py scripts/verify_paper_drafts.py` 无输出。
2. 验收脚本：`export PYTHONPATH= && .venv/Scripts/python.exe scripts/verify_paper_drafts.py` 输出 `✅ 验收通过`、退出码 0，且新增的「无主标题」用例通过。
3. 回归确认：`_build_final_paper` 处理「有主标题」场景仍以 `# 标题` 开头（原有用例不回归）。
4. `git status --short` 范围仍只有上一轮的 6 项（4 源文件 + brief + 验收脚本），本次只改 `reporting.py` 与 `scripts/verify_paper_drafts.py`，不新增其他文件。

## Explicitly Do NOT Do

- **不要**动第二分支（无 `SELF_CHECK_PASSED` 的 `head.startswith("# ", m)` 逆序遍历逻辑），保持逐字不变。
- **不要**改 `tools/__init__.py`、`agents/__init__.py`、`prompt_templates.py` 以及验收脚本里已有的其他断言。
- **不要**改依赖、不要提交 git、不要跑真实 LLM 请求。