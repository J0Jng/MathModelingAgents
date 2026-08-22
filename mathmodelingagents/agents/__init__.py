"""Agent 工厂函数 — 为每层每个角色创建 LangGraph 节点。

每个 create_* 函数接收 config 并返回一个可调用节点，
该节点接收 AgentState 并返回部分状态更新字典。
所有节点通过 langchain_openai.ChatOpenAI 调用 LLM。
"""

import logging
import re
from typing import Any, Callable

from langchain_core.messages import SystemMessage, HumanMessage

from mathmodelingagents.agents.utils.agent_states import AgentState
from mathmodelingagents.agents.utils.prompt_templates import get_prompt, get_global_constraints
from mathmodelingagents.llm_clients import invoke_with_fallback, resolve_max_tokens, is_retryable_error, create_layer_llm
from mathmodelingagents.default_config import resolve_sensitivity_mode
from mathmodelingagents.tools.web_search import web_search

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════
# 输出记录辅助
# ═══════════════════════════════════════════════════════════════════

def _record(state: AgentState, agent: str, layer: str, role: str,
            round_num: int, output: str) -> list:
    """追加一条 Agent 输出记录到 layer_outputs。"""
    records = list(state.get("layer_outputs", []))
    records.append({
        "agent": agent,
        "layer": layer,
        "role": role,
        "round_num": round_num,
        "output": output,
    })
    return records


def _run_sensitivity_decision(config: dict, problem_report: str) -> tuple[bool, str]:
    """Layer 1 敏感性决策（ADR-0001）：结构化 LLM 调用判定本题是否需要敏感性分析。

    ProblemManager 裁决 CONCLUDE 后调用，使用 problem 层 manager 档模型 +
    function calling（with_structured_output），Manager 本体保持无工具。
    任何异常（含结构化输出形状异常）fail-open 默认执行。

    Args:
        config: 全局配置。
        problem_report: ProblemManager 的最终裁决文本（含完整问题分析）。

    Returns:
        (enabled, reason) 二元组。
    """
    from pydantic import BaseModel, Field

    class SensitivityDecision(BaseModel):
        """敏感性决策结构化 schema。"""
        enabled: bool = Field(description="本题是否需要敏感性分析")
        reason: str = Field(description="一两句判断理由：题目是否存在值得扰动检验的关键参数、权重或不确定假设")

    try:
        # 温度不显式传：manager 角色由 temperature_overrides 解析为 0.1（低温），
        # 避免绕过配置链
        llm = create_layer_llm(config, "problem", "manager", max_tokens=1024)
        structured = llm.with_structured_output(SensitivityDecision)
        decision = structured.invoke([
            SystemMessage(content=(
                "你是数学建模竞赛的评审专家。根据题目分析报告，判断本题是否需要敏感性分析"
                "（灵敏度分析）：题目是否存在值得扰动检验的关键参数、权重或不确定假设。"
                "通常含优化参数、预测模型或权重设定的问题需要；"
                "纯描述统计或数据呈现类问题不需要。"
            )),
            HumanMessage(content=f"## 题目分析报告\n\n{problem_report}\n\n请给出敏感性决策。"),
        ])
        enabled = bool(decision.enabled)
        reason = str(getattr(decision, "reason", "") or "").strip() or "（未给出理由）"
        logger.info(f"[problem] 敏感性决策: {'启用' if enabled else '跳过'} - {reason}")
        return enabled, reason
    except Exception as e:
        logger.warning(f"[problem] 敏感性决策调用失败，fail-open 默认执行: {e}")
        return True, f"决策调用失败（{e}），fail-open 默认执行"


def _extract_final_output(messages: list) -> str:
    """从消息列表中提取最后一个非工具调用的文本输出。

    从消息列表末尾反向遍历，找到第一条有内容且不含 tool_calls
    且不是 tool 类型消息的内容，作为 Agent 的最终文本输出。

    Args:
        messages: LangChain 消息列表。

    Returns:
        Agent 最终文本输出，若未找到则返回空字符串。
    """
    for msg in reversed(messages):
        content = getattr(msg, "content", "") or ""
        has_tools = bool(getattr(msg, "tool_calls", None))
        tool_msg = getattr(msg, "type", "") == "tool"
        if content and not has_tools and not tool_msg:
            return content
    return ""


def _sanitize_tool_pairing(messages: list) -> list:
    """清洗消息列表中的孤立 ToolMessage 和未完成的 tool_calls AIMessage。

    处理两种截断导致的损坏场景：
    1. ToolMessage 的前置 AIMessage(tool_calls) 被截断切掉 → 丢弃该 ToolMessage
    2. AIMessage(tool_calls) 的对应 ToolMessage 被截断切掉 → 丢弃该 AIMessage
       （含末尾未完成和中间缺失两种情况，避免 API 400）

    采用计数法：对每条带 tool_calls 的 AIMessage，统计紧随其后的连续
    ToolMessage 数量。若数量不足，丢弃 AIMessage 及所有残余 ToolMessage。
    若 ToolMessage 超出 tool_calls 数量，多余 ToolMessage 视为孤立丢弃。
    不依赖 tool_call_id 匹配，因截断后 ID 可能失效。

    空列表安全返回；正常配对的 tool_calls/ToolMessage 序列原样保留。

    Args:
        messages: LangChain 消息列表。

    Returns:
        清洗后的消息列表（新列表，不修改原列表）。
    """
    if not messages:
        return messages

    cleaned: list = []
    i = 0
    n = len(messages)

    while i < n:
        msg = messages[i]
        is_tool = getattr(msg, "type", "") == "tool"
        tool_calls = getattr(msg, "tool_calls", None)

        if is_tool:
            # 孤立 ToolMessage：前面没有带 tool_calls 的 AIMessage
            content = str(getattr(msg, "content", ""))[:80]
            logger.warning(
                "_sanitize_tool_pairing: 丢弃孤立 ToolMessage "
                "(前一条消息无 tool_calls): %s",
                content,
            )
            i += 1
            continue

        if tool_calls:
            expected = len(tool_calls)
            # 统计紧随其后的连续 ToolMessage 数量
            actual = 0
            j = i + 1
            while j < n and getattr(messages[j], "type", "") == "tool":
                actual += 1
                j += 1

            if actual == 0:
                # 无任何 ToolMessage 跟随 → 丢弃此 AIMessage 避免 API 400
                content = str(getattr(msg, "content", ""))[:80]
                logger.warning(
                    "_sanitize_tool_pairing: 丢弃 tool_calls AIMessage "
                    "(无对应 ToolMessage): %s",
                    content,
                )
                i += 1
                continue

            if actual < expected:
                # ToolMessage 不足 → 丢弃 AIMessage + 所有残余 ToolMessage
                logger.warning(
                    "_sanitize_tool_pairing: 丢弃不完整 tool_calls "
                    "AIMessage（期望 %d 个 ToolMessage，实际 %d 个）",
                    expected, actual,
                )
                i = i + 1 + actual
                continue

            # actual >= expected：保留 AIMessage + 恰好 expected 个 ToolMessage
            cleaned.append(msg)
            for k in range(i + 1, i + 1 + expected):
                cleaned.append(messages[k])

            if actual > expected:
                # 多余的 ToolMessage 视为孤立，逐条警告但已跳过
                for k in range(i + 1 + expected, i + 1 + actual):
                    orphan_content = str(getattr(messages[k], "content", ""))[:80]
                    logger.warning(
                        "_sanitize_tool_pairing: 丢弃多余 ToolMessage "
                        "(tool_calls 仅 %d 个): %s",
                        expected, orphan_content,
                    )

            i = i + 1 + actual
            continue

        cleaned.append(msg)
        i += 1

    return cleaned


