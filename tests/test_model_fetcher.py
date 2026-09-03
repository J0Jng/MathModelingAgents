from cli.model_fetcher import provider_models_endpoint, fetch_models


def test_provider_has_codingplan_endpoint():
    # volcengine (Coding) 与 volcengine-plan (Agent) 端点不同，但都是 /models
    assert provider_models_endpoint("volcengine").endswith("/api/coding/v3/models")
    assert provider_models_endpoint("volcengine-plan").endswith("/api/plan/v3/models")


def test_fetch_unavailable_returns_empty_list(monkeypatch):
    # 认证失败 / 网络失败都应返回 []，由调用方降级到静态清单
    monkeypatch.setenv("VOLCENGINE_API_KEY", "bad-key")
    result = fetch_models("volcengine")
    assert result == []  # 不抛异常，不阻塞
