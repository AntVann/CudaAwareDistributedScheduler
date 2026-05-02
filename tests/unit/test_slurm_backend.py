import subprocess

import pytest

from control_plane.core.backends.slurm import SlurmBackend, SlurmSubmitError
from control_plane.core.models import JobSpec, JobState


def test_submit_stores_backend_ref(monkeypatch, tmp_path):
    backend = SlurmBackend()
    backend.script_dir = tmp_path
    backend.log_dir = tmp_path

    captured = {}

    def fake_run(args, capture_output, text, timeout):
        assert args[0] == "sbatch"
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="Submitted batch job 4242\n", stderr="")

    def fake_store(job_id, backend_ref):
        captured["job_id"] = job_id
        captured["backend_ref"] = backend_ref

    monkeypatch.setattr("control_plane.core.backends.slurm.subprocess.run", fake_run)
    monkeypatch.setattr("control_plane.core.backends.slurm.store_backend_ref", fake_store)

    spec = JobSpec(job_id="job-1", project="default", image="", cmd=["echo", "hello"], gpus=1)
    result = backend.submit(spec, node_hint="node-a")

    assert result == "4242"
    assert captured == {"job_id": "job-1", "backend_ref": "4242"}


def test_submit_raises_on_sbatch_error(monkeypatch, tmp_path):
    backend = SlurmBackend()
    backend.script_dir = tmp_path
    backend.log_dir = tmp_path

    def fake_run(args, capture_output, text, timeout):
        return subprocess.CompletedProcess(args=args, returncode=1, stdout="", stderr="sbatch failed")

    monkeypatch.setattr("control_plane.core.backends.slurm.subprocess.run", fake_run)

    spec = JobSpec(job_id="job-1", project="default", image="", cmd=["echo", "hello"], gpus=1)
    with pytest.raises(SlurmSubmitError):
        backend.submit(spec)


def test_poll_status_maps_completed(monkeypatch):
    backend = SlurmBackend()
    monkeypatch.setattr("control_plane.core.backends.slurm.get_backend_ref", lambda job_id: "4242")

    def fake_run(args, capture_output, text, timeout):
        assert args[0] == "sacct"
        return subprocess.CompletedProcess(
            args=args,
            returncode=0,
            stdout="4242|COMPLETED|0:0|2026-03-13T01:00:00|2026-03-13T01:05:00|gpu-01\n",
            stderr="",
        )

    monkeypatch.setattr("control_plane.core.backends.slurm.subprocess.run", fake_run)

    status = backend.poll_status("job-1")
    assert status is not None
    assert status.state == JobState.DONE
    assert status.exit_code == 0
    assert status.node_id == "gpu-01"
    assert "done" in status.timestamps


def test_poll_status_prefers_parent_row_over_step_rows(monkeypatch):
    backend = SlurmBackend()
    monkeypatch.setattr("control_plane.core.backends.slurm.get_backend_ref", lambda job_id: "4242")

    def fake_run(args, capture_output, text, timeout):
        return subprocess.CompletedProcess(
            args=args,
            returncode=0,
            stdout=(
                "4242.batch|FAILED|1:0|2026-03-13T01:00:00|2026-03-13T01:01:00|gpu-01\n"
                "4242|COMPLETED|0:0|2026-03-13T01:00:00|2026-03-13T01:05:00|gpu-01\n"
            ),
            stderr="",
        )

    monkeypatch.setattr("control_plane.core.backends.slurm.subprocess.run", fake_run)
    status = backend.poll_status("job-1")
    assert status is not None
    assert status.state == JobState.DONE
    assert status.exit_code == 0