# ═══════════════════════════════════════════════════════════════════
# 共享 tool-calling 循环（Ticket D 收敛）：Solver/Viz/Paper 三 agent 复用同一骨架
# ═══════════════════════════════════════════════════════════════════

def _run_tool_loop(
    *,
    llm,
    llm_with_tools,
    tools,
    layer_tag: str,
    agent_tag: str,
    max_iterations: int,
    initial_messages: list,
    max_retries: int = 3,
    consecutive_no_tool_limit: int = 3,
    sanitize: Callable[[list], list] | None = None,
    on_summary_after_exhaust: Callable | None = None,
    on_selfcheck: Callable | None = None,
) -> tuple[list, str]:
    """运行 agentic tool-calling 循环，返回 (messages, final_output)。

    invoke（含 retry 退避）→ 派发工具 → consecutive_no_tool 计数 →
    SELF_CHECK_PASSED 自检 → _extract_final_output。差异由调用方通过回调表达：
    - sanitize:           每次交给 llm 前清洗消息（Paper REVISE 堵住裸回放损坏 pairing）
    - on_summary_after_exhaust: 仅 Solver，自检未通过时追加 summary 兜底
    - on_selfcheck:       仅 Paper，SELF_CHECK 后读盘补充论文正文

    logging 文案逐字保留（layer_tag/agent_tag 注入前缀），依赖日志的测试/运维不受影响。
    与原始实现一致：重试耗尽或非瞬态错误仍然向上抛出（不静默吞错）。
    """
    import json as _json
    import time as _time
    from langchain_core.messages import ToolMessage

    messages = list(initial_messages)
    consecutive_no_tool = 0

    for iteration in range(max_iterations):
        logger.info(
            f"[{layer_tag}] {agent_tag} iteration {iteration + 1}/{max_iterations}"
        )

        # ── 交给 llm 前清洗（可选；对配对良好的消息为无副作用，仅去掉孤立消息）──
        if sanitize is not None:
            messages = sanitize(messages)

        # ── Invoke LLM with retry ──
        response = None
        for attempt in range(1, max_retries + 1):
            try:
                response = llm_with_tools.invoke(messages)
                break
            except Exception as e:
                if attempt < max_retries and is_retryable_error(e):
                    delay = 2 ** attempt
                    logger.warning(
                        f"[{layer_tag}] {agent_tag} LLM 调用重试 {attempt}/{max_retries}, "
                        f"{delay}s: {e}"
                    )
                    _time.sleep(delay)
                else:
                    raise

        messages.append(response)

        # ── Process tool calls ──
        if response.tool_calls:
            consecutive_no_tool = 0
            for tc in response.tool_calls:
                tool_name = tc.get("name", "")
                tool_args = tc.get("args", {})
                tool_id = tc.get("id", "")

                tool_fn = None
                for t in tools:
                    if t.name == tool_name:
                        tool_fn = t
                        break

                if tool_fn is not None:
                    try:
                        result = tool_fn.invoke(tool_args)
                    except Exception as e:
                        result = f"[工具执行异常] {tool_name}: {e}"
                        logger.error(
                            f"[{layer_tag}] 工具 {tool_name} 执行失败: {e}"
                        )
                else:
                    result = f"[未知工具] {tool_name}"

                result_str = (
                    _json.dumps(result, ensure_ascii=False)
                    if isinstance(result, dict) else str(result)
                )
                messages.append(ToolMessage(
                    content=result_str, tool_call_id=tool_id,
                ))
                logger.info(
                    f"[{layer_tag}] {agent_tag} 工具 {tool_name}: "
                    f"{result_str[:120]}..."
                )
        else:
            consecutive_no_tool += 1
            content = response.content or ""

            if "SELF_CHECK_PASSED" in content:
                logger.info(
                    f"[{layer_tag}] {agent_tag} 自检通过 "
                    f"(iteration {iteration + 1})"
                )
                break

            if consecutive_no_tool >= consecutive_no_tool_limit:
                logger.warning(
                    f"[{layer_tag}] {agent_tag} {consecutive_no_tool} 轮无工具调用，"
                    f"强制中断"
                )
                break

    # ── Extract final text output ──
    final_output = _extract_final_output(messages)

    # ── 兜底：仅 Solver（自检未通过且回调非空）──
    if "SELF_CHECK_PASSED" not in final_output and on_summary_after_exhaust is not None:
        summary_text = on_summary_after_exhaust(llm, messages)
        if summary_text:
            final_output = summary_text

    # ── 收尾：仅 Paper（SELF_CHECK 后读盘补正文）──
    if on_selfcheck is not None and "SELF_CHECK_PASSED" in final_output:
        final_output = on_selfcheck(final_output, messages) or final_output

    return messages, final_output


def _solver_summary(llm, messages) -> str | None:
    """Layer3 SolverAgent 的 summary 兜底：自检未通过时让 LLM 总结已完成工作。"""
    logger.info("[Layer3] SolverAgent 未自检通过，追加 summary 调用...")
    try:
        summary_msg = HumanMessage(content=(
            "你的工具调用轮次已用完。请用一段文字总结你的工作成果：\n"
            "1. 你创建了哪些代码文件？（用 write_file 保存的）\n"
            "2. results.json 保存在哪里？里面有哪些关键数值？\n"
            "3. 代码是否能成功执行？如果还有问题，列出未解决的部分。\n\n"
            "请直接总结，不要调用工具。"
        ))
        summary_response = llm.invoke(_sanitize_tool_pairing(messages) + [summary_msg])
        summary_text = summary_response.content or ""
        if summary_text:
            logger.info(
                f"[Layer3] SolverAgent summary: "
                f"{len(summary_text)} 字符"
            )
            return summary_text
    except Exception as e:
        logger.warning(f"[Layer3] SolverAgent summary 调用失败: {e}")
    return None


