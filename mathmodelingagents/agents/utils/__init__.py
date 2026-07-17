"""Agent 工具函数 — 公共辅助。"""

import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def read_file_safe(path: str) -> str:
    """安全读取文件内容。"""
    p = Path(path)
    if not p.exists():
        return f"[错误] 文件不存在: {path}"
    try:
        return p.read_text(encoding="utf-8")
    except Exception as e:
        return f"[错误] 读取失败: {e}"


def extract_numbers_from_text(text: str) -> list[float]:
    """从文本中提取所有数字，用于验证 Agent 是否基于实际数据。"""
    import re
    return [float(m) for m in re.findall(r'\d+\.?\d*', text)]
