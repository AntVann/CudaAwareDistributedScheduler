import json
import logging
import os
import re
import shlex
import subprocess
import tempfile
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from control_plane.core.backend import ExecutionBackend
from control_plane.core.models import GpuInfo, JobSpec, JobState, JobStatus, NodeInfo
from control_plane.core.persistence import (
    get_backend_ref,
    get_jobs_in_states,
    set_job_state,
    store_backend_ref,
)

logger = logging.getLogger("control_plane.backends.slurm")


class SlurmSubmitError(RuntimeError):
    pass


class SlurmBackend(ExecutionBackend):
    def __init__(self):
        self.default_partition = os.getenv("SLURM_DEFAULT_PARTITION", "gpu")
        self.log_dir = Path(os.getenv("SLURM_LOG_DIR", "/tmp/scheduler-logs")).expanduser()
        self.script_dir = Path(os.getenv("SLURM_SCRIPT_DIR", "/tmp/scheduler-scripts")).expanduser()
        self.control_plane_url = os.getenv("CONTROL_PLANE_CALLBACK_URL", "http://127.0.0.1:8000").rstrip("/")
        self.agent_token = os.getenv("AGENT_API_TOKEN", "").strip()
        self.poll_interval_secs = int(os.getenv("SLURM_POLL_INTERVAL_SECS", "15"))
        self.poller_enabled = os.getenv("SLURM_POLLER_ENABLED", "1").strip() != "0"

        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.script_dir.mkdir(parents=True, exist_ok=True)

        self._stop_event = threading.Event()
        self._poller_thread: Optional[threading.Thread] = None

    def submit(self, spec: JobSpec, node_hint: Optional[str] = None) -> str:
        script = self._generate_batch_script(spec, node_hint)
        script_path = self._write_temp_script(spec.job_id, script)
        result = subprocess.run(
            ["sbatch", str(script_path)],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "sbatch failed").strip()
            raise SlurmSubmitError(detail)

        slurm_job_id = self._parse_submit_output(result.stdout)
        store_backend_ref(spec.job_id, slurm_job_id)
        return slurm_job_id

    def poll_status(self, job_id: str) -> Optional[JobStatus]:
        slurm_id = get_backend_ref(job_id)
        if not slurm_id:
            return None

        result = subprocess.run(
            [
                "sacct",
                "-j",
                slurm_id,
                "--parsable2",
                "--noheader",
                "--format=JobIDRaw,State,ExitCode,Start,End,NodeList",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            logger.warning("sacct failed for job %s (%s): %s", job_id, slurm_id, (result.stderr or "").strip())
            return None

        parsed = self._parse_sacct(result.stdout, slurm_id)
        if not parsed:
            return None
        slurm_state, exit_code, start, end, nodelist = parsed
        mapped_state = self._map_slurm_state(slurm_state)
        if mapped_state is None:
            return None

        timestamps = self._build_timestamps(mapped_state, start, end)
        reason = slurm_state if mapped_state in {JobState.FAILED, JobState.CANCELLED} else None
        return JobStatus(
            state=mapped_state,
            node_id=nodelist or None,
            exit_code=exit_code,
            timestamps=timestamps,
            reason=reason,
        )

    def list_nodes(self, recent_secs: int = 30) -> List[NodeInfo]:
        del recent_secs  # not used by SLURM node discovery

        # Try JSON first (modern SLURM >= 22.05), fall back to text parsing.
        nodes = self._list_nodes_json()
        if nodes is not None:
            return nodes
        return self._list_nodes_text()

    def _list_nodes_json(self) -> Optional[List[NodeInfo]]:
        result = subprocess.run(
            ["sinfo", "--json"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            return None  # --json not supported, fall back to text

        try:
            payload = json.loads(result.stdout or "{}")
        except json.JSONDecodeError:
            return None

        rows = payload.get("nodes", [])
        now = time.time()
        nodes: List[NodeInfo] = []
        for row in rows:
            node_id = self._coerce_node_name(row)
            if not node_id:
                continue
            partitions = row.get("partitions") or []
            partition_label = ",".join(partitions) if isinstance(partitions, list) else str(partitions)
            state_label = self._coerce_state_label(row.get("state", "unknown"))
            gpu_count = self._parse_gres(row.get("gres", ""))
            gpus = [
                GpuInfo(index=i, name="unknown", mem_total_mb=0, utilization=0.0, mem_used_mb=0)
                for i in range(gpu_count)
            ]
            nodes.append(
                NodeInfo(
                    node_id=node_id,
                    gpus=gpus,
                    labels={"partition": partition_label, "state": state_label},
                    agent_health={"heartbeat_ts": now},
                    last_seen=now,
                )
            )
        return nodes

    def _list_nodes_text(self) -> List[NodeInfo]:
        """Parse sinfo text output for older SLURM versions."""
        result = subprocess.run(
            [
                "sinfo",
                "--Node",
                "--noheader",
                "--format=%N|%P|%T|%G",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            logger.warning("sinfo text fallback failed: %s", (result.stderr or "").strip())
            return []

        # Aggregate partitions per node since sinfo prints one row per node-partition pair.
        node_map: Dict[str, dict] = {}
        for line in (result.stdout or "").splitlines():
            line = line.strip()
            if not line:
                continue
            parts = line.split("|")
            if len(parts) < 4:
                continue
            node_id = parts[0].strip()
            partition = parts[1].strip().rstrip("*")  # Remove default partition marker
            state = parts[2].strip()
            gres = parts[3].strip()
            if not node_id:
                continue
            if node_id not in node_map:
                node_map[node_id] = {"partitions": set(), "state": state, "gres": gres}
            node_map[node_id]["partitions"].add(partition)

        now = time.time()
        nodes: List[NodeInfo] = []
        for node_id, info in sorted(node_map.items()):
            partition_label = ",".join(sorted(info["partitions"]))
            state_label = info["state"]
            gpu_count = self._parse_gres(info["gres"])
            gpus = [
                GpuInfo(index=i, name="unknown", mem_total_mb=0, utilization=0.0, mem_used_mb=0)
                for i in range(gpu_count)
            ]
            nodes.append(
                NodeInfo(
                    node_id=node_id,
                    gpus=gpus,
                    labels={"partition": partition_label, "state": state_label},
                    agent_health={"heartbeat_ts": now},
                    last_seen=now,
                )
            )
        return nodes

    def cancel(self, job_id: str) -> bool:
        slurm_id = get_backend_ref(job_id)
        if not slurm_id:
            return False
        result = subprocess.run(
            ["scancel", slurm_id],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return result.returncode == 0

    def start(self) -> None:
        if not self.poller_enabled:
            return
        if self._poller_thread and self._poller_thread.is_alive():
            return
        self._stop_event.clear()
        self._poller_thread = threading.Thread(target=self._poller_loop, daemon=True)
        self._poller_thread.start()
        logger.info("Started SLURM poller loop (interval=%ss)", self.poll_interval_secs)

    def stop(self) -> None:
        self._stop_event.set()
        if self._poller_thread and self._poller_thread.is_alive():
            self._poller_thread.join(timeout=1)

    def _poller_loop(self) -> None:
        active_states = [JobState.QUEUED.value, JobState.PLACED.value, JobState.RUNNING.value]
        while not self._stop_event.is_set():
            try:
                rows = get_jobs_in_states(active_states)
                batch_statuses = self._poll_status_batch(rows)
                for row in rows:
                    if not row.get("backend_ref"):
                        continue
                    job_id = row["job_id"]
                    current_state = row["status"]
                    status = batch_statuses.get(job_id)
                    if status is None:
                        continue
                    next_state = status.state.value
                    if next_state != current_state:
                        set_job_state(
                            job_id,
                            next_state,
                            exit_code=status.exit_code,
                            reason=status.reason,
                        )
            except Exception:
                logger.exception("SLURM poller iteration failed")
            self._stop_event.wait(self.poll_interval_secs)

    def _poll_status_batch(self, rows: List[dict]) -> Dict[str, JobStatus]:
        ref_to_job_ids: Dict[str, List[str]] = {}
        for row in rows:
            backend_ref = str(row.get("backend_ref") or "").strip()
            job_id = str(row.get("job_id") or "").strip()
            if not backend_ref or not job_id:
                continue
            ref_to_job_ids.setdefault(backend_ref, []).append(job_id)

        if not ref_to_job_ids:
            return {}

        backend_refs = sorted(ref_to_job_ids.keys())
        result = subprocess.run(
            [
                "sacct",
                "-j",
                ",".join(backend_refs),
                "--parsable2",
                "--noheader",
                "--format=JobIDRaw,State,ExitCode,Start,End,NodeList",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            logger.warning("batched sacct failed for %d jobs: %s", len(backend_refs), (result.stderr or "").strip())
            return {}

        records = self._parse_sacct_records(result.stdout)
        status_map: Dict[str, JobStatus] = {}
        for backend_ref, job_ids in ref_to_job_ids.items():
            selected = self._select_sacct_record(records, backend_ref)
            if not selected:
                continue

            _, slurm_state, exit_code, start, end, nodelist = selected
            mapped_state = self._map_slurm_state(slurm_state)
            if mapped_state is None:
                continue

            timestamps = self._build_timestamps(mapped_state, start, end)
            reason = slurm_state if mapped_state in {JobState.FAILED, JobState.CANCELLED} else None
            status = JobStatus(
                state=mapped_state,
                node_id=nodelist or None,
                exit_code=exit_code,
                timestamps=timestamps,
                reason=reason,
            )
            for job_id in job_ids:
                status_map[job_id] = status
        return status_map

    def _select_partition(self, spec: JobSpec) -> str:
        preferred = (spec.metadata or {}).get("partition")
        if preferred:
            return str(preferred)
        return self.default_partition

    def _generate_batch_script(self, spec: JobSpec, node_hint: Optional[str]) -> str:
        lines = [
            "#!/bin/bash",
            "set -euo pipefail",
            f"#SBATCH --job-name={spec.job_id}",
            f"#SBATCH --gres=gpu:{spec.gpus}",
            f"#SBATCH --output={self.log_dir}/{spec.job_id}-%j.out",
            f"#SBATCH --error={self.log_dir}/{spec.job_id}-%j.err",
            f"#SBATCH --partition={self._select_partition(spec)}",
        ]

        if spec.cpu:
            lines.append(f"#SBATCH --cpus-per-task={spec.cpu}")
        if spec.mem_gb:
            lines.append(f"#SBATCH --mem={int(spec.mem_gb * 1024)}M")
        if node_hint:
            lines.append(f"#SBATCH --nodelist={node_hint}")

        for key, value in (spec.env or {}).items():
            lines.append(f"export {key}={shlex.quote(value)}")

        callback_url = f"{self.control_plane_url}/api/admin/jobs/{spec.job_id}/state"
        auth_header = ""
        if self.agent_token:
            auth_header = f"-H {shlex.quote(f'Authorization: Bearer {self.agent_token}')}"
        callback_common = f"curl -fsS -X POST {shlex.quote(callback_url)} -H 'Content-Type: application/json' {auth_header}"

        lines.append("")
        lines.append(f"{callback_common} -d '{json.dumps({'state': 'RUNNING'})}' || true")
        lines.append("")

        # Keep strict mode for setup, but allow workload failures to be captured and reported.
        lines.append("set +e")
        if spec.image:
            lines.append(f"apptainer exec --nv {shlex.quote(spec.image)} {shlex.join(spec.cmd)}")
        else:
            lines.append(shlex.join(spec.cmd))

        lines.append("")
        lines.append("EXIT_CODE=$?")
        lines.append("set -e")
        lines.append('if [ "$EXIT_CODE" -eq 0 ]; then STATE="DONE"; else STATE="FAILED"; fi')
        lines.append(
            f'{callback_common} -d "{{\\"state\\": \\"$STATE\\", \\"exit_code\\": $EXIT_CODE}}" || true'
        )
        lines.append("exit $EXIT_CODE")
        return "\n".join(lines) + "\n"

    def _write_temp_script(self, job_id: str, script: str) -> Path:
        safe_job_id = re.sub(r"[^a-zA-Z0-9._-]", "-", job_id)
        fd, raw_path = tempfile.mkstemp(prefix=f"{safe_job_id}-", suffix=".sbatch", dir=self.script_dir)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(script)
        os.chmod(raw_path, 0o700)
        return Path(raw_path)

    def _parse_submit_output(self, stdout: str) -> str:
        match = re.search(r"Submitted batch job (\d+)", stdout or "")
        if not match:
            raise SlurmSubmitError(f"Unable to parse sbatch output: {stdout!r}")
        return match.group(1)

    def _parse_sacct(self, output: str, slurm_id: str) -> Optional[tuple[str, Optional[int], str, str, str]]:
        selected = self._select_sacct_record(self._parse_sacct_records(output), slurm_id)
        if not selected:
            return None
        _, state, exit_code, start, end, nodelist = selected
        return state, exit_code, start, end, nodelist

    def _parse_sacct_records(self, output: str) -> List[tuple[str, str, Optional[int], str, str, str]]:
        records: List[tuple[str, str, Optional[int], str, str, str]] = []
        for line in (output or "").splitlines():
            line = line.strip()
            if not line:
                continue
            parts = line.split("|")
            if len(parts) < 6:
                continue
            job_id_raw = parts[0].strip()
            state = parts[1].strip()
            exit_code = self._parse_exit_code(parts[2].strip())
            start = parts[3].strip()
            end = parts[4].strip()
            nodelist = parts[5].strip()
            records.append((job_id_raw, state, exit_code, start, end, nodelist))
        return records

    def _select_sacct_record(
        self,
        records: List[tuple[str, str, Optional[int], str, str, str]],
        slurm_id: str,
    ) -> Optional[tuple[str, str, Optional[int], str, str, str]]:
        if not records:
            return None

        # Prefer the top-level job row over step rows like ".batch" / ".extern".
        for rec in records:
            if rec[0] == slurm_id:
                return rec

        parent_rows = [rec for rec in records if "." not in rec[0]]
        for rec in parent_rows:
            if rec[0].startswith(slurm_id):
                return rec

        return records[0]

    @staticmethod
    def _parse_exit_code(value: str) -> Optional[int]:
        if not value:
            return None
        head = value.split(":", 1)[0]
        try:
            return int(head)
        except ValueError:
            return None

    @staticmethod
    def _map_slurm_state(state: str) -> Optional[JobState]:
        normalized = (state or "").strip().upper().split("+", 1)[0]
        normalized = normalized.split(" ", 1)[0]
        if normalized == "PENDING":
            return JobState.QUEUED
        if normalized in {"RUNNING", "COMPLETING"}:
            return JobState.RUNNING
        if normalized == "COMPLETED":
            return JobState.DONE
        if normalized.startswith("CANCELLED"):
            return JobState.CANCELLED
        if normalized in {
            "FAILED",
            "TIMEOUT",
            "NODE_FAIL",
            "BOOT_FAIL",
            "OUT_OF_MEMORY",
            "PREEMPTED",
            "DEADLINE",
            "REVOKED",
        }:
            return JobState.FAILED
        return None

    def _build_timestamps(self, mapped_state: JobState, start: str, end: str) -> dict[str, float]:
        timestamps: dict[str, float] = {}
        start_ts = self._parse_slurm_time(start)
        if start_ts is not None:
            timestamps["running"] = start_ts

        end_ts = self._parse_slurm_time(end)
        if end_ts is not None:
            if mapped_state == JobState.DONE:
                timestamps["done"] = end_ts
            elif mapped_state == JobState.FAILED:
                timestamps["failed"] = end_ts
            elif mapped_state == JobState.CANCELLED:
                timestamps["cancelled"] = end_ts
        return timestamps

    @staticmethod
    def _parse_slurm_time(value: str) -> Optional[float]:
        raw = (value or "").strip()
        if not raw or raw in {"Unknown", "None", "N/A"}:
            return None

        # Some clusters report epoch-style timestamps.
        if re.fullmatch(r"\d+(?:\.\d+)?", raw):
            int_part = raw.split(".", 1)[0]
            if len(int_part) >= 9:
                try:
                    return float(raw)
                except ValueError:
                    pass

        # Common format from sacct: 2026-03-13T23:12:40
        try:
            return datetime.fromisoformat(raw).timestamp()
        except ValueError:
            pass

        # Alternate format: 2026-03-13 23:12:40
        try:
            return datetime.strptime(raw, "%Y-%m-%d %H:%M:%S").timestamp()
        except ValueError:
            return None

    @staticmethod
    def _coerce_node_name(row: dict) -> str:
        for key in ("name", "node_name", "hostname"):
            value = row.get(key)
            if value:
                return str(value)
        return ""

    @staticmethod
    def _coerce_state_label(value: object) -> str:
        if isinstance(value, str):
            return value
        if isinstance(value, list):
            return ",".join(str(v) for v in value)
        if isinstance(value, dict):
            return ",".join(f"{k}={v}" for k, v in value.items())
        return str(value)

    @staticmethod
    def _parse_gres(value: object) -> int:
        if isinstance(value, int):
            return max(value, 0)
        if isinstance(value, list):
            return sum(SlurmBackend._parse_gres(item) for item in value)
        if isinstance(value, dict):
            total = 0
            for key, item in value.items():
                if "gpu" in str(key).lower():
                    total += SlurmBackend._parse_gres(item)
            return total

        raw = str(value or "")
        if not raw:
            return 0
        total = 0
        for chunk in raw.split(","):
            chunk = chunk.strip()
            if not chunk:
                continue
            if "gpu" not in chunk.lower():
                continue
            match = re.search(r"gpu(?::[^:,()]+)*:(\d+)", chunk, flags=re.IGNORECASE)
            if match:
                total += int(match.group(1))
                continue
            alt = re.search(r"gpu[=:](\d+)", chunk, flags=re.IGNORECASE)
            if alt:
                total += int(alt.group(1))
                continue
            # Plain "gpu" entry implies at least one GPU.
            total += 1
        return total