def _paper_read_disk(output_dir: str, final_output: str, messages: list) -> str:
    """Layer4 PaperAgent 收尾：SELF_CHECK 通过后读盘补充论文正文。

    仅在 final_output 含 SELF_CHECK_PASSED 时生效；无正文则回退到文本输出。
    """
    if "SELF_CHECK_PASSED" not in final_output:
        return final_output
    from pathlib import Path as _Path
    paper_candidates = ["paper.md", "PaperAgent_paper.md", "final_paper.md"]
    paper_dir = _Path(output_dir)
    paper_text = ""
    for fname in paper_candidates:
        fpath = paper_dir / fname
        if fpath.is_file():
            try:
                paper_text = fpath.read_text(encoding="utf-8")
                logger.info(
                    f"[Layer4] PaperAgent 从磁盘读取论文: "
                    f"{fpath.name} ({len(paper_text)} 字符)"
                )
                break
            except Exception as e:
                logger.warning(
                    f"[Layer4] PaperAgent 读取论文文件失败 {fpath}: {e}"
                )
    if paper_text and len(paper_text) > len(final_output):
        combined = (
            f"{final_output}\n\n"
            f"---\n"
            f"## 论文正文\n\n{paper_text}"
        )
        logger.info(
            f"[Layer4] PaperAgent 输出已补充论文正文: "
            f"{len(paper_text)} 字符"
        )
        return combined
    logger.info(
        f"[Layer4] PaperAgent 未找到论文文件，已回退到文本输出"
    )
    return final_output


# ═══════════════════════════════════════════════════════════════════
# ImplManager 文件扫描辅助 — 参考 TradingAgents Manager 模式：
# 不依赖工具调用，在 LLM 调用前用 Python 扫描磁盘产出、
# 将文件清单和关键内容注入审查上下文。
# ═══════════════════════════════════════════════════════════════════

def _scan_solver_output(output_dir: str) -> dict:
    """扫描 SolverAgent 在磁盘上的产出文件。

    返回 dict:
        file_count: 文件总数
        file_names: 文件名列表
        file_details: 每个文件的详细信息（大小、行数等）
        results_json_summary: results.json 的摘要（若存在）
        code_file_listing: 代码文件路径列表
    """
    from pathlib import Path
    import json as _json

    root = Path(output_dir)
    result = {
        "file_count": 0,
        "file_names": [],
        "file_details": [],
        "results_json_summary": "",
        "code_file_listing": [],
    }

    # 扫描 code/ 目录
    code_dir = root / "code"
    if code_dir.is_dir():
        for f in sorted(code_dir.iterdir()):
            if f.is_file():
                size = f.stat().st_size
                lines = f.read_text(encoding="utf-8", errors="replace").count("\n") + 1
                result["file_count"] += 1
                result["file_names"].append(f"code/{f.name}")
                result["file_details"].append(
                    f"  code/{f.name} — {size:,} bytes, {lines} 行"
                )
                result["code_file_listing"].append(str(f))

    # 扫描 results/ 目录
    results_dir = root / "results"
    if results_dir.is_dir():
        for f in sorted(results_dir.iterdir()):
            if f.is_file():
                size = f.stat().st_size
                result["file_count"] += 1
                result["file_names"].append(f"results/{f.name}")

                if f.suffix == ".json":
                    result["file_details"].append(
                        f"  results/{f.name} — {size:,} bytes (JSON)"
                    )
                    if f.name == "results.json":
                        try:
                            data = _json.loads(f.read_text(encoding="utf-8"))
                            keys = list(data.keys()) if isinstance(data, dict) else []
                            result["results_json_summary"] = (
                                f"results.json 存在，包含顶层键: {keys}"
                            )
                            # 提取每个子问题的关键数值
                            for key in keys:
                                if isinstance(data[key], dict):
                                    nums = {
                                        k: v for k, v in data[key].items()
                                        if isinstance(v, (int, float)) and not isinstance(v, bool)
                                    }
                                    if nums:
                                        result["results_json_summary"] += (
                                            f"\n  {key}: {nums}"
                                        )
                        except Exception:
                            result["results_json_summary"] = "results.json 存在但解析失败"

                elif f.suffix == ".png":
                    result["file_details"].append(
                        f"  results/{f.name} — {size:,} bytes (PNG 图表)"
                    )
                else:
                    desc = f"  results/{f.name} — {size:,} bytes"
                    if not f.suffix:
                        desc += " (无扩展名)"
                    result["file_details"].append(desc)

    return result


def _format_file_evidence(evidence: dict) -> str:
    """将扫描结果格式化为 ImplManager 审查上下文的一部分。"""
    parts = ["## 🔍 SolverAgent 磁盘产出扫描"]

    if evidence["file_count"] == 0:
        parts.append(
            "\n⚠️ **未发现任何磁盘产出文件。** SolverAgent 可能未实际执行代码 "
            "或未保存结果。"
        )
        return "\n".join(parts)

    parts.append(f"\n扫描到 **{evidence['file_count']}** 个文件：\n")
    parts.extend(evidence["file_details"])

    if evidence["results_json_summary"]:
        parts.append(f"\n### results.json 内容摘要\n{evidence['results_json_summary']}")

    if evidence["code_file_listing"]:
        parts.append(f"\n### 代码文件列表 ({len(evidence['code_file_listing'])} 个)")
        for p in evidence["code_file_listing"]:
            parts.append(f"  - {p}")

    return "\n".join(parts)


# ═══════════════════════════════════════════════════════════════════
# 基础 LLM 节点工厂
# ═══════════════════════════════════════════════════════════════════

