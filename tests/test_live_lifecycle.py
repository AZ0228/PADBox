"""The detached child must outlive the startup command and stop cleanly."""

import os
import socket
import subprocess
import sys

import pytest

from padbox.live.lifecycle import home_id, probe, start_background, stop_background


def available_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def test_background_server_survives_launcher_and_reuses_port(tmp_path):
    home = tmp_path / "collection"
    port = available_port()
    command = [
        sys.executable,
        "-m",
        "padbox.cli",
        "--home",
        str(home),
        "live",
        "--background",
        "--port",
        str(port),
    ]
    try:
        # Launch from another short-lived process, matching the chat/terminal startup path.
        result = subprocess.run(command, capture_output=True, text=True, timeout=20)
        assert result.returncode == 0, result.stderr
        assert "Background server ready" in result.stdout
        ready = probe(port)
        assert ready and ready["pid"] != os.getpid()
        assert ready["home_id"] == home_id(home)
        assert (home / f"live-server-{port}.pid").read_text() == str(ready["pid"])
        second = start_background(home, port)
        assert second["pid"] == ready["pid"]
        other = tmp_path / "other"
        other.mkdir()
        with pytest.raises(ValueError, match="another PADBox collection"):
            start_background(other, port)
        with pytest.raises(ValueError, match="another collection"):
            stop_background(other, port)
    finally:
        stop_background(home, port)
    assert probe(port) is None
    assert not (home / f"live-server-{port}.pid").exists()


def test_background_preserves_custom_layout_on_restart(tmp_path):
    home = tmp_path / "collection"
    home.mkdir()
    port = available_port()
    layout = tmp_path / "layout.json"
    layout.write_text('{"roi":[0.1,0.2,0.9,0.8],"columns":5}')
    try:
        start_background(home, port, layout)
        assert (home / "live-layout.json").read_text().find("0.2") >= 0
        stop_background(home, port)
        start_background(home, port)
        assert (home / "live-layout.json").read_text().find("0.2") >= 0
    finally:
        stop_background(home, port)


def test_default_server_stays_in_foreground_and_ctrl_c_pauses_sessions(tmp_path):
    import json
    import re
    import signal
    import time
    import urllib.request
    from contextlib import closing

    from padbox.database.store import Store

    home = tmp_path / "foreground-collection"
    port = available_port()
    command = [sys.executable, "-m", "padbox.cli", "--home", str(home), "live", "--port", str(port)]
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        for _ in range(100):
            if probe(port):
                break
            assert process.poll() is None
            time.sleep(0.05)
        ready = probe(port)
        assert ready and ready["pid"] == process.pid
        assert process.poll() is None  # The launched command owns the server and remains running.
        origin = f"http://127.0.0.1:{port}"
        with urllib.request.urlopen(origin, timeout=3) as response:
            token = re.search(
                r'name="padbox-token" content="([^"]+)"', response.read().decode()
            ).group(1)

        def post(path, data):
            req = urllib.request.Request(
                origin + path,
                data=json.dumps(data).encode(),
                headers={"Content-Type": "application/json", "X-PADBox-Token": token},
            )
            with urllib.request.urlopen(req, timeout=3) as response:
                return json.load(response)

        session = post("/api/sessions", {"layout": {}})
        post(f"/api/sessions/{session['id']}/control", {"action": "resume"})
        process.send_signal(signal.SIGINT)
        output, error = process.communicate(timeout=5)
        assert process.returncode == 0, error
        assert "Press Ctrl+C to stop" in output
        assert "Live sessions saved and paused" in output
        assert probe(port) is None
        assert not (home / f"live-server-{port}.pid").exists()
        with closing(Store(home).connect()) as db:
            row = db.execute("SELECT status FROM recording WHERE id=?", (session["id"],)).fetchone()
            assert row["status"] == "paused"
    finally:
        if process.poll() is None:
            process.terminate()
            process.communicate(timeout=5)
