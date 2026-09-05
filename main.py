"""MathModelingAgents — 多智能体数学建模竞赛框架

Usage:
    python main.py <题目文件路径> [--output <输出名>] [--sensitivity [auto|always|never]] [--max-rounds N] [--provider P] [--from-layer1 DIR]
"""

import sys
import argparse
import re
import subprocess
import tempfile
from pathlib import Path

from mathmodelingagents.default_config import DEFAULT_CONFIG
from mathmodelingagents.graph.modeling_graph import MathModelingGraph
from mathmodelingagents.llm_clients import get_layer_model
from mathmodelingagents.tools import build_preamble


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
                # 以与沙盒一致的执行环境复现：拼上 build_preamble()，
                # 使 agent 依赖沙盒内部名字（如 _original_import）或
                # 自行配置 matplotlib 后端/中文字体的脚本在验证阶段也能通过，
                # 避免“沙盒里能跑、验证裸进程挂掉”的假阴性。
                # preamble 是独立子进程，_exec 模块命名空间注入后用户源码可见。
                script_src = f.read_text(encoding='utf-8')
                if 'matplotlib' in script_src:
                    # 显式置 Agg 后端，避免 GUI 后端在无显示环境（验证子进程）触发
                    script_src = "import matplotlib as _mpl\n_mpl.use('Agg', force=True)\n" + script_src
                # 不拼 name/main 判断：preamble + 源码整体作为脚本顶层执行，
                # 若源码自带 if __name__=='__main__' guard，顶层执行时恒为真分支。
                sandboxed_src = build_preamble() + '\n' + script_src
                result = subprocess.run(
                    [sys.executable, '-c', sandboxed_src],
                    capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=120,
                    cwd=str(code_dir),
                )
                if result.returncode == 0:
                    passed += 1
                    stdout_preview = result.stdout.strip()[:300]
                    line = f'- {f.name}: \u2705 \u901a\u8fc7'
                    if stdout_preview:
                        line += f'\n  stdout: {stdout_preview}'
                    report_lines.append(line)
                else:
                    failed += 1
                    stderr_preview = result.stderr.strip()[:300]
                    report_lines.append(
                        f'- {f.name}: \u274c \u5931\u8d25 (exit={result.returncode})\n'
                        f'  stderr: {stderr_preview}'
                    )
            except subprocess.TimeoutExpired:
                failed += 1
                report_lines.append(f'- {f.name}: \u274c \u8d85\u65f6 (120s)')
            except Exception as e:
                failed += 1
                report_lines.append(f'- {f.name}: \u274c \u6267\u884c\u5f02\u5e38: {e}')

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
                    # 与路径 A/沙盒一致：拼上 build_preamble()，避免 agent 依赖沙盒内部名字
                    # （如 _original_import）时在裸进程验证阶段假阴性。哑变量置 Agg，防无显示环境触发 GUI。
                    if "matplotlib" in combined_code:
                        combined_code = "import matplotlib as _mpl\n_mpl.use('Agg', force=True)\n" + combined_code
                    f_tmp.write(build_preamble() + "\n" + combined_code)
                    tmp_path = f_tmp.name

        try:
            result = subprocess.run(
                [sys.executable, tmp_path],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
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


def build_config_from_args(args) -> dict:
    """CLI 参数 → config 组装（含交互式模型选择）。可测：交互函数经 cli.model_picker 注入。"""
    config = DEFAULT_CONFIG.copy()
    if args.provider:
        config["llm_provider"] = args.provider
    config["max_debate_rounds"] = args.max_rounds
    config["max_modeling_rounds"] = args.max_rounds
    config["max_revision_rounds"] = args.max_rounds
    if args.sensitivity:
        config["sensitivity_mode"] = args.sensitivity

    # ── CLI 交互式模型（agent）选择 ──
    try:
        from cli.model_picker import prompt_model_selection
        selection = prompt_model_selection(config)
        if selection:
            from cli.model_picker import _apply_model_selection
            config = _apply_model_selection(config, config["llm_provider"], selection)
    except Exception as exc:  # 交互层任何异常都不应阻断建模
        import logging
        logging.getLogger("main").warning("交互模型选择失败，使用默认模型: %s", exc)

    # --from-layer1：从既有 Layer 1 输出恢复，只跑 L2→L3(+L5) 产出模型解释文档
    if args.from_layer1:
        config["selected_layers"] = [2, 3]
        config["explain_mode"] = True

    return config


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
        nargs="?",
        const="always",
        choices=["auto", "always", "never"],
        default=None,
        help="敏感性模式 (默认: auto，由 Layer 1 分析题目后决策；裸 -s 等价 always)",
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
        help="LLM provider (opencode/deepseek/volcengine/volcengine-plan)",
    )
    parser.add_argument(
        "--from-layer1",
        type=str,
        default=None,
        metavar="DIR",
        help="从已完成的 Layer 1 输出目录恢复，只跑 L2→L3(+L5) 并生成模型解释文档",
    )

    args = parser.parse_args()

    # 验证输入文件
    problem_path = Path(args.problem_path)
    if not problem_path.exists():
        print(f"错误: 文件不存在: {args.problem_path}")
        sys.exit(1)

    # 配置组装 + 交互式模型选择
    config = build_config_from_args(args)

    # 输出名
    output_name = args.output or problem_path.stem

    # --from-layer1：从既有 Layer 1 输出恢复，只跑 L2→L3(+L5) 产出模型解释文档
    recovered: dict | None = None
    if args.from_layer1:
        from mathmodelingagents.graph.recovery import load_layer1_state

        recovered = load_layer1_state(args.from_layer1)
        # 旧目录可能没有敏感性决策落盘：重新判定一次（内存使用，随流程传递）
        if "sensitivity_enabled" not in recovered:
            from mathmodelingagents.agents import _run_sensitivity_decision

            enabled, reason = _run_sensitivity_decision(config, recovered["problem_report"])
            recovered["sensitivity_enabled"] = enabled
            recovered["sensitivity_reason"] = reason
        output_name = args.output or f"{problem_path.stem}_explain"
        # 从已有 Layer 1 恢复：让流式层横幅从 L2 开始，不再打印「L1·问题分析 开始」
        recovered["current_layer"] = str(config["selected_layers"][0])

    from mathmodelingagents.default_config import resolve_sensitivity_mode
    sensitivity_mode = resolve_sensitivity_mode(config)

    # 模式行长标签：--from-layer1 时明确说明只跑 L2→L3(+L5)→解释
    if config.get("explain_mode"):
        mode_label = "恢复 (L1 已有 → L2·L3 → 解释)"
    else:
        mode_label = "完整流程 (L1→L4)"

    # Banner 逐层展示实际解析出的模型（get_layer_model 与运行期完全一致，
    # 含 layer_model_overrides / provider 级覆盖 / 别名映射）。
    # 角色名以各层节点的实际调用为准：L1/L2 agent+manager，L3 coder+manager，L4 writer+manager。
    # 实际执行哪些层由 config.selected_layers 控制，这里保守全部列出。
    _layer_roles = [
        ("L1 problem", "problem", ("agent", "manager")),
        ("L2 modeling", "modeling", ("agent", "manager")),
        ("L3 implementation", "implementation", ("coder", "manager")),
        ("L4 paper", "paper", ("writer", "manager")),
    ]
    _banner_lines = [
        "       MathModelingAgents v0.1.0",
        f"  Provider:  {config['llm_provider']}",
    ]
    for _label, _layer, _roles in _layer_roles:
        _models = " / ".join(
            f"{_role}={get_layer_model(config, _layer, _role)}" for _role in _roles
        )
        _banner_lines.append(f"  {_label}  {_models}")
    _banner_lines += [
        f"  Problem:   {problem_path.name}",
        f"  Output:    {output_name}",
        f"  Mode:      {mode_label}",
        f"  Sensitivity: {sensitivity_mode}",
        f"  Max Rounds: {config['max_debate_rounds']}",
    ]
    _width = max(len(_line) for _line in _banner_lines)
    _body = "".join(f"║{_line:<{_width}}║\n" for _line in _banner_lines)
    _sep = f"╠{'═' * _width}╣\n"
    _title, _rest = _body.split("\n", 1)
    print(f"""
╔{'═' * _width}╗
{_title}
{_sep}{_rest}╚{'═' * _width}╝
""")

    # 初始化并运行
    mm = MathModelingGraph(config=config, debug=True)

    print("[Layer 0] 开始分析问题...")
    if recovered is not None:
        mm.propagate(
            problem_path=str(problem_path),
            output_name=output_name,
            initial_state_overrides=recovered,
        )
        print(f"\n✅ 完成！模型解释文档已输出到: {config.get('output_dir')}")
    else:
        mm.propagate(
            problem_path=str(problem_path),
            output_name=output_name,
        )
        print(f"\n✅ 完成！论文已输出到: {config.get('output_dir')}")

    # 代码验证：实际执行 Layer 3 代码块
    output_dir = config.get("output_dir", "")
    if output_dir:
        print("\n[验证] 执行 Layer 3 代码验证...")
        verify_report = _verify_layer3_code(output_dir)
        print(verify_report)

    return 0


if __name__ == "__main__":
    sys.exit(main())
