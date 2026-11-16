"""Native Windows smoke of the actual packaged EXEs after relocation, without Python on PATH."""

import base64
import io
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
                    folder = root / "diagnostics" / "Python с пробелами"
                    folder.mkdir(parents=True, exist_ok=True)
                    (folder / "helper.py").write_text('value="проверено"', encoding="utf-8")
                    entry = folder / "main.py"
                    code = 'import math, statistics, httpx\nimport helper\ndef run(request): return {"actions": [], "value":helper.value,"median":statistics.median([1,2,3]),"sqrt":math.isqrt(81),"httpx":httpx.__version__}'
                    entry.write_text(code, encoding="utf-8")
                    envelope = {
                        "runtime": "trusted_python",
                        "trusted_full_access": True,
                        "entry_path": str(entry),
                        "input": {},
                        "code": base64.b64encode(code.encode()).decode(),
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
                    assert result["ok"] is True, result
                    assert (
                        result["result"]["value"] == "проверено"
                        and result["result"]["median"] == 2
                        and result["result"]["sqrt"] == 9
                    )
                    assert (
                        result["host_limits"]["process_count"] == 8
                        and result["host_limits"]["process_commit_bytes"] == 512 * 1024 * 1024
                    )
                    pid = str(uuid4())
                    out = io.BytesIO()
                    with ZipFile(out, "w") as package:
                        package.writestr(
                            "manifest.json",
                            json.dumps(
                                {
                                    "id": pid,
                                    "name": "Внутренний smoke",
                                    "version": "1.0.0",
                                    "runtime": "wasm",
                                    "entry": "main.wasm",
                                    "requests": [
                                        {
                                            "capability": cap,
                                            "reason": "Native broker smoke",
                                            "scopes": ["smoke"],
                                        }
                                        for cap in ("own.read", "own.write")
                                    ],
                                }
                            ),
                        )
                        plan_bytes = json.dumps(
                            {
                                "format": 1,
                                "actions": [
                                    {
                                        "type": "own.set",
                                        "target": "smoke",
                                        "expected_revision": 0,
                                        "value": {"portable": True},
                                    }
                                ],
                                "message": "Native broker smoke",
                            }
                        ).encode()
                        wat_data = "".join("\\" + format(b, "02x") for b in plan_bytes)
                        module = wasmtime.wat2wasm(
                            f'(module (memory (export "memory") 1 256) (data (i32.const 0) "{wat_data}") (func (export "alloc") (param i32) (result i32) i32.const 4096) (func (export "run") (param i32 i32) (result i64) i64.const {len(plan_bytes)}))'
                        )
                        package.writestr("main.wasm", module)
                    raw = out.getvalue()
                    preview = client.post(
                        base + "/api/v1/plugins/import-preview",
                        content=raw,
                        headers={"X-Taverna-CSRF": csrf},
                        timeout=10,
                    )
                    assert preview.status_code == 200, preview.text
                    checked = preview.json()
                    installed = client.post(
                        base + "/api/v1/plugins/import",
                        content=raw,
                        headers={"X-Taverna-CSRF": csrf},
                        params={
                            "expected_revision": checked["expected_revision"],
                            "catalog_revision": checked["catalog_revision"],
                            "approved_sha256": checked["sha256"],
                            "confirmed": True,
                        },
                        timeout=10,
                    )
                    assert installed.status_code == 200 and installed.json()["payload"]["enabled"] is False, (
                        installed.text
                    )
                    catalog = client.get(base + "/api/v1/plugins", timeout=10).json()
                    enabled = client.put(
                        base + "/api/v1/plugins/" + pid,
                        json={
                            "expected_revision": 1,
                            "catalog_revision": catalog["catalog_revision"],
                            "approved_sha256": checked["sha256"],
                            "enabled": True,
                            "grants": [
                                {"capability": cap, "scopes": ["smoke"]} for cap in ("own.read", "own.write")
                            ],
                            "trusted_full_access_ack": False,
                            "confirmed": True,
                        },
                        headers={"X-Taverna-CSRF": csrf},
                        timeout=10,
                    )
                    assert enabled.status_code == 200 and enabled.json()["payload"]["enabled"] is True, (
                        enabled.text
                    )
                    captured = enabled.json()
                    catalog_revision = client.get(base + "/api/v1/plugins", timeout=10).json()[
                        "catalog_revision"
                    ]
                    queued = client.post(
                        base + f"/api/v1/plugins/{pid}/run",
                        json={
                            "plugin_revision": captured["revision"],
                            "catalog_revision": catalog_revision,
                            "permission_epoch": captured["payload"]["permission_epoch"],
                            "package_sha256": checked["sha256"],
                        },
                        headers={
                            "X-Taverna-CSRF": csrf,
                            "Idempotency-Key": "native-plugin-broker-" + executable,
                        },
                        timeout=10,
                    )
                    assert queued.status_code == 200, queued.text
                    jid = queued.json()["id"]
                    deadline = time.monotonic() + 30
                    while True:
                        job = client.get(base + f"/api/v1/generations/{jid}", timeout=10).json()
                        if job["state"] not in ("queued", "running"):
                            break
                        if time.monotonic() > deadline:
                            raise AssertionError("Native plugin job did not finish")
                        time.sleep(0.05)
                    assert job["state"] == "completed", job
                    data_url = base + f"/api/v1/plugins/{pid}/data/smoke"
                    assert client.get(data_url, timeout=10).json()["revision"] == 0
                    proposal_id = job["result"]["proposal_id"]
                    for _attempt in range(2):
                        applied = client.post(
                            base + f"/api/v1/plugins/previews/{proposal_id}/accept",
                            json={"expected_revision": 1, "confirmed": True},
                            headers={"X-Taverna-CSRF": csrf},
                            timeout=10,
                        )
                        assert (
                            applied.status_code == 200 and applied.json()["payload"]["status"] == "applied"
                        ), applied.text
                    own = client.get(data_url, timeout=10).json()
                    assert own["revision"] == 1 and own["value"] == {"portable": True}, own
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
                    "PASS: relocated portable, restricted PATH, HTTP/session/storage/media/documents/book exports/isolated host/OS limits/Python helpers/plugin registry/queued WASM broker/atomic receipt/shutdown",
                )
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait()


if __name__ == "__main__":
    main()
