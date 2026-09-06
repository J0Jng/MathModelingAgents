"""knowledge 模块专项单测（ADR-0003）：纯逻辑 + 候选池检索 + 注入边界。

不依赖 91MB embedding 模型文件：`search_models` 走 mock（monkeypatch
`retrieval._encode`），真实检索冒烟用 skipif 保护。
"""

from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

import mathmodelingagents.agents as agents_module
from mathmodelingagents.knowledge import (
    build_query,
    entry_text,
    format_model_entries,
    load_model_library,
)
from mathmodelingagents.knowledge import retrieval as retrieval_module

_MODEL_LIB_PATH = (
    Path(__file__).parent.parent / "mathmodelingagents" / "knowledge" / "model_library.json"
)


# ═══════════════════════════════════════════════════════════════════
# 纯逻辑（无 mock）
# ═══════════════════════════════════════════════════════════════════


def test_load_model_library_has_52_entries():
    library = load_model_library()
    assert len(library) == 52
    for m in library:
        assert m.get("name"), f"条目缺 name: {m}"
        assert m.get("category"), f"条目缺 category: {m}"
        assert m.get("traits"), f"条目缺 traits: {m}"
        assert m.get("usage"), f"条目缺 usage: {m}"


def test_build_query_joins_traits_and_description():
    query = build_query(["小样本", "指数增长"], "数据量少且呈指数增长")
    assert query == "小样本 指数增长 数据量少且呈指数增长"


def test_build_query_filters_empty_items():
    query = build_query([" 小样本 ", "", None, "  "], "  ")
    assert query == "小样本"


def test_entry_text_includes_name_traits_usage():
    entry = {
        "name": "灰色预测 GM(1,1)",
        "category": "预测",
        "traits": ["小样本", "指数增长"],
        "usage": "少数据时序预测",
    }
    text = entry_text(entry)
    assert "灰色预测 GM(1,1)" in text
    assert "小样本" in text
    assert "少数据时序预测" in text


def test_format_model_entries_markdown():
    entries = [
        {"name": "模型A", "category": "预测", "traits": ["小样本"], "usage": "时序预测"},
        {"name": "模型B", "category": "优化", "traits": ["多目标"], "usage": "规划求解"},
    ]
    text = format_model_entries(entries)
    assert "### 1. 模型A（预测）" in text
    assert "- 特点：小样本" in text
    assert "- 适用：时序预测" in text
    assert "### 2. 模型B（优化）" in text


# ═══════════════════════════════════════════════════════════════════
# search_models（mock _encode，不依赖 embedding 模型文件）
# ═══════════════════════════════════════════════════════════════════


def _patch_encode(monkeypatch, q: np.ndarray):
    """mock retrieval._encode 并绕开预计算 .npz 缓存，使相似度完全可控。

    - 检索库向量编码为单位矩阵 e_i（len(texts)==52 时），
      则「余弦相似度 = q[i]」，排序由 q 的取值直接决定；
    - query（len(texts)==1）返回 q 本身；
    - 同时把 _VECTORS_PATH 指向不存在路径，强制 _get_vectors 走现场计算
      fallback（否则真实 .npz 缓存会绕开 mock 的 _encode，维度也不匹配）。
    """

    def _encode(texts: list[str]) -> np.ndarray:
        if len(texts) == 1:
            return q.reshape(1, -1).astype(np.float32)
        return np.eye(len(texts), dtype=np.float32)

    monkeypatch.setattr(retrieval_module, "_encode", _encode)
    monkeypatch.setattr(retrieval_module, "_VECTORS_PATH", Path("nonexistent.npz"))


def test_search_models_returns_top_k_sorted(monkeypatch):
    # 构造 query 向量：条目 3 相似度最高，其次条目 1，其余为 0
    n = len(load_model_library())
    q = np.zeros(n, dtype=np.float32)
    q[3] = 0.9
    q[1] = 0.5
    _patch_encode(monkeypatch, q)

    results = retrieval_module.search_models("query", top_k=5)
    assert len(results) == 5
    library = load_model_library()
    assert results[0]["name"] == library[3]["name"]
    assert results[1]["name"] == library[1]["name"]
    # 命中路径返回的是完整条目
    for m in results:
        assert {"name", "category", "traits", "usage"} <= set(m)


