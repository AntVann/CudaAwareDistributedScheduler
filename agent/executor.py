import time


def run_fake(job_id: str, cmd: list[str], image: str | None = None) -> int:
    """
    Fake executor for Milestone 4. Sleeps briefly to simulate work.
    """
    time.sleep(2)
    return 0
