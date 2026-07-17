"""Unified tools module — tool functions for MathModelingAgents.

These are the core tools that agents invoke via LangChain tool wrappers.
Raw functions are provided for direct use; create_langchain_tools() wraps
them as LangChain @tool instances.
"""

from __future__ import annotations

import json
import logging
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
    "os", "subprocess", "shutil", "socket", "requests", "urllib",
    "http", "ftplib", "telnetlib", "smtplib", "poplib", "imaplib",
    "multiprocessing", "threading", "ctypes", "signal",
    "pty", "pipes", "fcntl", "grp", "pwd", "resource",
    "syslog", "crypt", "spwd", "sched",
    "email", "nntplib", "xmlrpc",
    "asyncio", "concurrent",
    "webbrowser",
}


def run_code(
    code: str,
    timeout: int = 30,
    allowed_modules: list[str] | None = None,
) -> dict[str, Any]:
    """Execute Python code in a sandboxed subprocess and return results.

    The code runs in an isolated temporary directory. Dangerous modules
    (os, subprocess, socket, etc.) are blocked via import hook.

    Args:
        code: Python source code to execute.
        timeout: Maximum execution time in seconds.
        allowed_modules: Extra modules to allow (data-science stack is always
            allowed). Defaults to numpy/scipy/pandas/matplotlib/sklearn/etc.

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
        "# --- end preamble ---",
        "",
    ]
    sandboxed_code = "\n".join(preamble_lines) + "\n" + code

    with tempfile.TemporaryDirectory(prefix="mm_sandbox_") as tmpdir:
        script_path = Path(tmpdir) / "_exec.py"
        script_path.write_text(sandboxed_code, encoding="utf-8")

        start = time.perf_counter()
        try:
            proc = subprocess.run(
                ["python3", str(script_path)],
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=tmpdir,
                env={
                    "PATH": "/usr/bin:/usr/local/bin",
                    "PYTHONPATH": tmpdir,
                    "HOME": tmpdir,
                },
            )
            elapsed = time.perf_counter() - start

            stdout = proc.stdout[:10000] if proc.stdout else ""
            stderr = proc.stderr[:5000] if proc.stderr else ""
            exit_code = proc.returncode

            logger.info(
                "run_code: exit=%d, time=%.2fs, stdout=%d chars, stderr=%d chars",
                exit_code, elapsed, len(stdout), len(stderr),
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
    def read_problem_file_tool(path: str) -> str:
        """Read the problem description file at the given path."""
        return read_problem_file(path)

    @tool
    def run_code_tool(
        code: str,
        timeout: int = 30,
    ) -> dict:
        """Execute Python code in a sandboxed subprocess.

        Returns dict with stdout, stderr, exit_code, success, execution_time.
        """
        return run_code(code, timeout=timeout)

    @tool
    def web_search_tool(query: str) -> str:
        """Search the web (placeholder — not yet implemented)."""
        return web_search(query)

    @tool
    def save_result_tool(
        data: str,
        filename: str,
        output_dir: str = "./results",
    ) -> str:
        """Save result string to a file under output_dir."""
        return save_result(data, filename, output_dir=output_dir)

    return [
        read_problem_file_tool,
        run_code_tool,
        web_search_tool,
        save_result_tool,
    ]


# ═══════════════════════════════════════════════════════════════════════════════
# Module exports
# ═══════════════════════════════════════════════════════════════════════════════

__all__ = [
    "read_problem_file",
    "run_code",
    "web_search",
    "save_result",
    "create_langchain_tools",
    "DEFAULT_ALLOWED_MODULES",
]
