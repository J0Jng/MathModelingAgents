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
from mathmodelingagents.llm_clients import invoke_with_fallback, resolve_max_tokens, is_retryable_error

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

def _build_context(state: AgentState, layer: str, agent: str, config: dict) -> str:
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
        if state.get("code_results"):
            parts.append(f"## CodingAgent 产出\n\n{state['code_results']}")
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

    # 元信息
    debate = state.get("model_debate_state") or state.get("debate_state") or {}
    round_info = debate.get("round_count", 0)
    max_rounds = config.get("max_debate_rounds", 10)
    remaining = max(0, max_rounds - round_info)
    current_layer_info = state.get('current_layer', layer)
    output_dir = config.get("output_dir", "output")
    parts.append(
        f"## 当前状态\n"
        f"- 当前层: {current_layer_info}\n"
        f"- 输出目录: {output_dir}\n"
        f"- 辩论轮次: {round_info}/{max_rounds} (剩余 {remaining} 轮)\n"
        f"- 实现重试次数: {state.get('impl_retry_count', 0)}\n"
        f"- 敏感性分析: {'已启用' if config.get('enable_sensitivity') else '未启用'}"
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
            # REVISE → 保留 PaperAgent 的输出，存入修改意见供其查看
            if judge_decision == "CONCLUDE":
                updates["final_paper"] = result
            else:
                # REVISE — 把审查反馈单独存起来，PaperAgent 下一轮会看到
                updates["paper_feedback"] = result
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

        debate = dict(state.get("model_debate_state") or state.get("debate_state") or {})
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
# Layer 3: Implementation — CodingAgent + ImplManager
# ═══════════════════════════════════════════════════════════════════

def create_coding_agent(config: dict) -> Callable[[AgentState], dict[str, Any]]:
    """Layer 3 Coding Agent — 有工具、自我迭代的编码 Agent。

    使用 LangChain tool calling 实现内部 agentic loop：
    写代码 → 执行(run_code) → 看结果 → 修复 → 再执行 → ... → 自检通过。

    工具列表：run_code, read_file, write_file, list_dir
    """
    import json as _json
    import time as _time
    from langchain_core.messages import ToolMessage
    from mathmodelingagents.tools import create_coding_agent_tools

    def node_fn(state: AgentState) -> dict[str, Any]:
        logger.info("[Layer3] CodingAgent 开始...")

        max_tok = resolve_max_tokens(config, "coder", "coding_agent")
        output_dir = config.get("output_dir", "output")

        # ── Build prompt and context ──
        system_prompt = get_prompt("coding_agent")

        context = _build_context(state, "implementation", "coding_agent", config)
        user_msg = f"请根据以下上下文执行你的任务：\n\n{context}"

        messages = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_msg),
        ]

        # ── Create tools scoped to output_dir ──
        tools = create_coding_agent_tools(output_dir)
        llm = create_layer_llm(config, "implementation", "coder")
        llm_with_tools = llm.bind_tools(tools)

        max_iterations = 30
        consecutive_no_tool = 0

        for iteration in range(max_iterations):
            logger.info(f"[Layer3] CodingAgent iteration {iteration + 1}/{max_iterations}")

            # ── Invoke LLM with retry ──
            response = None
            for attempt in range(1, 4):
                try:
                    response = llm_with_tools.invoke(messages)
                    break
                except Exception as e:
                    if attempt < 3 and is_retryable_error(e):
                        delay = 2 ** attempt
                        logger.warning(
                            f"[Layer3] CodingAgent LLM 调用重试 {attempt}/3, "
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

                    # Find the matching tool
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
                            logger.error(f"[Layer3] 工具 {tool_name} 执行失败: {e}")
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
                        f"[Layer3] CodingAgent 工具 {tool_name}: "
                        f"{result_str[:120]}..."
                    )
            else:
                consecutive_no_tool += 1
                content = response.content or ""

                if "SELF_CHECK_PASSED" in content:
                    logger.info(
                        f"[Layer3] CodingAgent 自检通过 "
                        f"(iteration {iteration + 1})"
                    )
                    break

                if consecutive_no_tool >= 3:
                    logger.warning(
                        f"[Layer3] CodingAgent {consecutive_no_tool} 轮无工具调用，"
                        f"强制中断"
                    )
                    break

        # ── Extract final text output ──
        final_output = ""
        for msg in reversed(messages):
            content = getattr(msg, "content", "") or ""
            has_tools = bool(getattr(msg, "tool_calls", None))
            tool_msg = getattr(msg, "type", "") == "tool"
            if content and not has_tools and not tool_msg:
                final_output = content
                break

        retry_count = state.get("impl_retry_count", 0)

        logger.info(
            f"[Layer3] CodingAgent 完成: {len(messages)} 条消息, "
            f"最终输出 {len(final_output)} 字符"
        )

        return {
            "code_results": final_output,
            "layer_outputs": _record(
                state, "coding_agent", "implementation", "coder",
                retry_count + 1, final_output,
            ),
        }

    node_fn.__name__ = "coding_agent"
    return node_fn