def _build_context(state: AgentState, layer: str, agent: str, config: dict) -> str:
    """根据层和角色，从 state 中构建传给 LLM 的上下文。

    跨层原则：建模层传入完整 problem_report，实现层传入完整 model_spec，
    论文层和敏感性层传入 layer_summary 摘要（Agent 用工具获取完整数据）。
    同层原则：辩论/重试循环内传递完整历史（Agent 需要互相看到发言）。
    VizAgent 特殊处理：跳过题目描述，只给数据和路径。
    """
    parts = []

    # 题目信息（所有层通用，但 VizAgent 不需要——它只读取 results.json）
    if agent != "viz_agent":
        problem = state.get("problem_description", "")
        if problem:
            parts.append(f"## 题目内容\n\n{problem}")

    # ── 跨层上下文：只传精华摘要 ──
    # 建模层和实现层已有完整的前层输出（problem_report / model_spec），摘要冗余
    # 论文层和敏感性层保留摘要作为快速定位（Agent 用工具获取完整数据）
    if layer not in ("problem", "modeling", "implementation") and state.get("layer_summary"):
        parts.append(f"## 前层综合摘要\n\n{state['layer_summary']}")

    # ── 同层上下文：辩论/重试循环内完整传递 ──
    if layer == "problem":
        if state.get("problem_report"):
            parts.append(f"## Decomposer 报告\n\n{state['problem_report']}")
        if state.get("data_insights"):
            parts.append(f"## DataAnalyst 报告\n\n{state['data_insights']}")
        if state.get("constraints"):
            parts.append(f"## ConstraintAnalyst 报告\n\n{state['constraints']}")
        if state.get("assumptions"):
            parts.append(f"## 假设清单\n\n{state['assumptions']}")

    elif layer == "modeling":
        # Layer 1 完整分析是建模的基础（替代摘要，提供完整的问题定义/数据/约束）
        if state.get("problem_report"):
            parts.append(f"## Layer 1 综合问题分析（建模基准——阅读后开始设计模型）\n\n{state['problem_report']}")
        if state.get("background_research"):
            parts.append(f"## 题目背景资料（Layer 1 自动搜索）\n\n{state['background_research']}")
        debate = state.get("model_debate_state", {})
        if debate.get("a_history"):
            parts.append(f"## 建模师 A 历史发言\n\n{debate['a_history']}")
        if debate.get("b_history"):
            parts.append(f"## 建模师 B 历史发言\n\n{debate['b_history']}")
        if debate.get("c_history"):
            parts.append(f"## 建模师 C 历史发言\n\n{debate['c_history']}")
        if debate.get("current_a_response"):
            parts.append(f"## 建模师 A 本轮发言\n\n{debate['current_a_response']}")
        if debate.get("current_b_response"):
            parts.append(f"## 建模师 B 本轮发言\n\n{debate['current_b_response']}")
        if debate.get("current_c_response"):
            parts.append(f"## 建模师 C 本轮发言\n\n{debate['current_c_response']}")

    elif layer == "implementation":
        if agent == "viz_agent":
            # VizAgent 只需知道数据在哪，不需要完整 SolverAgent 输出
            output_dir = config.get("output_dir", "output")
            parts.append(
                f"## 数据文件位置\n\n"
                f"- results.json: `{output_dir}/results/results.json`\n"
                f"- 求解脚本: `{output_dir}/code/solver.py`\n"
                f"- 图表保存到: `{output_dir}/results/` (PNG, 150 DPI)\n"
            )
        else:
            # Layer 2 model_spec 是编码的基准——必须原样传递给 SolverAgent 和 ImplManager
            if state.get("model_spec"):
                parts.append(f"## Layer 2 最终模型方案（编码基准——必须严格对照实现）\n\n{state['model_spec']}")
            if state.get("code_results"):
                parts.append(f"## SolverAgent 产出\n\n{state['code_results']}")
            if state.get("error_analysis"):
                parts.append(f"## 上一轮审查意见\n\n{state['error_analysis']}")

    elif layer == "paper":
        if state.get("paper_feedback"):
            parts.append(f"## ⚠️ 上一轮审查未通过\n\n以下是论文经理的修改意见，请逐条修正（只修改有问题的节，不要重写其他节）：\n\n{state['paper_feedback']}")
        if state.get("final_paper"):
            parts.append(f"## 上一轮论文\n\n{state['final_paper']}")

    elif layer == "sensitivity":
        if state.get("sensitivity_scan"):
            parts.append(f"## 敏感性扫描\n\n{state['sensitivity_scan']}")
        if state.get("sensitivity_report"):
            parts.append(f"## 敏感性报告\n\n{state['sensitivity_report']}")

    # ── 敏感性分析计划（ADR-0001）：决策+理由注入建模/实现层，为扰动预留参数面 ──
    if layer in ("modeling", "implementation"):
        from mathmodelingagents.graph.conditional_logic import sensitivity_active
        reason = state.get("sensitivity_reason", "")
        if sensitivity_active(config, state):
            parts.append(
                "## 敏感性分析计划\n\n"
                "本题将进行敏感性分析（灵敏度分析）。请在建模和实现中将关键参数"
                "显式化为可调输入，并在求解结果中结构化输出参数取值，"
                "以便后续扰动检验结论稳健性。"
                + (f"\n\nLayer 1 决策理由：{reason}" if reason else "")
            )
        else:
            parts.append(
                "## 敏感性分析计划\n\n本题不进行敏感性分析。"
                + (f"\n\nLayer 1 决策理由：{reason}" if reason else "")
            )

    # 元信息
    debate = state.get("model_debate_state", {})
    round_info = debate.get("round_count", 0)
    max_rounds = config.get("max_debate_rounds", 10)
    remaining = max(0, max_rounds - round_info)
    current_layer_info = state.get('current_layer', layer)
    output_dir = config.get("output_dir", "output")
    from mathmodelingagents.default_config import resolve_sensitivity_mode
    parts.append(
        f"## 当前状态\n"
        f"- 当前层: {current_layer_info}\n"
        f"- 输出目录: {output_dir}\n"
        f"- 辩论轮次: {round_info}/{max_rounds} (剩余 {remaining} 轮)\n"
        f"- 实现重试次数: {state.get('impl_retry_count', 0)}\n"
        f"- 敏感性模式: {resolve_sensitivity_mode(config)}"
    )

    return "\n\n---\n\n".join(parts)


def _make_llm_node(
    config: dict,
    agent_name: str,
    layer: str,
    role: str,
    state_key: str,
) -> Callable[[AgentState], dict[str, Any]]:
    """创建通用 LLM Agent 节点。

    Args:
        config: 全局配置
        agent_name: prompt 注册表中的 agent 名（如 'decomposer'）
        layer: 层名（如 'problem', 'modeling', 'implementation'）
        role: 角色名（如 'agent', 'manager'）
        state_key: 输出写入的 state 字段名

    Returns:
        LangGraph 节点函数
    """
    def node_fn(state: AgentState) -> dict[str, Any]:
        logger.info(f"[{layer}] {agent_name} 执行中...")
        from mathmodelingagents.llm_clients import get_layer_model
        model = get_layer_model(config, layer, role)
        print(f"[{layer}] {agent_name} ⏳ calling {model}...", flush=True)

        # ── 解析 max_tokens ──
        max_tok = resolve_max_tokens(config, role, agent_name)

        # 获取静态 prompt（所有动态变量已移至用户消息以实现缓存）
        system_prompt = get_prompt(agent_name)

        # 追加全局约束（Layer 1 的 agent 需要）
        if layer == "problem" and role != "manager":
            system_prompt = system_prompt + "\n\n" + get_global_constraints()

        # 构建用户消息（上下文 + 动态配置均在此）
        context = _build_context(state, layer, agent_name, config)
        user_msg = f"请根据以下上下文执行你的任务：\n\n{context}"

        # 调用 LLM（统一降级链）
        messages = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_msg),
        ]
        try:
            result = invoke_with_fallback(config, layer, role, messages, agent_name, max_tokens=max_tok)
            logger.info(f"[{layer}] {agent_name} 完成，输出 {len(result)} 字符")
            print(f"[{layer}] {agent_name} ✅ {len(result)} chars", flush=True)
        except Exception as e:
            logger.error(f"[{layer}] {agent_name} 全部降级耗尽: {e}")
            result = f"LLM 调用失败（全部降级耗尽）: {e}"

        round_num = state.get("model_debate_state", {}).get("round_count", 0) or 1
        return {state_key: result, "layer_outputs": _record(state, agent_name, layer, role, round_num, result)}

    node_fn.__name__ = agent_name
    return node_fn


