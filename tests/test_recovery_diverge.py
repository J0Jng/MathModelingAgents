"""load_layer1_state diverge 模式测试（--from-layer1 --diverge）。

- 默认（diverge=False）：行为不变，回填 model_candidates。
- diverge=True：跳过候选池回填；从 Layer2_数学建模.md 提取最终方案
  作为 previous_model_spec；文件缺失时 fail-open（key 不存在、不抛异常）。
"""

import json

import pytest

from mathmodelingagents.graph.recovery import load_layer1_state

LAYER1_MD = """# Layer 1: 问题分析

### 问题分析经理 [裁决]

**CONCLUDE**

题目为生产计划优化问题，需在资源约束下最大化利润。

---

### 其他段落
"""

CANDIDATES = {"text": "1. 线性规划\n2. 动态规划\n3. 图论匹配"}

LAYER2_MD = """# Layer 2: 数学建模

--- 第 1 轮 ---

### 建模师 A [创新与优雅]

初步想法：线性规划……

### 建模经理 [裁决]

**CONTINUE**

第 1 轮方案尚不成熟，继续辩论。

---

--- 第 2 轮 ---

### 建模经理 [裁决]

**CONCLUDE**

最终方案：整数规划模型，目标函数为最大化利润，约束为产能与人力。

---

### 尾注
"""


@pytest.fixture
def layer1_dir(tmp_path):
    (tmp_path / "Layer1_问题分析.md").write_text(LAYER1_MD, encoding="utf-8")
    (tmp_path / "model_candidates.json").write_text(
        json.dumps(CANDIDATES, ensure_ascii=False), encoding="utf-8"
    )
    return tmp_path


def test_default_backfills_model_candidates(layer1_dir):
    """默认路径行为不变：仍回填 model_candidates。"""
    recovered = load_layer1_state(str(layer1_dir))
    assert recovered["model_candidates"] == CANDIDATES["text"]
    assert "previous_model_spec" not in recovered


def test_diverge_skips_candidates_and_extracts_layer2_spec(layer1_dir):
    """diverge=True：不回填候选池；previous_model_spec 取 Layer2 最后一次裁决。"""
    (layer1_dir / "Layer2_数学建模.md").write_text(LAYER2_MD, encoding="utf-8")

    recovered = load_layer1_state(str(layer1_dir), diverge=True)

    assert "model_candidates" not in recovered
    assert "整数规划模型" in recovered["previous_model_spec"]
    # 取最后一次裁决，而非首轮 CONTINUE 段
    assert "尚不成熟" not in recovered["previous_model_spec"]


def test_diverge_missing_layer2_file_fail_open(layer1_dir, caplog):
    """diverge=True 且 Layer2 文件缺失：key 不存在、不抛异常、记 warning。"""
    with caplog.at_level("WARNING"):
        recovered = load_layer1_state(str(layer1_dir), diverge=True)

    assert "previous_model_spec" not in recovered
    assert "model_candidates" not in recovered
    assert any("Layer2" in r.message for r in caplog.records)
