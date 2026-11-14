"""Native Windows smoke of the actual packaged EXEs after relocation, without Python on PATH."""

import json
import os
import subprocess
import tempfile
import time
from pathlib import Path
from zipfile import ZipFile

import httpx


def main():
    if os.name != "nt":
        raise RuntimeError("Этот smoke требует Windows.")
    archive = Path("dist/Taverna-Internal-Windows-x64.zip")
    with tempfile.TemporaryDirectory(prefix="Таверна перенос ") as temp:
        root = Path(temp)
        with ZipFile(archive) as source:
            source.extractall(root)
        env = dict(os.environ)
        env.pop("PYTHONPATH", None)
        env.pop("PYTHONHOME", None)
        env["PATH"] = os.environ.get("SystemRoot", "C:\\Windows") + "\\System32"
        for executable in ("Taverna.exe", "Taverna-Diagnostics.exe"):
            process = subprocess.Popen([str(root / executable), "--no-browser"], cwd=root, env=env)
            try:
                info = root / "data/config/instance.json"
                with httpx.Client(trust_env=False, timeout=2) as client:
                    for _ in range(300):
                        try:
                            port = json.loads(info.read_text(encoding="utf-8"))["port"]
                            base = f"http://127.0.0.1:{port}"
                            if client.get(base + "/api/v1/health").status_code == 200:
                                break
                        except (OSError, ValueError, httpx.HTTPError):
                            pass
                        if process.poll() is not None:
                            raise RuntimeError(f"Упакованный {executable} завершился до готовности")
                        time.sleep(0.1)
                    else:
                        raise RuntimeError("Упакованный EXE не запустился")
                    key = (root / "data/config/launcher.key").read_text(encoding="utf-8")
                    link = client.post(
                        base + "/api/v1/launcher/open", headers={"X-Taverna-Launcher": key}
                    ).json()["url"]
                    csrf = client.post(
                        base + "/api/v1/session/bootstrap", json={"token": link.split("#launch=")[1]}
                    ).json()["csrf"]
                    assert client.get(base + "/").status_code == 200
                    assert client.get(base + "/api/v1/diagnostics/storage").json()["ok"]
                    assert (
                        client.post(
                            base + "/api/v1/system/shutdown", headers={"X-Taverna-CSRF": csrf}
                        ).status_code
                        == 200
                    )
                    process.wait(timeout=15)
                    assert process.returncode == 0
                    assert not info.exists()
                print(executable, "PASS: relocated portable, restricted PATH, HTTP/session/storage/shutdown")
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait()


if __name__ == "__main__":
    main()
