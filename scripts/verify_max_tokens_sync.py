#!/usr/bin/env python
"""验证所有 agent 角色的 max_tokens 统一为 16384。

检查所有调用 resolve_max_tokens 的地方，确保没有角色被覆盖为其他值。
"""

import sys
sys.path.insert(0, '.')

from mathmodelingagents.default_config import DEFAULT_CONFIG
from mathmodelingagents.llm_clients import resolve_max_tokens

# 所有已知的 (role, agent_name) 组合
ROLES = [
    # Layer 1: Problem Analysis
    ("agent", "constraint_analyst"),
    # Layer 2: Modeling
    ("agent", "modeling_agent_1"),
    ("agent", "modeling_agent_2"),
    ("agent", "modeling_agent_3"),
    # Layer 2 managers
    ("manager", ""),
    # Layer 3: Implementation
    ("coder", "solver_agent"),
    ("coder", "viz_agent"),
    ("manager", "impl_manager"),
    # Layer 4: Paper
    ("writer", "paper_agent"),
    ("writer", "paper_manager"),
]

EXPECTED = 16384
failed = []

print("=" * 60)
print("max_tokens 同步验证")
print(f"期望值: {EXPECTED}")
print("=" * 60)

for role, agent_name in ROLES:
    label = f"role={role}" + (f", agent={agent_name}" if agent_name else "")
    actual = resolve_max_tokens(DEFAULT_CONFIG, role, agent_name)
    status = "✓" if actual == EXPECTED else "✗"
    print(f"  {status} {label}: {actual}")
    if actual != EXPECTED:
        failed.append((label, actual))

# 检查 config 本身
default_val = DEFAULT_CONFIG.get("default_max_tokens", 0)
print(f"\n  config.default_max_tokens: {default_val}")

overrides = DEFAULT_CONFIG.get("max_tokens_overrides", {})
if overrides:
    print(f"  config.max_tokens_overrides: {overrides}")
    for k, v in overrides.items():
        if v != EXPECTED:
            failed.append((f"override[{k}]", v))
else:
    print(f"  config.max_tokens_overrides: (empty — all use default)")

print()
if failed:
    print(f"❌ 失败: {len(failed)} 个角色未使用 {EXPECTED}")
    for label, actual in failed:
        print(f"   - {label}: got {actual}")
    sys.exit(1)
else:
    print(f"✅ 全部通过: 所有角色 max_tokens = {EXPECTED}")
    sys.exit(0)
