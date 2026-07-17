"""[bold cyan]MathModelingAgents[/] — 多智能体数学建模竞赛框架

Usage:
    python main.py <题目文件路径> [--output <输出名>] [--sensitivity]
"""

import sys
import argparse
import re
import subprocess
import tempfile
from pathlib import Path

from mathmodelingagents.default_config import DEFAULT_CONFIG
from mathmodelingagents.graph.modeling_graph import MathModelingGraph


def _verify_layer3_code(output_dir: str) -> str:
    """从 Layer 3 输出中提取代码块，实际执行并验证。

    从 Layer3_代码实现.md 中提取 Python 代码块，
    逐个执行并检查是否报错。生成验证报告。
    """
    layer3_path = Path(output_dir) / "Layer3_代码实现.md"
    if not layer3_path.exists():
        return "Layer3_代码实现.md 不存在，跳过代码验证"

    content = layer3_path.read_text(encoding="utf-8")
    # 提取所有 Python 代码块
    code_blocks = re.findall(r"```python\n(.*?)```", content, re.DOTALL)
    if not code_blocks:
        return "未找到 Python 代码块"

    report_lines = ["## 代码验证报告", f"找到 {len(code_blocks)} 个 Python 代码块\n"]
    passed = 0
    failed = 0

    for i, block in enumerate(code_blocks):
        # 跳过空代码块
        if not block.strip():
            continue
        # 写入临时文件执行
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".py", delete=False, encoding="utf-8"
        ) as f:
            f.write(block)
            tmp_path = f.name

        try:
            result = subprocess.run(
                ["python3", tmp_path],
                capture_output=True, text=True, timeout=60,
                cwd=str(Path(output_dir).parent),
            )
            if result.returncode == 0:
                passed += 1
                stdout_preview = result.stdout.strip()[:200]
                report_lines.append(
                    f"- 代码块 {i+1}: ✅ 通过 (stdout: {stdout_preview})"
                )
            else:
                failed += 1
                stderr_preview = result.stderr.strip()[:200]
                report_lines.append(
                    f"- 代码块 {i+1}: ❌ 失败 (exit={result.returncode})\n"
                    f"  stderr: {stderr_preview}"
                )
        except subprocess.TimeoutExpired:
            failed += 1
            report_lines.append(f"- 代码块 {i+1}: ❌ 超时 (60s)")
        except Exception as e:
            failed += 1
            report_lines.append(f"- 代码块 {i+1}: ❌ 执行异常: {e}")
        finally:
            Path(tmp_path).unlink(missing_ok=True)

    summary = (
        f"\n### 结果: {passed} 通过, {failed} 失败\n"
        f"{'⚠️ 代码验证失败，Layer 3 输出不可信' if failed > 0 else '✅ 全部代码通过验证'}"
    )
    report_lines.append(summary)

    report = "\n".join(report_lines)
    # 写入验证报告
    verify_path = Path(output_dir) / "CODE_VERIFICATION.md"
    verify_path.write_text(report, encoding="utf-8")
    return report


def main():
    parser = argparse.ArgumentParser(
        description="MathModelingAgents — 多智能体数学建模竞赛框架"
    )
    parser.add_argument(
        "problem_path",
        help="题目 Markdown 文件路径",
    )
    parser.add_argument(
        "--output", "-o",
        default=None,
        help="输出文件夹名（默认根据题目文件名自动生成）",
    )
    parser.add_argument(
        "--sensitivity", "-s",
        action="store_true",
        help="启用 Layer 5 敏感性分析",
    )
    parser.add_argument(
        "--max-rounds", "-r",
        type=int,
        default=10,
        help="每层最大辩论轮次 (默认: 10)",
    )
    parser.add_argument(
        "--provider", "-p",
        default=None,
        help="LLM provider (opencode/deepseek)",
    )
    parser.add_argument(
        "--start-layer",
        type=int,
        default=1,
        choices=[1, 2, 3, 4, 5],
        help="从指定层开始执行，跳过前面的层（默认: 1）",
    )

    args = parser.parse_args()

    # 验证输入文件
    problem_path = Path(args.problem_path)
    if not problem_path.exists():
        print(f"错误: 文件不存在: {args.problem_path}")
        sys.exit(1)

    # 配置
    config = DEFAULT_CONFIG.copy()
    if args.provider:
        config["llm_provider"] = args.provider
    config["max_debate_rounds"] = args.max_rounds
    config["enable_sensitivity"] = args.sensitivity
    config["selected_layers"] = list(range(args.start_layer, 5))

    # 输出名
    output_name = args.output or problem_path.stem

    print(f"""
╔══════════════════════════════════════════════╗
║       MathModelingAgents v0.1.0               ║
╠══════════════════════════════════════════════╣
║  Provider:  {config['llm_provider']:<34}║
║  Problem:   {problem_path.name:<34}║
║  Output:    {output_name:<34}║
║  Sensitivity: {'Yes' if config['enable_sensitivity'] else 'No':<33}║
║  Max Rounds: {config['max_debate_rounds']:<33}║
╚══════════════════════════════════════════════╝
""")

    # 初始化并运行
    mm = MathModelingGraph(config=config, debug=True)

    print("[Layer 0] 开始分析问题...")
    state, final_paper = mm.propagate(
        problem_path=str(problem_path),
        output_name=output_name,
    )

    print(f"\\n✅ 完成！论文已输出到: {config.get('output_dir')}")

    # 代码验证：实际执行 Layer 3 代码块
    output_dir = config.get("output_dir", "")
    if output_dir:
        print("\n[验证] 执行 Layer 3 代码验证...")
        verify_report = _verify_layer3_code(output_dir)
        print(verify_report)

    return 0


if __name__ == "__main__":
    sys.exit(main())