def test_list_nodes_parses_gpu_count(monkeypatch):
    backend = SlurmBackend()

    def fake_run(args, capture_output, text, timeout):
        assert args[0] == "sinfo"
        return subprocess.CompletedProcess(
            args=args,
            returncode=0,
            stdout='{"nodes":[{"name":"gpu-01","gres":"gpu:a100:4","partitions":["gpu-a100"],"state":"idle"}]}',
            stderr="",
        )

    monkeypatch.setattr("control_plane.core.backends.slurm.subprocess.run", fake_run)
    nodes = backend.list_nodes()

    assert len(nodes) == 1
    assert nodes[0].node_id == "gpu-01"
    assert len(nodes[0].gpus) == 4
    assert nodes[0].labels["partition"] == "gpu-a100"
    assert nodes[0].labels["state"] == "idle"
    assert nodes[0].labels["gpu_available"] == "4"


def test_list_nodes_falls_back_to_text_when_json_payload_has_no_nodes(monkeypatch):
    backend = SlurmBackend()
    calls = []

    def fake_run(args, capture_output, text, timeout):
        calls.append(args)
        if args == ["sinfo", "--json"]:
            return subprocess.CompletedProcess(
                args=args,
                returncode=0,
                stdout='{"meta":{"cluster":"test"},"errors":[],"warnings":[]}',
                stderr="",
            )
        assert args == ["sinfo", "--Node", "--noheader", "--Format=NodeList,Partition,StateLong,Gres,GresUsed"]
        return subprocess.CompletedProcess(
            args=args,
            returncode=0,
            stdout="gpu-01 gpu-a100* idle gpu:a100:4 gpu:a100:1\n",
            stderr="",
        )

    monkeypatch.setattr("control_plane.core.backends.slurm.subprocess.run", fake_run)
    nodes = backend.list_nodes()

    assert calls == [
        ["sinfo", "--json"],
        ["sinfo", "--Node", "--noheader", "--Format=NodeList,Partition,StateLong,Gres,GresUsed"],
    ]
    assert len(nodes) == 1
    assert nodes[0].node_id == "gpu-01"
    assert len(nodes[0].gpus) == 4
    assert nodes[0].labels["partition"] == "gpu-a100"
    assert nodes[0].labels["gpu_available"] == "3"


def test_list_nodes_text_caches_unsupported_format(monkeypatch):
    """Older SLURM rejects --Format=...,GresUsed. We probe once; subsequent
    calls must skip the 5-column probe to avoid logging WARNING every tick."""
    backend = SlurmBackend()
    five_col = ["sinfo", "--Node", "--noheader", "--Format=NodeList,Partition,StateLong,Gres,GresUsed"]
    four_col = ["sinfo", "--Node", "--noheader", "--Format=NodeList,Partition,StateLong,Gres"]
    calls = []

    def fake_run(args, capture_output, text, timeout):
        calls.append(args)
        if args == ["sinfo", "--json"]:
            return subprocess.CompletedProcess(args=args, returncode=1, stdout="", stderr="")
        if args == five_col:
            # Mimic the actual SLURM error message that triggers fallback.
            return subprocess.CompletedProcess(
                args=args,
                returncode=1,
                stdout="",
                stderr="sinfo: error: Invalid job format specification: GresUsed",
            )
        assert args == four_col
        return subprocess.CompletedProcess(
            args=args,
            returncode=0,
            stdout="gpu-01 gpu-a100 idle gpu:a100:4\n",
            stderr="",
        )

    monkeypatch.setattr("control_plane.core.backends.slurm.subprocess.run", fake_run)

    backend.list_nodes()
    backend.list_nodes()
    backend.list_nodes()

    five_col_attempts = [c for c in calls if c == five_col]
    four_col_attempts = [c for c in calls if c == four_col]
    assert len(five_col_attempts) == 1, "5-column probe should run only on the first call"
    assert len(four_col_attempts) == 3, "4-column fallback should run every list_nodes() call"


