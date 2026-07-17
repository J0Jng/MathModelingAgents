"""报告输出模块 — 支持增量写入 + 最终汇总。

关键设计：
  - propagate() 使用 stream() 逐节点执行
  - 每个节点完成后立即 append_agent_output() 写盘
  - 进程崩溃时，已完成的层文件已存在于磁盘，不会丢失
  - 全部完成后，finalize_reports() 写入 summary.md 和 final_paper.md
"""

import logging
from pathlib import Path
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)

# ── Agent 显示名称和角色描述 ──
AGENT_DISPLAY: dict[str, tuple[str, str]] = {
    "decomposer":         ("问题拆解师",   "分析"),
    "data_analyst":       ("数据洞察师",   "分析"),
    "constraint_analyst": ("约束分析师",   "分析"),
    "problem_manager":    ("问题分析经理", "裁决"),
    "modeler_a":          ("建模师 A",     "创新与优雅"),
    "modeler_b":          ("建模师 B",     "实用与稳健"),
    "modeler_c":          ("建模师 C",     "简洁与可解释"),
    "modeling_manager":   ("建模经理",     "裁决"),
    "algorithm_designer": ("算法设计师",   "设计"),
    "coder":              ("代码工程师",   "实现"),
    "visualizer":         ("可视化师",     "可视化"),
    "impl_manager":       ("实现经理",     "裁决"),
    "paper_architect":    ("论文架构师",   "写作"),
    "section_writer":     ("章节作者",     "写作"),
    "chart_designer":     ("图表设计师",   "设计"),
    "paper_manager":      ("论文经理",     "裁决"),
    "param_perturber":    ("参数扰动师",   "分析"),
    "robustness_analyst": ("稳健性分析师", "分析"),
    "sensitivity_manager":("敏感性经理",   "裁决"),
}

LAYER_NAMES: dict[str, str] = {
    "problem":        "Layer1_问题分析",
    "modeling":       "Layer2_数学建模",
    "implementation": "Layer3_代码实现",
    "paper":          "Layer4_论文写作",
    "sensitivity":    "Layer5_敏感性分析",
}

LAYER_TITLES: dict[str, str] = {
    "problem":        "Layer 1: 问题分析",
    "modeling":       "Layer 2: 数学建模",
    "implementation": "Layer 3: 代码实现",
    "paper":          "Layer 4: 论文写作",
    "sensitivity":    "Layer 5: 敏感性分析",
}

LAYER_ORDER = ["problem", "modeling", "implementation", "paper", "sensitivity"]


# ═══════════════════════════════════════════════════════════════════
# 增量写入 API（stream 模式使用）
# ═══════════════════════════════════════════════════════════════════

class _LayerWriter:
    """追踪每层的写入状态，处理轮次标题。"""

    def __init__(self):
        self._layer_rounds: dict[str, int] = {}  # layer → 已写入的最大轮数
        self._layer_started: dict[str, bool] = {}  # layer → 是否已写文件头

    def write(self, output_dir: str, record: dict) -> None:
        """将一条 agent 输出追加到正确的层文件。"""
        layer = record.get("layer", "unknown")
        fname = LAYER_NAMES.get(layer, f"{layer}.md")
        path = Path(output_dir) / f"{fname}.md"

        agent = record.get("agent", "unknown")
        display, role_desc = AGENT_DISPLAY.get(agent, (agent, ""))
        rnd = record.get("round_num", 1)
        output = record.get("output", "").strip()

        lines: list[str] = []

        if not self._layer_started.get(layer):
            # 首次写入此层：写文件头
            title = LAYER_TITLES.get(layer, layer)
            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            lines.append(f"# {title}\n")
            lines.append(f"> 生成时间: {ts}\n")
            self._layer_started[layer] = True
            self._layer_rounds[layer] = 0

        last_rnd = self._layer_rounds.get(layer, 0)
        if rnd > last_rnd:
            # 新轮次
            max_r = max(rnd, 1)  # placeholder, actual max determined at finalize
            lines.append(f"## 第 {rnd} 轮\n")
            self._layer_rounds[layer] = rnd

        lines.append("---")
        lines.append(f"### {display} [{role_desc}]")
        lines.append("")
        lines.append(output)
        lines.append("")

        # 追加写入
        with open(path, "a", encoding="utf-8") as f:
            f.write("\n".join(lines))


# 全局单例（propagate 一次运行期间复用）
_writer: _LayerWriter | None = None


def setup_incremental(output_dir: str) -> str:
    """初始化输出目录，准备增量写入。"""
    global _writer
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "results").mkdir(exist_ok=True)
    (out / "code").mkdir(exist_ok=True)
    _writer = _LayerWriter()
    logger.info(f"增量输出目录已就绪: {output_dir}")
    return str(out)


def append_agent_output(output_dir: str, record: dict) -> None:
    """逐条追加 agent 输出到对应层文件。"""
    global _writer
    if _writer is None:
        _writer = _LayerWriter()
    _writer.write(output_dir, record)


def finalize_reports(
    output_dir: str,
    state: dict[str, Any],
    problem_name: str = "",
) -> list[str]:
    """流程结束后写入 summary.md 和 final_paper.md。

    层文件已通过 append_agent_output 增量写入完毕，
    这里只生成需要完整 state 的汇总和论文。
    """
    out = Path(output_dir)
    written: list[str] = []
    outputs: list[dict] = state.get("layer_outputs", [])

    # summary.md
    summary_path = out / "summary.md"
    summary_path.write_text(_build_summary(outputs, state), encoding="utf-8")
    written.append(str(summary_path))

    # final_paper.md
    paper_path = out / "final_paper.md"
    paper_path.write_text(_build_final_paper(state, problem_name), encoding="utf-8")
    written.append(str(paper_path))

    logger.info(f"最终报告完成: {len(written)} 个文件 → {output_dir}")
    return written


