from abc import ABC, abstractmethod
from typing import List, Optional

from control_plane.core.models import JobSpec, JobStatus, NodeInfo


class ExecutionBackend(ABC):
    """
    Abstraction over how jobs are dispatched and monitored.
    The scheduler decides WHERE to place a job. The backend
    decides HOW to execute that decision.
    """

    @abstractmethod
    def submit(self, spec: JobSpec, node_hint: Optional[str] = None) -> str:
        """
        Dispatch a job for execution.
        Returns a backend-specific job reference (e.g., Redis queue entry, SLURM job ID).
        """
        ...

    @abstractmethod
    def poll_status(self, job_id: str) -> Optional[JobStatus]:
        """
        Check the current state of a submitted job.
        Returns None if the backend has no information about this job.
        """
        ...

    @abstractmethod
    def list_nodes(self, recent_secs: int = 30) -> List[NodeInfo]:
        """
        Return the currently available compute nodes with GPU inventory.
        Used by the scheduler to find eligible placement targets.
        """
        ...

    @abstractmethod
    def cancel(self, job_id: str) -> bool:
        """
        Attempt to cancel a running or queued job.
        Returns True if cancellation was accepted.
        """
        ...