def _make_manager_node(
    config: dict,
    agent_name: str,
    layer: str,
    role: str = "manager",
) -> Callable[[AgentState], dict[str, Any]]:
    """创建 Manager 节点（需处理 CONTINUE/CONCLUDE 裁决）。

    Manager 的 prompt 会要求输出 **CONCLUDE** 或 **CONTINUE**，
    我们解析 LLM 输出中的裁决决定，更新 debate_state。
    """
    def node_fn(state: AgentState) -> dict[str, Any]:
        logger.info(f"[{layer}] {agent_name} 执行中...")
        from mathmodelingagents.llm_clients import get_layer_model
        model = get_layer_model(config, layer, role)
        print(f"[{layer}] {agent_name} ⏳ calling {model}...", flush=True)

        # ── 解析 max_tokens ──
        max_tok = resolve_max_tokens(config, role, agent_name)

        # 辩论状态
        debate = dict(state.get("model_debate_state", {}))
        round_count = debate.get("round_count", 0) + 1

        # 静态 prompt + 层摘要要求
        system_prompt = (
            get_prompt(agent_name)
            + "\n\n## 层摘要要求\n"
            "若裁决为 CONCLUDE，你必须在输出末尾附加一段「## 层摘要」，"
            "用 200-400 字精炼总结本层的核心产出，供下一层 Agent 使用。"
            "摘要只需包含：关键结论、核心数据、最终方案要点。不要包含裁决标记。"
        )

        context = _build_context(state, layer, agent_name, config)
        user_msg = f"请根据以下上下文进行裁决：\\n\\n{context}"

        messages = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_msg),
        ]
        try:
            result = invoke_with_fallback(config, layer, role, messages, agent_name, max_tokens=max_tok)
        except Exception as e:
            logger.error(f"[{layer}] {agent_name} 全部降级耗尽: {e}")
            result = f"LLM 调用失败（全部降级耗尽）: {e}"

        # 解析裁决决定（Paper 层用 REVISE，其他层用 CONTINUE/RETRY）
        if "**CONCLUDE**" in result:
            judge_decision = "CONCLUDE"
        elif "**REVISE**" in result:
            judge_decision = "REVISE"
        elif "**CONTINUE**" in result or "**RETRY**" in result:
            judge_decision = "CONTINUE"
        else:
            # 默认：首轮后继续，或回合数达到上限则结束
            max_r = config.get("max_debate_rounds", 10)
            judge_decision = "CONCLUDE" if round_count >= max_r else "CONTINUE"
            logger.info(f"[{layer}] {agent_name} 未检测到明确裁决，默认: {judge_decision}")

        debate["round_count"] = round_count
        debate["judge_decision"] = judge_decision
        if judge_decision == "CONCLUDE":
            debate["history"] = result

        updates: dict[str, Any] = {
            "model_debate_state": debate,
        }

        # ── 提取层摘要（仅 CONCLUDE 时）──
        if judge_decision == "CONCLUDE":
            summary_match = re.search(
                r'## 层摘要\s*\n(.*?)(?=\n## |\n\*\*|\Z)',
                result, re.DOTALL,
            )
            if summary_match:
                summary_text = summary_match.group(1).strip()
                layer_names = {
                    "problem": "Layer 1 问题分析",
                    "modeling": "Layer 2 数学建模",
                    "implementation": "Layer 3 代码实现",
                    "paper": "Layer 4 论文写作",
                    "sensitivity": "Layer 5 敏感性分析",
                }
                layer_label = layer_names.get(layer, layer)
                existing = state.get("layer_summary", "")
                if existing:
                    updates["layer_summary"] = existing + f"\n\n### {layer_label}\n{summary_text}"
                else:
                    updates["layer_summary"] = f"### {layer_label}\n{summary_text}"
                logger.info(f"[{layer}] 层摘要已提取 ({len(summary_text)} 字符)")
            else:
                logger.warning(f"[{layer}] 未在 Manager 输出中找到 ## 层摘要 标记")

            # ── Layer 1 敏感性决策（ADR-0001）：CONCLUDE 后结构化调用 ──
            # 仅 auto 模式需要该决策；always/never 由模式直接接管，避免白跑一次 LLM
            if layer == "problem" and resolve_sensitivity_mode(config) == "auto":
                enabled, reason = _run_sensitivity_decision(config, result)
                updates["sensitivity_enabled"] = enabled
                updates["sensitivity_reason"] = reason
                print(f"[problem] 敏感性决策: {'✅ 启用' if enabled else '⏭️ 跳过'} - {reason}", flush=True)

        # 根据层写入特定字段
        if layer == "problem":
            updates["problem_report"] = result
        elif layer == "modeling":
            updates["model_spec"] = result
        elif layer == "implementation":
            updates["code_results"] = result
        elif layer == "paper":
            # CONCLUDE → 用 Manager 的整合输出作为最终论文
            # REVISE → 保留 PaperAgent 的输出，存入修改意见供其查看
            if judge_decision == "CONCLUDE":
                updates["final_paper"] = result
            else:
                # REVISE — 把审查反馈单独存起来，PaperAgent 下一轮会看到
                updates["paper_feedback"] = result
        elif layer == "sensitivity":
            updates["sensitivity_report"] = result

        logger.info(f"[{layer}] {agent_name} 裁决: {judge_decision} (round {round_count})")
        print(f"[{layer}] {agent_name} ✅ {judge_decision} ({len(result)} chars)", flush=True)
        updates["layer_outputs"] = _record(state, agent_name, layer, role, round_count, result)
        return updates

    node_fn.__name__ = agent_name
    return node_fn


# ═══════════════════════════════════════════════════════════════════
# Layer 1: Problem Analysis
# ═══════════════════════════════════════════════════════════════════

def create_decomposer(config: dict) -> Callable[[AgentState], dict[str, Any]]:
    def node_fn(state: AgentState) -> dict[str, Any]:
        logger.info("[Layer1] Decomposer 执行中...")
        max_tok = resolve_max_tokens(config, "agent", "decomposer")
        system_prompt = get_prompt("decomposer") + "\n\n" + get_global_constraints()
        context = _build_context(state, "problem", "decomposer", config)
        user_msg = f"请根据以下上下文执行你的任务：\n\n{context}"

        # ── 预搜索注入：题目背景知识（失败静默，不影响主流程）──
        # web_search 模块内部统一吞错并返回 [搜索失败] 文案（不 raise），
        # 这里只检查前缀，不再两层 try/except 吞错。
        search_combined = ""
        problem_text = (state.get("problem_description") or "").strip()
        if problem_text:
            parts_search = []

            # 查询 1: 题目背景（题目前 150 字符）
            r1 = web_search(problem_text[:150], max_results=5, config=config)
            if not r1.startswith("[搜索失败]") and not r1.startswith("[搜索未启用]"):
                parts_search.append(f"### 查询 1（题目背景）\n{r1}")

            # 查询 2: 建模方法参考（题目首行 + " 数学建模"）
            first_line = problem_text.split("\n")[0].strip()
            if len(first_line) >= 10:
                q2 = (first_line[:80] + " 数学建模").strip()
                r2 = web_search(q2, max_results=3, config=config)
                if not r2.startswith("[搜索失败]") and not r2.startswith("[搜索未启用]"):
                    parts_search.append(f"### 查询 2（建模方法参考）\n{r2}")

            if parts_search:
                search_combined = "\n\n".join(parts_search)
                user_msg += "\n\n## 题目背景资料（自动搜索）\n\n" + search_combined

        messages = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_msg),
        ]
        try:
            result = invoke_with_fallback(config, "problem", "agent", messages, "decomposer", max_tokens=max_tok)
        except Exception as e:
            logger.error(f"[Layer1] decomposer 全部降级耗尽: {e}")
            result = f"LLM 调用失败（全部降级耗尽）: {e}"
        return {
            "problem_report": result,
            "background_research": search_combined,  # 原始搜索资料（可能为空字符串），供 Layer 2 直接使用
            "layer_outputs": _record(state, "decomposer", "problem", "agent", 1, result),
        }

    node_fn.__name__ = "decomposer"
    return node_fn


