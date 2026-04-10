"""Tests for auto_agent.agent.codex — Codex CLI headless wrapper."""

from __future__ import annotations

import asyncio
import signal
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from auto_agent.agent.codex import (
    CodexNotFoundError,
    CodexResult,
    CodexTimeoutError,
    run_codex,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_fake_process(
    stdout_lines: list[bytes],
    stderr: bytes = b"",
    returncode: int = 0,
) -> AsyncMock:
    """Build a mock asyncio.subprocess.Process that yields *stdout_lines*."""
    proc = AsyncMock()
    proc.returncode = returncode

    # stdout: async readline that yields lines then b""
    readline_iter = iter(stdout_lines + [b""])
    stdout_mock = MagicMock()
    stdout_mock.readline = AsyncMock(side_effect=lambda: next(readline_iter))
    proc.stdout = stdout_mock

    # stderr: async read returns full blob
    stderr_mock = MagicMock()
    stderr_iter = iter([stderr, b""] if stderr else [b""])
    stderr_mock.read = AsyncMock(side_effect=lambda _size=-1: next(stderr_iter))
    proc.stderr = stderr_mock

    stdin_mock = MagicMock()
    stdin_mock.write = MagicMock()
    stdin_mock.close = MagicMock()
    proc.stdin = stdin_mock

    proc.terminate = MagicMock()
    proc.wait = AsyncMock()
    return proc


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestRunCodex:
    """Unit tests for run_codex (subprocess is always mocked)."""

    @pytest.mark.asyncio
    async def test_basic_invocation(self) -> None:
        """run_codex returns captured stdout, stderr, and exit_code."""
        fake_proc = _make_fake_process(
            stdout_lines=[b"Hello, world!\n"],
            stderr=b"debug info\n",
            returncode=0,
        )

        with (
            patch("auto_agent.agent.codex.shutil.which", return_value="/usr/bin/codex"),
            patch(
                "asyncio.create_subprocess_exec", return_value=fake_proc
            ) as mock_exec,
        ):
            result = await run_codex("Say hello")

        assert isinstance(result, CodexResult)
        assert result.stdout == "Hello, world!"
        assert result.stderr == "debug info\n"
        assert result.exit_code == 0
        mock_exec.assert_called_once()
        args, kwargs = mock_exec.call_args
        assert args[:6] == (
            "/usr/bin/codex",
            "--ask-for-approval",
            "never",
            "exec",
            "--skip-git-repo-check",
            "-",
        )
        assert "Say hello" not in args
        assert kwargs["stdin"] == asyncio.subprocess.PIPE
        fake_proc.stdin.write.assert_called_once_with(b"Say hello")
        fake_proc.stdin.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_streaming_callback(self) -> None:
        """on_output receives each line in real time."""
        lines_received: list[str] = []
        fake_proc = _make_fake_process(
            stdout_lines=[b"line1\n", b"line2\n"],
            returncode=0,
        )

        with (
            patch("auto_agent.agent.codex.shutil.which", return_value="/usr/bin/codex"),
            patch("asyncio.create_subprocess_exec", return_value=fake_proc),
        ):
            await run_codex("prompt", on_output=lines_received.append)

        assert lines_received == ["line1", "line2"]
        fake_proc.stdin.write.assert_called_once_with(b"prompt")
        fake_proc.stdin.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_codex_not_found(self) -> None:
        """Raises CodexNotFoundError when the binary is missing."""
        with patch("auto_agent.agent.codex.shutil.which", return_value=None):
            with pytest.raises(CodexNotFoundError, match="not found on PATH"):
                await run_codex("anything")

    @pytest.mark.asyncio
    async def test_cancellation_sends_sigterm(self) -> None:
        """Setting cancel_event terminates the subprocess."""
        cancel = asyncio.Event()
        cancel.set()  # pre-cancelled

        fake_proc = _make_fake_process(
            stdout_lines=[b"should not appear\n"],
            returncode=-15,
        )

        with (
            patch("auto_agent.agent.codex.shutil.which", return_value="/usr/bin/codex"),
            patch("asyncio.create_subprocess_exec", return_value=fake_proc),
        ):
            result = await run_codex("prompt", cancel_event=cancel)

        fake_proc.terminate.assert_called_once()

    @pytest.mark.asyncio
    async def test_timeout_kills_subprocess(self) -> None:
        """Exceeding timeout raises CodexTimeoutError and terminates."""

        async def _hang(*args: object, **kwargs: object) -> AsyncMock:
            """Simulate a process whose stdout never finishes."""
            proc = AsyncMock()
            proc.returncode = -9

            async def never_return() -> bytes:
                await asyncio.sleep(3600)
                return b""

            stdout_mock = MagicMock()
            stdout_mock.readline = never_return
            proc.stdout = stdout_mock

            stderr_mock = MagicMock()
            stderr_mock.read = AsyncMock(return_value=b"")
            proc.stderr = stderr_mock
            stdin_mock = MagicMock()
            stdin_mock.write = MagicMock()
            stdin_mock.close = MagicMock()
            proc.stdin = stdin_mock
            proc.terminate = MagicMock()
            proc.wait = AsyncMock()
            return proc

        with (
            patch("auto_agent.agent.codex.shutil.which", return_value="/usr/bin/codex"),
            patch("asyncio.create_subprocess_exec", side_effect=_hang),
        ):
            with pytest.raises(CodexTimeoutError, match="timed out"):
                await run_codex("prompt", timeout=0)

    @pytest.mark.asyncio
    async def test_nonzero_exit_code(self) -> None:
        """Non-zero exit codes are captured, not raised as exceptions."""
        fake_proc = _make_fake_process(
            stdout_lines=[b"error output\n"],
            stderr=b"fatal\n",
            returncode=1,
        )

        with (
            patch("auto_agent.agent.codex.shutil.which", return_value="/usr/bin/codex"),
            patch("asyncio.create_subprocess_exec", return_value=fake_proc),
        ):
            result = await run_codex("bad prompt")

        assert result.exit_code == 1
        assert result.stderr == "fatal\n"

    @pytest.mark.asyncio
    async def test_large_stderr_is_drained_while_stdout_waits(self) -> None:
        """stderr draining starts concurrently so the run cannot deadlock."""
        stderr_started = asyncio.Event()
        stdout_release = asyncio.Event()
        stderr_blob = b"x" * (128 * 1024 + 1)
        first_read = True

        async def _stdout_readline() -> bytes:
            await stdout_release.wait()
            return b""

        async def _stderr_read(_size: int = -1) -> bytes:
            nonlocal first_read
            if first_read:
                first_read = False
                stderr_started.set()
                stdout_release.set()
                return stderr_blob
            return b""

        fake_proc = AsyncMock()
        fake_proc.returncode = 0
        stdout_mock = MagicMock()
        stdout_mock.readline = AsyncMock(side_effect=_stdout_readline)
        fake_proc.stdout = stdout_mock
        stderr_mock = MagicMock()
        stderr_mock.read = AsyncMock(side_effect=_stderr_read)
        fake_proc.stderr = stderr_mock
        fake_proc.terminate = MagicMock()
        fake_proc.wait = AsyncMock()
        stdin_mock = MagicMock()
        stdin_mock.write = MagicMock()
        stdin_mock.close = MagicMock()
        fake_proc.stdin = stdin_mock

        with (
            patch("auto_agent.agent.codex.shutil.which", return_value="/usr/bin/codex"),
            patch("asyncio.create_subprocess_exec", return_value=fake_proc),
        ):
            result = await asyncio.wait_for(run_codex("prompt", timeout=5), timeout=1)

        assert stderr_started.is_set()
        assert len(result.stderr.encode()) == len(stderr_blob)

    @pytest.mark.asyncio
    async def test_working_dir_passed_to_subprocess(self) -> None:
        """working_dir is forwarded as cwd to the subprocess."""
        fake_proc = _make_fake_process(stdout_lines=[], returncode=0)

        with (
            patch("auto_agent.agent.codex.shutil.which", return_value="/usr/bin/codex"),
            patch(
                "asyncio.create_subprocess_exec", return_value=fake_proc
            ) as mock_exec,
        ):
            await run_codex("prompt", working_dir="/tmp/project")

        mock_exec.assert_called_once()
        _, kwargs = mock_exec.call_args
        assert kwargs["cwd"] == "/tmp/project"
        fake_proc.stdin.write.assert_called_once_with(b"prompt")
        fake_proc.stdin.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_large_prompt_is_sent_via_stdin(self) -> None:
        """Large prompts are written to stdin instead of argv."""
        large_prompt = "a" * (200 * 1024)
        fake_proc = _make_fake_process(stdout_lines=[b"ok\n"], returncode=0)

        with (
            patch("auto_agent.agent.codex.shutil.which", return_value="/usr/bin/codex"),
            patch(
                "asyncio.create_subprocess_exec", return_value=fake_proc
            ) as mock_exec,
        ):
            result = await run_codex(large_prompt)

        assert result.stdout == "ok"
        args, kwargs = mock_exec.call_args
        assert large_prompt not in args
        assert args[:6] == (
            "/usr/bin/codex",
            "--ask-for-approval",
            "never",
            "exec",
            "--skip-git-repo-check",
            "-",
        )
        assert kwargs["stdin"] == asyncio.subprocess.PIPE
        fake_proc.stdin.write.assert_called_once_with(large_prompt.encode())
        fake_proc.stdin.close.assert_called_once()
