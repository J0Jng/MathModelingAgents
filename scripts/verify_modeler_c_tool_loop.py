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
from mathmodelingagents.agents import _extract_plan_text, _run_modeler_turn

complete = _make_complete_plan()

# 3. _extract_plan_text：submit_plan 优先 / 纯文本退化 / 两者皆无返回 None
submit_msg = AIMessage(
    content="",
    tool_calls=[{"name": "submit_plan_tool", "args": {"plan": complete}, "id": "call_s"}],
)
check(_extract_plan_text(submit_msg) == complete,
      "3a. submit_plan_tool 携带非空 plan → 返回 plan")

plain_msg = AIMessage(content=complete)
check(_extract_plan_text(plain_msg) == complete,
      "4a. 无 tool_calls 且有 content → 返回 content")
check(_extract_plan_text(AIMessage(content="")) is None,
      "4b. 无 tool_calls 且 content 空 → 返回 None")
check(_extract_plan_text(AIMessage(
    content="",
    tool_calls=[{"name": "model_search_tool", "args": {"query": "x"}, "id": "call_m"}],
)) is None,
      "4c. 非 submit_plan 工具调用且无 content → 返回 None")

# 5. _run_modeler_turn：submit_plan 交卷
calls: list[int] = []


def invoke_fn(messages: list):
    calls.append(1)
    return AIMessage(
        content="",
        tool_calls=[{"name": "submit_plan_tool", "args": {"plan": complete}, "id": "call_s"}],
    )


msgs, result = _run_modeler_turn(
    tools=[],
    layer_tag="Layer2",
    agent_tag="modeler_c",
    max_iterations=10,
    initial_messages=[],
    invoke_fn=invoke_fn,
)
check(result == complete, "5a. submit_plan 交卷：result == plan")
check(len(calls) == 1, "5b. submit_plan 交卷：invoke_fn 仅被调用 1 次")

# ── 汇总 ──
failed = [label for ok, label in results if not ok]
print(f"\n{len(results) - len(failed)}/{len(results)} 项通过")
if failed:
    sys.exit(1)
print("ALL CHECKS PASSED")