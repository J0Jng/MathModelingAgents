# -*- coding: utf-8 -*-
"""验收脚本：修复 ModelerC 发言循环「连续工具调用 → 发言失败」（impl-brief AC 1-5）。

用法（仓库根目录）:
    export PYTHONPATH=
    .venv/Scripts/python.exe scripts/verify_modeler_c_tool_loop.py
"""

import subprocess
import sys
from pathlib import Path

from langchain_core.messages import AIMessage

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

results: list[tuple[bool, str]] = []


def check(ok: bool, label: str) -> None:
    results.append((ok, label))
    print(f"{'PASS' if ok else 'FAIL'}  {label}")


def _make_complete_plan() -> str:
    """构造一段 >=500 字且含 >=2 处 `### N.` 章节标记的完整建模方案。"""
    section_1 = (
        "### 1. 模型选择\n"
        "本方案针对该优化问题选用带正则化的线性回归作为基准模型。"
        "考虑数据维度较高且存在共线性，优先采用岭回归与 Lasso 的折衷方案。"
        "为验证选型合理性，将通过交叉验证比较均方误差与可解释性指标。"
    )
    section_2 = (
        "### 2. 精度损失分析\n"
        "在 5 折交叉验证下，简化模型的 RMSE 相较完整模型上升约 1.7%，"
        "该损失在可接受范围内，且换来了显著的参数稀疏性与泛化提升。"
        "下方通过 run_code 验证上述结论并附量化指标。"
    )
    pad = "（为保证决策可解释性，模型结构保持三层以内，并附带敏感性检验。）"
    content = section_1 + section_2 + pad * 12
    assert len(content) >= 500, f"测试文案长度不足: {len(content)}"
    return content


def _make_long_no_sections() -> str:
    return ("这是一段没有任何章节标记的长文字。" * 30)


# ── 1. py_compile ──
target = REPO_ROOT / "mathmodelingagents" / "agents" / "__init__.py"
r = subprocess.run(
    [sys.executable, "-m", "py_compile", str(target)],
    capture_output=True, text=True,
)
if r.returncode != 0:
    print(r.stderr)
check(r.returncode == 0, "1. py_compile mathmodelingagents/agents/__init__.py 无错误")

# ── 2. 冒烟 import ──
r = subprocess.run(
    [sys.executable, "-c", "import mathmodelingagents.agents"],
    capture_output=True, text=True, cwd=str(REPO_ROOT),
)
check(r.returncode == 0, f"2. import 冒烟成功{'' if r.returncode == 0 else ': ' + r.stderr}")

# ── 3/4/5. 单元 + 集成断言 ──
from mathmodelingagents.agents import (
    _looks_like_complete_plan,
    _extract_last_substantial_text,
    _run_modeler_turn,
)

check(_looks_like_complete_plan(_make_complete_plan()) is True,
      "3a. 完整方案（≥500 字 + ≥2 处 ### N.）→ True")
check(_looks_like_complete_plan("太短的了不起的方案") is False,
      "3b. 短文字（<500 字）→ False")
check(_looks_like_complete_plan(_make_long_no_sections()) is False,
      "3c. 长文字但无章节标记 → False")

long_msg = AIMessage(content=_make_complete_plan(), tool_calls=[
    {"name": "run_code", "args": {}, "id": "call_1"},
])
short_msg = AIMessage(content="好的。")
check(_extract_last_substantial_text([long_msg, short_msg]) == long_msg.content,
      "4a. 带 tool_calls 的 ≥500 字 AI 消息 + 后续短消息 → 返回该长文字")
check(_extract_last_substantial_text([AIMessage(content="短"), AIMessage(content="也短")]) == "",
      "4b. 全部消息 content <500 字 → 返回 ''")

calls: list[int] = []
complete = _make_complete_plan()


def invoke_fn(messages: list):
    calls.append(1)
    return AIMessage(
        content=complete,
        tool_calls=[{"name": "run_code", "args": {"code": "print(1)"}, "id": "call_1"}],
    )

msgs, result = _run_modeler_turn(
    tools=[],
    layer_tag="Layer2",
    agent_tag="modeler_c",
    max_iterations=10,
    initial_messages=[],
    invoke_fn=invoke_fn,
)
check(result == complete, "5a. 提前退出：result == 完整方案 content")
check(len(calls) == 1, "5b. 提前退出：invoke_fn 仅被调用 1 次（未执行工具、未耗尽循环）")

# ── 汇总 ──
failed = [label for ok, label in results if not ok]
print(f"\n{len(results) - len(failed)}/{len(results)} 项通过")
if failed:
    sys.exit(1)
print("ALL CHECKS PASSED")