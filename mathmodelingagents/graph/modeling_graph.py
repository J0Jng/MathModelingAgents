"""MathModelingGraph — 主入口类，对标 TradingAgentsGraph。

提供一站式接口：初始化配置 → 构建图 → 编译图 → 执行流程。
"""

import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any

from mathmodelingagents.default_config import DEFAULT_CONFIG
from mathmodelingagents.graph.propagation import Propagator
from mathmodelingagents.graph.setup import GraphSetup
from mathmodelingagents.agents.utils.agent_states import AgentState

logger = logging.getLogger(__name__)


class MathModelingGraph:
    """数学建模多 Agent 系统的主入口类。

    对标 TradingAgents 的 TradingAgentsGraph，负责：
    1. 加载配置
    2. 初始化 GraphSetup 构建 StateGraph
    3. 编译图
    4. 提供 propagate() 方法运行完整流程

    Attributes:
        config: 全局配置字典。
        debug: 是否启用调试模式。
        graph_setup: GraphSetup 实例。
        propagator: Propagator 实例。
        workflow: 未编译的 StateGraph。
        graph: 编译后的可执行图。
    """

    def __init__(
        self,
        config: dict | None = None,
        debug: bool = False,
    ):
        """初始化 MathModelingGraph。

        Args:
            config: 全局配置字典。为 None 时使用 DEFAULT_CONFIG。
            debug: 启用调试日志。
        """
        self.config = config if config is not None else DEFAULT_CONFIG
        self.debug = debug

        if debug:
            logging.getLogger("mathmodelingagents").setLevel(logging.DEBUG)
            logger.setLevel(logging.DEBUG)

        logger.info("初始化 MathModelingGraph...")

        # 初始化组件
        self.propagator = Propagator(
            max_recur_limit=self.config.get("max_recur_limit", 100),
        )
        self.graph_setup = GraphSetup(self.config)

        # 构建并编译图
        self.workflow = self.graph_setup.setup_graph()
        self.graph = self.workflow.compile()

        logger.info("MathModelingGraph 初始化完成，图已编译")

    def propagate(
        self,
        problem_path: str,
        output_name: str | None = None,
    ) -> tuple[dict[str, Any], str]:
        """运行完整的数学建模流程（流式 + 增量写盘）。

        每个节点完成后立即将 agent 输出写入对应层文件，
        即使后续节点崩溃，已完成层的内容也不会丢失。

        Args:
            problem_path: 题目 Markdown 文件路径。
            output_name: 输出文件夹名。为 None 时自动生成。

        Returns:
            (final_state, final_paper) 元组。
        """
        from mathmodelingagents.reporting import (
            setup_incremental, append_agent_output, finalize_reports,
        )

        problem_file = Path(problem_path)
        if not problem_file.exists():
            raise FileNotFoundError(f"题目文件不存在: {problem_path}")

        logger.info(f"开始执行流程: problem={problem_file.name}, output={output_name}")

        initial_state = self.propagator.create_initial_state(
            problem_path=str(problem_file.resolve()),
            output_name=output_name,
        )

        # ── 确定输出目录 ──
        output_dir = self.config.get("output_dir")
        if not output_dir:
            desktop = Path.home() / "Desktop"
            if not desktop.exists():
                desktop = Path.home() / "桌面"
            output_dir = str(desktop / initial_state.get("output_name", "output"))
        self.config["output_dir"] = output_dir
        setup_incremental(output_dir)
        problem_name = Path(initial_state.get("problem_path", "")).stem

        # ── 流式执行，逐节点写盘 ──
        logger.info("正在流式执行图...")
        prev_count = 0
        result = initial_state

        try:
            for chunk in self.graph.stream(
                initial_state,
                stream_mode="values",
                config={"recursion_limit": self.propagator.max_recur_limit},
            ):
                result = chunk
                outputs = chunk.get("layer_outputs", [])
                # 写入新增的记录
                for rec in outputs[prev_count:]:
                    append_agent_output(output_dir, rec)
                prev_count = len(outputs)
        except Exception as e:
            # 崩溃时已完成层文件已在磁盘，写入崩溃标记后重新抛出
            logger.error(f"图执行失败: {e}")
            crash_path = Path(output_dir) / "CRASHED.txt"
            crash_path.write_text(
                f"执行中断于: {datetime.now().isoformat()}\n"
                f"已完成 {prev_count} 条 agent 输出记录\n"
                f"错误: {e}\n",
                encoding="utf-8",
            )
            raise RuntimeError(f"图执行失败（已完成 {prev_count} 条记录已保存至 {output_dir}）: {e}") from e

        logger.info(f"图执行完成，共 {prev_count} 条记录")

        # ── 最终汇总 ──
        finalize_reports(output_dir, result, problem_name)

        final_paper = result.get("final_paper", "")
        return result, final_paper

    def stream(
        self,
        problem_path: str,
        output_name: str | None = None,
    ):
        """流式执行图，逐步产出状态更新。

        Args:
            problem_path: 题目 Markdown 文件路径。
            output_name: 输出文件夹名。

        Yields:
            每个节点的状态更新字典。
        """
        problem_file = Path(problem_path)
        if not problem_file.exists():
            raise FileNotFoundError(f"题目文件不存在: {problem_path}")

        initial_state = self.propagator.create_initial_state(
            problem_path=str(problem_file.resolve()),
            output_name=output_name,
        )

        graph_args = self.propagator.get_graph_args()
        stream_mode = graph_args.get("stream_mode", "values")

        logger.info(f"开始流式执行 (mode={stream_mode})...")

        for chunk in self.graph.stream(
            initial_state,
            stream_mode=stream_mode,
            config={"recursion_limit": self.propagator.max_recur_limit},
        ):
            yield chunk

        logger.info("流式执行完成")

    @property
    def selected_layers(self) -> list[int]:
        """返回当前配置中启用的层列表。"""
        return self.config.get("selected_layers", [1, 2, 3, 4])

    def get_graph_visualization(self) -> str:
        """获取图的 Mermaid 文本表示（用于调试）。

        Returns:
            Mermaid 格式的图描述文本。
        """
        try:
            return self.graph.get_graph().draw_mermaid()
        except Exception as e:
            logger.warning(f"无法生成图可视化: {e}")
            return ""
