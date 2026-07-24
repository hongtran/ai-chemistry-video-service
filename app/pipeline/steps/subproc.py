"""Run a pipeline subprocess without leaking its descendants.

The steps shell out to process *trees* (`bash build-video.sh` -> `npm exec` ->
`node hyperframes` -> `chrome-headless-shell`). `Process.kill()` signals only
the direct child, so a timeout used to orphan everything below it — node and
Chrome kept running (and holding RAM) long after the job was failed.

`start_new_session=True` puts the child and all its descendants in a fresh
process group, so on timeout or cancellation the whole group can be signalled
at once. SIGTERM goes first: hyperframes/puppeteer shut down their Chrome on
SIGTERM, which also covers a Chrome that detached into its own group. SIGKILL
follows only if the group is still alive after the grace period.
"""
import asyncio
import os
import signal

# Seconds between SIGTERM (clean shutdown — lets node kill its Chrome) and
# SIGKILL for whatever is still alive.
_TERM_GRACE_SECONDS = 5.0


def _signal_group(proc: asyncio.subprocess.Process, sig: signal.Signals) -> None:
    try:
        # pgid == pid because the child was started with start_new_session.
        os.killpg(proc.pid, sig)
    except ProcessLookupError:
        pass


async def _kill_tree(proc: asyncio.subprocess.Process) -> None:
    if proc.returncode is not None:
        return
    _signal_group(proc, signal.SIGTERM)
    try:
        await asyncio.wait_for(proc.wait(), timeout=_TERM_GRACE_SECONDS)
    except asyncio.TimeoutError:
        _signal_group(proc, signal.SIGKILL)
        await proc.wait()


async def run(
    program: str, args: list[str], cwd: str, timeout: float
) -> tuple[int | None, str, str]:
    """Run `program` to completion; returns (returncode, stdout, stderr).

    On timeout, kills the child's whole process group and re-raises
    asyncio.TimeoutError for the caller to wrap in its own domain error.
    On cancellation, kills the group and re-raises CancelledError.
    """
    proc = await asyncio.create_subprocess_exec(
        program, *args,
        cwd=cwd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=True,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        await _kill_tree(proc)
        raise
    except asyncio.CancelledError:
        # Shielded so the cleanup itself survives the in-flight cancellation.
        await asyncio.shield(_kill_tree(proc))
        raise
    return (
        proc.returncode,
        stdout.decode("utf-8", "replace"),
        stderr.decode("utf-8", "replace"),
    )
