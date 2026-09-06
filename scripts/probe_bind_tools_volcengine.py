"""最小复现：langchain ChatOpenAI + bind_tools 在 volcengine-plan 端点是否返回空。

完全复刻框架路径（llm_clients.create_llm_client → llm.bind_tools → invoke）。
对比：绑定工具 vs 不绑定工具，看 content / tool_calls / 文本标签。
"""
import os
from dotenv import load_dotenv

load_dotenv()

from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage

KEY = os.getenv("VOLCENGINE_PLAN_API_KEY") or os.getenv("VOLCENGINE_API_KEY")
BASE_URL = "https://ark.cn-beijing.volces.com/api/plan/v3"


def make_llm(**kw):
    return ChatOpenAI(
        model="deepseek-v4-pro",
        api_key=KEY,
        base_url=BASE_URL,
        temperature=0.2,
        max_tokens=16384,
        request_timeout=180,
        max_retries=1,
        reasoning_effort="medium",
        **kw,
    )


# 一个简单的工具 schema（模拟 run_code）
def _tools():
    from langchain_core.tools import tool

    @tool
    def run_code_tool(code: str) -> str:
        """Execute Python code in a sandbox and return its stdout."""
        return "ok"

    return [run_code_tool]


LONG_USER = "请用 run_code 验证 1+1 的计算结果，写出 Python 代码并运行。建模师 A 请提出一个数学模型方案并用工具验证。"

msgs_no_tool = [
    SystemMessage(content="你是数学建模专家，请回答问题。"),
    HumanMessage(content=LONG_USER),
]


def probe_no_tool():
    print("[A] 不绑定工具（纯文本 invoke）")
    try:
        llm = make_llm()
        r = llm.invoke(msgs_no_tool)
        c = r.content or ""
        tc = getattr(r, "tool_calls", None)
        print("    content=" + str(len(c)) + "字符, tool_calls=" + repr(tc))
        if c:
            print("    前120字: " + repr(c[:120]))
    except Exception as e:
        print("    失败: " + type(e).__name__ + ": " + str(e)[:200])


def probe_bind_tools():
    print("[B] bind_tools 后 invoke（复刻框架 modeler 路径）")
    try:
        llm = make_llm()
        llm_tools = llm.bind_tools(_tools())
        r = llm_tools.invoke(msgs_no_tool)
        c = r.content or ""
        tc = getattr(r, "tool_calls", None)
        print("    content=" + str(len(c)) + "字符, tool_calls=" + repr(tc))
        if c:
            print("    前120字: " + repr(c[:120]))
        if tc:
            print("    tool_calls 名称: " + repr([t.get("name") for t in tc]))
    except Exception as e:
        print("    失败: " + type(e).__name__ + ": " + str(e)[:200])


probe_no_tool()
print()
print("-" * 60)
print()
probe_bind_tools()