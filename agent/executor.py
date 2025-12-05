import os
import subprocess
from typing import Dict, List, Optional


def run_job(cmd: List[str], image: Optional[str] = None, env: Optional[Dict[str, str]] = None) -> int:
    """
    Execute a job either directly on the host or inside Apptainer if an image is provided.
    Returns the process exit code.
    """
    merged_env = os.environ.copy()
    if env:
        merged_env.update(env)

    if image:
        appt_cmd = ["apptainer", "exec", "--nv", image] + cmd
        proc = subprocess.run(appt_cmd, env=merged_env)
        return proc.returncode

    proc = subprocess.run(cmd, env=merged_env)
    return proc.returncode