def test_list_nodes_json_uses_tres_when_gres_is_empty(monkeypatch):
    backend = SlurmBackend()

    def fake_run(args, capture_output, text, timeout):
        assert args == ["sinfo", "--json"]
        return subprocess.CompletedProcess(
            args=args,
            returncode=0,
            stdout=(
                '{"nodes":[{"name":"gpu-01","gres":"","tres":"cpu=32,mem=257000M,gres/gpu=4",'
                '"partitions":["gpu-a100"],"state":"idle"}]}'
            ),
            stderr="",
        )

    monkeypatch.setattr("control_plane.core.backends.slurm.subprocess.run", fake_run)
    nodes = backend.list_nodes()

    assert len(nodes) == 1
    assert nodes[0].node_id == "gpu-01"
    assert len(nodes[0].gpus) == 4


def test_list_nodes_json_tracks_gpu_usage_from_gres_used(monkeypatch):
    backend = SlurmBackend()

    def fake_run(args, capture_output, text, timeout):
        assert args == ["sinfo", "--json"]
        return subprocess.CompletedProcess(
            args=args,
            returncode=0,
            stdout=(
                '{"nodes":[{"name":"gpu-01","gres":"gpu:a100:4","gres_used":"gpu:a100:3",'
                '"partitions":["gpu-a100"],"state":"mixed"}]}'
            ),
            stderr="",
        )

    monkeypatch.setattr("control_plane.core.backends.slurm.subprocess.run", fake_run)
    nodes = backend.list_nodes()

    assert len(nodes) == 1
    assert nodes[0].labels["gpu_total"] == "4"
    assert nodes[0].labels["gpu_used"] == "3"
    assert nodes[0].labels["gpu_available"] == "1"


def test_cancel_uses_scancel(monkeypatch):
    backend = SlurmBackend()
    monkeypatch.setattr("control_plane.core.backends.slurm.get_backend_ref", lambda job_id: "777")

    def fake_run(args, capture_output, text, timeout):
        assert args == ["scancel", "777"]
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")

    monkeypatch.setattr("control_plane.core.backends.slurm.subprocess.run", fake_run)
    assert backend.cancel("job-1") is True


def test_parse_slurm_time_accepts_epoch_seconds():
    backend = SlurmBackend()
    parsed = backend._parse_slurm_time("1710374400")
    assert parsed == 1710374400.0


def test_parse_slurm_time_rejects_short_numeric_values():
    backend = SlurmBackend()
    assert backend._parse_slurm_time("2026") is None


def test_generate_batch_script_captures_exit_code_even_on_failure():
    backend = SlurmBackend()
    spec = JobSpec(job_id="job-1", project="default", image="", cmd=["false"], gpus=1)
    script = backend._generate_batch_script(spec, node_hint=None)
    lines = script.splitlines()
    idx_set_plus_e = lines.index("set +e")
    idx_cmd = lines.index("false")
    idx_capture = lines.index("EXIT_CODE=$?")
    idx_set_e = lines.index("set -e")
    assert idx_set_plus_e < idx_cmd < idx_capture < idx_set_e


def test_poll_status_batch_uses_single_sacct_call(monkeypatch):
    backend = SlurmBackend()
    calls = []

    def fake_run(args, capture_output, text, timeout):
        calls.append(args)
        return subprocess.CompletedProcess(
            args=args,
            returncode=0,
            stdout=(
                "100.batch|FAILED|1:0|2026-03-13T01:00:00|2026-03-13T01:01:00|gpu-01\n"
                "100|COMPLETED|0:0|2026-03-13T01:00:00|2026-03-13T01:05:00|gpu-01\n"
                "101|RUNNING|0:0|2026-03-13T01:06:00|Unknown|gpu-02\n"
            ),
            stderr="",
        )

    monkeypatch.setattr("control_plane.core.backends.slurm.subprocess.run", fake_run)

    rows = [
        {"job_id": "job-a", "status": "PLACED", "backend_ref": "100"},
        {"job_id": "job-b", "status": "RUNNING", "backend_ref": "101"},
    ]
    status_map = backend._poll_status_batch(rows)

    assert len(calls) == 1
    assert calls[0][0] == "sacct"
    assert "100,101" in calls[0]
    assert status_map["job-a"].state == JobState.DONE
    assert status_map["job-b"].state == JobState.RUNNING
