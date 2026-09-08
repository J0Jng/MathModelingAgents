# -*- coding: utf-8 -*-
"""验收脚本：Modeler 工具调用收敛机制（阈值 5→8 + 三 modeler 统一收敛铁律）。

用法（仓库根目录）:
    export PYTHONPATH=
    .venv/Scripts/python.exe scripts/verify_modeler_convergence.py
"""

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

results: list[tuple[bool, str]] = []


def check(ok: bool, label: str) -> None:
    results.append((ok, label))
    print(f"{'PASS' if ok else 'FAIL'}  {label}")


TIELU_MARKER = "## 工具调用收敛铁律"
OUTPUT_MARKER = "## 输出模板"


# ── 1. py_compile 两个被改文件 ──
targets = [
    REPO_ROOT / "mathmodelingagents" / "agents" / "__init__.py",
    REPO_ROOT / "mathmodelingagents" / "agents" / "utils" / "prompt_templates.py",
]
for t in targets:
    r = subprocess.run(
        [sys.executable, "-m", "py_compile", str(t)],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        print(r.stderr)
    check(r.returncode == 0, f"1. py_compile {t.relative_to(REPO_ROOT)} 无错误")

# ── 2. 冒烟 import ──
r = subprocess.run(
    [sys.executable, "-c", "import mathmodelingagents.agents"],
    capture_output=True, text=True, cwd=str(REPO_ROOT),
)
check(r.returncode == 0, f"2. import 冒烟成功{'' if r.returncode == 0 else ': ' + r.stderr}")

# ── 3/4. 三 modeler 铁律逐字一致 + 顺序断言 ──
from mathmodelingagents.agents.utils.prompt_templates import (
    get_modeler_a_prompt,
    get_modeler_b_prompt,
    get_modeler_c_prompt,
)

prompts = {
    "modeler_a": get_modeler_a_prompt(),
    "modeler_b": get_modeler_b_prompt(),
    "modeler_c": get_modeler_c_prompt(),
}


def extract_tielu(text: str) -> str:
    start = text.index(TIELU_MARKER)
    end = text.index(OUTPUT_MARKER, start)
    return text[start:end].rstrip()


tielu_blocks = {name: extract_tielu(p) for name, p in prompts.items()}

for name, block in tielu_blocks.items():
    check(block.startswith(TIELU_MARKER) and len(block) > len(TIELU_MARKER),
          f"3a. {name} 含「工具调用收敛铁律」段落")

first = tielu_blocks["modeler_a"]
rest = [tielu_blocks["modeler_b"], tielu_blocks["modeler_c"]]
check(all(b == first for b in rest),
      "3b. 三个 modeler 的铁律文案逐字一致")

for name, p in prompts.items():
    i_quan = p.index("## 工具权限")
    i_tielu = p.index(TIELU_MARKER)
    i_out = p.index(OUTPUT_MARKER)
    check(i_quan < i_tielu < i_out,
          f"4. {name} 顺序: 工具权限 < 工具调用收敛铁律 < 输出模板")

# ── 5. 源码阈值断言 ──
src = (REPO_ROOT / "mathmodelingagents" / "agents" / "__init__.py").read_text(
    encoding="utf-8"
)
check("consecutive_tool_only >= 8" in src, "5a. 源码含 consecutive_tool_only >= 8")
check("consecutive_tool_only >= 5" not in src, "5b. 源码不再含 consecutive_tool_only >= 5")

# ── 6. 回归: verify_modeler_c_tool_loop.py ──
reg = subprocess.run(
    [sys.executable, str(REPO_ROOT / "scripts" / "verify_modeler_c_tool_loop.py")],
    capture_output=True, text=True, cwd=str(REPO_ROOT),
)
print("\n[回归] verify_modeler_c_tool_loop.py 输出:")
print(reg.stdout, end="")
if reg.stderr:
    print(reg.stderr, end="")
check(reg.returncode == 0, "6. 回归: verify_modeler_c_tool_loop.py 仍全部通过")

# ── 汇总 ──
failed = [label for ok, label in results if not ok]
print(f"\n{len(results) - len(failed)}/{len(results)} 项通过")
if failed:
    sys.exit(1)
print("ALL CHECKS PASSED")