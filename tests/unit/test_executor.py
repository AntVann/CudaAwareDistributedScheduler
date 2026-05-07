from agent.executor import run_job


def test_run_job_writes_stdout_and_stderr(tmp_path):
    stdout_path = tmp_path / "job.out"
    stderr_path = tmp_path / "job.err"

    result = run_job(
        ["sh", "-c", "echo hello; echo boom >&2"],
        stdout_path=str(stdout_path),
        stderr_path=str(stderr_path),
    )

    assert result.exit_code == 0
    assert stdout_path.read_text(encoding="utf-8") == "hello\n"
    assert stderr_path.read_text(encoding="utf-8") == "boom\n"


def test_run_job_writes_reason_when_executable_missing(tmp_path):
    stdout_path = tmp_path / "missing.out"
    stderr_path = tmp_path / "missing.err"

    result = run_job(
        ["definitely-not-a-real-command-123"],
        stdout_path=str(stdout_path),
        stderr_path=str(stderr_path),
    )

    assert result.exit_code == 127
    assert "Executable not found" in (result.reason or "")
    assert stdout_path.read_text(encoding="utf-8") == ""
    assert "Executable not found" in stderr_path.read_text(encoding="utf-8")
