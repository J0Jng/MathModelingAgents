"""批2：explain 模式路由接线（任务书 D4）。

覆盖 _route_after_impl / should_continue_sensitivity 的 explain_mode 分支、
explain_mode=False 的回归真值表（与 test_sensitivity_routing.py 一致），
以及 GraphSetup 解释模式下的节点集、入口边与条件边目标。
"""

from langgraph.graph import END

from mathmodelingagents.graph.conditional_logic import ConditionalLogic
from mathmodelingagents.graph.setup import GraphSetup


def make_logic(
    mode: str = "auto",
    selected: list[int] | None = None,
    explain_mode: bool = False,
) -> ConditionalLogic:
    return ConditionalLogic(
        max_problem_rounds=2,
        max_modeling_rounds=2,
        max_revision_rounds=2,
        max_impl_retries=2,
        selected_layers=selected or [2, 3],
        sensitivity_mode=mode,
        explain_mode=explain_mode,
    )


def make_config() -> dict:
    return {
        "llm_provider": "opencode",
        "selected_layers": [2, 3],
        "explain_mode": True,
        "sensitivity_mode": "auto",
        "max_debate_rounds": 2,
        "max_problem_rounds": 2,
        "max_modeling_rounds": 2,
        "max_revision_rounds": 2,
        "max_impl_retries": 2,
    }


class TestRouteAfterImplExplain:
    """explain 模式下 clear_impl 后的路由。"""

    def test_sensitivity_wins_over_explainer(self):
        # 敏感性启用时仍前置 L5（D4：敏感性判定优先于 explain 分支）
        logic = make_logic(explain_mode=True)
        assert logic._route_after_impl({"sensitivity_enabled": True}) == "param_perturber"

    def test_explain_routes_to_explainer(self):
        logic = make_logic(explain_mode=True)
        assert logic._route_after_impl({"sensitivity_enabled": False}) == "explainer"

    def test_explain_fail_open_sensitivity(self):
        # auto 模式拿不到决策 fail-open → 仍走敏感性，不直接进解释
        logic = make_logic(explain_mode=True)
        assert logic._route_after_impl({}) == "param_perturber"


class TestRouteAfterSensitivityExplain:
    """explain 模式下 L5 结束后进入模型解释而非论文。"""

    def test_routes_to_explainer(self):
        logic = make_logic(explain_mode=True)
        assert logic.should_continue_sensitivity({}) == "explainer"


class TestExplainModeOffRegression:
    """explain_mode=False（默认）时真值表与既有敏感性路由完全一致。"""

    def test_default_explain_mode_is_false(self):
        assert make_logic().explain_mode is False

    def test_auto_decision_false_routes_to_paper(self):
        logic = make_logic(selected=[1, 2, 3, 4])
        assert logic._route_after_impl({"sensitivity_enabled": False}) == "paper_agent"

    def test_never_decision_true_routes_to_paper(self):
        logic = make_logic(mode="never", selected=[1, 2, 3, 4])
        assert logic._route_after_impl({"sensitivity_enabled": True}) == "paper_agent"

    def test_no_layer4_never_ends(self):
        logic = make_logic(mode="never", selected=[1, 2, 3])
        assert logic._route_after_impl({}) == END

    def test_sensitivity_routes_to_paper_when_layer4(self):
        logic = make_logic(selected=[1, 2, 3, 4])
        assert logic.should_continue_sensitivity({}) == "paper_agent"

    def test_sensitivity_ends_when_no_layer4(self):
        logic = make_logic(selected=[1, 2, 3])
        assert logic.should_continue_sensitivity({}) == END


class TestExplainGraphTopology:
    """GraphSetup 解释模式（selected_layers=[2,3]）的图编译与拓扑。"""

    def _compile(self):
        return GraphSetup(make_config()).setup_graph().compile()

    def _node_names(self, compiled) -> set:
        return set(compiled.get_graph().nodes.keys())

    def test_nodes_contain_explainer(self):
        assert "explainer" in self._node_names(self._compile())

    def test_start_enters_modeler_a(self):
        g = self._compile().get_graph()
        start_targets = {e.target for e in g.edges if e.source == "__start__"}
        assert start_targets == {"modeler_a"}

    def test_explainer_is_terminal(self):
        g = self._compile().get_graph()
        targets = {e.target for e in g.edges if e.source == "explainer"}
        assert targets == {"__end__"}

    def test_layer3_destinations_explainer_not_paper(self):
        # 解释模式下 clear_impl 的静态目标映射：含 explainer，不含 paper_agent
        dests = GraphSetup(make_config())._get_layer3_destinations()
        assert "explainer" in dests
        assert "paper_agent" not in dests
        assert {"param_perturber", "explainer", END} == set(dests.keys())

    def test_clear_impl_edges_cover_routing_values(self):
        # 条件边目标覆盖 _route_after_impl 在解释模式的全部返回值
        g = self._compile().get_graph()
        targets = {e.target for e in g.edges if e.source == "clear_impl"}
        assert {"param_perturber", "explainer", "__end__"} <= targets
        assert "paper_agent" not in targets

    def test_sensitivity_manager_edges_route_to_explainer(self):
        # L5 结束后条件边指向 explainer 而非 paper_agent
        g = self._compile().get_graph()
        targets = {e.target for e in g.edges if e.source == "sensitivity_manager"}
        assert "explainer" in targets
        assert "paper_agent" not in targets

    def test_explain_off_builds_paper_route(self):
        # 回归：非解释模式图拓扑与之前一致（clear_impl 可达 paper_agent）
        config = make_config()
        config["explain_mode"] = False
        config["selected_layers"] = [1, 2, 3, 4]
        compiled = GraphSetup(config).setup_graph().compile()
        g = compiled.get_graph()
        targets = {e.target for e in g.edges if e.source == "clear_impl"}
        assert "paper_agent" in targets
        assert "explainer" not in targets
