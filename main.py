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
    """从 Layer 3 输出的 code/ 目录执行 Python 文件验证。

    优先执行 code/ 目录下的实际脚本（CodingAgent 通过 write_file 工具产出），
    以 main/solver 命名的文件优先。如果 code/ 目录不存在，回退到从
    Layer3_代码实现.md 中提取代码块拼接执行。
    """
    output_path = Path(output_dir)
    code_dir = output_path / "code"

    # ── 路径 A：执行 code/ 目录中的实际脚本 ──
    python_files: list[Path] = []
    if code_dir.is_dir():
        python_files = sorted(
            f for f in code_dir.iterdir()
            if f.suffix == ".py" and f.name != "_exec.py"
        )
        # 优先执行主求解器：文件名含 solver/main/run 的排前面
        def _priority(p: Path) -> int:
            name = p.stem.lower()
            if "main" in name or "run" in name:
                return 0
            if "solver" in name:
                return 1
            if "chart" in name or "plot" in name or "figure" in name:
                return 3
            return 2
        python_files.sort(key=_priority)

    if python_files:
        report_lines = [
            "## 代码验证报告",
            f"code/ 目录中找到 {len(python_files)} 个 Python 文件，按优先级执行\n",
        ]
        passed = 0
        failed = 0
        for f in python_files:
            try:
                result = subprocess.run(
                    [sys.executable, str(f)],
                    capture_output=True, text=True, timeout=120,
                    cwd=str(code_dir),
                )
                if result.returncode == 0:
                    passed += 1
                    stdout_preview = result.stdout.strip()[:300]
                    line = f"- {f.name}: ✅ 通过"
                    if stdout_preview:
                        line += f"\n  stdout: {stdout_preview}"
                    report_lines.append(line)
                else:
                    failed += 1
                    stderr_preview = result.stderr.strip()[:300]
                    report_lines.append(
                        f"- {f.name}: ❌ 失败 (exit={result.returncode})\n"
                        f"  stderr: {stderr_preview}"
                    )
            except subprocess.TimeoutExpired:
                failed += 1
                report_lines.append(f"- {f.name}: ❌ 超时 (120s)")
            except Exception as e:
                failed += 1
                report_lines.append(f"- {f.name}: ❌ 执行异常: {e}")

        total = passed + failed
        if failed == 0:
            summary = f"\n### 结果: ✅ {passed}/{total} 全部通过"
        else:
            summary = f"\n### 结果: {passed} 通过, {failed} 失败\n⚠️ 代码验证失败，Layer 3 输出不可信"
        report_lines.append(summary)
    else:
        # ── 路径 B：回退到从 Markdown 提取代码块拼接执行 ──
        layer3_md = output_path / "Layer3_代码实现.md"
        if not layer3_md.exists():
            return "Layer3_代码实现.md 不存在，且 code/ 目录无 Python 文件，跳过代码验证"

        content = layer3_md.read_text(encoding="utf-8")
        code_blocks = re.findall(r"```python\n(.*?)```", content, re.DOTALL)
        code_blocks = [b for b in code_blocks if b.strip()]
        if not code_blocks:
            return "未找到 Python 代码块，且 code/ 目录无 Python 文件"

        report_lines = [
            "## 代码验证报告",
            f"从 Layer3_代码实现.md 提取到 {len(code_blocks)} 个 Python 代码块，拼接为完整脚本执行\n",
        ]
        combined_code = "\n\n".join(code_blocks)

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".py", delete=False, encoding="utf-8"
        ) as f_tmp:
            f_tmp.write(combined_code)
            tmp_path = f_tmp.name

        try:
            result = subprocess.run(
                [sys.executable, tmp_path],
                capture_output=True, text=True, timeout=120,
                cwd=str(output_path.parent),
            )
            if result.returncode == 0:
                stdout_preview = result.stdout.strip()[:500]
                report_lines.append("### 结果: ✅ 整体执行通过")
                if stdout_preview:
                    report_lines.append(f"\nstdout:\n```\n{stdout_preview}\n```")
            else:
                stderr_preview = result.stderr.strip()[:800]
                report_lines.append(
                    f"### 结果: ❌ 执行失败 (exit={result.returncode})\n"
                    f"\nstderr:\n```\n{stderr_preview}\n```"
                )
        except subprocess.TimeoutExpired:
            report_lines.append("### 结果: ❌ 执行超时 (120s)")
        except Exception as e:
            report_lines.append(f"### 结果: ❌ 执行异常: {e}")
        finally:
            Path(tmp_path).unlink(missing_ok=True)

    report = "\n".join(report_lines)
    verify_path = output_path / "CODE_VERIFICATION.md"
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
