"""RAG 检索失败跳过候选池注入的单元测试（impl-brief candidate-pool-skip-on-rag-fail）。

覆盖真实路径：strict=True 时 search_models 内部 re-raise（而非吞异常返回全量），
上层 _run_model_candidate_search 捕获后返回 empty、跳过候选池注入。
"""

import pytest
from unittest.mock import Mock

from mathmodelingagents import agents
from mathmodelingagents.agents import _run_model_candidate_search
from mathmodelingagents import knowledge
from mathmodelingagents.knowledge import search_models
from mathmodelingagents.knowledge import retrieval


def test_candidate_pool_skip_on_strict_re_raise(monkeypatch):
    """search_models 抛异常（真实场景为 strict=True 时的 re-raise）→ 上层返回 empty，且不调用 load_model_library 兜底。"""
    # 让 LLM 提炼返回合法 JSON，确保流程能推进到 search_models
    monkeypatch.setattr(
        agents,
        "invoke_with_fallback",
        lambda *args, **kwargs: '{"problem_traits": ["小样本"], "description": "样本少"}',
    )

    # 检索异常路径：search_models re-raise（strict=True 时 fastembed 不可用 / 向量化失败等）
    def _boom(query, top_k=5, **kwargs):
        raise RuntimeError("embedding 模型不可用")

    monkeypatch.setattr(knowledge, "search_models", _boom)

    # 兜底函数应是死代码，打桩为 Mock 以便断言未被调用
    load_mock = Mock(return_value=[{"name": "x"}])
    monkeypatch.setattr(knowledge, "load_model_library", load_mock)

    pool = _run_model_candidate_search({}, "## 题目分析报告\n\n...")

    assert pool.source == "empty"
    assert pool.text == ""
    assert pool.query == ""
    load_mock.assert_not_called()


def test_search_models_strict_raises_on_real_embed_failure(monkeypatch):
    """真实 embedding 失败路径（不 mock search_models 本身）：_get_embedder 不可用。

    strict=True 应 re-raise RuntimeError；默认 strict=False 应 fail-open 返回 52 条全量。
    """
    # 模拟真实失败：embedding 模型未下载 / 加载失败 → _get_embedder 返回 None
    monkeypatch.setattr(retrieval, "_get_embedder", lambda: None)

    with pytest.raises(RuntimeError):
        search_models("小样本 指数增长", top_k=5, strict=True)

    results = search_models("小样本 指数增长", top_k=5)
    assert len(results) == 52


def test_candidate_pool_skip_integration_real_strict_path(monkeypatch):
    """集成：不 mock knowledge.search_models，走真实 strict=True 路径 → 上层返回 empty。"""
    # 让 LLM 提炼返回合法 JSON，确保流程能推进到 search_models
    monkeypatch.setattr(
        agents,
        "invoke_with_fallback",
        lambda *args, **kwargs: '{"problem_traits": ["小样本"], "description": "样本少"}',
    )

    # 真实 embedding 失败路径（不 mock search_models，strict=True 触发 re-raise）
    monkeypatch.setattr(retrieval, "_get_embedder", lambda: None)

    # 候选池路径不应重新引入 load_model_library 兜底
    load_mock = Mock(return_value=[{"name": "x"}])
    monkeypatch.setattr(knowledge, "load_model_library", load_mock)

    pool = _run_model_candidate_search({}, "## 题目分析报告\n\n...")

    assert pool.source == "empty"
    assert pool.text == ""
    assert pool.query == ""
    load_mock.assert_not_called()
