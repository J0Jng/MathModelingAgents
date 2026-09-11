"""RAG 检索失败跳过候选池注入的单元测试（impl-brief candidate-pool-skip-on-rag-fail）。"""

from unittest.mock import Mock

from mathmodelingagents import agents
from mathmodelingagents.agents import _run_model_candidate_search
from mathmodelingagents import knowledge


def test_candidate_pool_skip_on_rag_fail(monkeypatch):
    """search_models 抛异常 → 返回 empty，且不调用 load_model_library 兜底。"""
    # 让 LLM 提炼返回合法 JSON，确保流程能推进到 search_models
    monkeypatch.setattr(
        agents,
        "invoke_with_fallback",
        lambda *args, **kwargs: '{"problem_traits": ["小样本"], "description": "样本少"}',
    )

    # 检索异常路径：search_models 抛异常（fastembed 不可用 / 向量化失败等）
    def _boom(query, top_k=5):
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