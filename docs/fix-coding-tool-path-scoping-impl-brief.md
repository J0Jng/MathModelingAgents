# Fix: Layer 3 文件工具路径作用域错位（impl brief）

> 状态：待执行 | 方案由 Hermes 产出，待 Joe 拍板后交 Claude Code 执行
> 本文档是给 Claude Code 执行的任务书，附可运行验收标准。

## 背景

实测（`--from-layer1`，题目 `C:\Users\joeji\Desktop\what\MMA_testing\question.md`，
output_dir `C:\Users\joeji\Desktop\question_explain`）发现：一道「3 子问题、就是
N×μ / 除法 / 乘法」的极简题，SolverAgent 花了 19 轮、VizAgent 花了 9 轮仍接近耗尽上限，
其中约一半轮次是**路径迷路**。

日志铁证（同一次运行）：

```
[Layer3] SolverAgent 第 4 轮  write_file_tool → 文件已写入: F:\code\projects\MathModelingAgents\code\solve_q1.py
[Layer3] SolverAgent 第 5 轮  run_code_tool  → CWD: C:\Users\joeji\Desktop\question_explain\code
[Layer3] SolverAgent 第 1 轮  list_dir_tool  → .claude/ .env .git/ ...   ← 仓库根，不是题目目录
[Layer3] VizAgent    第 4 轮  read_file_tool → [错误] 文件不存在: F:\...\MathModelingAgents\results\results.json
[Layer3] VizAgent    第 5 轮  read_file_tool → 才读到正确的 results.json
```

**根因**：`create_coding_agent_tools()` 里，只有 `run_code_tool` 被 scoped 到
`output_dir/code`（`run_code(code, timeout, cwd=work_dir)`），而 `read_file_tool` /
`write_file_tool` / `list_dir_tool` 三个文件工具全部裸用 `Path(path).expanduser().resolve()`，
相对路径落到**进程 CWD（仓库根目录）**，与 run_code 的执行目录（题目目录）完全背离。

结果：模型「写文件写去 A 处、执行在 B 处、读取去 C 处」，三处对不上，只能靠反复
`list_dir` / 写探针 / `sys.executable` 猜路径找回同步——这是轮次黑洞，不是模型太弱
（deepseek-v4-flash 只是放大器，根因是作用域错位）。

## 设计决策

1. **统一坐标系**：`create_coding_agent_tools` 内的 `read_file_tool` / `write_file_tool` /
   `list_dir_tool` 三者的**相对路径基准改为 `output_dir`**（与 `run_code_tool` 的
   `cwd=output_dir/code` 语义一致）。绝对路径照常放行（模型偶尔传绝对路径，保持兼容）。
2. **不改 run_code 沙盒**：run_code 的 cwd 本来就是 `output_dir/code`，是正确的；本次不碰。
3. **不新增逃逸防护**：与现状一致的安全边界（paper 工具的「只取 basename 扁平化」不适用，
   coding 工具需要写 `code/` `results/` 子目录）。本次聚焦「坐标系一致」，不引入新安全边界，
   避免 scope 蔓延。
4. **不改 prompt / graph / config / 降级链**：Viz prompt 已在 `_build_context` 给了
   `results.json: {output_dir}/results/results.json` 绝对路径；Solver prompt 里的
   `code/solve_qN.py`（相对 output_dir）与 `../results/xxx.json`（run_code 内部 cwd 语义）
   均不受影响。本次不动 prompt。

## 改动清单（全部在 `mathmodelingagents/tools/__init__.py` 的 `create_coding_agent_tools`）

### 文件：`mathmodelingagents/tools/__init__.py`

`create_coding_agent_tools()` 定义约在 443 行。当前结构：

- 465–468 行：`work_dir` / `results_dir` 构建，`Path(output_dir).resolve()`
- 470–477 行：`read_file_tool`（裸 resolve，**要改**）
- 479–495 行：`run_code_tool`（**不动**）
- 497–515 行：`write_file_tool`（裸 resolve，**要改**）
- 517–539 行：`list_dir_tool`（默认 `.` 裸 resolve，**要改**）
- 541–544 行：`web_search_tool`（**不动**）
- 546 行：`return [...]`

**改动 1**：在 `create_coding_agent_tools` 函数体开头（`work_dir = ...` 之前）新增一个
统一的路径决议 helper（闭包，捕获 `output_dir`）：

```python
    out_root = Path(output_dir).expanduser().resolve()

    def _resolve(p: str) -> Path:
        """相对路径统一相对 output_dir；绝对路径照常。与 run_code 的 cwd=output_dir/code 对齐。"""
        pp = Path(p).expanduser()
        if not pp.is_absolute():
            pp = out_root / pp
        return pp.resolve()
```

并把现有的 `work_dir` / `results_dir` 改用 `out_root` 表达（等价，纯重构，行为不变）：

```python
    work_dir = str(out_root / "code")
    results_dir = str(out_root / "results")
```

