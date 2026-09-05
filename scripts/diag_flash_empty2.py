"""诊断2: 长度阈值、max_tokens、backend_url 的影响。"""
import os, sys
from pathlib import Path
from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent.parent / ".env")
from langchain_openai import ChatOpenAI

base = os.getenv("MATHMODELING_BACKEND_URL") or "https://opencode.ai/zen/go/v1"
print("BACKEND_URL =", base)

def probe(model, text, max_tokens, label=""):
    llm = ChatOpenAI(model=model, api_key=os.getenv("OPENCODE_GO_API_KEY",""),
                     base_url=base, temperature=0.0, max_tokens=max_tokens,
                     request_timeout=90, max_retries=0)
    from langchain_core.messages import HumanMessage
    try:
        r = llm.invoke([HumanMessage(content=text)])
        c = r.content or ""
        print(f"  [{label or model}] tok={max_tokens} chars={len(c)} ok=True raw={repr(c)[:200]} refusal={getattr(r.additional_kwargs,'get',lambda _:None)('refusal') if r.additional_kwargs else None}")
    except Exception as e:
        print(f"  [{label or model}] tok={max_tokens} EXC {type(e).__name__}: {str(e)[:200]}")

base_txt = "请用数学建模解决一个城市交通信号灯配时优化问题，建立多目标优化模型：通行效率、延误、排放，给出决策变量、目标函数与约束，设计求解算法并分析灵敏度。请输出完整中文技术方案。"
for n in (60, 120, 141, 300, 800):
    probe("deepseek-v4-flash", base_txt[:n], 16384, f"len{n}")
probe("deepseek-v4-flash", base_txt, 2048, "max2048")
probe("deepseek-v4-pro", base_txt, 16384, "PRO-16384")
probe("ark-code-latest", base_txt + "内容较长，请继续详细展开模型细节。", 16384, "ARK")