def create_data_analyst(config: dict) -> Callable[[AgentState], dict[str, Any]]:
    return _make_llm_node(config, "data_analyst", "problem", "agent", "data_insights")


def create_constraint_analyst(config: dict) -> Callable[[AgentState], dict[str, Any]]:
    def node_fn(state: AgentState) -> dict[str, Any]:
        logger.info("[Layer1] ConstraintAnalyst 执行中...")

        # ── 解析 max_tokens ──
        max_tok = resolve_max_tokens(config, "agent", "constraint_analyst")

        system_prompt = get_prompt("constraint_analyst") + "\n\n" + get_global_constraints()

        context = _build_context(state, "problem", "constraint_analyst", config)
        user_msg = f"请根据以下上下文执行你的任务：\n\n{context}"

        messages = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_msg),
        ]
        try:
            result = invoke_with_fallback(config, "problem", "agent", messages, "constraint_analyst", max_tokens=max_tok)
        except Exception as e:
            logger.error(f"[Layer1] constraint_analyst 全部降级耗尽: {e}")
            result = f"LLM 调用失败（全部降级耗尽）: {e}"

        # ConstraintAnalyst 输出同时写入 constraints 和 assumptions
        return {
            "constraints": result,
            "assumptions": result,
            "layer_outputs": _record(state, "constraint_analyst", "problem", "agent", 1, result),
        }

    node_fn.__name__ = "constraint_analyst"
    return node_fn


def create_problem_manager(config: dict) -> Callable[[AgentState], dict[str, Any]]:
    return _make_manager_node(config, "problem_manager", "problem")


# ═══════════════════════════════════════════════════════════════════
# Layer 2: Modeling (Debate)
# ═══════════════════════════════════════════════════════════════════

def _make_modeler_node(
    config: dict,
    agent_name: str,
    response_key: str,
    history_key: str,
) -> Callable[[AgentState], dict[str, Any]]:
    """创建建模师节点（辩论参与者）。"""
    def node_fn(state: AgentState) -> dict[str, Any]:
        logger.info(f"[Layer2] {agent_name} 执行中...")

        # ── 解析 max_tokens ──
        max_tok = resolve_max_tokens(config, "agent", agent_name)

        debate = dict(state.get("model_debate_state", {}))
        round_count = debate.get("round_count", 0)  # 不递增，由 Manager 管理轮数

        system_prompt = get_prompt(agent_name)

        context = _build_context(state, "modeling", agent_name, config)
        user_msg = f"请根据以下上下文执行你的任务。当前是第 {round_count} 轮：\n\n{context}"

        messages = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_msg),
        ]
        try:
            result = invoke_with_fallback(config, "modeling", "agent", messages, agent_name, max_tokens=max_tok)
        except Exception as e:
            logger.error(f"[Layer2] {agent_name} 全部降级耗尽: {e}")
            result = f"LLM 调用失败（全部降级耗尽）: {e}"

        # 更新辩论状态
        debate["round_count"] = round_count
        debate[response_key] = result
        debate["latest_speaker"] = agent_name
        # 追加到历史
        existing = debate.get(history_key, "")
        debate[history_key] = existing + f"\n\n--- 第 {round_count} 轮 ---\n{result}"

        return {
            "model_debate_state": debate,
            "layer_outputs": _record(state, agent_name, "modeling", "agent", round_count, result),
        }

    node_fn.__name__ = agent_name
    return node_fn


def create_modeler_a(config: dict) -> Callable[[AgentState], dict[str, Any]]:
    return _make_modeler_node(config, "modeler_a", "current_a_response", "a_history")


def create_modeler_b(config: dict) -> Callable[[AgentState], dict[str, Any]]:
    return _make_modeler_node(config, "modeler_b", "current_b_response", "b_history")


def create_modeler_c(config: dict) -> Callable[[AgentState], dict[str, Any]]:
    return _make_modeler_node(config, "modeler_c", "current_c_response", "c_history")


def create_modeling_manager(config: dict) -> Callable[[AgentState], dict[str, Any]]:
    return _make_manager_node(config, "modeling_manager", "modeling")


# ═══════════════════════════════════════════════════════════════════
# Layer 3: Implementation — SolverAgent + VizAgent + ImplManager
# ═══════════════════════════════════════════════════════════════════

def create_solver_agent(config: dict) -> Callable[[AgentState], dict[str, Any]]:
    """Layer 3 Solver Agent — 有工具、自我迭代的求解 Agent。

    使用 LangChain tool calling 实现内部 agentic loop：
    写代码 → 执行(run_code) → 看结果 → 修复 → 再执行 → ... → 自检通过。

    RETRY 时保留消息历史（impl_messages），在已有的完整对话基础上
    追加 ImplManager 的审查反馈继续修改，而非冷启动重写。
    """
    from mathmodelingagents.tools import create_coding_agent_tools

    def node_fn(state: AgentState) -> dict[str, Any]:
        logger.info("[Layer3] SolverAgent 开始...")

        max_tok = resolve_max_tokens(config, "coder", "solver_agent")
        output_dir = config.get("output_dir", "output")

        # ── Build prompt and context ──
        system_prompt = get_prompt("solver_agent")

        # Check for existing messages (RETRY scenario with message persistence)
        existing = state.get("impl_messages") or []
        error_analysis = state.get("error_analysis", "")

        if existing and error_analysis:
            # RETRY mode: continue from previous conversation, append feedback
            # 截断策略：保留前 2 条（system+user）+ 后 18 条（最新上下文），
            # 中间截断防止消息历史过长导致上下文溢出或 API 崩溃。
            # 参考 TradingAgents：辩论历史是累积的文本字符串，而非完整消息列表。
            if len(existing) > 24:
                truncated = existing[:2] + existing[-18:]
                logger.info(
                    f"[Layer3] SolverAgent RETRY 截断消息: "
                    f"{len(existing)} → {len(truncated)} "
                    f"(保留首2+尾18)"
                )
                existing = _sanitize_tool_pairing(truncated)

            logger.info(
                f"[Layer3] SolverAgent RETRY 模式：继承 {len(existing)} 条消息，"
                f"追加审查反馈"
            )
            feedback_msg = HumanMessage(content=(
                f"## ⚠️ 上一轮审查未通过\n\n"
                f"以下是实现经理的修改意见，请逐条修正（只修改有问题的部分，"
                f"不要重写其他模块）：\n\n{error_analysis}"
            ))
            messages = existing + [feedback_msg]
        else:
            # First run: fresh messages
            context = _build_context(state, "implementation", "solver_agent", config)
            user_msg = f"请根据以下上下文执行你的任务：\n\n{context}"
            messages = [
                SystemMessage(content=system_prompt),
                HumanMessage(content=user_msg),
            ]

        # ── Create tools scoped to output_dir ──
        tools = create_coding_agent_tools(output_dir)
        llm = create_layer_llm(config, "implementation", "coder")
        llm_with_tools = llm.bind_tools(tools)

        # 共享 agentic loop；summary 兜底仅 Solver 启用
        messages, final_output = _run_tool_loop(
            llm=llm,
            llm_with_tools=llm_with_tools,
            tools=tools,
            layer_tag="Layer3",
            agent_tag="SolverAgent",
            max_iterations=30,
            initial_messages=messages,
            sanitize=_sanitize_tool_pairing,
            on_summary_after_exhaust=_solver_summary,
        )

        retry_count = state.get("impl_retry_count", 0)

        logger.info(
            f"[Layer3] SolverAgent 完成: {len(messages)} 条消息, "
            f"最终输出 {len(final_output)} 字符"
        )

        return {
            "code_results": final_output,
            "impl_messages": messages,  # 保存完整历史供 RETRY 时继承
            "layer_outputs": _record(
                state, "solver_agent", "implementation", "coder",
                retry_count + 1, final_output,
            ),
        }

    node_fn.__name__ = "solver_agent"
    return node_fn


