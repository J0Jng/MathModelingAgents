# -*- coding: utf-8 -*-
"""探针：观察 ModelerC 在「纯自然停止」循环下的工具调用行为。

不经过 _run_modeler_turn 的中间打断逻辑（consecutive_tool_only 提醒、提前返回），
只保留「无 tool_calls → 自然停止」+ 高上限 max_iterations。每次工具调用打印到 CLI。

用法（仓库根目录）:
    export PYTHONPATH=
    .venv/Scripts/python.exe scripts/probe_modeler_tool_calls.py
"""

import json
import os
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from dotenv import load_dotenv
load_dotenv(REPO_ROOT / ".env")

from mathmodelingagents.default_config import DEFAULT_CONFIG
from mathmodelingagents.llm_clients import (
    invoke_with_tools_with_fallback,
    get_layer_model,
)
from mathmodelingagents.agents.utils.prompt_templates import get_modeler_c_prompt
from mathmodelingagents.tools import create_langchain_tools
from langchain_core.messages import SystemMessage, HumanMessage, ToolMessage

# ── 数据源（B 题真实输出目录）──
B_DIR = Path(r"C:\Users\joeji\Desktop\what\MMA_testing\B题-全部转换合并")


def read_section(path: Path, start_marker: str, end_marker: str | None) -> str:
    """提取两个标题之间的文本（子串匹配，跳过起始标题行本身）。"""
    lines = path.read_text(encoding="utf-8").splitlines()
    out = []
    in_range = False
    for ln in lines:
        if not in_range and start_marker in ln:
            in_range = True
            continue
        if in_range and end_marker and end_marker in ln:
            break
        if in_range:
            out.append(ln)
    return "\n".join(out).strip()


def main() -> int:
    # ── 提取真实输入 ──
    layer1 = B_DIR / "Layer1_问题分析.md"
    layer2 = B_DIR / "Layer2_数学建模.md"
    problem_report = read_section(layer1, "## 综合问题分析", "## 层摘要")
    a_plan = read_section(layer2, "### 建模师 A", "### 建模师 B")
    b_plan = read_section(layer2, "### 建模师 B", "### 建模师 C")

    mc_json = json.loads((B_DIR / "model_candidates.json").read_text(encoding="utf-8"))
    candidates = mc_json.get("text", "")

    # ── 构造 config ──
    config = DEFAULT_CONFIG.copy()
    config["llm_provider"] = os.getenv("MATHMODELING_LLM_PROVIDER", "volcengine-plan")
    config["output_dir"] = str(B_DIR)

    model = get_layer_model(config, "modeling", "agent")
    print(f"[探针] provider={config['llm_provider']}  model={model}", flush=True)
    print(f"[探针] problem_report {len(problem_report)} 字 | A方案 {len(a_plan)} 字 | "
          f"B方案 {len(b_plan)} 字 | 候选池 {len(candidates)} 字", flush=True)

    # ── 构造 context（对齐 _build_context 的 modeling 分支）──
    parts = [
        f"## Layer 1 综合问题分析（建模基准——阅读后开始设计模型）\n\n{problem_report}",
    ]
    if candidates:
        parts.append(f"## 候选模型池（参考起点，可超越）\n\n{candidates}")
    if a_plan:
        parts.append(f"## 建模师 A 本轮发言\n\n{a_plan}")
    if b_plan:
        parts.append(f"## 建模师 B 本轮发言\n\n{b_plan}")
    parts.append(
        f"## 附件数据位置\n\n附件数据已转为文本表格（两列：波数 cm^-1、反射率 %），"
        f"绝对路径目录：\n{B_DIR / 'attachments'}\n"
        f"文件：01_附件1.xlsx.md ~ 04_附件4.xlsx.md。\n"
        f"注：A/B 方案代码里硬编码的 C:/Users/joeji/Desktop/B题-全部转换合并/attachments "
        f"已失效，请用上方真实路径读数据。"
    )
    parts.append("## 当前状态\n- 当前层: 2\n- 辩论轮次: 0/3（剩余 3 轮）")
    context = "\n\n".join(parts)

    user_msg = f"请根据以下上下文执行你的任务。当前是第 1 轮：\n\n{context}"
    messages = [SystemMessage(content=get_modeler_c_prompt()), HumanMessage(content=user_msg)]

    # ── 绑定工具（与 _make_modeler_node 一致）──
    wanted = {"model_search_tool", "web_search_tool", "run_code_tool"}
    tools = [t for t in create_langchain_tools() if t.name in wanted]

    MAX_ITER = 30
    start = time.time()
    final_text = ""
    iteration = 0

    for iteration in range(1, MAX_ITER + 1):
        print(f"\n{'=' * 60}", flush=True)
        print(f"[iteration {iteration}/{MAX_ITER}] 调用 LLM ...", flush=True)
        try:
            response = invoke_with_tools_with_fallback(
                config, "modeling", "agent", tools, messages, "modeler_c",
            )
        except Exception as e:
            print(f"[探针] LLM 调用失败: {e}", flush=True)
            break
        messages.append(response)

        content = (getattr(response, "content", "") or "").strip()
        tool_calls = getattr(response, "tool_calls", None) or []

        print(f"[content] {content[:240]}{'...' if len(content) > 240 else ''}", flush=True)

        if not tool_calls:
            print("[自然停止] 无 tool_calls → 返回纯文本方案", flush=True)
            final_text = content
            break

        print(f"[tool_calls] 本轮 {len(tool_calls)} 个：", flush=True)
        for tc in tool_calls:
            arg_str = json.dumps(tc.get("args", {}), ensure_ascii=False)
            if len(arg_str) > 200:
                arg_str = arg_str[:200] + "..."
            print(f"    • {tc.get('name')}({arg_str})", flush=True)

        for tc in tool_calls:
            tool_name = tc.get("name", "")
            tool_args = tc.get("args", {})
            tool_id = tc.get("id", "")
            tool_fn = next((t for t in tools if t.name == tool_name), None)
            if tool_fn is None:
                result = f"[未知工具] {tool_name}"
            else:
                try:
                    result = tool_fn.invoke(tool_args)
                except Exception as e:
                    result = f"[工具执行异常] {tool_name}: {e}"
            result_str = (
                json.dumps(result, ensure_ascii=False)
                if isinstance(result, dict) else str(result)
            )
            print(f"    ✓ {tool_name} → {result_str[:200]}{'...' if len(result_str) > 200 else ''}",
                  flush=True)
            messages.append(ToolMessage(content=result_str, tool_call_id=tool_id))

    elapsed = time.time() - start
    print(f"\n{'=' * 60}", flush=True)
    print(f"[探针结束] 迭代 {iteration}/{MAX_ITER}，耗时 {elapsed:.1f}s，"
          f"最终文字 {len(final_text)} 字", flush=True)
    if iteration >= MAX_ITER and not final_text:
        print("[结论] 撞上限仍未自然停止 → 发散（一直工具调用不停）", flush=True)
    else:
        print(f"[结论] 自然停止 → 收敛（第 {iteration} 轮停手）", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())