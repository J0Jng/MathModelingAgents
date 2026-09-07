# -*- coding: utf-8 -*-
"""候选模型池提炼容错 JSON 解析（_parse_traits_json）测试。

火山通道下模型常回 markdown 文本非纯 JSON，_parse_traits_json 与
_parse_sensitivity_json 同款容错：剥围栏 → 正则取首个 {...} → json.loads。
"""

import pytest

from mathmodelingagents.agents import _parse_traits_json


class TestParseTraitsJson:
    def test_pure_json_object(self):
        text = '{"problem_traits": ["小样本", "多目标"], "description": "数据少且需权衡多个目标。"}'
        traits, description = _parse_traits_json(text)
        assert traits == ["小样本", "多目标"]
        assert description == "数据少且需权衡多个目标。"

    def test_markdown_fence_wrapped(self):
        text = '```json\n{"problem_traits": ["非线性", "时序预测"], "description": "时间序列且关系非线性。"}\n```'
        traits, description = _parse_traits_json(text)
        assert traits == ["非线性", "时序预测"]
        assert description == "时间序列且关系非线性。"

    def test_json_surrounded_by_chinese_narrative(self):
        text = (
            "题目特点标签：\n"
            "数据量偏小，需要在小样本条件下兼顾多个优化目标，并讨论结果的可靠性。\n"
            '{"problem_traits": ["小样本", "多目标优化"], "description": "数据少，多目标权衡。"}\n'
            "与结果可靠性。"
        )
        traits, description = _parse_traits_json(text)
        assert traits == ["小样本", "多目标优化"]
        assert description == "数据少，多目标权衡。"

    def test_missing_traits_returns_empty(self):
        traits, description = _parse_traits_json('{"description": "只有描述没有标签。"}')
        assert traits == []
        assert description == "只有描述没有标签。"

    def test_traits_as_single_string_wrapped_to_list(self):
        traits, description = _parse_traits_json('{"problem_traits": "单标签", "description": ""}')
        assert traits == ["单标签"]
        assert description == ""

    def test_pure_chinese_text_without_braces_raises(self):
        with pytest.raises(ValueError):
            _parse_traits_json("这是一段没有任何大括号的纯中文叙述文本。")
