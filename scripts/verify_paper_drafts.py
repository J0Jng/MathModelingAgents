"""验收脚本：PaperAgent 草稿下沉 + final_paper 去污染。

覆盖并断言：
  1. create_paper_agent_tools 的 write_file 一律重定向到 drafts/（扁平化、防 .. 逃逸）；
  2. 同工具 read_file 在 basename 落盘到 drafts/ 时仍能读回（drafts 回退）；
  3. _paper_read_disk 改为扫描 drafts/*.md 并按大小降序取第一份含 ## 摘要 的草稿；
  4. _build_final_paper 剥离自检清单序言（兜底 REVISE 耗尽路径）。

运行：.venv/Scripts/python.exe scripts/verify_paper_drafts.py
（先 export PYTHONPATH= 清除外部 PYTHONPATH 干扰）
"""

import os
import sys
import tempfile
from pathlib import Path

# 保证能从仓库根导入 mathmodelingagents（无论从哪里调用本脚本）
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.pop("PYTHONPATH", None)

from mathmodelingagents.tools import create_paper_agent_tools  # noqa: E402
from mathmodelingagents.agents import _paper_read_disk  # noqa: E402
from mathmodelingagents.reporting import _build_final_paper  # noqa: E402


def main() -> int:
    tmp_output_dir = tempfile.mkdtemp(prefix="verify_paper_drafts_")
    tmp = Path(tmp_output_dir)

    tools = {t.name: t for t in create_paper_agent_tools(tmp_output_dir)}
    write_file = tools["write_file_tool"]
    read_file = tools["read_file_tool"]

    # ── 1. write_file 重定向 drafts/ + 防 .. 逃逸 ──
    content = "这是论文正文草稿内容。"
    w1 = write_file.invoke({"content": content, "path": "paper.md"})
    drafts_paper = tmp / "drafts" / "paper.md"
    root_paper = tmp / "paper.md"
    print(f"[1] write_file(path=paper.md) -> {w1}")
    print(f"    drafts/paper.md 存在: {drafts_paper.exists()}, 根目录 paper.md 存在: {root_paper.exists()}")
    assert drafts_paper.exists(), "写 paper.md 应落到 drafts/paper.md"
    assert not root_paper.exists(), "根目录不应出现 paper.md"

    escape_val = "逃逸探测内容"
    write_file.invoke({"content": escape_val, "path": "../escape.md"})
    escaped = tmp / "drafts" / "escape.md"
    root_escaped = tmp / "escape.md"
    print(f"    write_file(path=../escape.md) -> drafts/escape.md 存在: {escaped.exists()}, "
          f"根目录 escape.md 存在: {root_escaped.exists()}")
    assert escaped.exists(), "../escape.md 应被扁平化到 drafts/escape.md"
    assert not root_escaped.exists(), "不应发生 .. 目录逃逸"

    # ── 2. read_file 走 drafts 回退 ──
    read_back = read_file.invoke({"path": "paper.md"})
    print(f"[2] read_file(path=paper.md) 读回 {len(read_back)} 字符")
    assert read_back == content, "read_file 应经 drafts 回退读回已写入内容"

    # ── 3. _paper_read_disk 扫描 drafts/ 并按大小降序取最长 ──
    drafts_dir = tmp / "drafts"
    drafts_dir.mkdir(parents=True, exist_ok=True)
    short_body = "# 短稿\n\n## 摘要\nSHORT_MARKER_短稿"
    long_tail = "LONG_MARKER_长正文"
    long_body = "# 长稿\n\n## 摘要\n" + (long_tail * 300)
    (drafts_dir / "paper_full.md").write_text(short_body, encoding="utf-8")
    (drafts_dir / "paper.md").write_text(long_body, encoding="utf-8")
    final_output = "自检清单\n## SELF_CHECK_PASSED\n"
    picked = _paper_read_disk(tmp_output_dir, final_output, [])
    print(f"[3] _paper_read_disk 返回 {len(picked)} 字符；含长稿正文: {long_body in picked}, "
          f"含短稿正文: {short_body in picked}")
    assert long_body in picked, "应丢弃较短草稿、取最长的含 ## 摘要 草稿"
    assert short_body not in picked, "短稿不应进入返回值"

    # ── 4. _build_final_paper 剥离自检清单序言 ──
    dirty_raw = "全稿自审…✅…\n## SELF_CHECK_PASSED\n\n# 论文标题\n\n## 摘要\n…正文…"
    built = _build_final_paper({"final_paper": dirty_raw})
    body = built.split("---\n", 1)[1]
    print(f"[4] _build_final_paper 正文起始: {body.strip()[:20]!r}")
    print(f"    保留 SELF_CHECK_PASSED: {'SELF_CHECK_PASSED' in built}, "
          f"保留 全稿自审: {'全稿自审' in built}")
    assert body.strip().startswith("# 论文标题"), "剥离后正文应以论文标题 # 论文标题 开头"
    assert "SELF_CHECK_PASSED" not in built, "最终论文不应残留 SELF_CHECK_PASSED"
    assert "全稿自审" not in built, "最终论文不应残留自检清单序言"

    # 无主标题：正文直接以 ## 摘要 开头（prompt 规定的标准输出格式），不得截断成 "# 摘要"
    raw_no_title = (
        "全稿自审…✅ 检查项通过\n\n## SELF_CHECK_PASSED\n\n"
        "## 摘要\n\n这是论文摘要正文\n\n## 1. 问题重述\n\n正文……\n"
    )
    final_no_title = _build_final_paper({"final_paper": raw_no_title}, "")
    body_no_title = final_no_title.split("---\n", 1)[1]
    first_line = body_no_title.strip().split("\n", 1)[0].strip()
    print(f"[4b] 无主标题正文首行: {first_line!r}")
    assert first_line == "## 摘要", "无主标题场景正文首行应为 ## 摘要"
    assert body_no_title.strip().startswith("## 摘要"), "无主标题场景正文应以 ## 摘要 开头"
    assert not body_no_title.strip().startswith("# 摘要"), "无主标题场景正文不应被截断成 # 摘要"
    assert "SELF_CHECK_PASSED" not in final_no_title, "无主标题场景最终论文不应残留 SELF_CHECK_PASSED"
    assert "全稿自审" not in final_no_title, "无主标题场景最终论文不应残留全稿自审"

    print()
    print("✅ 验收通过：write_file 下沉 drafts/ 且防逃逸、read_file 回退读回、"
          "_paper_read_disk 取最长草稿、_build_final_paper 剥离自检清单序言")
    return 0


if __name__ == "__main__":
    sys.exit(main())