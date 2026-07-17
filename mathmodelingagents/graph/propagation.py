"""状态初始化和传播 — 创建初始状态并管理图执行参数。"""

import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any

from mathmodelingagents.agents.utils.agent_states import AgentState, DebateState

logger = logging.getLogger(__name__)


class Propagator:
    """状态初始化和传播。

    负责创建流程的初始 AgentState，以及提供图执行参数。
    对标 TradingAgents 的 propagator 模块。

    Attributes:
        max_recur_limit: LangGraph recursion_limit，防止无限循环。
    """

    def __init__(self, max_recur_limit: int = 100):
        """初始化 Propagator。

        Args:
            max_recur_limit: LangGraph 递归上限。
        """
        self.max_recur_limit = max_recur_limit

    def create_initial_state(
        self,
        problem_path: str,
        output_name: str | None = None,
    ) -> dict[str, Any]:
        """创建初始 AgentState，包含所有层的默认值。

        Args:
            problem_path: 题目 Markdown 文件路径。
            output_name: 输出文件夹名。为 None 时自动生成。

        Returns:
            初始化的 AgentState 字典。

        Raises:
            FileNotFoundError: 如果 problem_path 不存在。
        """
        problem_file = Path(problem_path)
        if not problem_file.exists():
            raise FileNotFoundError(f"题目文件不存在: {problem_path}")

        # 读取问题描述
        problem_description = problem_file.read_text(encoding="utf-8")

        # 自动生成输出名
        if output_name is None:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            stem = problem_file.stem
            output_name = f"{stem}_{timestamp}"

        # 构建初始 debate_state
        initial_debate_state: DebateState = {
            "round_count": 0,
            "max_rounds": 10,
            "a_history": "",
            "b_history": "",
            "c_history": "",
            "history": "",
            "latest_speaker": "",
            "current_a_response": "",
            "current_b_response": "",
            "current_c_response": "",
            "judge_decision": "CONTINUE",
        }

        state: AgentState = {
            # ═══ 输入 ═══
            "problem_path": str(problem_file.resolve()),
            "problem_description": problem_description,
            "output_name": output_name,
            # ═══ 消息历史 ═══
            "messages": [],
            # ═══ Layer 1 ═══
            "problem_report": "",
            "constraints": "",
            "assumptions": "",
            "data_insights": "",
            "problem_messages": [],
            # ═══ Layer 2 ═══
            "model_debate_state": initial_debate_state,
            "model_spec": "",
            "formulas": "",
            "solution_approach": "",
            "model_validation": "",
            "modeling_messages": [],
            # ═══ Layer 3 ═══
            "algorithm_spec": "",
            "code_results": "",
            "visualizations": [],
            "error_analysis": "",
            "impl_messages": [],
            "impl_retry_count": 0,
            # ═══ Layer 4 ═══
            "paper_outline": "",
            "final_paper": "",
            "paper_messages": [],
            # ═══ Layer 5 ═══
            "sensitivity_scan": "",
            "sensitivity_report": "",
            "sensitivity_messages": [],
            # ═══ 元信息 ═══
            "current_layer": "1",
            "layer_results": {},
            "debate_state": initial_debate_state,
            "layer_outputs": [],
        }

        logger.info(f"初始状态已创建: problem={problem_file.name}, output={output_name}")
        return state  # type: ignore[return-value]

    def get_graph_args(self) -> dict[str, Any]:
        """获取 langgraph.graph 执行参数。

        Returns:
            包含 stream_mode、config 等参数的字典。
        """
        return {
            "stream_mode": "values",
            "config": {"recursion_limit": self.max_recur_limit},
        }
