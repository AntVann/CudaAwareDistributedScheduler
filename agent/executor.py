import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional


@dataclass(frozen=True)
class ExecutionResult:
    exit_code: int
    reason: Optional[str] = None


def _write_log(path: Optional[str], content: str) -> None:
    if not path:
        return
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


def run_job(
    cmd: List[str],
    image: Optional[str] = None,
    env: Optional[Dict[str, str]] = None,
    stdout_path: Optional[str] = None,
    stderr_path: Optional[str] = None,
) -> ExecutionResult:
    """
    Execute a job either directly on the host or inside Apptainer if an image is provided.
    Returns the process exit code and an optional failure reason.
    """
    merged_env = os.environ.copy()
    if env:
        merged_env.update(env)

    if not cmd:
        reason = "No command provided"
        _write_log(stdout_path, "")
        _write_log(stderr_path, reason + "\n")
        return ExecutionResult(exit_code=2, reason=reason)

    if image:
        if shutil.which("apptainer") is None:
            reason = "Image execution requested but 'apptainer' is not installed or not in PATH"
            _write_log(stdout_path, "")
            _write_log(stderr_path, reason + "\n")
            return ExecutionResult(
                exit_code=127,
                reason=reason,
            )
        appt_cmd = ["apptainer", "exec", "--nv", image] + cmd
        proc = subprocess.run(appt_cmd, env=merged_env, capture_output=True, text=True)
        _write_log(stdout_path, proc.stdout or "")
        _write_log(stderr_path, proc.stderr or "")
        return ExecutionResult(exit_code=proc.returncode)

    try:
        proc = subprocess.run(cmd, env=merged_env, capture_output=True, text=True)
        _write_log(stdout_path, proc.stdout or "")
        _write_log(stderr_path, proc.stderr or "")
        return ExecutionResult(exit_code=proc.returncode)
    except FileNotFoundError as exc:
        reason = f"Executable not found: {exc.filename}"
        _write_log(stdout_path, "")
        _write_log(stderr_path, reason + "\n")
        return ExecutionResult(
            exit_code=127,
            reason=reason,
        )
