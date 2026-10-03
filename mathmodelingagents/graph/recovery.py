"""Layer 1 状态恢复 — 从既有输出目录加载 Layer 1 产出（--from-layer1 支持）。

读取 `<dir>/Layer1_问题分析.md` 提取 ProblemManager 裁决文本作为 problem_report，
并读取 `<dir>/sensitivity_decision.json`（若存在）恢复敏感性决策。

diverge=True（--from-layer1 --diverge）时：跳过候选池回填（不锚定旧方案），
额外读取 `<dir>/Layer2_数学建模.md` 提取建模经理最终裁决作为 previous_model_spec
（文件缺失时 fail-open：记 warning，不设置该键）。
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

# 建模经理裁决段落提取（diverge 模式取 Layer2 最终方案）：多次裁决时取最后一次。
# 截断点为下一分隔线（`---` / `--- 第 N 轮 ---`）或下一 `### ` 小节——Layer2 文件
# 轮次间有 `--- 第 N 轮 ---` 分隔，不能沿用 Layer1 仅按小节截断的模式。
_MODELING_VERDICT_PATTERN = re.compile(
    r"### 建模经理 \[裁决\]\n(.*?)(?=\n---|\n### |\Z)",
    re.DOTALL,
)


def load_layer1_state(output_dir: str, diverge: bool = False) -> dict:
    """从既有输出目录恢复 Layer 1 状态。

    Args:
        output_dir: 已完成的 Layer 1 输出目录。
        diverge: --from-layer1 --diverge 发散模式。True 时跳过候选模型池
            回填，并尝试提取 Layer 2 最终方案为 ``previous_model_spec``。

    Returns:
        至少含 ``problem_report`` 的字典；若 `<dir>/sensitivity_decision.json`
        存在且解析成功，额外含 ``sensitivity_enabled`` / ``sensitivity_reason``。
        json 缺失或解析失败时不含这两个键（由上层判定未决并重新判定）。
        diverge=True 时不含 ``model_candidates``；`<dir>/Layer2_数学建模.md`
        存在且提取到非空文本时含 ``previous_model_spec``（提取失败为 fail-open，
        记 warning 不抛异常）。

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

    if not diverge:
        candidates_path = Path(output_dir) / "model_candidates.json"
        if candidates_path.exists():
            try:
                data = json.loads(candidates_path.read_text(encoding="utf-8"))
                text = str(data.get("text", "") or "").strip()
                if text:
                    recovered["model_candidates"] = text
            except (json.JSONDecodeError, OSError, AttributeError) as e:
                logger.warning(f"[recovery] 候选模型池 json 解析失败，跳过回填: {e}")
    else:
        layer2_path = Path(output_dir) / "Layer2_数学建模.md"
        if not layer2_path.exists():
            logger.warning(
                f"[recovery] diverge 模式：Layer2 文件不存在，跳过旧方案提取"
                f"（发散退化为仅去锚定）: {layer2_path}"
            )
        else:
            try:
                content = layer2_path.read_text(encoding="utf-8")
                matches = _MODELING_VERDICT_PATTERN.findall(content)
                # 多次裁决取最后一次（CONCLUDE 轮），未命中则回退整文件
                spec = (matches[-1] if matches else content).strip()
                if spec:
                    recovered["previous_model_spec"] = spec
                    logger.info(
                        f"[recovery] diverge 模式：已提取旧模型方案 "
                        f"({len(spec)} 字符, {'最终裁决' if matches else '整文件'})"
                    )
            except OSError as e:
                logger.warning(f"[recovery] diverge 模式：Layer2 文件读取失败，跳过旧方案提取: {e}")

    return recovered
