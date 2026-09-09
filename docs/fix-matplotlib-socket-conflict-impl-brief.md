# Fix: matplotlib 加载链 vs socket 阻断冲突（impl brief）

> 状态：待执行 | 方案由 Hermes 产出，待 Joe 拍板后交 Claude Code 执行
> 本文档是给 Claude Code 执行的任务书，附可运行验收标准。

## 背景

实测（minimax-m3，`--from-layer1`，题目 `C:\Users\joeji\Desktop\what\MMA_testing\question.md`，
output_dir `C:\Users\joeji\Desktop\question_explain`）发现：VizAgent 生成的图表脚本
`plot_charts.py` / `_plot_run.py` 在**代码验证阶段**报错失败：

```
File ".../matplotlib/backend_bases.py", line 40, in <module>
    import socket
File ".../_repro.py", line 18, in _safe_import
    raise ImportError(f'Module {name} is blocked for security reasons')
ImportError: Module socket is blocked for security reasons
```

（注意：`fig1~4.png` 实际是生成的——VizAgent 在沙盒运行阶段靠 panic 后塞 `sys.modules`
假模块的 hack 勉强绕过；但**验证阶段** `_verify_layer3_code` 用 `build_preamble()` 重新拼
脚本裸跑，`_safe_import` 的 block 检查先于任何用户 hack 抛异常，必然失败。所以这是
**确定性** bug，不是模型偶发。）

**根因**：`matplotlib.pyplot` 的加载链 `pyplot → image → backend_bases` 中，
`backend_bases.py:40` 是顶层 `import socket`。而沙盒 preamble 的 `build_preamble()` 把
`socket` 列入 `_RISKY_MODULES`（blocked），`_safe_import` 对 `_root in _blocked` 一律
`raise ImportError`。两者硬冲突：**凡是需要 `matplotlib.pyplot` 的图表脚本必然失败**。

关键事实（已验证）：`backend_bases` 里对 socket 的**实际能力调用**只有
`_maybe_allow_interrupt` 上下文管理器里的 `socket.socketpair()`（第 1685 行），该路径只在
GUI 交互后端处理 SIGINT 时触发，**Agg 后端保存 PNG 不经过**。因此 socket 对 matplotlib
是「必须 import 成功、但不必真的用网络能力」的依赖。

## 设计决策

**核心**：给 `socket` 注入一个「阻断 stub」到沙盒运行时的 `sys.modules`，让顶层
`import socket` 成功（matplotlib 加载链走通），但任何真实的 socket 网络能力（`socket.socket`、
`socket.socketpair`、`connect` 等）在属性访问时**显式抛 `RuntimeError`**（安全边界不破）。

理由与取舍：
1. **不能把 socket 从 blocked 列表移除**——那会开网络后门，违背沙盒「阻断所有网络向量」的
   安全承诺。`import socket` 本身无害，但 `socket.socket().connect()` 有害；移除 = 后门。
2. **不能只对 matplotlib 放行 socket**——等同于全放行，同样是后门。
3. **stub 注入是唯一既守住安全、又满足 matplotlib 加载的方案**。stub 用
   `__getattr__` 抛 `RuntimeError`（而非静默返回 None），保证：真用到 socket 能力时**显式报错**，
   不会静默失败或绕过。

stub 的形态（**抛错版**，不是模型 hack 的 lambda 静默版）：

```python
class _BlockedSocketStub:
    def __getattr__(self, name):
        raise RuntimeError(f"socket.{name} 在沙盒中被禁用（禁止网络）")
```

放在 `build_preamble()` 生成的 preamble 里、`builtins.__import__ = _safe_import` 挂载**之前**
注入 `sys.modules["socket"]`，并在 `_safe_import` 的 block 检查**之前**增加一条「预注入 stub
模块放行」分支，使 `import socket` 命中放行、返回 stub。

## 改动清单（全部在 `mathmodelingagents/tools/__init__.py` 的 `build_preamble()`）

### 文件：`mathmodelingagents/tools/__init__.py`

`build_preamble()` 定义约在 87 行，`preamble_lines` 列表约在 119–182 行。当前相关行：

```
119  preamble_lines = [
120      "import sys",
121      "import builtins",
122      "# --- code sandbox preamble ---",
123      f"_blocked = {sorted(block_set)!r}",
124      f"_safe_allow = {sorted(allow_set)!r}",
125      f"_safe_submods = {sorted(safe_submod_set)!r}",
126      "# Snapshot of modules that were loaded before user code runs",
127      "_preloaded = set(sys.modules.keys())",
128      "_original_import = __import__",
129      "def _safe_import(name, *args, **kwargs):",
130      "    # Precise safe-submodule allow: urllib.parse is pure parsing (no network),",
131      "    # and is imported internally by matplotlib.font_manager (CJK font config).",
132      "    if name in _safe_submods:",
133      "        return _original_import(name, *args, **kwargs)",
134      "    _root = name.split('.')[0]",
135      "    # ALWAYS block risky modules — even if preloaded by the runtime",
136      "    if _root in _blocked:",
137      "        raise ImportError(f'Module {name} is blocked for security reasons')",
```

**改动 1**：在 128 行 `"_original_import = __import__",` 之后、129 行 `"def _safe_import..."`
之前，插入 socket stub 注入 + 预注入集合（约 6 行）：

