"""条件路由逻辑 — 控制辩论循环和层间流转。

ConditionalLogic 封装了所有层的路由决策函数，
供 LangGraph 的 conditional_edges 使用。
"""

import logging
from typing import Any

from langgraph.graph import END

from mathmodelingagents.agents.utils.agent_states import AgentState

logger = logging.getLogger(__name__)


class ConditionalLogic:
    """条件路由逻辑 — 控制辩论循环和层间流转。

    每个 should_continue_* 方法对应一个层的结束节点，
    读取当前状态后返回下一个节点名称或 END。

    Attributes:
        max_debate_rounds: Layer 2 辩论最大轮数。
        max_risk_discuss_rounds: 风险评估讨论最大轮数。
        max_impl_retries: Layer 3 实现重试最大次数。
        selected_layers: 用户选择的要执行的层列表。
    """

    def __init__(
        self,
        max_debate_rounds: int = 10,
        max_risk_discuss_rounds: int = 10,
        max_impl_retries: int = 3,
        selected_layers: list[int] | None = None,
    ):
        """初始化路由逻辑。

        Args:
            max_debate_rounds: 辩论最大轮数。
            max_risk_discuss_rounds: 风险讨论最大轮数。
            max_impl_retries: 实现重试最大次数。
            selected_layers: 用户选择的层列表，默认 [1,2,3,4]。
        """
        self.max_debate_rounds = max_debate_rounds
        self.max_risk_discuss_rounds = max_risk_discuss_rounds
        self.max_impl_retries = max_impl_retries
        self.selected_layers = selected_layers or [1, 2, 3, 4]

    # ═══════════════════════════════════════════════════════════════
    # Layer 1: Problem Analysis 路由
    # ═══════════════════════════════════════════════════════════════

    def should_continue_problem(self, state: AgentState) -> str:
        """Layer 1 结束后决定：进入 Layer 2 还是跳过。

        从 ProblemManager 节点后调用。
        如果 Layer 2 在 selected_layers 中，路由到 modeler_a；
        否则查找下一个启用的层。

        Args:
            state: 当前全局 AgentState。

        Returns:
            下一个节点名称或 END。
        """
        if 2 in self.selected_layers:
            logger.info("Layer 1 → Layer 2: 进入建模辩论")
            return "modeler_a"
        return self._route_to_next_layer(2)

    # ═══════════════════════════════════════════════════════════════
    # Layer 2: Modeling 路由（辩论循环）
    # ═══════════════════════════════════════════════════════════════

    def should_continue_modeling(self, state: AgentState) -> str:
        """ModelingManager 后决定：继续辩论还是进入 Layer 3。

        读取 debate_state 中的 judge_decision 和 round_count，
        判断是否达到最大轮数或已得出结论。

        Args:
            state: 当前全局 AgentState。

        Returns:
            "modeler_a" 继续辩论，"solver_agent" 进入 Layer 3，
            或 END。
        """
        debate_state = state.get("model_debate_state") or state.get("debate_state", {})
        judge_decision = debate_state.get("judge_decision", "CONCLUDE")
        round_count = debate_state.get("round_count", 0)

        if judge_decision == "CONTINUE" and round_count < self.max_debate_rounds:
            logger.info(
                f"Layer 2 辩论继续 (round {round_count}/{self.max_debate_rounds})"
            )
            return "modeler_a"

        logger.info(f"Layer 2 辩论结束 (decision={judge_decision}, round={round_count})")

        if 3 in self.selected_layers:
            return "solver_agent"
        return self._route_to_next_layer(3)

    # ═══════════════════════════════════════════════════════════════
    # Layer 3: Implementation 路由（重试循环）
    # ═══════════════════════════════════════════════════════════════

    def should_continue_impl(self, state: AgentState) -> str:
        """ImplManager 后决定：重试求解还是进入可视化。

        检查 error_analysis 是否为空和 retry 次数。

        Args:
            state: 当前全局 AgentState。

        Returns:
            "solver_agent" 重试求解，"viz_agent" 进入可视化。
        """
        error_analysis = state.get("error_analysis", "")
        retry_count = state.get("impl_retry_count", 0)

        if error_analysis and retry_count < self.max_impl_retries:
            logger.info(
                f"Layer 3 求解重试 (retry {retry_count + 1}/{self.max_impl_retries})"
            )
            return "solver_agent"

        logger.info(f"Layer 3 求解通过 (retries={retry_count})，进入可视化")
        return "viz_agent"

    # ═══════════════════════════════════════════════════════════════
    # Layer 4: Paper Writing 路由
    # ═══════════════════════════════════════════════════════════════

    def should_continue_paper(self, state: AgentState) -> str:
        """PaperManager 后决定：退回修改 (REVISE) 还是进入下一层。

        读取 debate_state 中的 judge_decision 和 round_count，
        REVISE 时退回 PaperAgent，CONCLUDE 时进入 clear_paper。

        Args:
            state: 当前全局 AgentState。

        Returns:
            "paper_agent" 退回修改，或 "clear_paper" 进入下一层。
        """
        debate = state.get("model_debate_state") or state.get("debate_state") or {}
        judge_decision = debate.get("judge_decision", "CONCLUDE")
        round_count = debate.get("round_count", 0)

        if "REVISE" in judge_decision and round_count < self.max_debate_rounds:
            logger.info(
                f"Layer 4 论文退回修改 (round {round_count}/{self.max_debate_rounds})"
            )
            return "paper_agent"

        logger.info(f"Layer 4 论文通过 (decision={judge_decision}, round={round_count})")
        return "clear_paper"

    def _route_after_paper(self, state: AgentState) -> str:
        """clear_paper 后决定：进入 Layer 5 还是结束。"""
        if 5 in self.selected_layers:
            logger.info("Layer 4 → Layer 5: 进入敏感性分析")
            return "sensitivity_scanner"
        logger.info("Layer 4 完成，流程结束")
        return END

    # ═══════════════════════════════════════════════════════════════
    # Layer 5: Sensitivity Analysis 路由
    # ═══════════════════════════════════════════════════════════════

    def should_continue_sensitivity(self, state: AgentState) -> str:
        """Sensitivity 结束后：总是结束流程。

        Args:
            state: 当前全局 AgentState。

        Returns:
            END。
        """
        logger.info("Layer 5 敏感性分析完成，流程结束")
        return END

    def _route_after_impl(self, state: AgentState) -> str:
        """clear_impl 后决定：进入 Layer 4 还是结束。"""
        if 4 in self.selected_layers:
            logger.info("Layer 3 → Layer 4: 进入论文写作")
            return "paper_agent"
        if 5 in self.selected_layers:
            logger.info("Layer 3 → Layer 5: 进入敏感性分析")
            return "sensitivity_scanner"
        logger.info("Layer 3 完成，流程结束")
        return END

    # ═══════════════════════════════════════════════════════════════
    # 内部辅助
    # ═══════════════════════════════════════════════════════════════

    def _route_to_next_layer(self, current_layer: int) -> str:
        """查找当前层之后第一个启用的层，返回其入口节点。

        如果所有后续层都未启用，返回 END。

        Args:
            current_layer: 当前完成的层编号。

        Returns:
            下一个层的入口节点名或 END。
        """
        layer_entry_map: dict[int, str] = {
            2: "modeler_a",
            3: "solver_agent",
            4: "paper_agent",
            5: "sensitivity_scanner",
        }

        for layer in range(current_layer + 1, 6):
            if layer in self.selected_layers:
                entry = layer_entry_map.get(layer)
                if entry:
                    logger.info(f"跳过层，路由到 Layer {layer}: {entry}")
                    return entry

        logger.info("所有层完成，流程结束")
        return END
