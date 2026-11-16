"""Native Windows smoke of the actual packaged EXEs after relocation, without Python on PATH."""

import base64
import json
import os
import subprocess
import tempfile
import time
from pathlib import Path
from zipfile import ZipFile

import httpx
import pefile


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
            with pefile.PE(str(root / executable), fast_load=True) as image:
                assert image.OPTIONAL_HEADER.Subsystem == (2 if executable == "Taverna.exe" else 3)
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
                    fixtures = Path(__file__).resolve().parents[1] / "tests/fixtures/media"
                    for filename, category in [
                        ("background.png", "background"),
                        ("expression.webp", "expression"),
                        ("tone.wav", "music"),
                        ("tone.mp3", "music"),
                        ("tone.ogg", "ambient"),
                        ("tone.flac", "music"),
                    ]:
                        raw = (fixtures / filename).read_bytes()
                        imported = client.post(
                            base + "/api/v1/media/import",
                            params={"name": filename, "category": category, "confirmed": True},
                            content=raw,
                            headers={"X-Taverna-CSRF": csrf, "Content-Type": "application/octet-stream"},
                            timeout=10,
                        )
                        assert imported.status_code == 200, imported.text
                        asset_id = imported.json()["id"]
                        served = client.get(base + f"/api/v1/media/{asset_id}/file", timeout=10)
                        assert served.status_code == 200 and served.content == raw
                        if category in ("background", "expression"):
                            thumb = client.get(base + f"/api/v1/media/{asset_id}/thumbnail", timeout=10)
                            assert thumb.status_code == 200 and thumb.content.startswith(b"\x89PNG\r\n\x1a\n")
                    materials = fixtures.parent / "materials"
                    for filename in ("plain.pdf", "reference.docx", "reference.txt"):
                        raw = (materials / filename).read_bytes()
                        imported = client.post(
                            base + "/api/v1/library/import",
                            params={"name": filename, "filename": filename, "confirmed": True},
                            content=raw,
                            headers={"X-Taverna-CSRF": csrf},
                            timeout=10,
                        )
                        assert imported.status_code == 200, imported.text
                        served = client.get(
                            base + "/api/v1/library/" + imported.json()["id"] + "/file", timeout=10
                        )
                        assert served.status_code == 200 and served.content == raw
                    from uuid import uuid4

                    mid = str(uuid4())
                    book = client.put(
                        base + "/api/v1/manuscripts/" + mid,
                        json={
                            "expected_revision": 0,
                            "reason": "Frozen smoke",
                            "payload": {
                                "title": "Кириллица",
                                "chapters": [{"title": "Глава", "text": "Проверка portable"}],
                            },
                        },
                        headers={"X-Taverna-CSRF": csrf},
                        timeout=10,
                    )
                    assert book.status_code == 200, book.text
                    for fmt in ("txt", "markdown", "html", "epub"):
                        exported = client.get(
                            base + f"/api/v1/manuscripts/{mid}/export",
                            params={"format": fmt, "expected_revision": 1},
                            timeout=10,
                        )
                        assert (
                            exported.status_code == 200
                            and exported.content
                            and "attachment" in exported.headers["content-disposition"]
                        )
                    import wasmtime

                    for wat, success in [
                        (
                            '(module (memory (export "memory") 1) (data (i32.const 0) "{\\22actions\\22:[]}") (func (export "alloc") (param i32) (result i32) i32.const 4096) (func (export "run") (param i32 i32) (result i64) i64.const 14))',
                            True,
                        ),
                        ('(module (import "wasi_snapshot_preview1" "fd_write" (func)))', False),
                    ]:
                        envelope = {
                            "runtime": "wasm",
                            "code": base64.b64encode(wasmtime.wat2wasm(wat)).decode(),
                            "input": {},
                            "fuel": 10000,
                        }
                        host = subprocess.run(
                            [str(root / "Taverna-PluginHost.exe")],
                            input=json.dumps(envelope).encode(),
                            stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE,
                            cwd=root,
                            env=env,
                            timeout=30,
                            check=True,
                        )
                        result = json.loads(host.stdout)
                        assert result["ok"] is success, result
                        if success:
                            assert result["result"] == {"actions": []}
                        else:
                            assert result["code"] == "plugin_import_denied"
                    assert not list(root.rglob("*.sqlite*"))
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
                print(
                    executable,
                    "PASS: relocated portable, restricted PATH, HTTP/session/storage/media/documents/book exports/isolated WASM host/shutdown",
                )
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait()


if __name__ == "__main__":
    main()
