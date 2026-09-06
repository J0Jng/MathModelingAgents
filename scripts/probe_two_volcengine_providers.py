"""精确复刻 modeler_a 真实场景，分别实测两个火山 provider。

对比：volcengine（coding plan，不传 reasoning_effort）vs volcengine-plan（agent plan，传 medium）。
路径完全走框架：create_llm_client + bind_tools 三个工具 + 真实 system prompt + 长中文上下文。
每个 provider 测 1 次，避免烧钱/超时。
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from langchain_core.messages import SystemMessage, HumanMessage, AIMessage
from langchain_core.tools import tool as lc_tool

from mathmodelingagents.llm_clients import create_llm_client
from mathmodelingagents.agents.utils.prompt_templates import get_modeler_a_prompt

# 三个轻量工具（schema 与真实一致，bind_tools 只关心 schema；空响应与工具实现无关）
@lc_tool
def run_code_tool(code: str, timeout: int = 30) -> str:
    """运行 Python 代码并返回 stdout/stderr。"""
    return "[stub]"

@lc_tool
def web_search_tool(query: str) -> str:
    """联网搜索相关数学建模资料。"""
    return "[stub]"

@lc_tool
def model_search_tool(query: str) -> str:
    """在模型知识库中做向量语义检索，返回候选数学模型。"""
    return "[stub]"

TOOLS = [run_code_tool, web_search_tool, model_search_tool]

# 模拟真实长中文上下文：题目 + Layer1 分析片段 + 候选池片段（约 3k 字）
LONG_CTX = (
    "当前是第 1 轮。\n\n"
    "【问题重述】某城市地铁网络在高峰时段存在严重拥堵……（真实赛题全文约 5000 字，此处压缩）\n"
    + "数据集包含 3 个月的用户出行轨迹（约 120 万条）、站点坐标、列车时刻表……\n\n"
    "【Layer 1 综合分析】\n"
    + "问题归结为带容量约束的时空网络流量分配与分级定价问题。关键变量：时段 t、区间 (i,j) 的客流 x(t,i,j)、"
    + "票价价格 p。约束包括运力上限 C、换乘次数限制、站间最短路径。目标函数是最小化全网拥堵延误。\n\n"
    + "候选方向：1) 多商品流模型；2) 双层规划（公交定价 + 客流分配）；3) 随机动态规划。\n"
    + "数据特征：客流呈明显单峰分布（早高峰 7:30-8:30 占全天 42%），站点间 OD 对高度稀疏。\n\n"
    "【候选模型池（Top-5 检索结果）】\n"
    + "1. Frank-Wolfe 交通分配 / 2. 多项 Logit 出行方式选择 / 3. 双层规划公交定价 / "
    + "4. 时空图卷积神经网络客流预测 / 5. 排队论 M/M/c 站点服务模型。\n\n"
    "请提出你的建模方案，并用 run_code 验证核心公式。"
)

SYSTEM = get_modeler_a_prompt()


def probe(provider: str, model: str, label: str):
    print("=" * 60)
    print(f"[{label}] provider={provider}, model={model}")
    t0 = time.time()
    try:
        llm = create_llm_client(
            provider=provider,
            model=model,
            max_tokens=16384,
            request_timeout=180,
        )
        llm_tools = llm.bind_tools(TOOLS)
        resp = llm_tools.invoke([SystemMessage(content=SYSTEM), HumanMessage(content=LONG_CTX)])
        dt = time.time() - t0
        content = resp.content or ""
        calls = getattr(resp, "tool_calls", None) or []
        print(f"  用时 {dt:.0f}s")
        print(f"  content={len(content)} 字符")
        print(f"  tool_calls 数量={len(calls)}")
        if calls:
            print(f"  首个工具={calls[0].get('name')}")
        if content:
            print("  content 前 60 字: " + repr(content[:60]))
        if not content and not calls:
            print("  ⚠️ 空响应：无 content 且无 tool_calls")
    except Exception as e:
        dt = time.time() - t0
        print(f"  用时 {dt:.0f}s 后抛异常: {type(e).__name__}: {str(e)[:200]}")


if __name__ == "__main__":
    # 你的真实 provider：volcengine（coding plan）
    probe("volcengine", "deepseek-v4-pro", "Coding Plan")
    # 对照组：volcengine-plan（agent plan）
    probe("volcengine-plan", "deepseek-v4-pro", "Agent Plan")