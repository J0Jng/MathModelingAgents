"""模型知识库向量检索核心实现（ADR-0003）。

数据流：model_library.json（52 条，只读）→ fastembed 本地编码
（BAAI/bge-small-zh-v1.5，512 维，不引入 torch）→ 余弦相似度 → Top-K。

fail-open 约定：检索/向量化/模型加载任一步失败都不 raise 出模块边界——
`search_models` 内部捕获后降级返回全量谱系，由调用方直接注入，不阻塞主流程。
所有异常用 logging 记录，不 print。
"""

import json
import logging
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)

# ── 路径与常量 ──
_KNOWLEDGE_DIR = Path(__file__).parent
_LIBRARY_PATH = _KNOWLEDGE_DIR / "model_library.json"
_VECTORS_PATH = _KNOWLEDGE_DIR / "model_library_vectors.npz"
_MODELS_CACHE_DIR = _KNOWLEDGE_DIR / "models"

# fastembed 内部把 BAAI/bge-small-zh-v1.5 解析为 Qdrant/bge-small-zh-v1.5，
# 但调用时仍传 BAAI 名称（见 docs/adr/0003-model-library-rag.md）
_EMBED_MODEL_NAME = "BAAI/bge-small-zh-v1.5"

# ── 模块级单例缓存 ──
_embedder = None            # fastembed TextEmbedding 实例
_embedder_failed = False    # 首次加载失败后不再重试（避免每轮检索都卡在加载）


def load_model_library() -> list[dict]:
    """读取 model_library.json，返回 ``data["models"]``（list[dict]）。

    每条含 name/category/traits/usage 四个字段。文件只读，不修改内容。
    """
    data = json.loads(_LIBRARY_PATH.read_text(encoding="utf-8"))
    return data["models"]


def _get_embedder():
    """单例加载 fastembed TextEmbedding。

    - model_name 固定 BAAI/bge-small-zh-v1.5，cache_dir 指向 repo 内
      knowledge/models/，local_files_only=True（离线推理，不联网）。
    - 首次加载失败（模型目录不存在 / 未安装 fastembed / ONNX 损坏）
      返回 None 并置 _embedder_failed，由调用方 fail-open，不重试。
    """
    global _embedder, _embedder_failed
    if _embedder is not None:
        return _embedder
    if _embedder_failed:
        return None
    if not _MODELS_CACHE_DIR.is_dir():
        logger.warning(
            "embedding 模型目录不存在: %s（先运行 scripts/build_model_library.py "
            "--download-model 或按 README 镜像下载），检索将 fail-open",
            _MODELS_CACHE_DIR,
        )
        _embedder_failed = True
        return None
    try:
        from fastembed import TextEmbedding

        _embedder = TextEmbedding(
            model_name=_EMBED_MODEL_NAME,
            cache_dir=str(_MODELS_CACHE_DIR),
            local_files_only=True,
        )
        logger.info("fastembed 模型已加载: %s (cache_dir=%s)",
                    _EMBED_MODEL_NAME, _MODELS_CACHE_DIR)
        return _embedder
    except Exception as e:
        logger.warning("fastembed 加载失败，检索将 fail-open: %s", e)
        _embedder_failed = True
        return None


def _encode(texts: list[str]) -> np.ndarray:
    """编码文本列表为 L2 归一化后的向量矩阵 (len(texts), 512) float32。

    embedder 不可用时 raise，由上层（search_models）捕获触发 fail-open。
    """
    embedder = _get_embedder()
    if embedder is None:
        raise RuntimeError("embedding 模型不可用（未下载或 fastembed 加载失败）")
    vectors = np.array(list(embedder.embed(texts)), dtype=np.float32)
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return vectors / norms


def entry_text(entry: dict) -> str:
    """构造单条模型条目的检索文本：name + traits + usage（纯中文）。"""
    return " ".join([
        entry.get("name", ""),
        " ".join(entry.get("traits", [])),
        entry.get("usage", ""),
    ])


def build_query(problem_traits: list[str], description: str = "") -> str:
    """把题目特点标签 + 描述拼成检索 query 文本（纯中文）。"""
    parts = [t.strip() for t in problem_traits if t and t.strip()]
    if description and description.strip():
        parts.append(description.strip())
    return " ".join(parts)


def _get_vectors(library: list[dict]) -> np.ndarray:
    """取全部条目的预计算向量。

    优先读 model_library_vectors.npz（keys: vectors + names）；缺失或
    names 与当前知识库不一致时现场计算 fallback（不依赖 .npz）。
    """
    names = [m.get("name", "") for m in library]
    try:
        data = np.load(_VECTORS_PATH, allow_pickle=False)
        if list(data["names"]) == names:
            vectors = np.asarray(data["vectors"], dtype=np.float32)
            if vectors.shape[0] == len(library):
                return vectors
        logger.info("向量缓存与知识库不一致，现场重新计算")
    except Exception as e:
        logger.info("向量缓存缺失或不可读（%s），现场计算 fallback", e)
    return _encode([entry_text(m) for m in library])


def search_models(query: str, top_k: int = 5) -> list[dict]:
    """按 query 检索 Top-K 候选模型。

    query 编码 → 与预计算向量算余弦相似度（均已 L2 归一化，内积即余弦）
    → 返回 top_k 条完整模型条目（name/category/traits/usage）。

    fail-open：模型未下载 / 向量化失败 / 缓存损坏等任何异常 →
    返回全量谱系（调用方注入完整清单，不阻塞主流程）。
    """
    library = load_model_library()
    try:
        query_vec = _encode([query])[0]
        vectors = _get_vectors(library)
        similarities = vectors @ query_vec
        order = np.argsort(-similarities)[: max(1, int(top_k))]
        results = [library[int(i)] for i in order]
        logger.info(
            "模型检索: query=%r, top%d 首条=%s (相似度 %.4f)",
            query[:50], top_k, results[0].get("name", ""), float(similarities[order[0]]),
        )
        return results
    except Exception as e:
        logger.warning("模型检索失败，fail-open 返回全量谱系: %s", e)
        return library


def format_model_entries(entries: list[dict]) -> str:
    """把模型条目列表格式化为 markdown 文本（候选池注入 / model_search 工具共用）。"""
    if not entries:
        return "（模型知识库为空）"
    lines = []
    for i, m in enumerate(entries, 1):
        traits = "、".join(m.get("traits", []))
        lines.append(
            f"### {i}. {m.get('name', '')}（{m.get('category', '')}）\n"
            f"- 特点：{traits}\n"
            f"- 适用：{m.get('usage', '')}"
        )
    return "\n\n".join(lines)
