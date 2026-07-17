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
from mathmodelingagents.llm_clients import invoke_with_fallback, resolve_max_tokens

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


# ═══════════════════════════════════════════════════════════════════
# 基础 LLM 节点工厂
# ═══════════════════════════════════════════════════════════════════

def _build_context(state: AgentState, layer: str, agent: str) -> str:
    """根据层和角色，从 state 中构建传给 LLM 的上下文。

    跨层原则：只注入前层的 Manager 摘要（layer_summary），不传原始辩论/代码。
    同层原则：辩论/重试循环内传递完整历史（Agent 需要互相看到发言）。
    """
    parts = []

    # 题目信息（所有层通用）
    problem = state.get("problem_description", "")
    if problem:
        parts.append(f"## 题目内容\n\n{problem}")

    # ── 跨层上下文：只传精华摘要 ──
    if layer != "problem" and state.get("layer_summary"):
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
        debate = state.get("model_debate_state") or state.get("debate_state") or {}
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
        if state.get("algorithm_spec"):
            parts.append(f"## 算法规格书\n\n{state['algorithm_spec']}")
        if state.get("code_results"):
            parts.append(f"## 代码实现结果\n\n{state['code_results']}")
        if state.get("error_analysis"):
            parts.append(f"## 错误分析\n\n{state['error_analysis']}")
        if state.get("visualizations"):
            parts.append(f"## 可视化产出\n\n{chr(10).join('- ' + str(v) for v in state['visualizations'])}")

    elif layer == "paper":
        if state.get("paper_outline"):
            parts.append(f"## 论文大纲\n\n{state['paper_outline']}")
        if state.get("final_paper"):
            parts.append(f"## 已生成论文\n\n{state['final_paper']}")
        if state.get("visualizations"):
            parts.append(f"## 可视化产出\n\n{chr(10).join('- ' + str(v) for v in state['visualizations'])}")

    elif layer == "sensitivity":
        if state.get("sensitivity_scan"):
            parts.append(f"## 敏感性扫描\n\n{state['sensitivity_scan']}")
        if state.get("sensitivity_report"):
            parts.append(f"## 敏感性报告\n\n{state['sensitivity_report']}")

    # 元信息
    debate = state.get("model_debate_state") or state.get("debate_state") or {}
    round_info = debate.get("round_count", 0)
    current_layer_info = state.get('current_layer', layer)
    parts.append(
        f"## 当前状态\n"
        f"- 当前层: {current_layer_info}\n"
        f"- 辩论轮次: {round_info}\n"
        f"- 实现重试次数: {state.get('impl_retry_count', 0)}"
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

        # ── 解析 max_tokens ──
        max_tok = resolve_max_tokens(config, role, agent_name)

        # 获取 prompt
        prompt_kwargs = {
            "problem_path": state.get("problem_path", ""),
            "round_count": state.get("model_debate_state", {}).get("round_count", 1),
            "remaining_rounds": max(0, config.get("max_debate_rounds", 10) - state.get("model_debate_state", {}).get("round_count", 0)),
            "max_rounds": config.get("max_debate_rounds", 10),
            "retry_count": state.get("impl_retry_count", 0),
            "output_dir": config.get("output_dir", "output"),
            "enable_sensitivity": str(config.get("enable_sensitivity", False)).lower(),
        }
        system_prompt = get_prompt(agent_name, **prompt_kwargs)

        # 追加全局约束（Layer 1 的 agent 需要）
        if layer == "problem" and role != "manager":
            global_constraints = get_global_constraints(
                problem_path=state.get("problem_path", "")
            )
            system_prompt = system_prompt + "\n\n" + global_constraints

        # 构建用户消息（上下文）
        context = _build_context(state, layer, agent_name)
        user_msg = f"请根据以下上下文执行你的任务：\n\n{context}"

        # 调用 LLM（统一降级链）
        messages = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_msg),
        ]
        try:
            result = invoke_with_fallback(config, layer, role, messages, agent_name, max_tokens=max_tok)
            logger.info(f"[{layer}] {agent_name} 完成，输出 {len(result)} 字符")
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

        # ── 解析 max_tokens ──
        max_tok = resolve_max_tokens(config, role, agent_name)

        # 辩论状态
        debate = dict(state.get("model_debate_state") or state.get("debate_state") or {})
        round_count = debate.get("round_count", 0) + 1

        prompt_kwargs = {
            "problem_path": state.get("problem_path", ""),
            "round_count": round_count,
            "remaining_rounds": max(0, config.get("max_debate_rounds", 10) - round_count),
            "max_rounds": config.get("max_debate_rounds", 10),
            "retry_count": state.get("impl_retry_count", 0),
            "output_dir": config.get("output_dir", "output"),
            "enable_sensitivity": str(config.get("enable_sensitivity", False)).lower(),
        }
        system_prompt = get_prompt(agent_name, **prompt_kwargs)

        # 追加层摘要要求
        system_prompt += (
            f"\n\n## 层摘要要求\n"
            f"若裁决为 CONCLUDE，你必须在输出末尾附加一段「## 层摘要」，"
            f"用 200-400 字精炼总结本层的核心产出，供下一层 Agent 使用。"
            f"摘要只需包含：关键结论、核心数据、最终方案要点。不要包含裁决标记。"
        )

        context = _build_context(state, layer, agent_name)
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
            "debate_state": debate,
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

        # 根据层写入特定字段
        if layer == "problem":
            updates["problem_report"] = result
        elif layer == "modeling":
            updates["model_spec"] = result
            updates["solution_approach"] = result
            updates["formulas"] = result
        elif layer == "implementation":
            updates["code_results"] = result
        elif layer == "paper":
            # CONCLUDE → 用 Manager 的整合输出作为最终论文
            # REVISE → 保留 SectionWriter 的输出，不覆盖
            if judge_decision == "CONCLUDE":
                updates["final_paper"] = result
        elif layer == "sensitivity":
            updates["sensitivity_report"] = result

        logger.info(f"[{layer}] {agent_name} 裁决: {judge_decision} (round {round_count})")
        updates["layer_outputs"] = _record(state, agent_name, layer, role, round_count, result)
        return updates

    node_fn.__name__ = agent_name
    return node_fn


