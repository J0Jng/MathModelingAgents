"""
Quick runner: 从已有 Layer 2 数据启动 Layer 3（重新求解+可视化），使用 DeepSeek API。

Usage:
    python run_layer3_resume.py
"""
import sys
import re
import logging
from pathlib import Path

from mathmodelingagents.default_config import DEFAULT_CONFIG
from mathmodelingagents.graph.modeling_graph import MathModelingGraph

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)

# ── 配置 ──
EXISTING_OUTPUT = Path(r"C:\Users\joeji\Desktop\绿色物流配送_完整文档")
PROBLEM_FILE = Path(r"C:\Users\joeji\Desktop\1.绿色物流配送\绿色物流配送_完整文档.md")
OUTPUT_NAME = "绿色物流配送_L3重跑"

config = DEFAULT_CONFIG.copy()
config["llm_provider"] = "deepseek"
config["selected_layers"] = [3, 4]  # 只跑 Layer 3 + 4
config["max_debate_rounds"] = 3     # 给 PaperManager 足够的 REVISE 轮次
config["max_impl_retries"] = 3

# ── 读取已有 Layer 2 数据 ──
layer2_file = EXISTING_OUTPUT / "Layer2_数学建模.md"
if not layer2_file.exists():
    print(f"错误: Layer 2 文件不存在: {layer2_file}")
    sys.exit(1)

layer2_content = layer2_file.read_text(encoding="utf-8")

# 提取 Layer 2 精华：只取最后一轮（模型方案已收敛）
# 找最后一个 "第 N 轮" 标记
round_markers = list(re.finditer(r'## 第 (\d+) 轮', layer2_content))
if round_markers:
    last_round_start = round_markers[-1].start()
    # 取最后一轮的前 30000 字符（模型公式 + 裁决）
    model_summary = layer2_content[last_round_start:last_round_start + 30000]
else:
    model_summary = layer2_content[:5000]

# 如果最后一轮有 CONCLUDE，裁掉 CONTINUE 之前的无关内容
conclude_match = re.search(r'\*\*CONCLUDE\*\*', model_summary)
if conclude_match:
    # 保留 CONCLUDE 之前 8000 字符（建模师发言）+ CONCLUDE 之后
    start = max(0, conclude_match.start() - 8000)
    model_summary = model_summary[start:]

layer_summary = (
    "### Layer 1 问题分析（已完成）\n"
    "(前次运行中 Layer 1 已通过 ProblemManager 审核)\n\n"
    "### Layer 2 数学建模（已完成，以下是最终轮方案 + 经理裁决）\n"
    f"{model_summary}"
)

print(f"Layer 2 数据已加载 ({len(layer2_content)} 字符)")
print(f"layer_summary 精简为 {len(layer_summary)} 字符（仅最终方案）")
print()

# ── 初始化图 ──
mm = MathModelingGraph(config=config, debug=True)

# ── 创建初始 state 并注入 layer_summary ──
initial_state = mm.propagator.create_initial_state(
    problem_path=str(PROBLEM_FILE),
    output_name=OUTPUT_NAME,
)
initial_state["layer_summary"] = layer_summary
initial_state["solution_approach"] = model_summary
initial_state["model_spec"] = model_summary

# ── 确定输出目录 ──
from mathmodelingagents.reporting import (
    setup_incremental, append_agent_output, finalize_reports,
)
desktop = Path.home() / "Desktop"
output_dir = str(desktop / initial_state.get("output_name", "output"))
config["output_dir"] = output_dir
setup_incremental(output_dir)

problem_name = PROBLEM_FILE.stem

print(f"输出目录: {output_dir}")
print("开始流式执行...")
print("=" * 60)

prev_count = 0
result = initial_state

try:
    for chunk in mm.graph.stream(
        initial_state,
        stream_mode="values",
        config={"recursion_limit": mm.propagator.max_recur_limit},
    ):
        result = chunk
        outputs = chunk.get("layer_outputs", [])
        for rec in outputs[prev_count:]:
            append_agent_output(output_dir, rec)
            agent = rec.get("agent", "?")
            layer = rec.get("layer", "?")
            role = rec.get("role", "?")
            out_len = len(rec.get("output", ""))
            print(f"  ✓ {agent} ({layer}/{role}) → {out_len} 字符")
        prev_count = len(outputs)
except Exception as e:
    print(f"\n执行中断: {e}")
    crash_path = Path(output_dir) / "CRASHED.txt"
    from datetime import datetime
    crash_path.write_text(
        f"执行中断于: {datetime.now().isoformat()}\n"
        f"已完成 {prev_count} 条记录\n错误: {e}\n",
        encoding="utf-8",
    )
    raise

print("=" * 60)
print(f"完成！共 {prev_count} 条记录，输出目录: {output_dir}")

# 最终汇总
finalize_reports(output_dir, result, problem_name)

final_paper = result.get("final_paper", "")
print(f"论文长度: {len(final_paper)} 字符")
