from typing import Protocol


class VideoPipeline(Protocol):
    """Processes one job end-to-end, owning all status/current_step/error
    transitions on the job. Must not raise for expected failures — those are
    recorded as status=FAILED with an error_message; the worker treats an
    escaping exception as an internal bug."""

    async def run(self, job_id: str) -> None: ...
