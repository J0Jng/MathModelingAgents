"""
Quick API connectivity test for Layer 3 & Layer 4 agents.
Verifies LLM can be created, called, and tools can be bound.
"""
import json
from langchain_core.messages import SystemMessage, HumanMessage

from mathmodelingagents.default_config import DEFAULT_CONFIG
from mathmodelingagents.llm_clients import create_layer_llm
from mathmodelingagents.tools import create_coding_agent_tools, create_paper_agent_tools
from mathmodelingagents.agents.utils.prompt_templates import get_coding_agent_prompt, get_paper_agent_prompt

config = DEFAULT_CONFIG.copy()
config['output_dir'] = '/tmp/mma_api_test'

# ═══════════════════════════════════════════
# 1. LLM Client Connectivity
# ═══════════════════════════════════════════
print("1. Testing LLM client creation...")
try:
    llm = create_layer_llm(config, "implementation", "coder")
    print(f"   ✓ LLM created: {llm.model_name}")
except Exception as e:
    print(f"   ✗ LLM creation failed: {e}")
    exit(1)

# ═══════════════════════════════════════════
# 2. Basic API call (no tools)
# ═══════════════════════════════════════════
print("\n2. Testing basic API call (no tools)...")
try:
    response = llm.invoke([HumanMessage(content="Hello, reply with just 'OK' in English.")])
    print(f"   ✓ Response: {response.content[:100]}")
except Exception as e:
    print(f"   ✗ API call failed: {e}")
    exit(1)

# ═══════════════════════════════════════════
# 3. Tool binding
# ═══════════════════════════════════════════
print("\n3. Testing tool binding (CodingAgent tools)...")
try:
    tools = create_coding_agent_tools(config['output_dir'])
    llm_with_tools = llm.bind_tools(tools)
    print(f"   ✓ Tools bound: {[t.name for t in tools]}")
except Exception as e:
    print(f"   ✗ Tool binding failed: {e}")
    exit(1)

# ═══════════════════════════════════════════
# 4. API call with tools
# ═══════════════════════════════════════════
print("\n4. Testing API call with tools...")
system_prompt = """
You have a tool called `list_dir_tool` to list files in a directory.
Use it to check what's in /tmp, then say 'DONE'.
"""
try:
    response = llm_with_tools.invoke([
        SystemMessage(content=system_prompt),
        HumanMessage(content="List the contents of /tmp"),
    ])
    has_tool_calls = bool(response.tool_calls)
    print(f"   ✓ Response received, tool_calls: {has_tool_calls}")
    if has_tool_calls:
        for tc in response.tool_calls:
            print(f"     → {tc['name']}({json.dumps(tc['args'], ensure_ascii=False)})")
    else:
        print(f"   (LLM chose text reply instead of tool call)")
except Exception as e:
    print(f"   ✗ Tool-calling call failed: {e}")
    exit(1)

# ═══════════════════════════════════════════
# 5. PaperAgent tool binding (no run_code)
# ═══════════════════════════════════════════
print("\n5. Testing PaperAgent tool binding...")
try:
    pa_tools = create_paper_agent_tools(config['output_dir'])
    pa_names = [t.name for t in pa_tools]
    assert 'run_code_tool' not in pa_names, "PaperAgent should NOT have run_code!"
    print(f"   ✓ PaperAgent tools: {pa_names}")
except Exception as e:
    print(f"   ✗ PaperAgent tool test failed: {e}")
    exit(1)

# ═══════════════════════════════════════════
# 6. PaperAgent API call
# ═══════════════════════════════════════════
print("\n6. Testing PaperAgent API call (read_file)...")
paper_llm = create_layer_llm(config, "paper", "writer")
paper_llm_tools = paper_llm.bind_tools(pa_tools)
try:
    response = paper_llm_tools.invoke([
        SystemMessage(content="You have read_file, list_dir, write_file tools. Use read_file to check /tmp, then say DONE."),
        HumanMessage(content="Read /tmp/test_nonexistent.txt"),
    ])
    has_tc = bool(response.tool_calls)
    print(f"   ✓ Response received, tool_calls: {has_tc}")
    if has_tc:
        for tc in response.tool_calls:
            print(f"     → {tc['name']}({json.dumps(tc['args'], ensure_ascii=False)})")
except Exception as e:
    print(f"   ✗ PaperAgent API call failed: {e}")
    exit(1)

print("\n" + "=" * 50)
print("All connectivity tests passed ✓")
print("=" * 50)