# ═══════════════════════════════════════════════════════════════════
# Layer 1: Problem Analysis
# ═══════════════════════════════════════════════════════════════════

def create_decomposer(config: dict) -> Callable[[AgentState], dict[str, Any]]:
    return _make_llm_node(config, "decomposer", "problem", "agent", "problem_report")


def create_data_analyst(config: dict) -> Callable[[AgentState], dict[str, Any]]:
    return _make_llm_node(config, "data_analyst", "problem", "agent", "data_insights")


def create_constraint_analyst(config: dict) -> Callable[[AgentState], dict[str, Any]]:
    def node_fn(state: AgentState) -> dict[str, Any]:
        logger.info("[Layer1] ConstraintAnalyst 执行中...")

        # ── 解析 max_tokens ──
        max_tok = resolve_max_tokens(config, "agent", "constraint_analyst")

        prompt_kwargs = {"problem_path": state.get("problem_path", "")}
        system_prompt = get_prompt("constraint_analyst", **prompt_kwargs)
        global_constraints = get_global_constraints(problem_path=state.get("problem_path", ""))
        system_prompt = system_prompt + "\n\n" + global_constraints

        context = _build_context(state, "problem", "constraint_analyst")
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

        debate = dict(state.get("model_debate_state") or state.get("debate_state") or {})
        round_count = debate.get("round_count", 0)  # 不递增，由 Manager 管理轮数

        prompt_kwargs = {"round_count": round_count}
        system_prompt = get_prompt(agent_name, **prompt_kwargs)

        context = _build_context(state, "modeling", agent_name)
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
            "debate_state": debate,
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
# Layer 3: Implementation
# ═══════════════════════════════════════════════════════════════════

def create_algorithm_designer(config: dict) -> Callable[[AgentState], dict[str, Any]]:
    return _make_llm_node(config, "algorithm_designer", "implementation", "algorithm", "algorithm_spec")


def create_coder(config: dict) -> Callable[[AgentState], dict[str, Any]]:
    return _make_llm_node(config, "coder", "implementation", "coder", "code_results")


