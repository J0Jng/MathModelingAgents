"""problem_loader / 降级链加固 / 敏感性决策容错 单元测试。

对应 docs/input-overflow-and-fallback-hardening-impl-brief.md 的
Ticket A（附件剥离）、Ticket B（输入超长 fail-fast）、Ticket C（降级链去重）、
Ticket D（敏感性决策容错 JSON 解析）以及 Change List #4（_build_context 注入附件摘要）。
"""

import json

import pytest

from mathmodelingagents.problem_loader import (
    SPLIT_MARKER_RE,
    build_attachment_summary,
    extract_attachments,
    split_problem_document,
)

from mathmodelingagents.llm_clients import (
    _build_fallback_steps,
    is_input_too_long_error,
)

# ── 测试样本 ──────────────────────────────────────────────────────

MD_SAMPLE = """题目正文第一段。

问题 1：请建立数学模型。

<!-- ========== 附件A.xlsx ========== -->

# 附件A.xlsx

| 波数 | 反射率 |
| --- | --- |
| 399.6 | 0.00 |
| 401.2 | 0.01 |
| 402.9 | 0.02 |

<!-- ========== 附件B.xlsx ========== -->

| 深度 | 厚度 |
| --- | --- |
| 1.0 | 2.0 |
"""

# 真实场景：文档第 1 行就是分隔符（如「B题.pdf（题目正文）」），
# 首个分段是正文段而非附件。
MD_LEADING = """<!-- ========== B题.pdf（题目正文） ========== -->

题目正文。

附件说明：附件1 是波数与反射率数据。

<!-- ========== 附件1.xlsx ========== -->

| 波数 | 反射率 |
| --- | --- |
| 1 | 2 |
"""


# ═══ Ticket A: split_problem_document ═══

class TestSplitProblemDocument:
    def test_basic_split(self):
        body, attachments = split_problem_document(MD_SAMPLE)
        assert "题目正文第一段" in body
        assert "附件" not in body
        assert [name for name, _ in attachments] == ["附件A.xlsx", "附件B.xlsx"]
        a_text = dict(attachments)["附件A.xlsx"]
        assert "| 399.6 | 0.00 |" in a_text
        # 附件 A 的内容不应混入附件 B 的分隔符
        assert "附件B" not in a_text
        b_text = dict(attachments)["附件B.xlsx"]
        assert "| 1.0 | 2.0 |" in b_text

    def test_leading_marker_section_is_body(self):
        """文档以分隔符开头时，首个分段（如「B题.pdf（题目正文）」）归入正文段。"""
        body, attachments = split_problem_document(MD_LEADING)
        assert "题目正文" in body
        assert "附件说明" in body
        assert [name for name, _ in attachments] == ["附件1.xlsx"]

    def test_no_marker(self):
        text = "纯题目，没有任何附件分隔符。"
        body, attachments = split_problem_document(text)
        assert body == text
        assert attachments == []

    def test_marker_regex_format(self):
        m = SPLIT_MARKER_RE.search("前文<!-- ========== 附件1.xlsx ========== -->后文")
        assert m.group(1) == "附件1.xlsx"


# ═══ Ticket A: build_attachment_summary ═══

class TestBuildAttachmentSummary:
    def test_summary_contains_required_fields(self):
        _, attachments = split_problem_document(MD_SAMPLE)
        summary = build_attachment_summary(attachments)
        assert "附件A.xlsx" in summary
        assert "数据行数" in summary
        assert "3" in summary          # 附件A 有 3 条数据行
        assert "波数" in summary       # 表头列说明
        assert "399.6" in summary      # 第 1 条示例数据行
        assert "401.2" in summary      # 第 2 条示例数据行

    def test_summary_separator_row_not_counted(self):
        """表头分隔行 | --- | --- | 不计入数据行数。"""
        _, attachments = split_problem_document(MD_LEADING)
        summary = build_attachment_summary(attachments)
        # 附件1.xlsx 只有 1 条数据行（| 1 | 2 |），分隔行不算
        assert "1" in summary
        assert "| 1 | 2 |" in summary

    def test_summary_with_source_dir(self, tmp_path):
        att_dir = tmp_path / "附件"
        att_dir.mkdir()
        (att_dir / "附件A.xlsx").write_bytes(b"fake xlsx")
        _, attachments = split_problem_document(MD_SAMPLE)
        summary = build_attachment_summary(attachments, source_dir=str(tmp_path))
        assert str(att_dir / "附件A.xlsx") in summary


# ═══ Ticket A: extract_attachments ═══

class TestExtractAttachments:
    def test_writes_files_and_returns_summary(self, tmp_path):
        body, summary = extract_attachments(MD_SAMPLE, str(tmp_path))
        assert "题目正文第一段" in body
        att_dir = tmp_path / "attachments"
        files = sorted(p.name for p in att_dir.iterdir())
        assert files == ["01_附件A.xlsx.md", "02_附件B.xlsx.md"]
        # 落盘内容是附件全量表格
        assert "| 399.6 | 0.00 |" in (att_dir / "01_附件A.xlsx.md").read_text(encoding="utf-8")
        # summary 含落盘相对路径
        assert "attachments/01_附件A.xlsx.md" in summary
        assert "attachments/02_附件B.xlsx.md" in summary

    def test_filename_sanitized(self, tmp_path):
        md = "正文\n\n<!-- ========== 附/件:1.xlsx ========== -->\n\n| a |\n| --- |\n| 1 |"
        _, summary = extract_attachments(md, str(tmp_path))
        files = sorted(p.name for p in (tmp_path / "attachments").iterdir())
        assert files == ["01_附_件_1.xlsx.md"]
        assert "attachments/01_附_件_1.xlsx.md" in summary


