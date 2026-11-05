"""Detached local server startup with readiness checks and scoped shutdown."""

import hashlib
import json
import os
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path


def home_id(home: Path) -> str:
    return hashlib.sha256(str(home.resolve()).encode()).hexdigest()[:16]


def probe(port: int) -> dict | None:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(f"http://127.0.0.1:{port}/health", timeout=0.4) as response:
            data = json.load(response)
            return data if data.get("service") == "padbox-live" else None
    except (OSError, ValueError, urllib.error.URLError):
        return None


def start_background(home: Path, port: int, layout: Path | None = None) -> dict:
    home = home.resolve()
    existing = probe(port)
    if existing:
        if existing.get("home_id") != home_id(home):
            raise ValueError(f"Port {port} serves another PADBox collection; choose another port")
        return existing
    command = [sys.executable, "-m", "padbox.cli", "--home", str(home), "live", "--port", str(port)]
    if layout:
        command += ["--layout", str(layout.resolve())]
    log = home / f"live-server-{port}.log"
    with log.open("ab") as output:
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=output,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            cwd=home,
        )
    for _ in range(100):
        if process.poll() is not None:
            raise ValueError(f"Live server exited during startup. Details: {log}")
        ready = probe(port)
        if ready and ready.get("pid") == process.pid:
            return ready
        time.sleep(0.1)
    # Do not leave an unready process behind on a failed launch.
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)
    raise ValueError(f"Live server did not become ready. Details: {log}")


def stop_background(home: Path, port: int) -> bool:
    ready = probe(port)
    if ready is None:
        return False
    if ready.get("home_id") != home_id(home):
        raise ValueError("The server on that port belongs to another collection")
    path = home / f"live-server-{port}.pid"
    if not path.exists() or int(path.read_text()) != ready.get("pid"):
        raise ValueError("Cannot verify this server's process ID; stop it from its terminal")
    os.kill(ready["pid"], signal.SIGTERM)
    for _ in range(50):
        if probe(port) is None:
            return True
        time.sleep(0.1)
    raise ValueError("Live server has not stopped yet; check its log")
