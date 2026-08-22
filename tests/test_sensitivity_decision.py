"""接缝 3：ProblemManager 节点的敏感性决策（假 LLM 边界）。

monkeypatch LLM 客户端工厂，只测节点外部行为：
- CONCLUDE 后状态更新含敏感性决策字段
- CONTINUE 时不做决策调用
- 决策调用异常时 fail-open（enabled=True）
"""

from types import SimpleNamespace

import pytest

import mathmodelingagents.agents as agents_module
from mathmodelingagents.agents import create_problem_manager

CONCLUDE_VERDICT = (
    "## 问题分析结论\n\n本题为核心预测建模问题。\n\n**CONCLUDE**\n\n"
    "## 层摘要\n\n题目要求建立预测模型，含关键权重参数。"
)

CONTINUE_VERDICT = "**CONTINUE**\n\n需补充数据来源分析。"


def make_config() -> dict:
    return {
        "llm_provider": "opencode",
        "layer_model_overrides": {"problem": {"agent": "m-agent", "manager": "m-manager"}},
        "quick_think_llm": "flash",
        "deep_think_llm": "pro",
        "default_max_tokens": 256,
        "default_temperature": 0.0,
        "temperature_overrides": {},
        "max_tokens_overrides": {},
        "layer_timeouts": {"problem": 10},
        "max_debate_rounds": 3,
        "output_dir": "out",
    }


def make_state() -> dict:
    return {
        "problem_description": "题目描述",
        "model_debate_state": {"round_count": 0},
        "layer_outputs": [],
        "layer_summary": "",
    }


class FakeStructuredLLM:
    """替身 LLM：with_structured_output 返回预置的决策结果。"""

    def __init__(self, outcome=None, error: Exception | None = None):
        self._outcome = outcome
        self._error = error
        self.calls = 0

    def with_structured_output(self, schema):
        outer = self

        class _Runner:
            def invoke(self, messages):
                outer.calls += 1
                if outer._error:
                    raise outer._error
                return SimpleNamespace(
                    enabled=outer._outcome["enabled"],
                    reason=outer._outcome["reason"],
                )

        return _Runner()


def run_node(monkeypatch, verdict: str, decision_llm) -> dict:
    monkeypatch.setattr(
        agents_module, "invoke_with_fallback",
        lambda *a, **kw: verdict,
    )
    monkeypatch.setattr(agents_module, "create_layer_llm", lambda *a, **kw: decision_llm)
    node = create_problem_manager(make_config())
    return node(make_state())


class TestSensitivityDecisionOnConclude:
    def test_decision_true_written_to_state(self, monkeypatch):
        fake = FakeStructuredLLM(outcome={"enabled": True, "reason": "含关键权重参数，需扰动检验"})
        updates = run_node(monkeypatch, CONCLUDE_VERDICT, fake)
        assert updates["sensitivity_enabled"] is True
        assert updates["sensitivity_reason"] == "含关键权重参数，需扰动检验"
        assert fake.calls == 1

    def test_decision_false_written_to_state(self, monkeypatch):
        fake = FakeStructuredLLM(outcome={"enabled": False, "reason": "纯描述统计，无参数可扰动"})
        updates = run_node(monkeypatch, CONCLUDE_VERDICT, fake)
        assert updates["sensitivity_enabled"] is False
        assert updates["sensitivity_reason"] == "纯描述统计，无参数可扰动"

    def test_problem_report_still_written(self, monkeypatch):
        fake = FakeStructuredLLM(outcome={"enabled": True, "reason": "r"})
        updates = run_node(monkeypatch, CONCLUDE_VERDICT, fake)
        assert updates["problem_report"] == CONCLUDE_VERDICT


class TestNoDecisionOnContinue:
    def test_continue_makes_no_decision_call(self, monkeypatch):
        fake = FakeStructuredLLM(outcome={"enabled": True, "reason": "r"})
        updates = run_node(monkeypatch, CONTINUE_VERDICT, fake)
        assert fake.calls == 0
        assert "sensitivity_enabled" not in updates
        assert "sensitivity_reason" not in updates


class TestDecisionOnlyInAutoMode:
    """always/never 模式由模式直接接管，不做多余的决策调用（模式覆盖决策）。"""

    @pytest.mark.parametrize("mode", ["always", "never"])
    def test_non_auto_mode_skips_decision_call(self, monkeypatch, mode):
        config = make_config()
        config["sensitivity_mode"] = mode
        fake = FakeStructuredLLM(outcome={"enabled": True, "reason": "r"})
        monkeypatch.setattr(agents_module, "invoke_with_fallback", lambda *a, **kw: CONCLUDE_VERDICT)
        monkeypatch.setattr(agents_module, "create_layer_llm", lambda *a, **kw: fake)
        updates = create_problem_manager(config)(make_state())
        assert fake.calls == 0
        assert "sensitivity_enabled" not in updates


