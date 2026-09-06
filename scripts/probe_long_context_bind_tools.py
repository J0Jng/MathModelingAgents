"""复现真实 modeler 场景：长中文上下文 + bind_tools + reasoning_effort=medium。

对照上一个探针（短 prompt 正常），验证是否是「长上下文 → 推理吃满预算 → 正文空」。
实际 modeler user_msg = 题目全文 + Layer1 综合分析 + 候选模型池，可达上万字符。
"""
import os
import sys
from dotenv import load_dotenv

# 脚本直接运行时，sys.path[0] 是 scripts/ 而非项目根，需显式加入
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

load_dotenv()

from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage
from langchain_core.tools import tool

KEY = os.getenv("VOLCENGINE_PLAN_API_KEY") or os.getenv("VOLCENGINE_API_KEY")
BASE_URL = "https://ark.cn-beijing.volces.com/api/plan/v3"


@tool
def run_code_tool(code: str) -> str:
    """Execute Python code in a sandbox and return its stdout."""
    return "ok"


@tool
def web_search_tool(query: str) -> str:
    """Search the web and return results."""
    return "results"


@tool
def model_search_tool(query: str) -> str:
    """Search the model knowledge base."""
    return "models"


# ── 构造接近真实的长上下文（复用 get_modeler_a_prompt 的真实 system prompt）──
from mathmodelingagents.agents.utils.prompt_templates import get_modeler_a_prompt

SYSTEM_PROMPT = get_modeler_a_prompt()

# 模拟 Layer1 分析 + 候选池：约 8000 字中文
LAYER1_REPORT = """
## Layer 1 综合问题分析

### 题目定义
本问题是一个经典的设施选址与资源调度综合问题。某城市需要在给定区域内规划 N 个新能源充电站，
目标是在预算、地理、电网容量等多重约束下，最大化充电需求覆盖率，同时最小化用户总行驶距离。

### 数据全景
- 城市划分为 500 个网格，每个网格含：电动车保有量、日均充电需求（kWh）、是否适合建站、地价系数
- 候选站点 120 个，每个站点有建站成本、最大容量、电网接入等级
- 用户需求点 2000 个，含经纬度、日均需求、可接受最大距离
""" * 8  # 扩大到 ~8000+ 字

CANDIDATE_POOL = """
## 候选模型池（参考起点，可超越）

1. **p-median 模型** — 整数规划，最小化加权距离，经典设施选址
2. **最大覆盖选址（MCLP）** — 在预算约束下最大化覆盖需求
3. **双层规划（Bi-level）** — 上层选址、下层用户分配
4. **遗传算法 GA** — 组合优化启发式，适合 NP-hard 选址
5. **模拟退火 SA** — 局部搜索改进
6. **拉格朗日松弛** — 松弛容量约束求下界
7. **Benders 分解** — 大规模整数规划分解求解
8. **多目标加权法** — 覆盖最大化 + 距离最小化 + 成本最小化
""" * 6

USER_MSG = (
    "请根据以下上下文执行你的任务。当前是第 0 轮：\n\n"
    "## 题目内容\n\n" + LAYER1_REPORT + "\n\n" + CANDIDATE_POOL
)

print("user_msg 字符数 =", len(USER_MSG))
print("system_prompt 字符数 =", len(SYSTEM_PROMPT))
print()


def probe(label, reasoning_effort, max_tokens=16384):
    print("[" + label + "] reasoning_effort=" + str(reasoning_effort) + ", max_tokens=" + str(max_tokens))
    llm = ChatOpenAI(
        model="deepseek-v4-pro",
        api_key=KEY,
        base_url=BASE_URL,
        temperature=0.2,
        max_tokens=max_tokens,
        request_timeout=240,
        max_retries=1,
        reasoning_effort=reasoning_effort,
    )
    llm_tools = llm.bind_tools([run_code_tool, web_search_tool, model_search_tool])
    msgs = [SystemMessage(content=SYSTEM_PROMPT), HumanMessage(content=USER_MSG)]
    try:
        r = llm_tools.invoke(msgs)
        c = r.content or ""
        tc = getattr(r, "tool_calls", None)
        print("    content=" + str(len(c)) + "字符, tool_calls数=" + str(len(tc) if tc else 0))
        if tc:
            print("    tool 名: " + repr([t.get("name") for t in tc]))
        if c:
            print("    前100字: " + repr(c[:100]))
    except Exception as e:
        print("    失败: " + type(e).__name__ + ": " + str(e)[:200])


probe("L1-长上下文-medium", "medium")
print()
probe("L2-长上下文-low", "low")