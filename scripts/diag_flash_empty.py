"""诊断 opencode/deepseek-v4-flash 空返回问题。直接从 .env 加载 key，裸调用不改业务层。"""
import json, os, sys
from pathlib import Path
from dotenv import load_dotenv

# 定位项目根 .env
root = Path(__file__).resolve().parent.parent
load_dotenv(root / ".env")

from langchain_openai import ChatOpenAI

def probe(model, text, max_tokens=16384, label=""):
    print("=" * 60)
    print(f"[{label or model}] model={model} prompt_chars={len(text)} max_tokens={max_tokens}")
    llm = ChatOpenAI(
        model=model,
        api_key=os.getenv("OPENCODE_GO_API_KEY", ""),
        base_url="https://opencode.ai/zen/go/v1",
        temperature=0.0,
        max_tokens=max_tokens,
        request_timeout=120,
        max_retries=0,
    )
    try:
        from langchain_core.messages import HumanMessage
        r = llm.invoke([HumanMessage(content=text)])
        content = r.content
        print("  content_type:", type(content).__name__)
        print("  content_len:", len(content) if content is not None else None)
        print("  content_raw:", repr(content)[:500])
        # 额外字段
        ai = getattr(r, "additional_kwargs", {}) or {}
        print("  ai_kwargs_keys:", list(ai.keys()))
        for k, v in ai.items():
            if k in ("reasoning_content", "reasoning", "thinking", "refusal"):
                print(f"  ai.{k}:", repr(v)[:300])
    except Exception as e:
        print("  EXCEPTION:", type(e).__name__, str(e)[:400])

short = "用一句话解释梯度下降。"
long_cn = ("请对以下数学建模竞赛题目进行深度数学建模分析，设计完整的数学模型、变量、约束与求解策略。\n"
           "题目：某城市交通路口信号灯配时优化问题。请从多目标优化角度建立模型，考虑通行效率、延误、排放，"
           "给出决策变量、目标函数、约束条件，并设计求解算法，分析灵敏度。请务必详细展开，输出完整中文方案。\n" * 1)

probe("deepseek-v4-flash", short, label="SHORT")
probe("deepseek-v4-flash", long_cn, label="LONG")