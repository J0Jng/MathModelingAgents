"""模型知识库检索模块（ADR-0003：Layer 2 候选模型池 RAG）。

公开入口：
- load_model_library(): 读取 model_library.json，返回 52 条模型条目
- build_query():        把题目特点标签 + 描述拼成检索 query 文本
- search_models():      向量语义检索 Top-K 候选模型（fail-open 返回全量）
- format_model_entries(): 把模型条目格式化为 markdown 文本
"""

from mathmodelingagents.knowledge.retrieval import (
    build_query,
    entry_text,
    format_model_entries,
    load_model_library,
    search_models,
)

__all__ = [
    "load_model_library",
    "build_query",
    "search_models",
    "format_model_entries",
    "entry_text",
]