def create_impl_manager(config: dict) -> Callable[[AgentState], dict[str, Any]]:
    """实现经理 — 外部审查 CodingAgent 的产出，决定 RETRY/CONCLUDE。"""
    def node_fn(state: AgentState) -> dict[str, Any]:
        logger.info("[Layer3] ImplManager 执行中...")

        max_tok = resolve_max_tokens(config, "manager", "impl_manager")

        retry_count = state.get("impl_retry_count", 0) + 1
        max_retries = config.get("max_impl_retries", 3)

        system_prompt = (
            get_prompt("impl_manager")
            + "\n\n## 层摘要要求\n"
            "若裁决为 CONCLUDE，你必须在输出末尾附加一段「## 层摘要」，"
            "用 200-400 字精炼总结本层的核心产出，供下一层 Agent 使用。"
        )

        context = _build_context(state, "implementation", "impl_manager", config)
        user_msg = (
            f"请审查 CodingAgent 的产出并裁决"
            f"（当前重试 {retry_count}/{max_retries}）：\n\n{context}"
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

    工具列表：read_file, list_dir, write_file（只读为主，无 run_code）
    """
    import json as _json
    import time as _time
    from langchain_core.messages import ToolMessage
    from mathmodelingagents.tools import create_paper_agent_tools

    def node_fn(state: AgentState) -> dict[str, Any]:
        logger.info("[Layer4] PaperAgent 开始...")

        max_tok = resolve_max_tokens(config, "writer", "paper_agent")
        output_dir = config.get("output_dir", "output")

        # ── Build prompt and context ──
        system_prompt = get_prompt("paper_agent")

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

        max_iterations = 30
        consecutive_no_tool = 0

        for iteration in range(max_iterations):
            logger.info(
                f"[Layer4] PaperAgent iteration {iteration + 1}/{max_iterations}"
            )

            # ── Invoke LLM with retry ──
            response = None
            for attempt in range(1, 4):
                try:
                    response = llm_with_tools.invoke(messages)
                    break
                except Exception as e:
                    if attempt < 3 and is_retryable_error(e):
                        delay = 2 ** attempt
                        logger.warning(
                            f"[Layer4] PaperAgent LLM 调用重试 {attempt}/3, "
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
                                f"[Layer4] 工具 {tool_name} 执行失败: {e}"
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
                        f"[Layer4] PaperAgent 工具 {tool_name}: "
                        f"{result_str[:120]}..."
                    )
            else:
                consecutive_no_tool += 1
                content = response.content or ""

                if "SELF_CHECK_PASSED" in content:
                    logger.info(
                        f"[Layer4] PaperAgent 自检通过 "
                        f"(iteration {iteration + 1})"
                    )
                    break

                if consecutive_no_tool >= 3:
                    logger.warning(
                        f"[Layer4] PaperAgent {consecutive_no_tool} 轮无工具调用，"
                        f"强制中断"
                    )
                    break

        # ── Extract final text output ──
        final_output = ""
        for msg in reversed(messages):
            content = getattr(msg, "content", "") or ""
            has_tools = bool(getattr(msg, "tool_calls", None))
            tool_msg = getattr(msg, "type", "") == "tool"
            if content and not has_tools and not tool_msg:
                final_output = content
                break

        round_num = (state.get("model_debate_state") or {}).get("round_count", 0) or 1

        logger.info(
            f"[Layer4] PaperAgent 完成: {len(messages)} 条消息, "
            f"最终输出 {len(final_output)} 字符"
        )

        return {
            "final_paper": final_output,
            "layer_outputs": _record(
                state, "paper_agent", "paper", "writer",
                round_num, final_output,
            ),
        }

    node_fn.__name__ = "paper_agent"
    return node_fn


def create_paper_manager(config: dict) -> Callable[[AgentState], dict[str, Any]]:
    """论文经理 — 纯审查，不给工具。逐条指出需要修改的节。"""
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
    "create_coding_agent", "create_impl_manager",
    "create_paper_agent", "create_paper_manager",
    "create_param_perturber", "create_robustness_analyst",
    "create_sensitivity_manager",
    "create_msg_delete",
]
