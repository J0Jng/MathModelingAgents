# Fix: Windows GBK subprocess decode crash (UnicodeDecodeError)

> **状态：已完成（2026-09-03），Claude Code 执行，Hermes 独立复验通过。**
> 三处 `subprocess.run(..., text=True)` 已加 `encoding="utf-8", errors="replace"`；`.venv/Scripts/python.exe -m pytest tests/ -q` → **103 passed**；冒烟确认 `run_code("print('中文测试OK')")` stdout 含中文、exit 0、无 UnicodeDecodeError。

## 背景（Background）

`--from-layer1` 恢复模式经用户实测在 Windows 崩溃：

```
Exception in thread Thread-11 (_readerthread):
UnicodeDecodeError: 'gbk' codec can't decode byte 0x97 in position 165: illegal multibyte sequence
```

solver 脚本在 Windows 沙盒里 `run_code` 执行后打印了含中文/UTF-8 多字节的输出，子进程读线程用 **gbk** 解码失败 → `_readerthread` 抛异常 → 线程崩溃 → 进程中断（症状为 KeyboardInterrupt/挂起）。

**为什么只偶发**：solver 代码如果只 print ASCII（如 `m^3`）就不崩；一旦 print 中文（用户体验场景几乎必然）就崩。config/图逻辑/voleplan 无关。

## 根因

两处 `subprocess.run(..., text=True)` 未指定 `encoding`：
- Python 在 Windows 默认 locale 编码 = **gbk/cp936**（`locale.getpreferredencoding()`）。
- 子进程脚本内容是 UTF-8（write_file 用 `encoding="utf-8"` 写入 `_exec.py`），Python 子进程 stdout 在 UTF-8 控制台下输出 UTF-8 字节。
- `text=True` 让父进程用 gbk 解码这些 UTF-8 字节 → 遇到 0x97/0x80 等非法序列抛 `UnicodeDecodeError`。

## 修复

把两处 `text=True` 改为 `text=True, encoding="utf-8", errors="replace"`：

### 1. `mathmodelingagents/tools/__init__.py` — `_exec_script`（L241-248）
```python
proc = subprocess.run(
    [python_exe, str(script_path)],
    capture_output=True,
    text=True,
    encoding="utf-8",
    errors="replace",
    timeout=timeout,
    cwd=work_dir,
    env=env,
)
```

### 2. `main.py` — `_verify_layer3_code`（L56-59）
```python
result = subprocess.run(
    [sys.executable, str(f)],
    capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
    cwd=str(code_dir),
)
```
同文件 `_verify_layer3_code` 路径 B（L113-116 提取代码块拼接执行）也有一个 `subprocess.run(..., text=True)`，同样加 `encoding="utf-8", errors="replace"`。

> 说明：`errors="replace"` 是兜底——即使有个别非 UTF-8 字节，也不抛异常，仅替换为 ``，保证 read 线程永不因解码崩溃。这是关键，必须加。

## 检查清单（Claude Code 必须全覆盖）

1. `grep -rn "text=True" mathmodelingagents/ main.py` / 全项目，确认**所有** `subprocess.run(..., text=True)` 不带 `encoding=` 的地方都补上 `encoding="utf-8", errors="replace"`。
2. 若项目其他地方（scripts/、tests/）也有同类 `subprocess.run(..., text=True)`，一并修（保持一致性）。
3. 不要改动任何**逻辑**（超时、cwd、env、截断逻辑都不动），只加 encoding 实参。
4. 用项目解释器 `.venv/Scripts/python.exe` 运行测试确认无回归：
   ```
   .venv/Scripts/python.exe -m pytest tests/ -q
   ```
   全绿（103 passed）。

## 明确不要做（Do NOT Do）

- **不要**改 `write_file`/脚本写入端的 `encoding="utf-8"`（那是正确的）。
- **不要**改 `locale`/系统编码、不改命令行 `chcp`。
- **不要**动 `run_code` 的 timeout 逻辑、沙盒拦截规则、`_exec_script` 的其它任何行为。
- **不要**提交/推送。只编辑文件。
- **不要**改 `mathmodelingagents/tools/__init__.py` 里除 `_exec_script` subprocess.run 外的任何行。

## 验收标准

1. 上面两处 `subprocess.run` 均含 `encoding="utf-8", errors="replace"`。
2. 全项目无 `text=True` 且不含 `encoding=` 的 `subprocess.run` 残留。
3. `.venv/Scripts/python.exe -m pytest tests/ -q` 全绿。
4. （可选，若非阻塞）用项目解释器对 `_exec_script` 冒烟：构造一个 `print("中文测试OK")` 的脚本，确认 `run_code` 返回的 stdout 为 UTF-8 中文字符串而非抛 UnicodeDecodeError。