"""Checkpointer — SqliteSaver for crash recovery (参考 TradingAgents)."""
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def get_checkpointer(output_dir: str):
    """为指定输出目录创建 SqliteSaver 检查点实例。

    崩溃恢复：如果程序在执行中崩溃，下次使用相同 output_dir 运行时，
    可从上次检查点继续执行，而非从头开始。

    Args:
        output_dir: 输出目录路径，检查点数据库将存放在 .checkpoints/ 子目录。

    Returns:
        SqliteSaver 实例，或 None（导入失败 / 输出目录为空时）。
    """
    if not output_dir:
        logger.warning("output_dir 为空，跳过检查点初始化")
        return None

    try:
        from langgraph.checkpoint.sqlite import SqliteSaver
    except ImportError:
        logger.warning("langgraph-checkpoint-sqlite 不可用，回退到无检查点模式")
        return None

    checkpoint_dir = Path(output_dir) / ".checkpoints"
    checkpoint_dir.mkdir(parents=True, exist_ok=True)
    db_path = str(checkpoint_dir / "state.db")

    saver = SqliteSaver.from_conn_string(db_path)
    logger.info(f"检查点已启用: {db_path}")
    return saver