```python
        "_original_import = __import__",
        "# --- socket 阻断 stub：matplotlib.backend_bases 顶层 import socket，",
        "# 但 Agg 后端保存 PNG 不经网络。注入 stub 让 import 成功、能力按属性抛错。",
        "class _BlockedSocketStub:",
        "    def __getattr__(self, name):",
        "        raise RuntimeError(f'socket.{name} 在沙盒中被禁用（禁止网络）')",
        "_preinjected = {'socket', '_socket'}",
        "sys.modules['socket'] = _BlockedSocketStub()",
        "sys.modules['_socket'] = _BlockedSocketStub()",
        "def _safe_import(name, *args, **kwargs):",
```

**改动 2**：在 `_safe_import` 里，把「预注入 stub 放行」加到 `_safe_submods` 检查之后、
`_root` block 检查之前。即把当前：

```python
        "    if name in _safe_submods:",
        "        return _original_import(name, *args, **kwargs)",
        "    _root = name.split('.')[0]",
```

改为：

```python
        "    if name in _safe_submods:",
        "        return _original_import(name, *args, **kwargs)",
        "    # 预注入的阻断 stub（socket）直接放行：matplotlib 加载链需 import socket 成功，",
        "    # 真实网络能力由 stub 在属性访问时抛错阻断。",
        "    if name in _preinjected:",
        "        return _original_import(name, *args, **kwargs)",
        "    _root = name.split('.')[0]",
```

> 说明：`_original_import("socket")` 会先命中 `sys.modules["socket"]` 已缓存的 stub 返回，
> 所以这里不用手动 `return sys.modules[name]`，走 `_original_import` 即可保持与 Python import
> 语义一致。

**注意**：`_preinjected` 这个名字与第 127 行已有的 `_preloaded`（模块加载快照）含义不同，
不要改 127 行。新增的 `_preinjected` 是「被 stub 预注入的 blocked 模块名集合」。

### 不改动
- `_RISKY_MODULES` 集合本身（socket 仍留在 blocked 里，语义不变——只是多了 stub 放行分支）。
- `run_code` / `_exec_script` / `create_*_tools` 其他任何部分。
- `build_preamble` 的函数签名与 docstring（可选：在 docstring 补一句 stub 说明，非必须）。

## 验收标准

1. `py_compile` `mathmodelingagents/tools/__init__.py` 无错误。
2. `import mathmodelingagents.tools` 冒烟成功。
3. **核心探针**（临时脚本，均断言 exit_code）：
   a. **放行**：`build_preamble() + "\nimport matplotlib.pyplot as plt\nimport matplotlib\nmatplotlib.use('Agg', force=True)\nprint('MPL_OK')"` 经 `run_code`（或等价 subprocess）执行，`exit_code == 0` 且 stdout 含 `MPL_OK`。
   b. **阻断**：`build_preamble() + "\nimport socket\nsocket.socket()\n"` 执行后**非 0 退出**，且 stderr 含 `RuntimeError`（或 `socket.socket 在沙盒中被禁用`），证明网络能力仍被阻断。
   c. **对照组**：`build_preamble() + "\nimport requests\n"` 仍抛 `ImportError: Module requests is blocked`（保证 block 机制未被误伤）。
4. **验证阶段复现修复**：用当前 `C:\Users\joeji\Desktop\question_explain\code\plot_charts.py`（若存在）跑一遍 `_verify_layer3_code` 的等价流程——即 `build_preamble() + "import matplotlib as _mpl\n_mpl.use('Agg', force=True)\n" + 脚本源码` 经 subprocess 执行，断言 `plot_charts` 不再因 socket blocked 失败（exit_code 变为 0）。
5. `git diff --stat` 仅涉及 `mathmodelingagents/tools/__init__.py` 一个文件。

## Do NOT

- 不从 `_RISKY_MODULES` 移除 `socket`（会开网络后门）。
- 不把 stub 做成「静默返回 None/lambda」的形式（会静默失败、掩盖真实网络尝试）。
- 不改 `run_code` / `_exec_script` / `create_langchain_tools` / `create_coding_agent_tools` /
  `create_paper_agent_tools`。
- 不改 `main.py` 的 `_verify_layer3_code`（验证逻辑本身正确，修的是 preamble 冲突）。
- 不改任何 prompt（本轮不解决「模型写 socket hack」的 prompt 层问题，那是后续可选优化）。
- 不新增/修改 `pyproject.toml`、`.env`、依赖。
- 不新增依赖包（不引入任何新的第三方库来处理 socket）。

## 备注（可选后续，本次不做）

- prompt 层：VizAgent/Solver prompt 已明文「禁止沙盒内部补丁」，但模型仍会 panic 后写
  `sys.modules["socket"] = _FakeMod()` 这类 hack。修完工具层后，这类 hack 会因 stub 放行而
  「碰巧不再报错」，但仍是脏代码。若要根治，可在后续迭代给 prompt 加一句明确指引（如
  「matplotlib 已由框架处理好，直接 import 即可，不要写 socket 补丁」）。
- 若未来有其他 blocked 模块出现「顶层 import 但能力可阉割」的依赖（如某库顶层 import ctypes
  仅用于类型标注），可把本方案泛化为「可配置 stub 表」，当前硬编码 socket 已足够。