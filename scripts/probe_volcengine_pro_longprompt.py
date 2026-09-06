"""诊断用探针：验证 volcengine-plan 端点 + deepseek-v4-pro 在长中文数学 prompt 下是否返空。

对照 reasoning_effort 有/无、max_tokens 大小，实测 content 长度 / reasoning 长度 / finish_reason。
只读 .env，不写入任何文件。
"""
import os
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

KEY = os.getenv("VOLCENGINE_PLAN_API_KEY") or os.getenv("VOLCENGINE_API_KEY")
BASE_URL = "https://ark.cn-beijing.volces.com/api/plan/v3"

client = OpenAI(api_key=KEY, base_url=BASE_URL, timeout=150)

# 一个较长、接近真实赛题的中文数学建模 prompt（模拟 modeler 收到的上下文）
LONG_PROMPT = """# 建模师 A — 创新与优雅

## 题目内容
某城市需要规划一组新能源充电站的位置，已知该城市划分成 200 个网格，每个网格的电动车保有量和日均充电需求已知（见数据）。要求在预算约束下，选址使得覆盖的充电需求最大化，同时考虑用户到站距离的最小化与充电站容量约束。

## Layer 1 综合问题分析
本问题是一个典型的设施选址问题（Facility Location Problem），涉及多目标优化：覆盖最大化、距离最小化、容量约束。数据规模 200×5，属于小样本、非线性、混合整数规划问题。候选方法包括：贪心选址、p-median 整数规划、遗传算法、模拟退火、拉格朗日松弛等。

## 候选模型池（参考起点）
1. p-median 模型（整数规划）
2. 最大覆盖选址模型（MCLP）
3. 遗传算法
4. 模拟退火
5. 多目标加权方法

## 你的任务
请提出完整的数学模型方案，包含数学公式（LaTeX）、变量定义、目标函数、约束条件，并用 run_code 验证核心算法的数值可行性，给出小规模 toy example 的实际运行结果。所有数值结论必须来自 run_code 实际输出，不准口算。
"""


def probe(reasoning_effort, max_tokens, label):
    kwargs = {
        "model": "deepseek-v4-pro",
        "messages": [
            {"role": "system", "content": "你是一名数学建模专家。"},
            {"role": "user", "content": LONG_PROMPT},
        ],
        "temperature": 0.2,
    }
    if max_tokens:
        # Coding Plan 用 max_tokens，Agent Plan 端点应传 max_completion_tokens
        kwargs["max_completion_tokens"] = max_tokens
    if reasoning_effort:
        kwargs["reasoning_effort"] = reasoning_effort

    try:
        resp = client.chat.completions.create(**kwargs)
        msg = resp.choices[0].message
        content = msg.content or ""
        reasoning = getattr(msg, "reasoning_content", "") or ""
        finish = resp.choices[0].finish_reason
        usage = resp.usage or {}
        print(f"[{label}] content={len(content)}字符, reasoning={len(reasoning)}字符, "
              f"finish_reason={finish}, "
              f"completion_tokens={getattr(usage, 'completion_tokens', '?')}")
        if content:
            print("    content 前 80 字: " + repr(content[:80]))
        return len(content), finish
    except Exception as e:
        print(f"[{label}] 调用失败: {type(e).__name__}: {e}")
        return -1, str(e)


print("=" * 60)
print("探针 1: reasoning_effort=medium, max_completion_tokens=16384（当前框架配置）")
probe("medium", 16384, "P1")

print()
print("探针 2: 无 reasoning_effort, max_completion_tokens=16384")
probe(None, 16384, "P2")

print()
print("探针 3: reasoning_effort=low, max_completion_tokens=8192")
probe("low", 8192, "P3")

print()
print("探针 4: 短 prompt（1+1=?）做基线 sanity check")
saved = client.chat.completions.create(
    model="deepseek-v4-pro",
    messages=[{"role": "user", "content": "1+1=?"}],
    max_completion_tokens=16384,
    reasoning_effort="medium",
)
m4 = saved.choices[0].message
print("[P4 短prompt] content=" + str(len(m4.content or "")) + "字符, "
      "finish_reason=" + str(saved.choices[0].finish_reason) + ", "
      "content=" + repr((m4.content or "")[:80]))