"""Layer 1 状态恢复 — 从既有输出目录加载 Layer 1 产出（--from-layer1 支持）。

读取 `<dir>/Layer1_问题分析.md` 提取 ProblemManager 裁决文本作为 problem_report，
并读取 `<dir>/sensitivity_decision.json`（若存在）恢复敏感性决策。
"""

import json
import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)

# ProblemManager 裁决段落提取：从「### 问题分析经理 [裁决]」到下一个分段线或文件末尾
_VERDICT_PATTERN = re.compile(
    r"### 问题分析经理 \[裁决\]\n(.*?)(?=\n---\n### |\Z)",
    re.DOTALL,
)


def load_layer1_state(output_dir: str) -> dict:
    """从既有输出目录恢复 Layer 1 状态。

    Args:
        output_dir: 已完成的 Layer 1 输出目录。

    Returns:
        至少含 ``problem_report`` 的字典；若 `<dir>/sensitivity_decision.json`
        存在且解析成功，额外含 ``sensitivity_enabled`` / ``sensitivity_reason``。
        json 缺失或解析失败时不含这两个键（由上层判定未决并重新判定）。

    Raises:
        FileNotFoundError: 目录下没有 `Layer1_问题分析.md`。
    """
    layer1_path = Path(output_dir) / "Layer1_问题分析.md"
    if not layer1_path.exists():
        raise FileNotFoundError(f"Layer 1 输出文件不存在: {layer1_path}")

    content = layer1_path.read_text(encoding="utf-8")

    match = _VERDICT_PATTERN.search(content)
    if match:
        problem_report = match.group(1).strip()
        logger.info(f"[recovery] 已提取 ProblemManager 裁决段落 ({len(problem_report)} 字符)")
    else:
        problem_report = content
        logger.warning("[recovery] 未找到裁决段落，回退使用整个 Layer1 文件内容")

    recovered: dict = {"problem_report": problem_report}

    decision_path = Path(output_dir) / "sensitivity_decision.json"
    if decision_path.exists():
        try:
            decision = json.loads(decision_path.read_text(encoding="utf-8"))
            recovered["sensitivity_enabled"] = bool(decision.get("enabled"))
            recovered["sensitivity_reason"] = str(decision.get("reason", ""))
        except (json.JSONDecodeError, OSError, AttributeError) as e:
            logger.warning(f"[recovery] 敏感性决策 json 解析失败，按未决处理: {e}")

    return recovered
