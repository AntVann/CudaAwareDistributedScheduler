import os
import shutil
import subprocess
from dataclasses import dataclass
from typing import Dict, List, Optional


@dataclass(frozen=True)
class ExecutionResult:
    exit_code: int
    reason: Optional[str] = None


def run_job(
    cmd: List[str],
    image: Optional[str] = None,
    env: Optional[Dict[str, str]] = None,
) -> ExecutionResult:
    """
    Execute a job either directly on the host or inside Apptainer if an image is provided.
    Returns the process exit code and an optional failure reason.
    """
    merged_env = os.environ.copy()
    if env:
        merged_env.update(env)

    if not cmd:
        return ExecutionResult(exit_code=2, reason="No command provided")

    if image:
        if shutil.which("apptainer") is None:
            return ExecutionResult(
                exit_code=127,
                reason="Image execution requested but 'apptainer' is not installed or not in PATH",
            )
        appt_cmd = ["apptainer", "exec", "--nv", image] + cmd
        proc = subprocess.run(appt_cmd, env=merged_env)
        return ExecutionResult(exit_code=proc.returncode)

    try:
        proc = subprocess.run(cmd, env=merged_env)
        return ExecutionResult(exit_code=proc.returncode)
    except FileNotFoundError as exc:
        return ExecutionResult(
            exit_code=127,
            reason=f"Executable not found: {exc.filename}",
        )