# ═══ Ticket C: _build_fallback_steps 去重 ═══

class TestBuildFallbackStepsDedup:
    def test_primary_equals_flash_dedups_to_2_steps(self):
        """primary 模型 == flash 模型时，4 步降级链去重为 2 步。"""
        config = {
            "llm_provider": "opencode",
            "fallback_provider": "deepseek",
            "fallback_base_url": "https://api.deepseek.com/v1",
            "quick_think_llm": "m-flash",
            "layer_model_overrides": {"modeling": {"agent": "m-flash"}},
        }
        steps = _build_fallback_steps(config, "modeling", "agent")
        assert len(steps) == 2
        assert steps[0] == ("opencode", "m-flash", None)
        assert steps[1] == ("deepseek", "m-flash", "https://api.deepseek.com/v1")

    def test_distinct_models_keep_4_steps_in_order(self):
        """primary != flash 时保持 4 步且顺序不变。"""
        config = {
            "llm_provider": "opencode",
            "fallback_provider": "deepseek",
            "quick_think_llm": "m-flash",
            "layer_model_overrides": {"modeling": {"agent": "m-pro"}},
        }
        steps = _build_fallback_steps(config, "modeling", "agent")
        assert len(steps) == 4
        assert steps == [
            ("opencode", "m-pro", None),
            ("deepseek", "m-pro", None),
            ("opencode", "m-flash", None),
            ("deepseek", "m-flash", None),
        ]


# ═══ Ticket B: is_input_too_long_error ═══

class TestIsInputTooLongError:
    def test_volcengine_input_length_error(self):
        err = RuntimeError(
            "400 InvalidParameter: Input length 1255282 exceeds maximum length 1048566"
        )
        assert is_input_too_long_error(err) is True

    def test_context_window_error(self):
        assert is_input_too_long_error(ValueError("prompt exceeds the maximum context window")) is True

    def test_rate_limit_not_too_long(self):
        assert is_input_too_long_error(RuntimeError("Error code: 429 - rate limit exceeded")) is False

    def test_connection_error_not_too_long(self):
        assert is_input_too_long_error(ConnectionError("connection reset by peer")) is False


# ═══ Ticket D: _parse_sensitivity_json ═══

class TestParseSensitivityJson:
    def test_plain_json(self):
        from mathmodelingagents.agents import _parse_sensitivity_json
        obj = _parse_sensitivity_json('{"enabled": true, "reason": "含优化参数"}')
        assert obj == {"enabled": True, "reason": "含优化参数"}

    def test_fenced_json(self):
        from mathmodelingagents.agents import _parse_sensitivity_json
        text = '```json\n{"enabled": false, "reason": "纯数据呈现"}\n```'
        obj = _parse_sensitivity_json(text)
        assert obj["enabled"] is False

    def test_json_embedded_in_prose(self):
        from mathmodelingagents.agents import _parse_sensitivity_json
        text = '判断结果如下：\n{"enabled": true, "reason": "存在权重参数"}\n以上。'
        obj = _parse_sensitivity_json(text)
        assert obj["enabled"] is True

    def test_invalid_json_raises(self):
        from mathmodelingagents.agents import _parse_sensitivity_json
        with pytest.raises(ValueError):
            _parse_sensitivity_json("这不是 JSON")

    def test_broken_json_raises(self):
        from mathmodelingagents.agents import _parse_sensitivity_json
        with pytest.raises(ValueError):
            _parse_sensitivity_json('{"enabled": true, "reason": ')  # 截断


# ═══ Change List #4: _build_context 注入附件摘要 ═══

class TestBuildContextAttachmentSummary:
    def test_summary_injected_for_non_viz_agents(self):
        from mathmodelingagents.agents import _build_context
        state = {
            "problem_description": "题目正文",
            "attachment_summary": "附件1.xlsx：数据行数 7470",
        }
        config = {"output_dir": "out"}
        ctx = _build_context(state, "modeling", "modeler_a", config)
        assert "附件数据摘要" in ctx
        assert "数据行数 7470" in ctx
        assert "read_file/run_code" in ctx

    def test_viz_agent_excluded(self):
        from mathmodelingagents.agents import _build_context
        state = {"attachment_summary": "SUM"}
        ctx = _build_context(state, "implementation", "viz_agent", {"output_dir": "out"})
        assert "附件数据摘要" not in ctx

    def test_missing_summary_no_crash(self):
        from mathmodelingagents.agents import _build_context
        ctx = _build_context({}, "modeling", "modeler_a", {"output_dir": "out"})
        assert "附件数据摘要" not in ctx
