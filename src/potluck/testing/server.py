"""Hermetic real ``potluck serve`` subprocess helpers (bench + budget tests).

The serve cold-start bench (#141) measures a *process* — interpreter start,
imports, DB open, uvicorn bind — so it must spawn the real server, not build
an in-process app.  These helpers make that spawn hermetic: ``POTLUCK_*`` is
scrubbed from the environment and ``XDG_CONFIG_HOME`` is pinned inside the
workdir, so a developer's real database or ``config.toml`` can never leak in.
"""

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx


def free_port() -> int:
    """An OS-assigned free TCP port on localhost."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def spawn_serve(
    workdir: Path, port: int, *, web_dist: Path | None = None
) -> subprocess.Popen[bytes]:
    """Start ``potluck serve`` on *port* over a private DB inside *workdir*.

    Without *web_dist* the server runs SPA-less (``web_dist`` pointed at a
    nonexistent directory — hermetic even when the repo has a real build
    lying around).  The caller owns the process: kill + wait when done.
    """
    env = {key: value for key, value in os.environ.items() if not key.startswith("POTLUCK_")}
    env["POTLUCK_DB_PATH"] = str(workdir / "serve.db")
    env["POTLUCK_WEB_DIST"] = str(web_dist if web_dist is not None else workdir / "no-spa")
    env["XDG_CONFIG_HOME"] = str(workdir / "xdg-config")
    return subprocess.Popen(
        [sys.executable, "-m", "potluck", "serve", "--no-browser", "--port", str(port)],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def wait_for_health(port: int, proc: subprocess.Popen[bytes], timeout: float = 30.0) -> None:
    """Block until ``GET /api/health`` returns 200 (tight 20 ms poll).

    Raises if *proc* exits first or *timeout* passes — a hung scenario must
    fail loudly, never wedge the bench runner.
    """
    deadline = time.monotonic() + timeout
    url = f"http://127.0.0.1:{port}/api/health"
    while time.monotonic() < deadline:
        try:
            if httpx.get(url, timeout=1.0).status_code == 200:
                return
        except httpx.HTTPError:
            pass
        if proc.poll() is not None:
            raise RuntimeError(f"potluck serve exited early with code {proc.returncode}")
        time.sleep(0.02)
    raise RuntimeError(f"potluck serve not healthy within {timeout}s")
