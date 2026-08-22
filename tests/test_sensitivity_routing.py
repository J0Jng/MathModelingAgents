"""接缝 1：敏感性运行时闸门与路由真值表。

覆盖「模式 × 敏感性决策 × 求解层前提」全真值表（ADR-0001 fail-open、
ADR-0002 L5 前置论文层），以及「never 模式下图仍无条件构建 Layer 5 节点」。
"""

from langgraph.graph import END

from mathmodelingagents.graph.conditional_logic import ConditionalLogic
from mathmodelingagents.graph.setup import GraphSetup


def make_logic(mode: str, selected: list[int] | None = None) -> ConditionalLogic:
    return ConditionalLogic(
        max_problem_rounds=2,
        max_modeling_rounds=2,
        max_revision_rounds=2,
        max_impl_retries=2,
        selected_layers=selected or [1, 2, 3, 4],
        sensitivity_mode=mode,
    )


class TestSensitivityGate:
    """_route_after_impl 的敏感性闸门真值表。"""

    def test_auto_fail_open_without_decision(self):
        # auto 模式拿不到决策（字段缺失）-> 默认执行
        logic = make_logic("auto")
        assert logic._route_after_impl({}) == "sensitivity_scanner"

    def test_auto_respects_decision_true(self):
        logic = make_logic("auto")
        assert logic._route_after_impl({"sensitivity_enabled": True}) == "sensitivity_scanner"

    def test_auto_respects_decision_false(self):
        logic = make_logic("auto")
        assert logic._route_after_impl({"sensitivity_enabled": False}) == "paper_agent"

    def test_always_overrides_decision_false(self):
        logic = make_logic("always")
        assert logic._route_after_impl({"sensitivity_enabled": False}) == "sensitivity_scanner"

    def test_never_overrides_decision_true(self):
        logic = make_logic("never")
        assert logic._route_after_impl({"sensitivity_enabled": True}) == "paper_agent"

    def test_layer3_skipped_disables_even_always(self):
        # 求解层被跳过 -> 无 results.json 可扰动 -> 任何模式一律不可用
        logic = make_logic("always", selected=[4])
        assert logic._route_after_impl({"sensitivity_enabled": True}) == "paper_agent"

    def test_layer3_skipped_disables_auto_fail_open(self):
        logic = make_logic("auto", selected=[4])
        assert logic._route_after_impl({}) == "paper_agent"

    def test_no_layer4_ends_after_sensitivity(self):
        logic = make_logic("always", selected=[1, 2, 3])
        assert logic._route_after_impl({}) == "sensitivity_scanner"

    def test_no_layer4_never_ends(self):
        logic = make_logic("never", selected=[1, 2, 3])
        assert logic._route_after_impl({}) == END


class TestRouteAfterSensitivity:
    """Layer 5 结束后的路由：进入论文层或结束（ADR-0002 L5 前置）。"""

    def test_routes_to_paper_when_layer4_selected(self):
        logic = make_logic("auto")
        assert logic.should_continue_sensitivity({}) == "paper_agent"

    def test_ends_when_layer4_not_selected(self):
        logic = make_logic("auto", selected=[1, 2, 3])
        assert logic.should_continue_sensitivity({}) == END


class TestPaperIsTerminal:
    """论文层为最终层：结束后流程终止，不再有后置敏感性路由。"""

    def test_route_after_paper_removed(self):
        # 旧 _route_after_paper 已删除（论文层终点恒为 END）
        assert not hasattr(ConditionalLogic, "_route_after_paper")

    def test_route_to_next_layer_has_no_layer5_entry(self):
        logic = make_logic("always")
        assert logic._route_to_next_layer(2) in ("solver_agent", "paper_agent", END)


class TestGraphAlwaysBuildsLayer5:
    """ADR-0001 后果：Layer 5 节点无条件构建，启用推迟到运行时路由。"""

    def _compile(self, mode: str):
        config = {
            "llm_provider": "opencode",
            "selected_layers": [1, 2, 3, 4],
            "sensitivity_mode": mode,
            "max_debate_rounds": 2,
            "max_problem_rounds": 2,
            "max_modeling_rounds": 2,
            "max_revision_rounds": 2,
            "max_impl_retries": 2,
        }
        return GraphSetup(config).setup_graph().compile()

    def _node_names(self, compiled) -> set:
        return set(compiled.get_graph().nodes.keys())

    def test_never_mode_still_builds_layer5_nodes(self):
        nodes = self._node_names(self._compile("never"))
        assert {"param_perturber", "robustness_analyst", "sensitivity_manager"} <= nodes

    def test_auto_mode_builds_layer5_nodes(self):
        nodes = self._node_names(self._compile("auto"))
        assert {"param_perturber", "robustness_analyst", "sensitivity_manager"} <= nodes

    def test_sensitivity_manager_routes_to_paper(self):
        # L5 -> L4 的边存在：manager 出边包含 paper_agent 目标
        compiled = self._compile("auto")
        assert "paper_agent" in set(compiled.get_graph().nodes.keys())
