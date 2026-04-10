"""Async subprocess wrapper for invoking Codex CLI in headless mode."""

from __future__ import annotations

import asyncio
import shutil
from collections.abc import Callable
from dataclasses import dataclass, field


class CodexNotFoundError(RuntimeError):
    """Raised when the Codex CLI binary is not found on PATH."""


class CodexTimeoutError(RuntimeError):
    """Raised when the Codex subprocess exceeds its timeout."""


@dataclass
class CodexResult:
    """Captured output from a Codex CLI invocation."""

    stdout: str = ""
    stderr: str = ""
    exit_code: int = 0


async def run_codex(
    prompt: str,
    *,
    working_dir: str | None = None,
    cancel_event: asyncio.Event | None = None,
    on_output: Callable[[str], None] | None = None,
    timeout: int = 300,
) -> CodexResult:
    """Run Codex CLI as an async subprocess and stream its output.

    Args:
        prompt: The prompt string to pass to Codex.
        working_dir: Optional working directory for the subprocess.
        cancel_event: When set, the subprocess is terminated gracefully.
        on_output: Called with each stdout line for real-time streaming.
        timeout: Maximum seconds to wait before killing the process.

    Returns:
        A CodexResult containing captured stdout, stderr, and exit code.

    Raises:
        CodexNotFoundError: If the ``codex`` binary is not on PATH.
        CodexTimeoutError: If the subprocess exceeds *timeout* seconds.
    """
    codex_bin = shutil.which("codex")
    if codex_bin is None:
        raise CodexNotFoundError(
            "Codex CLI not found on PATH. Install it with: npm i -g @openai/codex"
        )

    proc = await asyncio.create_subprocess_exec(
        codex_bin,
        "--quiet",
        prompt,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=working_dir,
    )

    stdout_lines: list[str] = []
    try:
        stdout_lines = await asyncio.wait_for(
            _stream_output(proc, cancel_event, on_output),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        proc.terminate()
        await proc.wait()
        raise CodexTimeoutError(f"Codex subprocess timed out after {timeout}s")

    stderr_data = b""
    if proc.stderr is not None:
        stderr_data = await proc.stderr.read()

    await proc.wait()

    return CodexResult(
        stdout="\n".join(stdout_lines),
        stderr=stderr_data.decode(errors="replace"),
        exit_code=proc.returncode if proc.returncode is not None else -1,
    )


async def _stream_output(
    proc: asyncio.subprocess.Process,
    cancel_event: asyncio.Event | None,
    on_output: Callable[[str], None] | None,
) -> list[str]:
    """Read stdout line-by-line, forwarding to *on_output* and checking for cancellation."""
    lines: list[str] = []
    assert proc.stdout is not None  # guaranteed by PIPE  # noqa: S101

    while True:
        # Check cancellation before blocking on the next line
        if cancel_event is not None and cancel_event.is_set():
            proc.terminate()
            await proc.wait()
            break

        line_bytes = await proc.stdout.readline()
        if not line_bytes:
            break

        line = line_bytes.decode(errors="replace").rstrip("\n")
        lines.append(line)
        if on_output is not None:
            on_output(line)

    return lines
