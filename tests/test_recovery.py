"""tests for mathmodelingagents.graph.recovery.load_layer1_state."""

import json

import pytest

from mathmodelingagents.graph.recovery import load_layer1_state


def _make_layer1_dir(tmp_path, report: str, decision: dict | None = None):
    layer1 = (
        "# Layer 1: 问题分析\n"
        "\n"
        "> 生成时间: 2026-01-01 00:00:00\n"
        "\n"
        "---\n"
        "### 问题拆解师 [分析]\n"
        "\n"
        "拆解内容……\n"
        "\n"
        "---\n"
        "### 问题分析经理 [裁决]\n"
        "\n"
        f"{report}\n"
    )
    (tmp_path / "Layer1_问题分析.md").write_text(layer1, encoding="utf-8")
    if decision is not None:
        (tmp_path / "sensitivity_decision.json").write_text(
            json.dumps(decision, ensure_ascii=False), encoding="utf-8"
        )
    return tmp_path


class TestLoadLayer1State:
    def test_extracts_problem_report_from_manager_verdict(self, tmp_path):
        report = "**CONCLUDE**\n\n## 问题分析报告\n核心结论：本题是优化问题。\n\n## 层摘要\n核心产出摘要。"
        d = _make_layer1_dir(tmp_path, report)
        recovered = load_layer1_state(str(d))
        assert "本题是优化问题" in recovered["problem_report"]
        assert "问题分析经理" not in recovered["problem_report"].split("\n")[0]

    def test_extraction_falls_back_to_full_file(self, tmp_path):
        content = "# Layer 1: 问题分析\n\n没有裁决段落的旧文件内容。"
        (tmp_path / "Layer1_问题分析.md").write_text(content, encoding="utf-8")
        recovered = load_layer1_state(str(tmp_path))
        assert recovered["problem_report"] == content

    def test_returns_sensitivity_decision_when_json_exists(self, tmp_path):
        report = "**CONCLUDE** 裁决内容"
        d = _make_layer1_dir(
            tmp_path, report,
            decision={"enabled": False, "reason": "纯描述统计，无需扰动", "mode": "auto"},
        )
        recovered = load_layer1_state(str(d))
        assert recovered["sensitivity_enabled"] is False
        assert recovered["sensitivity_reason"] == "纯描述统计，无需扰动"

    def test_missing_json_omits_sensitivity_keys(self, tmp_path):
        d = _make_layer1_dir(tmp_path, "**CONCLUDE** 裁决内容")
        recovered = load_layer1_state(str(d))
        assert "sensitivity_enabled" not in recovered
        assert "sensitivity_reason" not in recovered

    def test_corrupt_json_omits_sensitivity_keys(self, tmp_path):
        d = _make_layer1_dir(tmp_path, "**CONCLUDE** 裁决内容")
        (d / "sensitivity_decision.json").write_text("{not json", encoding="utf-8")
        recovered = load_layer1_state(str(d))
        assert "sensitivity_enabled" not in recovered
        assert "sensitivity_reason" not in recovered

    def test_missing_layer1_file_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            load_layer1_state(str(tmp_path))
