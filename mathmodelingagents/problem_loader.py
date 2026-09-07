"""题目文档加载与附件剥离。

把「数据堆」式题目 md（附件表格被整体转成 markdown 表格塞进正文）切分为
正文段 + 附件表格，附件表格落盘为文件、仅以摘要形式进入 LLM prompt。
全量数据可达性由工具（read_file / run_code）保证。

背景：B题-全部转换合并.md 总 772,484 字符中 4 个附件表格占 96%，
导致 L2 输入膨胀到 ~1.25M 字符、超出模型上下文硬上限。
剥离后上下文可缩到 ~4K 字符。
"""

import re
from pathlib import Path

# 分隔符形如：<!-- ========== 附件1.xlsx ========== -->
SPLIT_MARKER_RE = re.compile(r"<!-- ========== (.+?) ========== -->")

# Windows 文件名非法字符
_ILLEGAL_FILENAME_CHARS = re.compile(r'[\\/:*?"<>|]')


def _sanitize_filename(name: str) -> str:
    """替换文件名中的非法字符为 _（附件名含 .xlsx 与中文）。"""
    return _ILLEGAL_FILENAME_CHARS.sub("_", name)


def split_problem_document(md_text: str) -> tuple[str, list[tuple[str, str]]]:
    """把题 md 切成 (正文段 body, [(附件名, 附件表格md), ...])。body 为第一个分隔符之前的全部内容。

    例外：当文档以分隔符开头（首个分段是源文档正文标记，如
    「B题.pdf（题目正文）」）时，首个分段归入正文段——否则正文段为空、
    题目文字会被当成附件剥离。
    """
    matches = list(SPLIT_MARKER_RE.finditer(md_text))
    if not matches:
        return md_text, []

    first = matches[0]
    if md_text[:first.start()].strip() == "":
        # 文档以分隔符开头：首个分段是正文段
        body = md_text[first.end():matches[1].start()] if len(matches) > 1 else md_text[first.end():]
        sections = matches[1:]
    else:
        body = md_text[:first.start()]
        sections = matches

    attachments: list[tuple[str, str]] = []
    for i, m in enumerate(sections):
        end = sections[i + 1].start() if i + 1 < len(sections) else len(md_text)
        attachments.append((m.group(1).strip(), md_text[m.end():end]))
    return body, attachments


def _parse_table_rows(attachment_md: str) -> tuple[list[str], list[str]]:
    """从附件 md 中提取 (表头单元格列表, 数据行原文列表)。

    表格行 = 以 | 开头的行；首个表格行视为表头，形如 | --- | --- | 的
    分隔行不计入数据行。
    """
    rows = [line.strip() for line in attachment_md.splitlines() if line.strip().startswith("|")]
    if not rows:
        return [], []

    def _is_separator(row: str) -> bool:
        cells = [c.strip() for c in row.strip("|").split("|")]
        return bool(cells) and all(re.fullmatch(r":?-{2,}:?", c) for c in cells)

    header_row = rows[0]
    data_rows = [r for r in rows[1:] if not _is_separator(r)]
    header_cells = [c.strip() for c in header_row.strip("|").split("|")]
    return header_cells, data_rows


def build_attachment_summary(
    attachments: list[tuple[str, str]],
    source_dir: str | None = None,
) -> str:
    """为每个附件生成摘要：附件名、表格行数、列说明（表头）、前 2 条数据行示例、可选源 xlsx 绝对路径。逐个附件用函数内统计，不要口算。

    每个附件同时给出计划落盘的相对路径 attachments/{i:02d}_{name}.md
    （output_dir 相对）——与 extract_attachments 的落盘命名约定一致。
    """
    blocks: list[str] = []
    for i, (name, content) in enumerate(attachments, 1):
        header_cells, data_rows = _parse_table_rows(content)
        rel_path = f"attachments/{i:02d}_{_sanitize_filename(name)}.md"
        lines = [
            f"### {name}",
            f"- 落盘路径: `{rel_path}`（output_dir 相对）",
            f"- 数据行数: {len(data_rows)}",
            f"- 列结构（表头）: {' | '.join(header_cells) if header_cells else '（无表格表头）'}",
        ]
        if data_rows:
            lines.append("- 前 2 条数据行示例:")
            for row in data_rows[:2]:
                lines.append(f"  - {row}")
        else:
            lines.append("- 前 2 条数据行示例:（无数据行）")
        if source_dir:
            source_xlsx = Path(source_dir) / "附件" / name
            if source_xlsx.exists():
                lines.append(f"- 源 xlsx: {source_xlsx.resolve()}")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def extract_attachments(
    md_text: str,
    output_dir: str,
    source_dir: str | None = None,
) -> tuple[str, str]:
    """高层入口：split → 把每个附件表格写成 output_dir/attachments/{i:02d}_{name}.md → 返回 (body, summary)。summary 含每个附件落盘后的相对路径。"""
    body, attachments = split_problem_document(md_text)

    att_dir = Path(output_dir) / "attachments"
    att_dir.mkdir(parents=True, exist_ok=True)
    for i, (name, content) in enumerate(attachments, 1):
        path = att_dir / f"{i:02d}_{_sanitize_filename(name)}.md"
        path.write_text(content, encoding="utf-8")

    summary = build_attachment_summary(attachments, source_dir=source_dir)
    return body, summary
