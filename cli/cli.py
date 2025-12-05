import os
import time

import requests
import typer

app = typer.Typer(help="CUDA Overlay CLI (Milestone 1)")

def api_base() -> str:
    return os.getenv("OVERLAY_API", "http://localhost:8000")

@app.command()
def health():
    """Ping control plane health."""
    r = requests.get(f"{api_base()}/health", timeout=5)
    typer.echo(r.json())

@app.command()
def version():
    """Get control plane version."""
    r = requests.get(f"{api_base()}/version", timeout=5)
    typer.echo(r.json())

@app.command()
def watch(job_id: str, interval: float = 1.0):
    """
    Poll a job's status until it reaches a terminal state.
    """
    url = f"{api_base()}/api/jobs/{job_id}"
    terminal = {"DONE", "FAILED", "CANCELLED"}
    while True:
        r = requests.get(url, timeout=5)
        if r.status_code != 200:
            typer.echo(f"[{job_id}] error {r.status_code}: {r.text}")
            break
        data = r.json()
        state = data.get("state")
        node = data.get("node_id")
        exit_code = data.get("exit_code")
        typer.echo(f"[{job_id}] state={state} node={node} exit={exit_code}")
        if state in terminal:
            break
        time.sleep(interval)

if __name__ == "__main__":
    app()
