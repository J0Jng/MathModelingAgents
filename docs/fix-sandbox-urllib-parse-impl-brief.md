# Implementation Brief: 修复沙盒误拦 urllib.parse 导致字体配置失败

## Background

沙盒 preamble（`mathmodelingagents/tools/__init__.py` 的 `build_preamble`）里的中文字体自动配置
（第 128-160 行）每次都会打印：

```
[sandbox] ⚠️ 字体配置异常: Module urllib.parse is blocked for security reasons
```

**根因（已用最小实验坐实，勿再推断）**：

1. `_RISKY_MODULES`（第 70-76 行）把根模块 `"urllib"` 列入 blocklist（安全边界：目的是拦网络）。
2. `_safe_import`（preamble 字符串里第 114-118 行）按 `_root = name.split('.')[0]` 前缀匹配，
   `urllib` 的任何子模块都命中 block。
3. 但 `matplotlib.font_manager` 是标准库里**纯字符串解析**的 `urllib.parse` 的合法消费者——它在内部
   `import urllib.parse`（用于解析 URL 形式的字体路径），本身零网络能力。
4. preamble 挂上 `_safe_import` 之后才 `import matplotlib.font_manager`，于是 `urllib.parse` 被拦，
   字体配置 try/except 吞掉 ImportError，打印「字体配置异常」，中文字体激活逻辑从未真正执行。

**验证结论**：`urllib.parse` 是纯解析模块（URL 解析/quote/unquote），不含 socket/http；
真正能联网的是 `urllib.request` / `urllib.error` / `urllib.robotparser`。当前按根前缀拦截误伤了
安全的 `urllib.parse`。

## 设计决策

1. **精确子模块豁免**（不放松网络边界）：保留 `_RISKY_MODULES` 里的 `"urllib"` 根**不变**，
   新增一个「精确完整模块名」的豁免集合，默认值 `{"urllib.parse"}`。
2. 豁免判断放在 `_safe_import` 里**根前缀检查之前**：仅当 `name == "urllib.parse"`（完整名精确
   匹配）才放行。这样 `import urllib.request`、`import socket`、`import requests`、`import urllib`
   （裸根）仍全部被拦，网络边界不动。
3. 保持 `build_preamble` 的「策略作为数据注入」纯函数架构：新增常量 + 新增可选参数注入字符串，
   以便单元测试隔离。

## 改动清单（只动 tools/__init__.py 一处 + 测试）

### A. `mathmodelingagents/tools/__init__.py`

1. **`_RISKY_MODULES` 下方（约第 77 行后）新增模块常量**：
   ```python
   # Safe submodules that live under a blocked root yet provide no network/unsafe
   # capability. urllib.parse is pure URL/string parsing (no sockets) and is
   # imported internally by matplotlib.font_manager — blocking it spuriously
   # breaks the CJK font auto-config. The root "urllib" stays blocked.
   _SAFE_SUBMODULES = {
       "urllib.parse",
   }
   ```

2. **`build_preamble` 签名**（第 79-82 行）增加一个可选参数 `safe_submodules`，默认
   `_SAFE_SUBMODULES`：
   ```python
   def build_preamble(
       blocked_modules: list[str] | set[str] | None = None,
       allowed_modules: list[str] | None = None,
       safe_submodules: list[str] | set[str] | None = None,
   ) -> str:
   ```
   并在 docstring 里补一句说明该参数含义。

3. **函数体内**（第 102-103 行后）：
   ```python
   block_set = set(_RISKY_MODULES if blocked_modules is None else blocked_modules)
   allow_set = set(allowed_modules or [])
   safe_submod_set = set(_SAFE_SUBMODULES if safe_submodules is None else safe_submodules)
   ```

4. **preamble_lines 注入新变量**（第 110 行 `_safe_allow` 之后加一行）：
   ```python
   f"_safe_submods = {sorted(safe_submod_set)!r}",
   ```