**改动 2**：`read_file_tool`（470–477 行）——把 `read_problem_file(path)` 前加路径决议。
当前：

```python
    @tool
    def read_file_tool(path: str) -> str:
        """Read a file at the given path. Returns its contents as a string.

        Use this to read data files, Layer 2 model output, or code you've
        previously saved.
        """
        return read_problem_file(path)
```

改为：

```python
    @tool
    def read_file_tool(path: str) -> str:
        """Read a file at the given path. Returns its contents as a string.

        Use this to read data files, Layer 2 model output, or code you've
        previously saved.

        相对路径相对于输出目录（output_dir），绝对路径照常。
        """
        return read_problem_file(str(_resolve(path)))
```

**改动 3**：`write_file_tool`（497–515 行）——把 `p = Path(path).expanduser().resolve()`
替换为 `p = _resolve(path)`。当前：

```python
    @tool
    def write_file_tool(
        content: str,
        path: str,
    ) -> str:
        """Write content to a file. Parent directories are created as needed.

        Use this to save final Python scripts, JSON results, or other
        artifacts you want to keep.
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

改为（仅替换 `p = ...` 一行 + docstring 加一句相对路径说明）：

```python
    @tool
    def write_file_tool(
        content: str,
        path: str,
    ) -> str:
        """Write content to a file. Parent directories are created as needed.

        Use this to save final Python scripts, JSON results, or other
        artifacts you want to keep.

        相对路径相对于输出目录（output_dir），绝对路径照常。
        """
        p = _resolve(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        try:
            p.write_text(content, encoding="utf-8")
            logger.info("write_file_tool: wrote %d chars to %s", len(content), p)
            return f"文件已写入: {p} ({len(content)} 字符)"
        except Exception as e:
            logger.exception("write_file_tool failed for %s", p)
            return f"[错误] 写入失败: {e}"
```

**改动 4**：`list_dir_tool`（517–539 行）——把 `p = Path(path).expanduser().resolve()`
替换为 `p = _resolve(path)`，并更新 docstring。当前：

```python
    @tool
    def list_dir_tool(
        path: str = ".",
    ) -> str:
        """List files in a directory. Returns a newline-separated list.

        Use this to check what files exist in the code or results directories
        before reading them or to verify files were created.
        """
        p = Path(path).expanduser().resolve()
        ...
```

改为：

```python
    @tool
    def list_dir_tool(
        path: str = ".",
    ) -> str:
        """List files in a directory. Returns a newline-separated list.

        Use this to check what files exist in the code or results directories
        before reading them or to verify files were created.

        相对路径相对于输出目录（output_dir），默认 "." 即列出输出目录内容，绝对路径照常。
        """
        p = _resolve(path)
        ...
```

（`...` 部分的其余逻辑 `p.exists()` / `p.is_dir()` / `sorted(p.iterdir())` 保持不变。）

## 验收标准

1. `py_compile` `mathmodelingagents/tools/__init__.py` 无错误。
2. `import mathmodelingagents.tools` 冒烟成功。
3. **路径作用域单元验证**（写一个探针脚本，或直接 `python -c` 断言），核心断言：
   - 用临时目录 `tmp = mkdtemp()` 调 `create_coding_agent_tools(tmp)`；
   - `read_file_tool.invoke("results/results.json")` 命中 `tmp/results/results.json`，
     而不是进程 cwd 下的 `results/results.json`；
   - `write_file_tool.invoke("code/x.py", ...)` 落盘到 `tmp/code/x.py`；
   - `list_dir_tool.invoke(".")` 返回的是 `tmp` 的内容（含 `code/`、`results/`），
     而非仓库根目录的 `.claude/ .env .git/`。
4. `git diff --stat` 仅涉及 `mathmodelingagents/tools/__init__.py` 一个文件。
5. 回归：现有工具相关测试仍通过（如有 `scripts/verify_*.py` 涉及 coding tools，跑一遍）。

## Do NOT

- 不碰 `run_code` / `build_preamble` / `_exec_script`（沙盒逻辑）。
- 不碰 `create_langchain_tools()`（Layer 2 用）与 `create_paper_agent_tools()`（Layer 4 已 scoped）。
- 不碰 `web_search_tool`。
- 不在 `write_file_tool` 引入 basename 扁平化或强制「必须落在 output_dir 内」的沙箱（保持现有安全边界）。
- 不改 `mathmodelingagents/agents/__init__.py`、`graph/*`、`default_config.py`、任何 prompt。
- 不新增/修改 `pyproject.toml`、`.env`、依赖。
- 不改 `main.py`、CLI 参数。

## 备注（可选后续，本次不做）

修完工具作用域后，若仍发现 Solver/Viz prompt 里的路径描述与工具语义存在歧义
（如 `../results/` 在 write/read 工具语境下的解读），可在 `_build_context` 或 prompt
中补一句「文件工具的相对路径相对于输出目录」。本次先聚焦 root cause 的工具层修复。