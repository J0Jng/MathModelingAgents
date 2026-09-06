"""离线建库脚本：为模型知识库生成预计算向量缓存（ADR-0003）。

职责：
1. 读取 mathmodelingagents/knowledge/model_library.json（52 条，只读），
   对每条模型构造检索文本（name + " " + " ".join(traits) + " " + usage）。
2. 用 fastembed（BAAI/bge-small-zh-v1.5，512 维，本地 ONNX 推理，不引入 torch）
   编码全部条目 → 保存 mathmodelingagents/knowledge/model_library_vectors.npz
   （keys: ``vectors`` 矩阵 [52, 512] + ``names`` 数组 [52]）。

用法（必须用项目 .venv 的 Python）：

    # 仅生成向量库（要求模型已存在于 knowledge/models/）
    .venv/Scripts/python.exe scripts/build_model_library.py

    # 先下载模型再生成向量库
    .venv/Scripts/python.exe scripts/build_model_library.py --download-model

镜像下载（国内网络实测，直连 HuggingFace 会 ConnectTimeout）：

    export HF_ENDPOINT=https://hf-mirror.com
    export HF_HUB_DISABLE_XET=1   # 不设会 CAS 401（xet 存储走不通镜像）

模型约 95MB（model_optimized.onnx 94.8MB + tokenizer/config），
下载到 mathmodelingagents/knowledge/models/（fastembed/HF-Hub 缓存布局）。

一致性铁律：更换 embedding 模型后必须重跑本脚本重建向量库，
否则 search_models 会因 names/维度不匹配而现场重算（或 fail-open）。
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
MODELS_DIR = REPO_ROOT / "mathmodelingagents" / "knowledge" / "models"
VECTORS_PATH = REPO_ROOT / "mathmodelingagents" / "knowledge" / "model_library_vectors.npz"
EMBED_MODEL_NAME = "BAAI/bge-small-zh-v1.5"


def _set_mirror_env() -> None:
    """设置国内镜像环境变量。

    必须在导入 huggingface_hub/fastembed 之前调用——HF_ENDPOINT 在
    huggingface_hub 模块导入时读取，导入后设置不生效。
    """
    os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
    os.environ.setdefault("HF_HUB_DISABLE_XET", "1")


def download_model() -> None:
    """下载 bge-small-zh-v1.5 到 knowledge/models/（镜像环境变量 + 禁 xet）。"""
    _set_mirror_env()
    from fastembed import TextEmbedding  # noqa: E402 须在设环境变量后导入

    print(f"开始下载 {EMBED_MODEL_NAME} 到 {MODELS_DIR} ...")
    print(f"HF_ENDPOINT={os.environ['HF_ENDPOINT']}, HF_HUB_DISABLE_XET={os.environ['HF_HUB_DISABLE_XET']}")
    # local_files_only 不设 → fastembed 内部经 huggingface_hub 走镜像下载
    TextEmbedding(model_name=EMBED_MODEL_NAME, cache_dir=str(MODELS_DIR))
    print(f"模型已下载: {MODELS_DIR}")


def build_vectors() -> None:
    """编码全部条目并生成 model_library_vectors.npz。"""
    import numpy as np  # noqa: E402

    sys.path.insert(0, str(REPO_ROOT))
    from mathmodelingagents.knowledge.retrieval import (  # noqa: E402
        _encode,
        entry_text,
        load_model_library,
    )

    library = load_model_library()
    texts = [entry_text(m) for m in library]
    print(f"编码 {len(texts)} 条模型条目（{EMBED_MODEL_NAME}, 512 维）...")
    vectors = _encode(texts)  # 内部单例加载 fastembed，L2 归一化
    names = np.array([m.get("name", "") for m in library])
    np.savez(VECTORS_PATH, vectors=vectors, names=names)
    print(f"向量库已生成: {VECTORS_PATH} (vectors={vectors.shape}, names={names.shape})")


def main() -> int:
    parser = argparse.ArgumentParser(description="模型知识库离线建库脚本")
    parser.add_argument(
        "--download-model", action="store_true",
        help="先经镜像下载 embedding 模型到 knowledge/models/（HF_ENDPOINT + 禁 xet）",
    )
    args = parser.parse_args()

    if args.download_model:
        download_model()
    elif not MODELS_DIR.is_dir():
        print(
            f"[错误] 模型目录不存在: {MODELS_DIR}\n"
            f"请先运行: {Path(sys.argv[0]).name} --download-model",
            file=sys.stderr,
        )
        return 1

    build_vectors()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
