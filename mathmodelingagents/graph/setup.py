"""Graph 构建 — 将所有 Agent 节点连接为完整的 LangGraph StateGraph。

对标 TradingAgents 的 setup.py，使用工厂函数创建 Agent 节点，
并按顺序、辩论循环、条件路由组织它们。
"""

import logging
from typing import Any

from langgraph.graph import END, START, StateGraph

from mathmodelingagents.agents.utils.agent_states import AgentState
from mathmodelingagents.agents import (
    # Layer 1
    create_decomposer, create_data_analyst, create_constraint_analyst, create_problem_manager,
    # Layer 2
    create_modeler_a, create_modeler_b, create_modeler_c, create_modeling_manager,
    # Layer 3
    create_solver_agent, create_viz_agent, create_impl_manager,
    # Layer 4
    create_paper_agent, create_paper_manager,
    # Layer 5
    create_param_perturber, create_robustness_analyst, create_sensitivity_manager,
    # Utility
    create_msg_delete,
)
from mathmodelingagents.graph.conditional_logic import ConditionalLogic

logger = logging.getLogger(__name__)


class GraphSetup:
    """构建完整的 5 层 LangGraph StateGraph。

    每层内部结构：
    - Layer 1: Decomposer → DataAnalyst → ConstraintAnalyst → ProblemManager → 路由
    - Layer 2: ModelerA → ModelerB → ModelerC → ModelingManager → 辩论路由
    - Layer 3: SolverAgent → ImplManager → 重试路由 → VizAgent → clear_impl → 路由
    - Layer 4: PaperAgent → PaperManager → 修改路由
    - Layer 5: ParamPerturber → RobustnessAnalyst → SensitivityManager → END
    """

    def __init__(self, config: dict):
        """初始化 GraphSetup。

        Args:
            config: 全局配置字典。
        """
        self.config = config
        self.max_debate_rounds = config.get("max_debate_rounds", 10)
        self.selected_layers = config.get("selected_layers", [1, 2, 3, 4])

        # 创建所有 Agent 节点
        self._create_agent_nodes()

        # 条件路由逻辑
        self.conditional_logic = ConditionalLogic(
            max_debate_rounds=self.max_debate_rounds,
            max_impl_retries=config.get("max_impl_retries", 3),
            selected_layers=self.selected_layers,
        )

    def _create_agent_nodes(self):
        """使用工厂函数创建所有 Agent 节点（Callable）。"""
        config = self.config

        # Layer 1
        self.decomposer = create_decomposer(config)
        self.data_analyst = create_data_analyst(config)
        self.constraint_analyst = create_constraint_analyst(config)
        self.problem_manager = create_problem_manager(config)

        # Layer 2
        self.modeler_a = create_modeler_a(config)
        self.modeler_b = create_modeler_b(config)
        self.modeler_c = create_modeler_c(config)
        self.modeling_manager = create_modeling_manager(config)

        # Layer 3
        self.solver_agent = create_solver_agent(config)
        self.viz_agent = create_viz_agent(config)
        self.impl_manager = create_impl_manager(config)

        # Layer 4
        self.paper_agent = create_paper_agent(config)
        self.paper_manager = create_paper_manager(config)

        # Layer 5
        self.param_perturber = create_param_perturber(config)
        self.robustness_analyst = create_robustness_analyst(config)
        self.sensitivity_manager = create_sensitivity_manager(config)

        # Utility nodes
        self.clear_problem = create_msg_delete()
        self.clear_modeling = create_msg_delete()
        self.clear_impl = create_msg_delete()
        self.clear_paper = create_msg_delete()

        logger.info("所有 Agent 节点已创建")

    def setup_graph(self) -> StateGraph:
        """构建完整的 StateGraph。

        Layer 1 的流转：
            START → decomposer → data_analyst → constraint_analyst → problem_manager → clear_problem → next_layer

        Layer 2 的流转（辩论循环）：
            modeler_a → modeler_b → modeler_c → modeling_manager
            → (CONTINUE) → modeler_a
            → (CONCLUDE) → clear_modeling → next_layer

        Layer 3 的流转（重试循环 → 可视化）：
            solver_agent → impl_manager
            → (RETRY) → solver_agent
            → (CONCLUDE) → viz_agent → clear_impl → next_layer

        Layer 4 的流转（修改循环）：
            paper_agent → paper_manager
            → (REVISE) → paper_agent
            → (CONCLUDE) → clear_paper → next_layer

        Layer 5 的流转：
            param_perturber → robustness_analyst → sensitivity_manager → END

        Returns:
            未编译的 StateGraph（调用 .compile() 后获得可执行的 graph）。
        """
        workflow = StateGraph(AgentState)

        # ═══ 添加所有节点 ═══
        self._add_layer1_nodes(workflow)
        self._add_layer2_nodes(workflow)
        self._add_layer3_nodes(workflow)
        self._add_layer4_nodes(workflow)
        self._add_layer5_nodes(workflow)

        # ═══ 添加边（层间连接） ═══
        self._connect_layers(workflow)

        logger.info(
            f"Graph 构建完成: layers={self.selected_layers}, "
            f"max_debate_rounds={self.max_debate_rounds}"
        )
        return workflow

    # ═══════════════════════════════════════════
    # 添加节点
    # ═══════════════════════════════════════════

    def _add_layer1_nodes(self, workflow: StateGraph):
        """添加 Layer 1 节点。"""
        workflow.add_node("decomposer", self.decomposer)
        workflow.add_node("data_analyst", self.data_analyst)
        workflow.add_node("constraint_analyst", self.constraint_analyst)
        workflow.add_node("problem_manager", self.problem_manager)
        workflow.add_node("clear_problem", self.clear_problem)

    def _add_layer2_nodes(self, workflow: StateGraph):
        """添加 Layer 2 节点。"""
        workflow.add_node("modeler_a", self.modeler_a)
        workflow.add_node("modeler_b", self.modeler_b)
        workflow.add_node("modeler_c", self.modeler_c)
        workflow.add_node("modeling_manager", self.modeling_manager)
        workflow.add_node("clear_modeling", self.clear_modeling)

    def _add_layer3_nodes(self, workflow: StateGraph):
        """添加 Layer 3 节点。"""
        workflow.add_node("solver_agent", self.solver_agent)
        workflow.add_node("impl_manager", self.impl_manager)
        workflow.add_node("viz_agent", self.viz_agent)
        workflow.add_node("clear_impl", self.clear_impl)

    def _add_layer4_nodes(self, workflow: StateGraph):
        """添加 Layer 4 节点。"""
        workflow.add_node("paper_agent", self.paper_agent)
        workflow.add_node("paper_manager", self.paper_manager)
        workflow.add_node("clear_paper", self.clear_paper)

    def _add_layer5_nodes(self, workflow: StateGraph):
        """添加 Layer 5 节点（仅当启用时）。"""
        if 5 not in self.selected_layers:
            return
        workflow.add_node("param_perturber", self.param_perturber)
        workflow.add_node("robustness_analyst", self.robustness_analyst)
        workflow.add_node("sensitivity_manager", self.sensitivity_manager)

    # ═══════════════════════════════════════════
    # 连接层
    # ═══════════════════════════════════════════

    def _connect_layers(self, workflow: StateGraph):
        """连接所有层，建立完整的图拓扑。"""

        # ── 确定第一层入口 ──
        first_layer = self.selected_layers[0] if self.selected_layers else 1
        first_entry = {
            1: "decomposer",
            2: "modeler_a",
            3: "solver_agent",
            4: "paper_agent",
            5: "param_perturber",
        }.get(first_layer, "decomposer")

        workflow.add_edge(START, first_entry)

        # ── Layer 1: 顺序连接 ──
        if 1 in self.selected_layers:
            workflow.add_edge("decomposer", "data_analyst")
            workflow.add_edge("data_analyst", "constraint_analyst")
            workflow.add_edge("constraint_analyst", "problem_manager")
            workflow.add_edge("problem_manager", "clear_problem")
            workflow.add_conditional_edges(
                "clear_problem",
                self.conditional_logic.should_continue_problem,
                self._get_layer1_destinations(),
            )

        # ── Layer 2: 辩论循环 ──
        if 2 in self.selected_layers:
            workflow.add_edge("modeler_a", "modeler_b")
            workflow.add_edge("modeler_b", "modeler_c")
            workflow.add_edge("modeler_c", "modeling_manager")
            workflow.add_conditional_edges(
                "modeling_manager",
                self.conditional_logic.should_continue_modeling,
                self._get_layer2_destinations(),
            )

        # ── Layer 3: SolverAgent → ImplManager → VizAgent → clear_impl（重试循环）──
        if 3 in self.selected_layers:
            workflow.add_edge("solver_agent", "impl_manager")
            workflow.add_conditional_edges(
                "impl_manager",
                self.conditional_logic.should_continue_impl,
                {
                    "solver_agent": "solver_agent",
                    "viz_agent": "viz_agent",
                },
            )
            workflow.add_edge("viz_agent", "clear_impl")
            workflow.add_conditional_edges(
                "clear_impl",
                self.conditional_logic._route_after_impl,
                self._get_layer3_destinations(),
            )

        # ── Layer 4: PaperAgent → PaperManager（修改循环）──
        if 4 in self.selected_layers:
            workflow.add_edge("paper_agent", "paper_manager")
            workflow.add_conditional_edges(
                "paper_manager",
                self.conditional_logic.should_continue_paper,
                {
                    "paper_agent": "paper_agent",
                    "clear_paper": "clear_paper",
                },
            )
            workflow.add_conditional_edges(
                "clear_paper",
                self.conditional_logic._route_after_paper,
                self._get_layer4_destinations(),
            )

        # ── Layer 5: 顺序连接 ──
        if 5 in self.selected_layers:
            workflow.add_edge("param_perturber", "robustness_analyst")
            workflow.add_edge("robustness_analyst", "sensitivity_manager")
            workflow.add_conditional_edges(
                "sensitivity_manager",
                self.conditional_logic.should_continue_sensitivity,
                {"__end__": END},
            )

    # ═══════════════════════════════════════════
    # 路由目标映射
    # ═══════════════════════════════════════════

    def _get_layer1_destinations(self) -> dict:
        """Layer 1 完成后的目标映射（动态，仅包含已添加的节点）。"""
        dests: dict = {}
        if 2 in self.selected_layers:
            dests["modeler_a"] = "modeler_a"
        if 3 in self.selected_layers:
            dests["solver_agent"] = "solver_agent"
        if 4 in self.selected_layers:
            dests["paper_agent"] = "paper_agent"
        if 5 in self.selected_layers:
            dests["sensitivity_scanner"] = "param_perturber"
        dests[END] = END
        return dests

    def _get_layer2_destinations(self) -> dict:
        """Layer 2 辩论路由目标（动态）。"""
        dests: dict = {"modeler_a": "modeler_a"}  # CONTINUE: 回辩论
        if 3 in self.selected_layers:
            dests["solver_agent"] = "solver_agent"
        if 4 in self.selected_layers:
            dests["paper_agent"] = "paper_agent"
        if 5 in self.selected_layers:
            dests["sensitivity_scanner"] = "param_perturber"
        dests[END] = END
        return dests

    def _get_layer3_destinations(self) -> dict:
        """Layer 3 完成后的目标映射（clear_impl → 下一层，动态）。"""
        dests: dict = {}
        if 4 in self.selected_layers:
            dests["paper_agent"] = "paper_agent"
        if 5 in self.selected_layers:
            dests["sensitivity_scanner"] = "param_perturber"
        dests[END] = END
        return dests

    def _get_layer4_destinations(self) -> dict:
        """Layer 4 完成后的目标映射（动态）。"""
        dests: dict = {}
        if 5 in self.selected_layers:
            dests["sensitivity_scanner"] = "param_perturber"
        dests[END] = END
        return dests
