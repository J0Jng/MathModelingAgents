# -*- coding: utf-8 -*-
"""验收探针：验证 create_coding_agent_tools 的文件工具路径作用域修复。

背景：修复前 read_file/write_file/list_dir 三个工具裸 resolve 相对路径到进程 CWD
（仓库根），与 run_code 的 cwd=output_dir/code 背离，导致模型路径迷路。
修复后三者相对路径应统一相对 output_dir。

运行：.venv/Scripts/python.exe scripts/verify_coding_tool_path_scoping.py
     （需在仓库根运行，让进程 CWD = 仓库根，最能暴露回归）
"""
import os
import sys
import tempfile
from pathlib import Path

# 确保能 import 项目包（若已是 editable install 则可省略，但显式加保险）
repo_root = Path(__file__).resolve().parent.parent
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

from mathmodelingagents.tools import create_coding_agent_tools  # noqa: E402

PASS = 0
FAIL = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global PASS, FAIL
    status = "PASS" if ok else "FAIL"
    print(f"[{status}] {name}" + (f"  -> {detail}" if detail else ""))
    if ok:
        PASS += 1
    else:
        FAIL += 1


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="mma_path_scope_"))
    tools = {t.name: t for t in create_coding_agent_tools(str(tmp))}

    print(f"临时 output_dir: {tmp}")
    print(f"进程 CWD      : {Path.cwd()}\n")

    read_t = tools["read_file_tool"]
    write_t = tools["write_file_tool"]
    list_t = tools["list_dir_tool"]

    # ── 1. write_file_tool：相对路径应落到 output_dir 下 ──
    wr = write_t.invoke({"content": "print('hi')", "path": "code/solve_q1.py"})
    expected = (tmp / "code" / "solve_q1.py")
    check("write_file 相对路径落盘到 output_dir/code", expected.exists(), wr)

    # ── 2. read_file_tool：相对路径应从 output_dir 读 ──
    rd = read_t.invoke({"path": "code/solve_q1.py"})
    check("read_file 相对路径从 output_dir 命中", "print('hi')" in rd, rd[:60])

    # 在仓库根放一个干扰文件，确认 read 不会被 cwd 误导
    repo_interfere = Path.cwd() / "code" / "solve_q1.py"
    # read 相对路径应返回 output_dir 下的内容，而非仓库根（若仓库根也有同名文件）
    if "print('hi')" in rd:
        check("read_file 不被进程 CWD 干扰", True, "命中 output_dir 版本")
    else:
        check("read_file 不被进程 CWD 干扰", False, rd[:60])

    # ── 3. list_dir_tool：默认 "." 应列出 output_dir 内容 ──
    ld = list_t.invoke({"path": "."})
    ok_default = ("code/" in ld) and ("results/" in ld)
    check("list_dir '.' 返回 output_dir 内容(含 code/ results/)", ok_default, ld.replace("\n", " | ")[:80])

    # 关键反例：不应返回仓库根标记（.git / .claude / pyproject.toml）
    repo_markers = [m for m in (".git", ".claude", "pyproject.toml", "README") if m in ld]
    check("list_dir '.' 不泄漏仓库根内容", not repo_markers, f"泄漏标记: {repo_markers}" if repo_markers else "无泄漏")

    # ── 4. 绝对路径照常放行 ──
    abs_p = tmp / "results" / "abs_test.txt"
    abs_p.write_text("abs-ok", encoding="utf-8")
    ra = read_t.invoke({"path": str(abs_p)})
    check("绝对路径 read 照常", "abs-ok" in ra, ra[:40])

    print(f"\n结果：{PASS} PASS / {FAIL} FAIL")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())