def test_search_models_fail_open_on_encode_error(monkeypatch):
    def _boom(texts):
        raise RuntimeError("模型不可用")

    monkeypatch.setattr(retrieval_module, "_encode", _boom)
    results = retrieval_module.search_models("query", top_k=5)
    assert len(results) == 52  # fail-open：返回全量谱系，不 crash


# ═══════════════════════════════════════════════════════════════════
# _build_context 注入边界（直接构造 state dict）
# ═══════════════════════════════════════════════════════════════════


def test_build_context_injects_pool_first_round_only():
    from mathmodelingagents.agents import _build_context

    config = {"output_dir": "out"}
    pool_text = "### 1. 灰色预测 GM(1,1)（预测）"

    def make_state(round_count, with_pool=True):
        state = {"problem_report": "报告", "model_debate_state": {"round_count": round_count}}
        if with_pool:
            state["model_candidates"] = pool_text
        return state

    # round_count=0：注入
    ctx = _build_context(make_state(0), "modeling", "modeler_a", config)
    assert "## 候选模型池（参考起点，可超越）" in ctx
    assert pool_text in ctx

    # round_count=2：不注入
    ctx = _build_context(make_state(2), "modeling", "modeler_a", config)
    assert "候选模型池" not in ctx

    # 无 candidates：不注入
    ctx = _build_context(make_state(0, with_pool=False), "modeling", "modeler_a", config)
    assert "候选模型池" not in ctx


# ═══════════════════════════════════════════════════════════════════
# _run_model_candidate_search（mock knowledge 层 + 假结构化 LLM）
# ═══════════════════════════════════════════════════════════════════


def _patch_fake_llm(monkeypatch, traits=("小样本", "指数增长"), description="数据量少"):
    """monkeypatch agents.create_layer_llm：with_structured_output 返回 ProblemTraits 形状。"""

    class FakeLLM:
        def with_structured_output(self, schema):
            class _Runner:
                def invoke(self, messages):
                    return SimpleNamespace(problem_traits=list(traits), description=description)

            return _Runner()

    monkeypatch.setattr(agents_module, "create_layer_llm", lambda *a, **kw: FakeLLM())


def test_run_model_candidate_search_rag_source(monkeypatch):
    _patch_fake_llm(monkeypatch)
    hits = [
        {"name": "灰色预测 GM(1,1)", "category": "预测", "traits": ["小样本"], "usage": "少数据时序预测"}
    ]
    import mathmodelingagents.knowledge as knowledge_module

    monkeypatch.setattr(knowledge_module, "search_models", lambda query, top_k=5: hits)
    pool = agents_module._run_model_candidate_search({"llm_provider": "opencode"}, "题目报告")
    assert pool.source == "rag"
    assert "灰色预测 GM(1,1)" in pool.text
    assert pool.query == "小样本 指数增长 数据量少"


def test_run_model_candidate_search_fail_open_full_library(monkeypatch):
    _patch_fake_llm(monkeypatch)
    import mathmodelingagents.knowledge as knowledge_module

    def _boom(query, top_k=5):
        raise RuntimeError("检索失败")

    monkeypatch.setattr(knowledge_module, "search_models", _boom)
    pool = agents_module._run_model_candidate_search({"llm_provider": "opencode"}, "题目报告")
    assert pool.source == "full_library"
    assert len(load_model_library()) == 52
    assert pool.text.count("### ") == 52


def test_run_model_candidate_search_empty_source(monkeypatch):
    _patch_fake_llm(monkeypatch)
    import mathmodelingagents.knowledge as knowledge_module

    def _boom(*a, **kw):
        raise RuntimeError("彻底失败")

    monkeypatch.setattr(knowledge_module, "search_models", _boom)
    monkeypatch.setattr(knowledge_module, "load_model_library", _boom)
    pool = agents_module._run_model_candidate_search({"llm_provider": "opencode"}, "题目报告")
    assert pool.source == "empty"
    assert pool.text == ""


# ═══════════════════════════════════════════════════════════════════
# 真实检索冒烟（skipif 保护：embedding 模型未下载时跳过）
# ═══════════════════════════════════════════════════════════════════


@pytest.mark.skipif(
    not (_MODEL_LIB_PATH.parent / "models").is_dir(),
    reason="embedding 模型未下载（knowledge/models/ 不存在）",
)
def test_real_retrieval_smoke():
    from mathmodelingagents.knowledge import search_models

    results = search_models("小样本 指数增长 预测", top_k=5)
    assert len(results) == 5
    assert "灰色" in results[0]["name"]