def create_viz_agent(config: dict) -> Callable[[AgentState], dict[str, Any]]:
    """Layer 3 Viz Agent — 专注图表生成，从 results.json 读取数据。

    使用 LangChain tool calling 实现内部 agentic loop：
    读 results.json → 生成图表 → 验证 → 修复 → 自检通过。

    如果图表生成失败，通过 impl_messages 实现自循环重试。
    """
    from mathmodelingagents.tools import create_coding_agent_tools

    def node_fn(state: AgentState) -> dict[str, Any]:
        logger.info("[Layer3] VizAgent 开始...")

        max_tok = resolve_max_tokens(config, "coder", "viz_agent")
        output_dir = config.get("output_dir", "output")

        # ── Build prompt and context ──
        system_prompt = get_prompt("viz_agent")

        # Check for existing messages (RETRY scenario for VizAgent)
        existing = state.get("viz_results") or ""

        if existing and "SELF_CHECK_PASSED" not in existing:
            # VizAgent RETRY: append feedback to continue fixing charts
            logger.info("[Layer3] VizAgent RETRY 模式：继承消息继续修复图表")
            messages = _sanitize_tool_pairing(state.get("impl_messages") or []) + [
                HumanMessage(content=(
                    f"## ⚠️ 上一轮图表生成未完成\n\n"
                    f"上一轮产出如下，请检查缺失的图表并补充生成：\n\n{existing}"
                ))
            ]
        else:
            # First run: fresh messages, context from _build_context only
            context = _build_context(state, "implementation", "viz_agent", config)
            user_msg = (
                f"请根据以下上下文，读取 results.json 并生成全部图表：\n\n{context}"
            )
            messages = [
                SystemMessage(content=system_prompt),
                HumanMessage(content=user_msg),
            ]

        # ── Create tools scoped to output_dir ──
        tools = create_coding_agent_tools(output_dir)
        llm = create_layer_llm(config, "implementation", "coder")
        llm_with_tools = llm.bind_tools(tools)

        # 共享 agentic loop；Viz：max_iterations=10，无 summary / 读盘兜底
        messages, final_output = _run_tool_loop(
            llm=llm,
            llm_with_tools=llm_with_tools,
            tools=tools,
            layer_tag="Layer3",
            agent_tag="VizAgent",
            max_iterations=10,  # 图表生成应该很快
            initial_messages=messages,
            sanitize=_sanitize_tool_pairing,
        )

        logger.info(
            f"[Layer3] VizAgent 完成: {len(messages)} 条消息, "
            f"最终输出 {len(final_output)} 字符"
        )

        return {
            "viz_results": final_output,
            "impl_messages": messages,  # 保存供潜在 VizAgent 自循环
            "layer_outputs": _record(
                state, "viz_agent", "implementation", "coder",
                1, final_output,
            ),
        }

    node_fn.__name__ = "viz_agent"
    return node_fn


def create_impl_manager(config: dict) -> Callable[[AgentState], dict[str, Any]]:
    """实现经理 — 外部审查 SolverAgent 的产出，决定 RETRY/CONCLUDE。

    参考 TradingAgents 的 Manager 模式：单次 LLM 调用，不做工具循环。
    但在调用前扫描磁盘产出（代码文件、results.json 等），将文件清单
    和关键文件内容注入审查上下文，确保 Manager 能基于实际产出判断。
    """
    def node_fn(state: AgentState) -> dict[str, Any]:
        logger.info("[Layer3] ImplManager 执行中...")

        max_tok = resolve_max_tokens(config, "manager", "impl_manager")

        retry_count = state.get("impl_retry_count", 0) + 1
        max_retries = config.get("max_impl_retries", 3)
        output_dir = config.get("output_dir", "output")

        # ── 扫描 SolverAgent 磁盘产出 ──
        # 参考 TradingAgents：Manager 的 prompt 中直接注入完整的文本产出，
        # 不做工具调用循环。在调用 LLM 前用 Python 扫描文件清单，
        # 确保 Manager 能看到 SolverAgent 实际保存的文件。
        file_evidence = _scan_solver_output(output_dir)
        logger.info(
            f"[Layer3] ImplManager 扫描到 {file_evidence['file_count']} 个文件: "
            f"{', '.join(file_evidence['file_names'][:10])}"
        )

        system_prompt = (
            get_prompt("impl_manager")
            + "\n\n## 层摘要要求\n"
            "若裁决为 CONCLUDE，你必须在输出末尾附加一段「## 层摘要」，"
            "用 200-400 字精炼总结本层的核心产出，供下一层 Agent 使用。"
        )

        context = _build_context(state, "implementation", "impl_manager", config)

        # 追加文件证据到审查上下文
        evidence_text = _format_file_evidence(file_evidence)
        user_msg = (
            f"请审查 SolverAgent 的产出并裁决"
            f"（当前重试 {retry_count}/{max_retries}）：\n\n{context}"
            f"\n\n---\n\n{evidence_text}"
        )

        messages = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_msg),
        ]
        try:
            result = invoke_with_fallback(
                config, "implementation", "manager", messages,
                "impl_manager", max_tokens=max_tok,
            )
        except Exception as e:
            logger.error(f"[Layer3] impl_manager 全部降级耗尽: {e}")
            result = f"LLM 调用失败（全部降级耗尽）: {e}"

        # ── 解析裁决 ──
        if "**CONCLUDE**" in result:
            error_analysis = ""
        elif "**RETRY**" in result and retry_count < max_retries:
            error_analysis = result
        else:
            error_analysis = "" if retry_count >= max_retries else result

        # ── 提取层摘要（仅 CONCLUDE 时）──
        layer_summary_update: dict[str, str] = {}
        if not error_analysis:
            summary_match = re.search(
                r'## 层摘要\s*\n(.*?)(?=\n## |\n\*\*|\Z)',
                result, re.DOTALL,
            )
            if summary_match:
                summary_text = summary_match.group(1).strip()
                existing = state.get("layer_summary", "")
                if existing:
                    layer_summary_update["layer_summary"] = (
                        existing + f"\n\n### Layer 3 代码实现\n{summary_text}"
                    )
                else:
                    layer_summary_update["layer_summary"] = (
                        f"### Layer 3 代码实现\n{summary_text}"
                    )

        logger.info(
            f"[Layer3] ImplManager 裁决: "
            f"{'RETRY' if error_analysis else 'CONCLUDE'} "
            f"(round {retry_count})"
        )

        return {
            **layer_summary_update,
            "impl_retry_count": retry_count,
            "error_analysis": error_analysis,
            "code_results": (
                result if not error_analysis
                else state.get("code_results", "")
            ),
            # CONCLUDE 时清空消息历史，保证进入下一层的 state 精简
            "impl_messages": [] if not error_analysis else state.get("impl_messages", []),
            "layer_outputs": _record(
                state, "impl_manager", "implementation", "manager",
                retry_count, result,
            ),
        }

    node_fn.__name__ = "impl_manager"
    return node_fn