# ═══════════════════════════════════════════════════════════════════
# 批量生成（保留，用于非 stream 场景或事后重建）
# ═══════════════════════════════════════════════════════════════════

def generate_reports(
    state: dict[str, Any],
    output_dir: str,
    problem_name: str = "",
) -> list[str]:
    """从完整 state 一次性生成所有文件（备用）。"""
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "results").mkdir(exist_ok=True)
    (out / "code").mkdir(exist_ok=True)

    written: list[str] = []
    outputs: list[dict] = state.get("layer_outputs", [])

    by_layer: dict[str, list[dict]] = {}
    for rec in outputs:
        by_layer.setdefault(rec["layer"], []).append(rec)

    for layer in LAYER_ORDER:
        records = by_layer.get(layer, [])
        if not records:
            continue
        fname = LAYER_NAMES.get(layer, f"{layer}.md")
        path = out / f"{fname}.md"
        content = _build_layer_file(layer, records)
        path.write_text(content, encoding="utf-8")
        written.append(str(path))

    summary_path = out / "summary.md"
    summary_path.write_text(_build_summary(outputs, state), encoding="utf-8")
    written.append(str(summary_path))

    paper_path = out / "final_paper.md"
    paper_path.write_text(_build_final_paper(state, problem_name), encoding="utf-8")
    written.append(str(paper_path))

    logger.info(f"报告生成完成: {len(written)} 个文件 → {output_dir}")
    return written


# ═══════════════════════════════════════════════════════════════════
# 内部构建函数
# ═══════════════════════════════════════════════════════════════════

def _build_layer_file(layer: str, records: list[dict]) -> str:
    """构建单层 Markdown 文件（批量模式）。"""
    title = LAYER_TITLES.get(layer, layer)
    lines = [f"# {title}\n", f"> 生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"]

    max_round = max((r.get("round_num", 1) for r in records), default=1)
    for rnd in range(1, max_round + 1):
        round_records = [r for r in records if r.get("round_num", 1) == rnd]
        if not round_records:
            continue
        if max_round > 1:
            lines.append(f"## 第 {rnd} 轮\n")
        for rec in round_records:
            agent = rec.get("agent", "unknown")
            display, role_desc = AGENT_DISPLAY.get(agent, (agent, ""))
            output = rec.get("output", "").strip()
            lines.append("---")
            lines.append(f"### {display} [{role_desc}]")
            lines.append("")
            lines.append(output)
            lines.append("")
    return "\n".join(lines)


def _build_summary(all_outputs: list[dict], state: dict) -> str:
    """构建总汇总文件。"""
    lines = [
        "# 运行总汇总\n",
        f"> 生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "---\n",
        "## 各层概况\n",
    ]
    by_layer: dict[str, list[dict]] = {}
    for rec in all_outputs:
        by_layer.setdefault(rec["layer"], []).append(rec)

    lines.append("| 层 | Agent 数 | 总轮数 | 输出总字数 |")
    lines.append("|----|---------|--------|-----------|")
    for layer in LAYER_ORDER:
        recs = by_layer.get(layer, [])
        if not recs:
            continue
        title = LAYER_TITLES.get(layer, layer)
        unique_agents = len(set(r["agent"] for r in recs))
        max_round = max((r.get("round_num", 1) for r in recs), default=1)
        total_chars = sum(len(r.get("output", "")) for r in recs)
        lines.append(f"| {title} | {unique_agents} | {max_round} | {total_chars:,} |")

    lines.append("\n---\n## 各层裁决结果\n")
    debate = state.get("model_debate_state") or state.get("debate_state") or {}
    jd = debate.get("judge_decision", "N/A")
    rc = debate.get("round_count", "N/A")
    lines.append(f"- Layer 2 建模辩论: **{jd}** (共 {rc} 轮)")
    impl_retries = state.get("impl_retry_count", 0)
    err = state.get("error_analysis", "")
    lines.append(f"- Layer 3 代码实现: 重试 {impl_retries} 次" + (" (有错误)" if err else " (成功)"))
    final = state.get("final_paper", "")
    lines.append(f"- Layer 4 论文: {'已生成' if final else '未生成'} ({len(final):,} 字)")

    lines.append("\n---\n## 输出文件\n")
    lines.append(f"- 各层详细输出: `LayerN_*.md`")
    lines.append(f"- 最终论文: `final_paper.md`")
    lines.append(f"- 图表: `results/`")
    lines.append(f"- 代码: `code/`")
    return "\n".join(lines)


def _build_final_paper(state: dict, problem_name: str = "") -> str:
    """从 state 提取并格式化最终论文。

    优先使用 final_paper 字段（SectionWriter 产出或 PaperManager CONCLUDE 整合版）。
    若内容疑似审查报告（含 ## 修改项 等），回退使用 visualizations 字段。
    """
    raw = state.get("final_paper", "")
    # 防御：若 final_paper 包含审查报告而非论文正文，回退到 visualizations
    if not raw or "## 修改项" in raw or raw.strip().startswith("("):
        raw = state.get("visualizations", "") or raw
    for marker in ["**CONCLUDE**", "**CONTINUE**", "**REVISE**", "**ACCEPT**", "**REJECT**"]:
        raw = raw.replace(marker, "")
    title = f"# {problem_name}" if problem_name else "# 最终论文"
    return "\n".join([
        title, "",
        f"> 生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "", "---\n", raw.strip(),
    ])
