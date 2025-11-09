import typer
import requests
import os

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

if __name__ == "__main__":
    app()
