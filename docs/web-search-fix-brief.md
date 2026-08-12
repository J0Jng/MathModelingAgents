# 修复任务书：web_search 代码审查发现的问题（第 2 轮）

> 由 Hermes 审查后发现的 4 处问题 + 1 项验证任务。完成后不要删除本文档。

## 背景

Claude Code 已完成第一轮实施（见 docs/web-search-impl-brief.md）。Hermes 审查后发现以下问题，请逐一修复。

## 修复项

### 1. `mathmodelingagents/tools/web_search.py` — ddgs region 笔误（bug）

第 158 行：`region="wt-t"` 是无效值（会导致 DDG 报错或空结果），正确值是 **`"wt-wt"`**（全球）。

```python
# 修改前
raw = list(ddgs.text(query, max_results=max_results, region="wt-t", safesearch="off"))
# 修改后
raw = list(ddgs.text(query, max_results=max_results, region="wt-wt", safesearch="off"))
```

### 2. `mathmodelingagents/tools/web_search.py` — Tavily 认证方式过时（bug）

当前 `_search_tavily`（httpx 版，约 89-116 行）和 `_search_tavily_urllib`（约 119-137 行）都在 **body 里传 `api_key`**。Tavily 官方现行认证是 **`Authorization: Bearer <key>` header**（body 只留 query/max_results/search_depth）。

两处都改为：

```python
headers = {"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"}
```

- httpx 版：`httpx.post("https://api.tavily.com/search", content=body, headers=headers, timeout=_TIMEOUT)`，body **不再包含** api_key
- urllib 版：`urllib.request.Request(url, data=body, headers=headers)`，同样去掉 body 中的 api_key
- 模块 docstring 中若有 api_key 说明，同步更新

### 3. `mathmodelingagents/agents/utils/prompt_templates.py` — Decomposer prompt 重复行

约第 17-18 行出现重复（替换后叠加了原有行）：

```
✅ 通读上下文中的题目全文（已注入），提取核心目标
✅ 提取核心目标：题目最终要你回答什么
```

删除第 17 行（`✅ 通读上下文中的题目全文（已注入），提取核心目标`），只保留原有的 `✅ 提取核心目标：题目最终要你回答什么`。

### 4. `mathmodelingagents/agents/__init__.py` — f-string logger（ruff W1203）

约第 577 行（create_decomposer 的预搜索异常处理）：

```python
# 修改前（触发 ruff W1203）
logger.info(f"[Layer1] Decomposer 背景搜索跳过: {e}")
# 修改后
logger.info("[Layer1] Decomposer 背景搜索跳过: %s", e)
```

### 5. 安装 ddgs 并真实验证 ddgs 搜索路径

```bash
python -m pip install duckduckgo-search
```

安装后运行：

```bash
python scripts/verify_web_search.py
```

预期：当前无 TAVILY_API_KEY → provider 为 ddgs → 真实返回搜索结果（region 修复后应能成功）。把真实输出前几行贴进汇报。

## 验收标准

1. `python -m pytest tests/ -v` 仍全绿（14 passed）
2. `python scripts/verify_web_search.py` 真实输出 ddgs 搜索结果（标题 + URL + 摘要）
3. `python -c "from mathmodelingagents.tools import web_search; print(web_search('test', max_results=2))"` 输出真实结果
4. `ruff check mathmodelingagents/tools/web_search.py mathmodelingagents/agents/__init__.py mathmodelingagents/agents/utils/prompt_templates.py` 无**新增**警告
5. 若 verify 脚本失败，报告真实错误信息（不要假装成功）

## 明确不做

- 不改动本轮修复清单之外的任何代码
- 不提交 git、不推送