# ═══════════════════════════════════════════════════════════════════
# Layer 4: Paper Writing — PaperAgent + PaperManager
# ═══════════════════════════════════════════════════════════════════

def create_paper_agent(config: dict) -> Callable[[AgentState], dict[str, Any]]:
    """Layer 4 Paper Agent — 有工具、分节迭代的论文撰写 Agent。

    使用 LangChain tool calling 实现内部 agentic loop：
    读前三层产出 → 分节撰写 → 核实数据 → 修改 → 自检通过。

    REVISE 时保留消息历史（paper_messages），在已有对话基础上
    追加 PaperManager 的审查反馈继续修改，而非冷启动重写。

    工具列表：read_file, list_dir, write_file（只读为主，无 run_code）
    """
    from mathmodelingagents.tools import create_paper_agent_tools

    def node_fn(state: AgentState) -> dict[str, Any]:
        logger.info("[Layer4] PaperAgent 开始...")

        max_tok = resolve_max_tokens(config, "writer", "paper_agent")
        output_dir = config.get("output_dir", "output")

        # ── Build prompt and context ──
        system_prompt = get_prompt("paper_agent")

        # Check for existing messages (REVISE scenario with message persistence)
        existing = state.get("paper_messages") or []
        paper_feedback = state.get("paper_feedback", "")

        if existing and paper_feedback:
            # REVISE mode: continue from previous conversation, append feedback
            logger.info(
                f"[Layer4] PaperAgent REVISE 模式：继承 {len(existing)} 条消息，"
                f"追加审查反馈"
            )
            feedback_msg = HumanMessage(content=(
                f"## ⚠️ 上一轮审查未通过\n\n"
                f"以下是论文经理的修改意见，请逐条修正（只修改有问题的节，"
                f"不要重写其他节）：\n\n{paper_feedback}"
            ))
            messages = existing + [feedback_msg]
        else:
            # First run: fresh messages
            context = _build_context(state, "paper", "paper_agent", config)
            user_msg = f"请根据以下上下文执行你的任务：\n\n{context}"
            messages = [
                SystemMessage(content=system_prompt),
                HumanMessage(content=user_msg),
            ]

        # ── Create tools (read-only: no run_code) ──
        tools = create_paper_agent_tools(output_dir)
        llm = create_layer_llm(config, "paper", "writer")
        llm_with_tools = llm.bind_tools(tools)

        # 共享 agentic loop；loop 内 sanitize 堵住 REVISE 裸回放损坏 pairing 的
        # latent bug（Paper-manager revision bug，本轮一并修）。
        # on_selfcheck：SELF_CHECK 后读盘补充论文正文（仅 Paper）。
        def selfcheck(final_output: str, messages: list) -> str:
            return _paper_read_disk(output_dir, final_output, messages)

        messages, final_output = _run_tool_loop(
            llm=llm,
            llm_with_tools=llm_with_tools,
            tools=tools,
            layer_tag="Layer4",
            agent_tag="PaperAgent",
            max_iterations=30,
            initial_messages=messages,
            sanitize=_sanitize_tool_pairing,
            on_selfcheck=selfcheck,
        )

        round_num = (state.get("model_debate_state") or {}).get("round_count", 0) or 1

        logger.info(
            f"[Layer4] PaperAgent 完成: {len(messages)} 条消息, "
            f"最终输出 {len(final_output)} 字符"
        )

        return {
            "final_paper": final_output,
            "paper_messages": messages,  # 保存完整历史供 REVISE 时继承
            "layer_outputs": _record(
                state, "paper_agent", "paper", "writer",
                round_num, final_output,
            ),
        }

    node_fn.__name__ = "paper_agent"
    return node_fn


def create_paper_manager(config: dict) -> Callable[[AgentState], dict[str, Any]]:
    """论文经理 — 纯审查，不给工具。逐条指出需要修改的节。

    REVISE 时保留 paper_messages，CONCLUDE 时清空。
    """
    base_manager = _make_manager_node(config, "paper_manager", "paper")

    def node_fn(state: AgentState) -> dict[str, Any]:
        result = base_manager(state)
        # 根据裁决决定是否清空 paper_messages
        debate = state.get("model_debate_state", {})
        judge_decision = debate.get("judge_decision", "CONCLUDE")
        if "REVISE" not in judge_decision:
            # CONCLUDE: 清空消息历史
            result["paper_messages"] = []
        return result

    node_fn.__name__ = "paper_manager"
    return node_fn


# ═══════════════════════════════════════════════════════════════════
# Layer 5: Sensitivity Analysis
# ═══════════════════════════════════════════════════════════════════

def create_param_perturber(config: dict) -> Callable[[AgentState], dict[str, Any]]:
    return _make_llm_node(config, "param_perturber", "sensitivity", "agent", "sensitivity_scan")


def create_robustness_analyst(config: dict) -> Callable[[AgentState], dict[str, Any]]:
    return _make_llm_node(config, "robustness_analyst", "sensitivity", "agent", "sensitivity_report")


def create_sensitivity_manager(config: dict) -> Callable[[AgentState], dict[str, Any]]:
    return _make_manager_node(config, "sensitivity_manager", "sensitivity")


# ═══════════════════════════════════════════════════════════════════
# Utility
# ═══════════════════════════════════════════════════════════════════

def create_msg_delete(config: dict = None) -> Callable[[AgentState], dict[str, Any]]:
    """创建消息清理节点 — 清除层间的 messages 列表。"""
    def node_fn(state: AgentState) -> dict[str, Any]:
        logger.info("[Utility] 清理消息历史")
        return {"messages": []}
    node_fn.__name__ = "msg_delete"
    return node_fn


__all__ = [
    "create_decomposer", "create_data_analyst", "create_constraint_analyst",
    "create_problem_manager",
    "create_modeler_a", "create_modeler_b", "create_modeler_c",
    "create_modeling_manager",
    "create_solver_agent", "create_viz_agent", "create_impl_manager",
    "create_paper_agent", "create_paper_manager",
    "create_param_perturber", "create_robustness_analyst",
    "create_sensitivity_manager",
    "create_msg_delete",
]
