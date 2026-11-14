import json
import subprocess
import sys
import time

import httpx
import pytest
from taverna.launcher import bind


def test_real_process_restart_single_instance_and_shutdown(tmp_path):
    static = tmp_path / "app/web/dist"
    static.mkdir(parents=True)
    (static / "index.html").write_text("<html lang='ru'>Таверна</html>", encoding="utf-8")
    command = [sys.executable, "-m", "taverna.launcher", "--root", str(tmp_path), "--no-browser"]
    process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        info = tmp_path / "data/config/instance.json"
        port = 0
        with httpx.Client(trust_env=False, timeout=1) as c:
            for _ in range(200):
                try:
                    port = json.loads(info.read_text())["port"]
                    if c.get(f"http://127.0.0.1:{port}/api/v1/health").status_code == 200:
                        break
                except (OSError, ValueError, httpx.HTTPError):
                    pass
                time.sleep(0.05)
            else:
                pytest.fail("Сервер не запустился")
            second = subprocess.run(command, capture_output=True, timeout=10)
            assert second.returncode == 0
            assert process.poll() is None
            key = (tmp_path / "data/config/launcher.key").read_text()
            base = f"http://127.0.0.1:{port}"
            assert c.post(base + "/api/v1/launcher/open").status_code == 403
            url = c.post(base + "/api/v1/launcher/open", headers={"X-Taverna-Launcher": key}).json()["url"]
            token = url.split("#launch=")[1]
            csrf = c.post(base + "/api/v1/session/bootstrap", json={"token": token}).json()["csrf"]
            assert c.post(base + "/api/v1/system/shutdown", headers={"X-Taverna-CSRF": csrf}).status_code == 200
            process.wait(timeout=10)
            assert process.returncode == 0
            assert not info.exists()
            assert not (tmp_path / "data/config/launcher.key").exists()
            assert key not in (tmp_path / "logs/taverna.log").read_text()
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()


def test_port_collision_does_not_share_listener():
    with bind(0) as first:
        with pytest.raises(OSError):
            with bind(first.getsockname()[1]):
                pass
