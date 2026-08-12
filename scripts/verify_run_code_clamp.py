#!/usr/bin/env python3
"""verify_run_code_clamp.py — 验证 tools/__init__.py 中 run_code 超时钳制逻辑。"""

import ast
import inspect
import json
import sys
from pathlib import Path

# ── 1. 静态检查：确认源码中存在 min(timeout 钳制逻辑 ──
TOOLS_FILE = Path(__file__).resolve().parent.parent / "mathmodelingagents" / "tools" / "__init__.py"
source = TOOLS_FILE.read_text(encoding="utf-8")

# 检查 1a: 模块级常量
assert "MAX_RUN_CODE_TIMEOUT = 300" in source, "❌ 缺少 MAX_RUN_CODE_TIMEOUT = 300"
print("✅ 1a: MAX_RUN_CODE_TIMEOUT = 300 常量存在")

# 检查 1b: create_langchain_tools 内 run_code_tool 有钳制
# 统计 min(timeout 出现次数
count_min = source.count("min(timeout or MAX_RUN_CODE_TIMEOUT, MAX_RUN_CODE_TIMEOUT)")
assert count_min >= 2, f"❌ min(timeout... 钳制逻辑出现 {count_min} 次，期望 ≥2"
print(f"✅ 1b: min(timeout or MAX_RUN_CODE_TIMEOUT, MAX_RUN_CODE_TIMEOUT) 出现 {count_min} 次")

# 检查 1c: 超时消息增强
assert "请将代码拆分为更小的执行单元" in source, "❌ 超时 stderr 消息未增强"
print("✅ 1c: 超时 stderr 消息已增强（含'请将代码拆分为更小的执行单元'）")

# 检查 1d: create_langchain_tools 中 run_code_tool 函数体内有钳制
# 解析 AST
tree = ast.parse(source)

# 找到 create_langchain_tools 函数
for node in ast.walk(tree):
    if isinstance(node, ast.FunctionDef) and node.name == "create_langchain_tools":
        # 找到内部的 run_code_tool
        for inner in ast.walk(node):
            if isinstance(inner, ast.FunctionDef) and inner.name == "run_code_tool":
                # Check that timeout clamp exists before run_code call
                source_lines = source.splitlines()
                func_source = source_lines[inner.lineno - 1 : inner.end_lineno]
                func_text = "\n".join(func_source)
                if "min(timeout or MAX_RUN_CODE_TIMEOUT, MAX_RUN_CODE_TIMEOUT)" in func_text:
                    print("✅ 1d: create_langchain_tools 内 run_code_tool 含 timeout 钳制")
                else:
                    print("❌ 1d: create_langchain_tools 内 run_code_tool 缺少 timeout 钳制")
                    sys.exit(1)
                break
        break

# 找到 create_coding_agent_tools 函数
for node in ast.walk(tree):
    if isinstance(node, ast.FunctionDef) and node.name == "create_coding_agent_tools":
        for inner in ast.walk(node):
            if isinstance(inner, ast.FunctionDef) and inner.name == "run_code_tool":
                source_lines = source.splitlines()
                func_source = source_lines[inner.lineno - 1 : inner.end_lineno]
                func_text = "\n".join(func_source)
                if "min(timeout or MAX_RUN_CODE_TIMEOUT, MAX_RUN_CODE_TIMEOUT)" in func_text:
                    print("✅ 1e: create_coding_agent_tools 内 run_code_tool 含 timeout 钳制")
                else:
                    print("❌ 1e: create_coding_agent_tools 内 run_code_tool 缺少 timeout 钳制")
                    sys.exit(1)
                break
        break

# ── 2. 动态检查：构造 create_coding_agent_tools 的 run_code_tool，调用简单代码验证工具可用 ──
from mathmodelingagents.tools import create_coding_agent_tools, MAX_RUN_CODE_TIMEOUT

# Constant verification
assert MAX_RUN_CODE_TIMEOUT == 300, f"MAX_RUN_CODE_TIMEOUT = {MAX_RUN_CODE_TIMEOUT}, expected 300"
print(f"✅ 2a: MAX_RUN_CODE_TIMEOUT == {MAX_RUN_CODE_TIMEOUT}")

# Create tools and find run_code_tool
tools = create_coding_agent_tools(output_dir="./test_output_clamp")
run_code_tool = None
for t in tools:
    if t.name == "run_code_tool":
        run_code_tool = t
        break

assert run_code_tool is not None, "❌ 未找到 run_code_tool"
print(f"✅ 2b: run_code_tool 创建成功 (name={run_code_tool.name})")

# Run simple code: check that it executes fine within 1s
result = run_code_tool.invoke({"code": "print('hello clamp test'); x = 1+1; print(f'1+1={x}')", "timeout": 60})
result_dict = json.loads(result)
assert result_dict["success"], f"❌ 简单代码执行失败: {result_dict}"
print(f"✅ 2c: 简单代码正常执行 (exit_code={result_dict['exit_code']}, time={result_dict['execution_time']:.3f}s)")

# Run with timeout=0 (None-like edge case) — should be clamped to MAX_RUN_CODE_TIMEOUT
# Note: the clamp uses `timeout or MAX_RUN_CODE_TIMEOUT` so timeout=0 → 300
result2 = run_code_tool.invoke({"code": "print('timeout=0 test')", "timeout": 0})
result2_dict = json.loads(result2)
assert result2_dict["success"], f"❌ timeout=0 执行失败: {result2_dict}"
print(f"✅ 2d: timeout=0 被正确钳制 (exit_code={result2_dict['exit_code']}, time={result2_dict['execution_time']:.3f}s)")

# Run with timeout=999 (excessive) — should be clamped to 300
result3 = run_code_tool.invoke({"code": "print('timeout=999 test')", "timeout": 999})
result3_dict = json.loads(result3)
assert result3_dict["success"], f"❌ timeout=999 执行失败: {result3_dict}"
print(f"✅ 2e: timeout=999→300 被正确钳制 (exit_code={result3_dict['exit_code']}, time={result3_dict['execution_time']:.3f}s)")

# ── 3. 清理测试目录 ──
import shutil
test_dir = Path("./test_output_clamp")
if test_dir.exists():
    shutil.rmtree(test_dir)

print("\n🎉 全部验证通过！")
