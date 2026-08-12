# 实施任务书：Layer 4 论文引用 URL 验证（check_url 工具）

> Hermes 架构，Claude Code 执行。完成后不要删除本文档。

## 背景与目标

Layer 1 现在可联网搜索（背景资料含真实搜索 URL），但 Layer 4 PaperAgent 写参考文献时**只能凭记忆或从文件抄**，没有任何 URL 真实性验证（现状只有"每条含完整 URL"的格式检查）。论文里可能混入幻觉 URL。
目标：给 PaperAgent 增加 `check_url` 工具，**写参考文献前先验证 URL 真伪，只把验证通过的 URL 写入论文**。

## 改动清单

### 1. `mathmodelingagents/tools/web_search.py` — 新增 `check_url()` 函数

在 `web_search()` 之后新增（与搜索模块同属网络工具，复用其异常吞噬风格）：

```python
def check_url(url: str) -> str:
    """Verify a URL is reachable. Returns a short verdict string (never raises).

    Verdicts:
      ✅ 可达 (HTTP xxx)              — 2xx/3xx
      ⚠️ 疑似存在但被拒绝访问 (HTTP xxx) — 401/403（反爬常见，不算失效）
      ⚠️ 服务器临时错误 (HTTP xxx)     — 5xx
      ❌ 失效 (HTTP xxx)              — 4xx（除 401/403）
      ❌ 无法连接 — <原因>             — 超时 / DNS / 连接失败
    """
```

实现要求：
- 标准库 `urllib.request`（零新依赖，本模块已遵循此原则）
- `Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})`，`urlopen(req, timeout=8)`，**跟随重定向**
- 捕获 `HTTPError`（区分状态码）、`URLError`/`TimeoutError`/`socket.timeout` 等
- 除 `HTTPError` 外任何异常 → `❌ 无法连接 — <截断的错误信息>`（复用 `_truncate_error`，≤200 字符）
- 返回单行短文本（LLM 工具结果要简短）

### 2. `mathmodelingagents/tools/__init__.py` — `create_paper_agent_tools` 加工具

在 `create_paper_agent_tools()` 内新增：

```python
    @tool
    def check_url_tool(url: str) -> str:
        """Verify a reference URL is reachable. Returns: ✅ 可达 / ⚠️ 反爬或临时错误 / ❌ 失效或无法连接."""
        from mathmodelingagents.tools.web_search import check_url
        return check_url(url)
```

并加入函数末尾的返回列表（与 read_file/list_dir/write_file 并列；**不要**加到 `create_coding_agent_tools`，那是 Solver/Viz 的工具集，本次不动）。

### 3. `mathmodelingagents/agents/utils/prompt_templates.py` — `get_paper_agent_prompt()` 三处更新

**a. 工具表**（当前 679-683 行附近，3 行工具）追加一行：

```
| `check_url(url)` | 验证参考文献 URL 是否真实可达：`✅ 可达` / `⚠️ 反爬或临时错误` / `❌ 失效`。只把 ✅ 或 ⚠️ 的 URL 写入参考文献，❌ 必须删除或替换 |
```

**b. 核心铁律**（当前 7 条，685-694 行）追加第 8 条：

```
8. **参考文献必须可验证**：论文 `## 参考文献` 中的每条 URL 必须先调用 `check_url` 验证。`❌` 的 URL 一律不得写入——删除或替换为已验证的；宁可参考文献少而真，不可多而假。Layer 1 背景资料和 Layer 2 输出中引用的 URL 同样需要验证后才能采用。
```

**c. 全稿自审硬性检查清单**（当前 747 行附近 `- [ ] 参考文献每条含完整 URL`）改为：

```
- [ ] 参考文献每条含完整 URL，且已通过 check_url 验证（无 ❌ 项）
```

### 4. 新增 `tests/test_check_url.py`

用 `unittest.mock.patch("urllib.request.urlopen", ...)` 测 `check_url`（不发起真实网络请求）：
- 200 → 包含 `✅ 可达`
- 404 → 包含 `❌ 失效`
- 403 → 包含 `⚠️`（不算失效）
- 5xx → 包含 `⚠️`
- 超时异常 → 包含 `❌ 无法连接`，不抛异常
- 任意异常 → 不抛异常，返回 `❌` 前缀文本

另加一个断言：`create_paper_agent_tools(".")` 返回的工具名列表包含 `check_url`（用 `[t.name for t in create_paper_agent_tools(".")]`）。

### 5. `README.md` — Web 搜索特性区块（低优先）

在"Layer 3 的 SolverAgent 可自主调用 `web_search`..."之后补一句：
"Layer 4 的 PaperAgent 写参考文献前会用 `check_url` 验证每条 URL 的真实可达性，失效引用自动剔除。"

## 验收标准（完成后自测并报告）

1. `python -m pytest tests/ -v` 全绿（原 20 + 新增）
2. `python -c "from mathmodelingagents.tools import create_paper_agent_tools; print([t.name for t in create_paper_agent_tools('.')])"` 输出含 `check_url`
3. `python -c "from mathmodelingagents.tools.web_search import check_url; print(check_url('https://doi.org/10.1214/aoms/1177730197'))"` 真实输出 ✅ 或 ⚠️（不崩溃）
4. `ruff check` 相关文件无**新增**警告
5. 报告每个文件的 diff 摘要

## 明确不做

- 不给 PaperAgent 加 `run_code`（保持只读性质）
- 不改 `create_coding_agent_tools`（Solver/Viz 工具集）
- 不改 Layer 2 / Layer 3 逻辑
- 不提交 git、不推送
