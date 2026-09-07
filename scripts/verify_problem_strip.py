"""验收脚本：真实题目附件剥离（Ticket A）。

读取真实题目 md（数据堆：附件表格占 96% 字符），调 extract_attachments
落到临时目录（不污染桌面），打印 body 字符数、attachment_summary 长度、
落盘附件文件清单，并断言剥离生效。

运行：.venv/Scripts/python.exe scripts/verify_problem_strip.py
（先 export PYTHONPATH= 清除外部 PYTHONPATH 干扰）
"""

import os
import sys
import tempfile
from pathlib import Path

# 保证能从仓库根导入 mathmodelingagents（无论从哪里调用本脚本）
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.pop("PYTHONPATH", None)

from mathmodelingagents.problem_loader import extract_attachments  # noqa: E402

PROBLEM_PATH = Path("C:/Users/joeji/Desktop/what/math.25b/B题-全部转换合并.md")


def main() -> int:
    if not PROBLEM_PATH.exists():
        print(f"❌ 题目文件不存在: {PROBLEM_PATH}")
        return 1

    md_text = PROBLEM_PATH.read_text(encoding="utf-8")
    original_chars = len(md_text)

    tmp_dir = tempfile.mkdtemp(prefix="verify_problem_strip_")
    body, summary = extract_attachments(md_text, tmp_dir, source_dir=str(PROBLEM_PATH.parent))

    att_dir = Path(tmp_dir) / "attachments"
    att_files = sorted(att_dir.iterdir()) if att_dir.exists() else []

    body_chars = len(body)
    summary_chars = len(summary)
    shrink_pct = 100 * (1 - body_chars / original_chars) if original_chars else 0.0

    print(f"题目文件: {PROBLEM_PATH}")
    print(f"原始字符数: {original_chars}")
    print(f"剥离后正文段字符数: {body_chars}")
    print(f"attachment_summary 长度: {summary_chars}")
    print(f"上下文缩减: {shrink_pct:.1f}%")
    print(f"落盘附件文件（{len(att_files)} 个，临时目录 {tmp_dir}）:")
    for f in att_files:
        print(f"  - {f.name} ({f.stat().st_size} bytes)")
    print()
    print("--- attachment_summary ---")
    print(summary)
    print("--- 正文段（前 500 字符）---")
    print(body[:500])

    # ── 断言（阈值来自验收标准，其余数值全部来自实际执行）──
    assert body_chars < 15000, f"正文段仍过长: {body_chars} 字符（要求 < 15000）"
    assert len(att_files) == 4, f"落盘附件文件数应为 4，实际 {len(att_files)}"

    print()
    print("✅ 验收通过：正文段 < 15000 字符，且落盘 4 个附件文件")
    return 0


if __name__ == "__main__":
    sys.exit(main())
