"""Async subprocess wrapper for invoking Codex CLI in headless mode."""

from __future__ import annotations

import asyncio
import shutil
from contextlib import suppress
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
        "--ask-for-approval",
        "never",
        "exec",
        "--skip-git-repo-check",
        "-",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=working_dir,
    )
    assert proc.stdin is not None  # guaranteed by PIPE  # noqa: S101
    proc.stdin.write(prompt.encode())
    proc.stdin.close()

    stderr_task: asyncio.Task[bytes] | None = None
    if proc.stderr is not None:
        stderr_task = asyncio.create_task(_stream_stderr(proc, cancel_event))

    stdout_lines: list[str] = []
    try:
        stdout_lines = await asyncio.wait_for(
            _stream_output(proc, cancel_event, on_output),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        proc.terminate()
        await proc.wait()
        if stderr_task is not None:
            stderr_task.cancel()
            with suppress(asyncio.CancelledError):
                await stderr_task
        raise CodexTimeoutError(f"Codex subprocess timed out after {timeout}s")

    stderr_data = b""
    if stderr_task is not None:
        stderr_data = await stderr_task

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
        line_bytes = await _readline_or_cancel(proc, cancel_event)
        if not line_bytes:
            break

        line = line_bytes.decode(errors="replace").rstrip("\n")
        lines.append(line)
        if on_output is not None:
            on_output(line)

    return lines


async def _stream_stderr(
    proc: asyncio.subprocess.Process,
    cancel_event: asyncio.Event | None,
) -> bytes:
    """Read stderr in chunks to avoid pipe-buffer deadlocks."""
    chunks: list[bytes] = []
    assert proc.stderr is not None  # guaranteed by PIPE  # noqa: S101

    while True:
        chunk = await _read_stderr_or_cancel(proc, cancel_event)
        if not chunk:
            break
        chunks.append(chunk)

    return b"".join(chunks)


async def _read_stderr_or_cancel(
    proc: asyncio.subprocess.Process,
    cancel_event: asyncio.Event | None,
) -> bytes:
    """Read one stderr chunk unless *cancel_event* requests termination."""
    assert proc.stderr is not None  # guaranteed by PIPE  # noqa: S101
    if cancel_event is None:
        return await proc.stderr.read(8192)

    read_task = asyncio.create_task(proc.stderr.read(8192))
    cancel_task = asyncio.create_task(cancel_event.wait())
    done, pending = await asyncio.wait(
        {read_task, cancel_task},
        return_when=asyncio.FIRST_COMPLETED,
    )

    for task in pending:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task

    if cancel_task in done and cancel_event.is_set():
        return b""

    return await read_task


async def _readline_or_cancel(
    proc: asyncio.subprocess.Process,
    cancel_event: asyncio.Event | None,
) -> bytes:
    """Read one stdout line unless *cancel_event* requests termination."""
    assert proc.stdout is not None  # guaranteed by PIPE  # noqa: S101
    if cancel_event is None:
        return await proc.stdout.readline()

    read_task = asyncio.create_task(proc.stdout.readline())
    cancel_task = asyncio.create_task(cancel_event.wait())
    done, pending = await asyncio.wait(
        {read_task, cancel_task},
        return_when=asyncio.FIRST_COMPLETED,
    )

    for task in pending:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task

    if cancel_task in done and cancel_event.is_set():
        proc.terminate()
        await proc.wait()
        return b""

    return await read_task
