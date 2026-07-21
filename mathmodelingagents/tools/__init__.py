"""Unified tools module — tool functions for MathModelingAgents.

These are the core tools that agents invoke via LangChain tool wrappers.
Raw functions are provided for direct use; create_langchain_tools() wraps
them as LangChain @tool instances.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Whitelist of allowed modules for code execution
DEFAULT_ALLOWED_MODULES = [
    "numpy", "scipy", "sympy", "pandas",
    "matplotlib", "sklearn", "statsmodels", "seaborn",
]


# ═══════════════════════════════════════════════════════════════════════════════
# Tool: read_problem_file
# ═══════════════════════════════════════════════════════════════════════════════

def read_problem_file(path: str) -> str:
    """Read the problem description file or data file.

    Args:
        path: Absolute or relative path to the file.

    Returns:
        File contents as a string, or an error message.
    """
    p = Path(path).expanduser().resolve()
    if not p.exists():
        return f"[错误] 文件不存在: {p}"
    try:
        content = p.read_text(encoding="utf-8")
        logger.info("read_problem_file: read %d chars from %s", len(content), p)
        return content
    except UnicodeDecodeError:
        try:
            content = p.read_text(encoding="gbk")
            return content
        except Exception as e:
            return f"[错误] 编码失败: {e}"
    except Exception as e:
        logger.exception("read_problem_file failed for %s", p)
        return f"[错误] 读取失败: {e}"


# ═══════════════════════════════════════════════════════════════════════════════
# Tool: run_code
# ═══════════════════════════════════════════════════════════════════════════════

# Modules that are ALWAYS blocked (security risk even if pre-loaded)
_RISKY_MODULES = {
    # Network access — the primary security boundary
    "socket", "requests", "urllib", "urllib2", "http", "httplib",
    "ftplib", "telnetlib", "smtplib", "poplib", "imaplib",
    # Native code execution
    "ctypes",
}


def run_code(
    code: str,
    timeout: int = 30,
    allowed_modules: list[str] | None = None,
    cwd: str | None = None,
) -> dict[str, Any]:
    """Execute Python code in a sandboxed subprocess and return results.

    The code runs in an isolated temporary directory by default. If *cwd* is
    provided the script executes there instead, so files the code writes
    (e.g. matplotlib charts) persist after the call.

    Dangerous modules (os, subprocess, socket, etc.) are blocked via import
    hook regardless of working directory.

    Args:
        code: Python source code to execute.
        timeout: Maximum execution time in seconds.
        allowed_modules: Extra modules to allow (data-science stack is always
            allowed). Defaults to numpy/scipy/pandas/matplotlib/sklearn/etc.
        cwd: Optional working directory. When given, the script runs in this
            directory and any files it creates survive the call.

    Returns:
        dict with keys:
            stdout: Captured standard output (truncated at 10000 chars).
            stderr: Captured standard error (truncated at 5000 chars).
            exit_code: Process exit code (0 = success).
            success: True if exit_code == 0.
            execution_time: Wall-clock time in seconds.
    """
    if allowed_modules is None:
        allowed_modules = DEFAULT_ALLOWED_MODULES

    # Build preamble that restricts imports via a blocklist approach.
    # We block dangerous modules; everything else (including Python internals
    # like _io, _abc, encodings) is allowed so the runtime works correctly.
    preamble_lines = [
        "import sys",
        "import builtins",
        "# --- code sandbox preamble ---",
        f"_blocked = {sorted(_RISKY_MODULES)!r}",
        "# Snapshot of modules that were loaded before user code runs",
        "_preloaded = set(sys.modules.keys())",
        "_original_import = __import__",
        "def _safe_import(name, *args, **kwargs):",
        "    _root = name.split('.')[0]",
        "    # ALWAYS block risky modules — even if preloaded by the runtime",
        "    if _root in _blocked:",
        f"        raise ImportError(f'Module {{name}} is blocked for security reasons')",
        "    # Allow re-imports of safe modules that were already loaded at sandbox start",
        "    if name in _preloaded:",
        "        return _original_import(name, *args, **kwargs)",
        "    return _original_import(name, *args, **kwargs)",
        "builtins.__import__ = _safe_import",
        "",
        "# --- matplotlib Chinese font auto-config ---",
        "_MM_CJK_FONT = None",
        "try:",
        "    import matplotlib",
        "    import matplotlib.font_manager as _fm",
        "    _cjk_keywords = ['hei', 'song', 'ming', 'kai', 'yuan', 'cjk', 'noto',",
        "                     'wenquan', 'yahei', 'simsun', 'fangsong', 'chinese',",
        "                     'han', 'jp', 'tc', 'sc', 'pming', 'lihei',",
        "                     'stheiti', 'stsong', 'stkaiti', 'stfangsong', 'microsoft yahei']",
        "    _cjk_fonts = [f for f in _fm.fontManager.ttflist",
        "                  if any(kw in f.name.lower() for kw in _cjk_keywords)]",
        "    if _cjk_fonts:",
        "        # Prefer most common Chinese fonts",
        "        _preferred = ['SimHei', 'Microsoft YaHei', 'STSong', 'KaiTi', 'FangSong',",
        "                      'Noto Sans CJK', 'WenQuanYi', 'AR PL UMing']",
        "        _chosen = None",
        "        for _pref in _preferred:",
        "            for _f in _cjk_fonts:",
        "                if _pref.lower() in _f.name.lower():",
        "                    _chosen = _f",
        "                    break",
        "            if _chosen:",
        "                break",
        "        if not _chosen:",
        "            _chosen = _cjk_fonts[0]",
        "        _MM_CJK_FONT = _chosen.name",
        "        matplotlib.rcParams['font.sans-serif'] = [_MM_CJK_FONT, 'DejaVu Sans', 'Arial']",
        "        matplotlib.rcParams['axes.unicode_minus'] = False",
        "        print(f'[sandbox] 中文字体已激活: {_MM_CJK_FONT}', flush=True)",
        "    else:",
        "        print('[sandbox] ⚠️ 未检测到中文字体，图表中文可能显示为方块', flush=True)",
        "except Exception as _e:",
        "    print(f'[sandbox] ⚠️ 字体配置异常: {_e}', flush=True)",
        "# --- end preamble ---",
        "",
    ]
    sandboxed_code = "\n".join(preamble_lines) + "\n" + code

    # ── persistent vs temp working directory ──
    if cwd:
        work_dir = Path(cwd).resolve()
        work_dir.mkdir(parents=True, exist_ok=True)
        _use_temp = False
    else:
        work_dir = None  # set inside context manager
        _use_temp = True

    if _use_temp:
        with tempfile.TemporaryDirectory(prefix="mm_sandbox_") as tmpdir:
            return _exec_script(sandboxed_code, tmpdir, timeout)
    else:
        return _exec_script(sandboxed_code, str(work_dir), timeout)


def _exec_script(sandboxed_code: str, work_dir: str, timeout: int) -> dict[str, Any]:
    """Internal: write script to *work_dir* and execute it."""
    import sys as _sys
    script_path = Path(work_dir) / "_exec.py"
    script_path.write_text(sandboxed_code, encoding="utf-8")

    # Use sys.executable (current Python) rather than hardcoded 'python3'
    # 'python3' doesn't exist on Windows
    python_exe = _sys.executable

    start = time.perf_counter()
    try:
        # Inherit parent environment, only override working-directory vars
        env = os.environ.copy()
        env["PYTHONPATH"] = work_dir
        env["HOME"] = work_dir

        proc = subprocess.run(
            [python_exe, str(script_path)],
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=work_dir,
            env=env,
        )
        elapsed = time.perf_counter() - start

        stdout = proc.stdout[:10000] if proc.stdout else ""
        stderr = proc.stderr[:5000] if proc.stderr else ""
        exit_code = proc.returncode

        logger.info(
            "run_code: exit=%d, time=%.2fs, stdout=%d chars, stderr=%d chars, cwd=%s",
            exit_code, elapsed, len(stdout), len(stderr), work_dir,
        )

        return {
            "stdout": stdout,
            "stderr": stderr,
            "exit_code": exit_code,
            "success": exit_code == 0,
            "execution_time": round(elapsed, 3),
        }

    except subprocess.TimeoutExpired:
        elapsed = time.perf_counter() - start
        logger.warning("run_code: timed out after %.1fs", timeout)
        return {
            "stdout": "",
            "stderr": f"[超时] 代码执行超过 {timeout} 秒",
            "exit_code": -1,
            "success": False,
            "execution_time": round(elapsed, 3),
        }
    except Exception as e:
        elapsed = time.perf_counter() - start
        logger.exception("run_code: unexpected error")
        return {
            "stdout": "",
            "stderr": f"[错误] 执行失败: {e}",
            "exit_code": -1,
            "success": False,
            "execution_time": round(elapsed, 3),
        }


# ═══════════════════════════════════════════════════════════════════════════════
# Tool: web_search
# ═══════════════════════════════════════════════════════════════════════════════

def web_search(query: str) -> str:
    """Search the web (placeholder — not yet implemented).

    Args:
        query: Search query string.

    Returns:
        A placeholder message.
    """
    logger.info("web_search called with query: %s", query)
    return (
        "web_search 尚未实现。\n"
        f"查询: {query}\n"
        "当此功能可用时，将返回相关网页摘要和链接。"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# Tool: save_result
# ═══════════════════════════════════════════════════════════════════════════════

def save_result(
    data: str,
    filename: str,
    output_dir: str = "./results",
) -> str:
    """Save result string to a file.

    Args:
        data: The string content to save.
        filename: Destination filename (relative to output_dir).
        output_dir: Base directory for output files. Created if needed.

    Returns:
        Absolute path to the saved file, or an error message.
    """
    out = Path(output_dir).expanduser().resolve()
    out.mkdir(parents=True, exist_ok=True)

    filepath = out / filename
    try:
        filepath.write_text(data, encoding="utf-8")
        logger.info("save_result: wrote %d chars to %s", len(data), filepath)
        return str(filepath)
    except Exception as e:
        logger.exception("save_result failed for %s", filepath)
        return f"[错误] 保存失败: {e}"


# ═══════════════════════════════════════════════════════════════════════════════
# LangChain tool wrappers
# ═══════════════════════════════════════════════════════════════════════════════

def create_langchain_tools() -> list:
    """Create LangChain @tool wrappers for all tool functions.

    Returns:
        List of LangChain BaseTool instances ready to bind to agents.

    Raises:
        ImportError: If langchain_core is not installed.
    """
    try:
        from langchain_core.tools import tool
    except ImportError:
        raise ImportError(
            "langchain_core is required to create LangChain tools. "
            "Install with: pip install langchain-core"
        )

    @tool
    def read_file_tool(path: str) -> str:
        """Read a file at the given path. Returns its contents as a string."""
        return read_problem_file(path)

    @tool
    def run_code_tool(
        code: str,
        timeout: int = 30,
    ) -> str:
        """Execute Python code in a sandboxed subprocess.

        Returns a JSON string with keys: stdout, stderr, exit_code, success, execution_time.
        """
        result = run_code(code, timeout=timeout)
        return json.dumps(result, ensure_ascii=False)

    @tool
    def web_search_tool(query: str) -> str:
        """Search the web (placeholder — not yet implemented)."""
        return web_search(query)

    @tool
    def write_file_tool(
        content: str,
        path: str,
    ) -> str:
        """Write content to a file at the given path.

        Parent directories are created if needed. Returns the absolute path
        on success or an error message on failure.
        """
        p = Path(path).expanduser().resolve()
        p.parent.mkdir(parents=True, exist_ok=True)
        try:
            p.write_text(content, encoding="utf-8")
            logger.info("write_file_tool: wrote %d chars to %s", len(content), p)
            return f"文件已写入: {p} ({len(content)} 字符)"
        except Exception as e:
            logger.exception("write_file_tool failed for %s", p)
            return f"[错误] 写入失败: {e}"

    return [
        read_file_tool,
        run_code_tool,
        web_search_tool,
        write_file_tool,
    ]


def create_coding_agent_tools(output_dir: str) -> list:
    """Create LangChain tools scoped to *output_dir* (for CodingAgent).

    These tools are identical to create_langchain_tools() except that
    run_code_tool runs in a persistent working directory so files like
    matplotlib charts survive across calls.

    Args:
        output_dir: Root output directory. Code runs in output_dir/code/,
            results are saved under output_dir/results/.

    Returns:
        List of LangChain BaseTool instances.
    """
    try:
        from langchain_core.tools import tool
    except ImportError:
        raise ImportError(
            "langchain_core is required to create LangChain tools. "
            "Install with: pip install langchain-core"
        )

    work_dir = str(Path(output_dir).resolve() / "code")
    results_dir = str(Path(output_dir).resolve() / "results")
    Path(work_dir).mkdir(parents=True, exist_ok=True)
    Path(results_dir).mkdir(parents=True, exist_ok=True)

    @tool
    def read_file_tool(path: str) -> str:
        """Read a file at the given path. Returns its contents as a string.

        Use this to read data files, Layer 2 model output, or code you've
        previously saved.
        """
        return read_problem_file(path)

    @tool
    def run_code_tool(
        code: str,
        timeout: int = 60,
    ) -> str:
        """Execute Python code in a sandbox and return a JSON result.

        The code runs in a persistent working directory — files created by
        matplotlib (plt.savefig) or other libraries survive and can be used
        later. To save charts: plt.savefig('../results/chart_name.png').

        Returns JSON with keys: stdout, stderr, exit_code, success, execution_time.
        A non-zero exit_code means the code failed — read stderr to debug.
        """
        result = run_code(code, timeout=timeout, cwd=work_dir)
        return json.dumps(result, ensure_ascii=False)

    @tool
    def write_file_tool(
        content: str,
        path: str,
    ) -> str:
        """Write content to a file. Parent directories are created as needed.

        Use this to save final Python scripts, JSON results, or other
        artifacts you want to keep.
        """
        p = Path(path).expanduser().resolve()
        p.parent.mkdir(parents=True, exist_ok=True)
        try:
            p.write_text(content, encoding="utf-8")
            logger.info("write_file_tool: wrote %d chars to %s", len(content), p)
            return f"文件已写入: {p} ({len(content)} 字符)"
        except Exception as e:
            logger.exception("write_file_tool failed for %s", p)
            return f"[错误] 写入失败: {e}"

    @tool
    def list_dir_tool(
        path: str = ".",
    ) -> str:
        """List files in a directory. Returns a newline-separated list.

        Use this to check what files exist in the code or results directories
        before reading them or to verify files were created.
        """
        p = Path(path).expanduser().resolve()
        if not p.exists():
            return f"[错误] 目录不存在: {p}"
        if not p.is_dir():
            return f"[错误] 不是目录: {p}"
        try:
            entries = []
            for entry in sorted(p.iterdir()):
                suffix = "/" if entry.is_dir() else ""
                size = f" ({entry.stat().st_size:,} bytes)" if entry.is_file() else ""
                entries.append(f"  {entry.name}{suffix}{size}")
            return "\n".join(entries) if entries else "(空目录)"
        except Exception as e:
            return f"[错误] 列出目录失败: {e}"

    return [read_file_tool, run_code_tool, write_file_tool, list_dir_tool]


def create_paper_agent_tools(output_dir: str) -> list:
    """Create read-only LangChain tools for the PaperAgent (no code execution).

    PaperAgent needs read_file (verify numbers against source data),
    list_dir (confirm chart files exist), and write_file (save drafts).

    Args:
        output_dir: Root output directory for file operations.

    Returns:
        List of LangChain BaseTool instances.
    """
    try:
        from langchain_core.tools import tool
    except ImportError:
        raise ImportError(
            "langchain_core is required to create LangChain tools. "
            "Install with: pip install langchain-core"
        )

    @tool
    def read_file_tool(path: str) -> str:
        """Read a file at the given path. Returns its contents as a string.

        Use this to read Layer 1/2/3 output files, data files, or your own
        previous drafts to verify numbers, formulas, and facts.
        """
        return read_problem_file(path)

    @tool
    def list_dir_tool(
        path: str = ".",
    ) -> str:
        """List files in a directory. Returns a newline-separated list.

        Use this to check what chart files exist in results/ before citing
        them in the paper.
        """
        p = Path(path).expanduser().resolve()
        if not p.exists():
            return f"[错误] 目录不存在: {p}"
        if not p.is_dir():
            return f"[错误] 不是目录: {p}"
        try:
            entries = []
            for entry in sorted(p.iterdir()):
                suffix = "/" if entry.is_dir() else ""
                size = f" ({entry.stat().st_size:,} bytes)" if entry.is_file() else ""
                entries.append(f"  {entry.name}{suffix}{size}")
            return "\n".join(entries) if entries else "(空目录)"
        except Exception as e:
            return f"[错误] 列出目录失败: {e}"

    @tool
    def write_file_tool(
        content: str,
        path: str,
    ) -> str:
        """Write content to a file. Parent directories are created as needed.

        Use this to save the final paper or intermediate drafts.
        """
        p = Path(path).expanduser().resolve()
        p.parent.mkdir(parents=True, exist_ok=True)
        try:
            p.write_text(content, encoding="utf-8")
            logger.info("write_file_tool: wrote %d chars to %s", len(content), p)
            return f"文件已写入: {p} ({len(content)} 字符)"
        except Exception as e:
            logger.exception("write_file_tool failed for %s", p)
            return f"[错误] 写入失败: {e}"

    return [read_file_tool, list_dir_tool, write_file_tool]


# ═══════════════════════════════════════════════════════════════════════════════
# Module exports
# ═══════════════════════════════════════════════════════════════════════════════

__all__ = [
    "read_problem_file",
    "run_code",
    "web_search",
    "save_result",
    "create_langchain_tools",
    "create_coding_agent_tools",
    "create_paper_agent_tools",
    "DEFAULT_ALLOWED_MODULES",
]