class TestFailOpen:
    def test_decision_call_exception_fails_open(self, monkeypatch):
        fake = FakeStructuredLLM(error=RuntimeError("全部降级耗尽"))
        updates = run_node(monkeypatch, CONCLUDE_VERDICT, fake)
        assert updates["sensitivity_enabled"] is True
        assert "失败" in updates["sensitivity_reason"]

    def test_malformed_decision_fails_open(self, monkeypatch):
        # 结构化输出返回异常形状（enabled 缺失）-> fail-open，不崩溃
        class _BadRunner:
            def invoke(self, messages):
                return SimpleNamespace(reason="缺 enabled 字段")

        class _BadLLM:
            def with_structured_output(self, schema):
                return _BadRunner()

        updates = run_node(monkeypatch, CONCLUDE_VERDICT, _BadLLM())
        assert updates["sensitivity_enabled"] is True


class TestEndToEndWithRealisticProblem:
    """一个小问题的端到端决策链路：完整 CONCLUDE 裁决 → 决策调用 → 状态写入。"""

    REALISTIC_VERDICT = (
        "## 问题分析结论\n\n"
        "本题要求建立预测模型，对外部数据依赖强，"
        "模型中的权重系数和初始条件对最终结果影响显著，"
        "建议进行敏感性分析以检验结论稳健性。\n\n"
        "**CONCLUDE**\n\n"
        "## 层摘要\n\n"
        "题目为多变量预测问题，需建立优化模型。"
        "关键参数：学习率、正则化系数、时间窗口。"
    )

    def test_full_decision_pipeline(self, monkeypatch):
        """完整决策链路：裁决 → 结构化调用 → 状态写入 → 下游可见。"""
        fake = FakeStructuredLLM(outcome={
            "enabled": True,
            "reason": "含学习率、正则化系数等关键参数，需扰动检验",
        })
        monkeypatch.setattr(
            agents_module, "invoke_with_fallback",
            lambda *a, **kw: self.REALISTIC_VERDICT,
        )
        monkeypatch.setattr(agents_module, "create_layer_llm", lambda *a, **kw: fake)

        node = create_problem_manager(make_config())
        updates = node(make_state())

        # 裁决通过
        assert updates["problem_report"] == self.REALISTIC_VERDICT
        assert "CONCLUDE" in updates["problem_report"]
        # 决策调用执行了一次
        assert fake.calls == 1
        # 决策写入状态
        assert updates["sensitivity_enabled"] is True
        assert "学习率" in updates["sensitivity_reason"]
        # 层摘要被提取
        assert "多变量预测" in updates.get("layer_summary", "")

    def test_simple_no_sensitivity(self, monkeypatch):
        """纯描述统计问题 → 不需要敏感性分析。"""
        fake = FakeStructuredLLM(outcome={
            "enabled": False,
            "reason": "纯描述统计类问题，无可扰动参数",
        })
        monkeypatch.setattr(agents_module, "invoke_with_fallback", lambda *a, **kw: self.REALISTIC_VERDICT)
        monkeypatch.setattr(agents_module, "create_layer_llm", lambda *a, **kw: fake)

        updates = create_problem_manager(make_config())(make_state())
        assert updates["sensitivity_enabled"] is False
        assert "无可扰动参数" in updates["sensitivity_reason"]
    def test_decision_call_exception_fails_open(self, monkeypatch):
        fake = FakeStructuredLLM(error=RuntimeError("全部降级耗尽"))
        updates = run_node(monkeypatch, CONCLUDE_VERDICT, fake)
        assert updates["sensitivity_enabled"] is True
        assert "失败" in updates["sensitivity_reason"]

    def test_malformed_decision_fails_open(self, monkeypatch):
        # 结构化输出返回异常形状（enabled 缺失）-> fail-open，不崩溃
        class _BadRunner:
            def invoke(self, messages):
                return SimpleNamespace(reason="缺 enabled 字段")

        class _BadLLM:
            def with_structured_output(self, schema):
                return _BadRunner()

        updates = run_node(monkeypatch, CONCLUDE_VERDICT, _BadLLM())
        assert updates["sensitivity_enabled"] is True