5. **`_safe_import` 定义（第 114 行起那段字符串）在 `_root = ...` 之前插入精确豁免**：
   现为：
   ```python
   "def _safe_import(name, *args, **kwargs):",
   "    _root = name.split('.')[0]",
   "    # ALWAYS block risky modules — even if preloaded by the runtime",
   "    if _root in _blocked:",
   "        raise ImportError(f'Module {name} is blocked for security reasons')",
   ```
   改为在函数首行与 `_root` 之间插入：
   ```python
   "def _safe_import(name, *args, **kwargs):",
   "    # Precise safe-submodule allow: urllib.parse is pure parsing (no network),",
   "    # and is imported internally by matplotlib.font_manager (CJK font config).",
   "    if name in _safe_submods:",
   "        return _original_import(name, *args, **kwargs)",
   "    _root = name.split('.')[0]",
   "    # ALWAYS block risky modules — even if preloaded by the runtime",
   "    if _root in _blocked:",
   "        raise ImportError(f'Module {name} is blocked for security reasons')",
   ```

**注意**：`_safe_import` 引用了 `_original_import`，而 `_original_import = __import__` 在
`_safe_import` 定义**之前**（第 113 行）已就位，运行时在 `_safe_import` 被调用时它已是模块级变量，
所以此处 `return _original_import(...)` 与下方第 124/125 行现有写法一致，无问题。

### B. `tests/test_build_preamble.py`（或在其旁新增 `tests/test_sandbox_safe_submodules.py`）

现有测试全是纯字符串断言。请**新增子进程级端到端测试**（真实 spawn 子进程验证行为，不用 mock）：

```python
import subprocess, sys
from mathmodelingagents.tools import build_preamble

def _run(payload: str):
    full = build_preamble() + "\n" + payload
    return subprocess.run(
        [sys.executable, "-c", full],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )

def test_urllib_parse_allowed():
    r = _run("import urllib.parse\nprint('OK', urllib.parse.urlparse('http://x/y').netloc)\n")
    assert r.returncode == 0, r.stderr
    assert "OK x" in r.stdout
    assert "字体配置异常" not in r.stdout  # 字体配置不再被 urllib.parse 打断

def test_urlib_request_still_blocked():
    r = _run("import urllib.request\n")
    assert r.returncode != 0
    assert "blocked for security reasons" in r.stderr

def test_socket_still_blocked():
    r = _run("import socket\n")
    assert r.returncode != 0

def test_safe_submods_injected():
    assert "_safe_submods" in build_preamble()
    assert "urllib.parse" in build_preamble()
```

若选择在 `test_build_preamble.py` 内追加，保持文件顶部 docstring 的风格一致；若新建文件，用
简短中文 docstring 说明目的。

## 验收标准（必须真跑，不能只看静态）

1. 导入冒烟：`export PYTHONPATH=` 后 `.venv/Scripts/python.exe -c "from mathmodelingagents.tools import build_preamble"` 成功。
2. 子进程端到端（Claude 自己跑，贴原文）：
   - `.venv/Scripts/python.exe` 跑 `build_preamble() + import urllib.parse` → returncode 0，且 stdout
     **不再出现**「字体配置异常」（可能变成「中文字体已激活」或「未检测到中文字体」，取决于机器字体，
     什么字体提示都行，关键是**不再是「字体配置异常」**）。
   - `.venv/Scripts/python.exe` 跑 `build_preamble() + import urllib.request` → returncode != 0，被拦。
3. 全量 `.venv/Scripts/python.exe -m pytest tests/ -q` 全绿（历史全量 + 新增）。

## 明确 Do NOT

- **不要**从 `_RISKY_MODULES` 删除或改动 `"urllib"` 根（网络边界必须保持）。
- **不要**放行 `urllib.request` / `urllib.error` / `urllib.robotparser` / `socket` / `requests` / `http`。
- **不要**动 `run_code` / `_exec_script` / `_verify_layer3_code` / modeler / RAG / CLI。
- **不要**动 `pyproject.toml` / 依赖 / lock。
- **不要**顺手「修复」`_RISKY_MODULES` 与第 179 行 docstring（那句写 `os, subprocess` 但集合里没有）
  的不一致——那是另一个独立问题，本次不碰，仅在完成报告中提一句即可。
- 跑测试一律 `export PYTHONPATH=` 前缀 + `.venv/Scripts/python.exe`。

## 报告格式

完成后报告：`tools/__init__.py` 与测试的 git diff 摘要、你新增测试文件名与断言、以及你**自己跑**
`.venv/Scripts/python.exe -m pytest tests/ -q` 的最终结果原文（含通过/失败计数）、以及上面两条
子进程端到端命令的 stdout/stderr 原文。