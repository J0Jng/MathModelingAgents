# Implementation Brief: 修复代码验证器 `__file__` 假阴性

## Background

`main.py` 的 `_verify_layer3_code()`（约第 20 行起）负责在 Layer 3 产出 code/ 目录后，
逐个执行其中的 Python 脚本做冒烟验证，生成 `CODE_VERIFICATION.md`。

**Bug**：路径 A（code/ 目录存在时）用 `python -c "<preamble+源码>"` 方式执行（第 70-74 行）。
Python 官方行为是——只有真正执行脚本**文件**时才注入内置变量 `__file__`，`-c`（命令行传码）
与 stdin 模式下 `__file__` 不存在。于是求解脚本里合法的一行

```python
BASE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "results")
```

在验证阶段必然抛 `NameError: name '__file__' is not defined`，导致 6 个脚本全部**假报警失败**，
而实际上它们在沙盒里都真实跑通了（results.json 数值正确）。

**关键事实**：同一函数里的路径 B（第 120-135 行，code/ 目录不存在、从 Markdown 提取代码块时）
**已经是正确写法**——用 `tempfile.NamedTemporaryFile` 落盘成 `.py` 文件，再 `[sys.executable, tmp_path]`
执行。修复本质是**让路径 A 对齐路径 B 的落盘方式**。

## 设计决策

1. 路径 A 不再用 `-c`，改为「把 `build_preamble() + script_src` 写入一个临时 `.py` 文件，再用
   `[sys.executable, tmp_path]` 执行，执行完 `unlink` 删除」。
2. **临时文件必须落在 `code_dir` 目录内**（例如 `code_dir / "_verify_<原文件名>.py"` 或用
   `tempfile.NamedTemporaryFile(dir=code_dir, suffix=".py", delete=False)`）。理由：脚本里
   `dirname(abspath(__file__))` 必须解析到 `code_dir`，这样 `".." / "results"` 才能指向正确的
   `output_dir/results`（与沙盒产物的落点一致）。若临时文件落在系统临时目录，`__file__` 的 dirname
   会指向临时目录，`..` 的解析全错。
3. `cwd` 保持 `str(code_dir)` 不变，别破坏脚本里用相对 cwd 的其他逻辑。
4. 保持现有的 Agg 后端注入逻辑（第 64-66 行的 matplotlib 判断）与 120s 超时、错误捕获结构不变。

## 改动清单（只动 main.py 一处函数）

### `main.py` —— `_verify_layer3_code()` 路径 A 分支（第 56-94 行）

- 把第 69-74 行的：
  ```python
  sandboxed_src = build_preamble() + '\n' + script_src
  result = subprocess.run(
      [sys.executable, '-c', sandboxed_src],
      capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=120,
      cwd=str(code_dir),
  )
  ```
  改为落盘执行，例如：
  ```python
  sandboxed_src = build_preamble() + '\n' + script_src
  import tempfile
  with tempfile.NamedTemporaryFile(
      mode="w", suffix=".py", prefix="_verify_", dir=code_dir,
      delete=False, encoding="utf-8",
  ) as f_tmp:
      f_tmp.write(sandboxed_src)
      tmp_path = f_tmp.name
  try:
      result = subprocess.run(
          [sys.executable, tmp_path],
          capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=120,
          cwd=str(code_dir),
      )
  finally:
      Path(tmp_path).unlink(missing_ok=True)
  ```
  （`tempfile` 若尚未 import，在文件顶部的 import 区补 `import tempfile`；注意 `Path` 已在文件顶部
  通过 `from pathlib import Path` 使用，请以实际 import 为准，不要重复引入。）
- 注意临时 `.py` 文件命名：前缀用 `_verify_`，且必须避免与第 35 行 `python_files` 过滤的
  `"_exec.py"` 冲突即可（当前过滤只排除 `_exec.py`，`_verify_` 前缀不会在**当前这次**循环里被重扫，
  因为 `python_files` 列表在循环前已固定；但为稳妥，`delete=False` 落盘后务必在 `finally` 删除）。
- **不动路径 B**（第 102-152 行已正确），也不动函数其余任何结构（报告文案、优先级排序、超时/异常
  捕获、`CODE_VERIFICATION.md` 写出）。

## 验收标准（必须真跑，不能只看静态）

1. 导入冒烟：`export PYTHONPATH=` 后 `.venv/Scripts/python.exe -c "import main"` 成功。
2. 单元测试通过性：新增一个测试 `tests/test_code_verify_file.py`，构造一个临时 `code/` 目录
   （含 `solver.py`，内容用 `os.path.dirname(os.path.abspath(__file__))`），调用
   `_verify_layer3_code(output_dir)`，断言返回的报告字符串里该脚本标记为「✅ 通过」而非「❌ 失败」，
   且没有 `NameError` / `__file__` 字样。测试里要 `import main`（或从 main 导入该函数）。
   —— 若 `main.py` 顶部 import 时会触发副作用（如 argparse / 重 init），用
   `from main import _verify_layer3_code` 依赖现有 `if __name__ == "__main__"` guard 隔离；请先读
   main.py 顶部确认有没有副作用再做。
3. `.venv/Scripts/python.exe -m pytest tests/ -q` 全绿（含历史全量 + 新增测试）。

## 明确 Do NOT

- 不要改路径 B、`build_preamble()`（`mathmodelingagents/tools/__init__.py`）、沙盒执行器
  `_exec_script`。
- 不要改任何提示词、CLI 参数、RAG/modeler 相关代码。
- 不要用当前 rm/echo 类手段改动其他文件；只改 `main.py` 一函数 + 新增一个测试文件。
- 不要动 `pyproject.toml` / 依赖 / lock 文件。
- 不要删掉 `-c` 那段里「以与沙盒一致的执行环境复现」的语义：落盘后仍要拼 `build_preamble()`。
- 跑测试一律 `export PYTHONPATH=` 前缀 + `.venv/Scripts/python.exe`。

## 报告格式

完成后报告：改动文件的 git diff 摘要、新增测试文件名与断言内容、以及**你自己跑**
`.venv/Scripts/python.exe -m pytest tests/ -q` 的最终结果原文（含通过/失败计数）。