def create_visualizer(config: dict) -> Callable[[AgentState], dict[str, Any]]:
    return _make_llm_node(config, "visualizer", "implementation", "visualizer", "visualizations")


def create_impl_manager(config: dict) -> Callable[[AgentState], dict[str, Any]]:
    """实现经理 — 检查代码结果，决定是否重试（RETRY/CONCLUDE）。"""
    def node_fn(state: AgentState) -> dict[str, Any]:
        logger.info("[Layer3] ImplManager 执行中...")

        # ── 解析 max_tokens ──
        max_tok = resolve_max_tokens(config, "manager", "impl_manager")

        retry_count = state.get("impl_retry_count", 0) + 1
        max_retries = config.get("max_impl_retries", 3)

        prompt_kwargs = {"retry_count": retry_count}
        system_prompt = get_prompt("impl_manager", **prompt_kwargs)

        # 追加层摘要要求
        system_prompt += (
            f"\n\n## 层摘要要求\n"
            f"若裁决为 CONCLUDE，你必须在输出末尾附加一段「## 层摘要」，"
            f"用 200-400 字精炼总结本层的核心产出，供下一层 Agent 使用。"
        )

        context = _build_context(state, "implementation", "impl_manager")
        user_msg = f"请根据以下上下文检查实现并裁决（当前重试 {retry_count}/{max_retries}）：\n\n{context}"

        messages = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_msg),
        ]
        try:
            result = invoke_with_fallback(config, "implementation", "manager", messages, "impl_manager", max_tokens=max_tok)
        except Exception as e:
            logger.error(f"[Layer3] impl_manager 全部降级耗尽: {e}")
            result = f"LLM 调用失败（全部降级耗尽）: {e}"

        # 解析裁决
        if "**CONCLUDE**" in result:
            error_analysis = ""
        elif "**RETRY**" in result and retry_count < max_retries:
            error_analysis = result
        else:
            error_analysis = "" if retry_count >= max_retries else result

        # ── 提取层摘要（仅 CONCLUDE 时）──
        layer_summary_update = {}
        if not error_analysis and "**CONCLUDE**" in result:
            summary_match = re.search(
                r'## 层摘要\s*\n(.*?)(?=\n## |\n\*\*|\Z)',
                result, re.DOTALL,
            )
            if summary_match:
                summary_text = summary_match.group(1).strip()
                existing = state.get("layer_summary", "")
                if existing:
                    layer_summary_update["layer_summary"] = existing + f"\n\n### Layer 3 代码实现\n{summary_text}"
                else:
                    layer_summary_update["layer_summary"] = f"### Layer 3 代码实现\n{summary_text}"

        return {
            **layer_summary_update,
            "impl_retry_count": retry_count,
            "error_analysis": error_analysis,
            "code_results": result if not error_analysis else state.get("code_results", ""),
            "layer_outputs": _record(state, "impl_manager", "implementation", "manager", retry_count, result),
        }

    node_fn.__name__ = "impl_manager"
    return node_fn


# ═══════════════════════════════════════════════════════════════════
# Layer 4: Paper Writing
# ═══════════════════════════════════════════════════════════════════

def create_paper_architect(config: dict) -> Callable[[AgentState], dict[str, Any]]:
    return _make_llm_node(config, "paper_architect", "paper", "architect", "paper_outline")


def create_section_writer(config: dict) -> Callable[[AgentState], dict[str, Any]]:
    return _make_llm_node(config, "section_writer", "paper", "writer", "final_paper")


def create_chart_designer(config: dict) -> Callable[[AgentState], dict[str, Any]]:
    return _make_llm_node(config, "chart_designer", "paper", "visualizer", "visualizations")


def create_paper_manager(config: dict) -> Callable[[AgentState], dict[str, Any]]:
    return _make_manager_node(config, "paper_manager", "paper")


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
    "create_algorithm_designer", "create_coder", "create_visualizer",
    "create_impl_manager",
    "create_paper_architect", "create_section_writer", "create_chart_designer",
    "create_paper_manager",
    "create_param_perturber", "create_robustness_analyst",
    "create_sensitivity_manager",
    "create_msg_delete",
]
