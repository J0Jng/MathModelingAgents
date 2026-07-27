#!/usr/bin/env python
"""验证模型路由和 prompt 模板的正确性。

检查:
1. 每个 layer/role 对应的模型名称是否正确
2. prompt 模板是否存在且内容合理
3. 关键函数（create_layer_llm, get_layer_model）可正常调用
"""

import sys
sys.path.insert(0, '.')

from mathmodelingagents.default_config import DEFAULT_CONFIG
from mathmodelingagents.llm_clients import get_layer_model, resolve_max_tokens
from mathmodelingagents.agents.utils.prompt_templates import (
    get_solver_agent_prompt,
    get_viz_agent_prompt,
    get_impl_manager_prompt,
    get_paper_agent_prompt,
    get_paper_manager_prompt,
)

config = DEFAULT_CONFIG.copy()
provider = config["llm_provider"]
print("=" * 60)
print("模型路由验证")
print(f"Provider: {provider}")
print("=" * 60)

# ── 1. 模型路由 ──
ROUTES = [
    # (layer, role, expected_key)
    ("problem", "agent", "quick_think_llm"),
    ("modeling", "agent", "quick_think_llm"),
    ("modeling", "manager", "deep_think_llm"),
    ("implementation", "coder", "quick_think_llm"),
    ("implementation", "manager", "deep_think_llm"),
    ("paper", "writer", "quick_think_llm"),
    ("paper", "manager", "deep_think_llm"),
]

route_failures = []
for layer, role, expected_key in ROUTES:
    model = get_layer_model(config, layer, role)
    expected_model = config.get(expected_key, "?")
    ok = True
    # deepseek 模式下 behavior 不同: manager → deep_think, else → quick_think
    if provider == "deepseek":
        if role == "manager":
            ok = (model == config["deep_think_llm"])
            expected_model = config["deep_think_llm"]
        else:
            ok = (model == config["quick_think_llm"])
            expected_model = config["quick_think_llm"]
    else:
        # opencode 模式: 检查 layer_model_overrides
        overrides = config.get("layer_model_overrides", {})
        lc = overrides.get(layer, {})
        if role in lc:
            ok = (model == lc[role])
        elif "agent" in lc:
            ok = (model == lc["agent"])

    status = "✓" if ok else "✗"
    print(f"  {status} layer={layer}, role={role}: model={model}")
    if not ok:
        route_failures.append((layer, role, model, expected_model))

# ── 2. Prompt 模板存在性 ──
print("\nPrompt 模板验证:")
prompts = {
    "solver_agent": get_solver_agent_prompt,
    "viz_agent": get_viz_agent_prompt,
    "impl_manager": get_impl_manager_prompt,
    "paper_agent": get_paper_agent_prompt,
    "paper_manager": get_paper_manager_prompt,
}

prompt_failures = []
for name, func in prompts.items():
    try:
        content = func()
        assert isinstance(content, str), f"type={type(content)}"
        assert len(content) > 100, f"too short ({len(content)} chars)"
        print(f"  ✓ {name}: {len(content)} chars")
    except Exception as e:
        print(f"  ✗ {name}: {e}")
        prompt_failures.append((name, str(e)))

# ── 3. LLM 客户端创建（不实际调用 API） ──
print("\nLLM 客户端创建验证（不调用 API）:")
# 检查 create_layer_llm 是否可以正常解析参数
# （这里只测函数签名和参数解析，不实际创建连接）
from mathmodelingagents.llm_clients import create_layer_llm

# 使用无效 API key 创建客户端，检查抛出的是 ValueError 而非其他错误
client_tests = [
    ("implementation", "coder", "solver"),
    ("paper", "writer", "paper"),
    ("implementation", "manager", "impl_manager"),
]

for layer, role, label in client_tests:
    try:
        create_layer_llm(config, layer, role)
        print(f"  ✓ {label} client created")
    except ValueError as e:
        # API key 问题是可以预期的
        if "API" in str(e) or "KEY" in str(e).upper() or "环境变量" in str(e):
            print(f"  ~ {label}: API key validation works (expected)")
        else:
            print(f"  ✗ {label}: unexpected ValueError: {e}")
    except Exception as e:
        print(f"  ✗ {label}: {e}")

# ── 汇总 ──
print()
total_failures = len(route_failures) + len(prompt_failures)
if total_failures:
    print(f"❌ {total_failures} failures found")
    if route_failures:
        for layer, role, got, exp in route_failures:
            print(f"  路由: {layer}/{role}: got {got}, expected {exp}")
    if prompt_failures:
        for name, err in prompt_failures:
            print(f"  Prompt: {name}: {err}")
    sys.exit(1)
else:
    print("✅ 模型路由和 prompt 模板全部正常")
    sys.exit(0)
