"""Native Windows smoke of the actual packaged EXEs after relocation, without Python on PATH."""

import base64
import hashlib
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
    with (
        tempfile.TemporaryDirectory(prefix="Таверна перенос ") as temp,
        tempfile.TemporaryDirectory(prefix="Таверна внешние ") as outside,
    ):
        external = Path(outside) / "Материал с пробелами.txt"
        external.write_text("Прочитано только по отдельному разрешению", encoding="utf-8")
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
                    # This timeout is enforced by the frozen host itself, not
                    # subprocess.run; the parent remains alive and gives it 15s.
                    deadline_envelope = {
                        "runtime": "trusted_python",
                        "trusted_full_access": True,
                        "input": {},
                        "timeout_seconds": 0.5,
                        "code": base64.b64encode(b"import time\ndef run(request): time.sleep(60)").decode(),
                    }
                    expired = subprocess.run(
                        [str(root / "Taverna-PluginHost.exe")],
                        input=json.dumps(deadline_envelope).encode(),
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        cwd=root,
                        env=env,
                        timeout=15,
                    )
                    assert expired.returncode == 124, "Frozen host did not enforce its own deadline"
                    # Command kernel trial only: no plugin API bypass or claim that
                    # owner confirmation/effect ledger have already been integrated.
                    command_exe = Path(os.environ["SystemRoot"]) / "System32" / "whoami.exe"
                    command_trial = {
                        "runtime": "trusted_python",
                        "trusted_full_access": True,
                        "timeout_seconds": 10,
                        "code": base64.b64encode(b"from taverna.plugin_command_worker import run\n").decode(),
                        "input": {
                            "executable": str(command_exe),
                            "sha256": hashlib.sha256(command_exe.read_bytes()).hexdigest(),
                            "argv": [],
                            "timeout_seconds": 3,
                        },
                    }
                    command_host = subprocess.run(
                        [str(root / "Taverna-PluginHost.exe")],
                        input=json.dumps(command_trial).encode(),
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        cwd=root,
                        env=env,
                        timeout=20,
                        check=True,
                    )
                    command_result = json.loads(command_host.stdout)
                    # Do not print the account name or command output in CI logs.
                    assert command_result["ok"] is True
                    assert command_result["result"]["state"] == "exited"
                    assert command_result["result"]["return_code"] == 0
                    assert command_result["result"]["stdout_base64"]
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
                    pid, template_id, note_id, generator_id, names_id = (str(uuid4()) for _ in range(5))
                    template = client.put(
                        base + f"/api/v1/tools/{template_id}",
                        json={
                            "expected_revision": 0,
                            "payload": {
                                "kind": "template",
                                "title": "Native template",
                                "template": {
                                    "action": "note",
                                    "text": "Герой: {{name}}",
                                    "variables": ["name"],
                                    "grants": ["notes.write"],
                                },
                            },
                        },
                        headers={"X-Taverna-CSRF": csrf},
                        timeout=10,
                    )
                    assert template.status_code == 200, template.text
                    generator = client.put(
                        base + f"/api/v1/tools/{generator_id}",
                        json={
                            "expected_revision": 0,
                            "payload": {
                                "kind": "name_generator",
                                "title": "Native names",
                                "generator": {
                                    "prefixes": ["Аль"],
                                    "middles": [""],
                                    "suffixes": ["ма"],
                                },
                            },
                        },
                        headers={"X-Taverna-CSRF": csrf},
                        timeout=10,
                    )
                    assert generator.status_code == 200, generator.text
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
                                    ]
                                    + [
                                        {
                                            "capability": "filesystem.read",
                                            "reason": "Native selected-file smoke",
                                            "scopes": [outside],
                                        },
                                        {
                                            "capability": "tools.run",
                                            "reason": "Native selected template",
                                            "scopes": [template_id, generator_id],
                                        },
                                        {
                                            "capability": "notes.write",
                                            "reason": "Native template note",
                                            "scopes": ["new"],
                                        },
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
                                    },
                                    {
                                        "type": "tool.template",
                                        "target": template_id,
                                        "expected_revision": 1,
                                        "result_id": note_id,
                                        "inputs": {"name": "Альма"},
                                    },
                                    {
                                        "type": "tool.names",
                                        "target": generator_id,
                                        "expected_revision": 1,
                                        "result_id": names_id,
                                        "seed": "native",
                                        "count": 3,
                                    },
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
                            ]
                            + [
                                {"capability": "filesystem.read", "scopes": [outside]},
                                {"capability": "tools.run", "scopes": [template_id, generator_id]},
                                {"capability": "notes.write", "scopes": ["new"]},
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
                    file_preview = client.post(
                        base + f"/api/v1/plugins/{pid}/files/preview",
                        json={
                            "plugin_revision": captured["revision"],
                            "catalog_revision": catalog_revision,
                            "permission_epoch": captured["payload"]["permission_epoch"],
                            "package_sha256": checked["sha256"],
                            "scope": outside,
                            "name": external.name,
                        },
                        headers={"X-Taverna-CSRF": csrf},
                        timeout=10,
                    )
                    assert file_preview.status_code == 200, file_preview.text
                    selected = file_preview.json()
                    assert selected["text_preview"] == external.read_text(encoding="utf-8")
                    queued = client.post(
                        base + f"/api/v1/plugins/{pid}/run",
                        json={
                            "plugin_revision": captured["revision"],
                            "catalog_revision": catalog_revision,
                            "permission_epoch": captured["payload"]["permission_epoch"],
                            "package_sha256": checked["sha256"],
                            "files": [
                                {
                                    "scope": outside,
                                    "name": external.name,
                                    "sha256": selected["sha256"],
                                    "confirmed": True,
                                }
                            ],
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
                    pending = client.get(base + f"/api/v1/plugins/{pid}/previews", timeout=10).json()["items"]
                    saved = next(p for p in pending if p["id"] == proposal_id)
                    assert saved["payload"]["metadata"]["external_sources"][0]["sha256"] == selected["sha256"]
                    assert saved["payload"]["metadata"]["tool_previews"][0]["text"] == "Герой: Альма"
                    assert saved["payload"]["metadata"]["tool_previews"][1]["text"] == "Альма\nАльма\nАльма"
                    assert client.get(base + f"/api/v1/tools/{note_id}", timeout=10).status_code == 404
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
                    note = client.get(base + f"/api/v1/tools/{note_id}", timeout=10).json()
                    assert note["revision"] == 1 and note["payload"]["text"] == "Герой: Альма"
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
                    "PASS: relocated portable, restricted PATH, HTTP/session/storage/media/documents/book exports/isolated host/OS limits/Python helpers/plugin registry/queued WASM broker/selected external file/atomic receipt/shutdown",
                )
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait()


        # Exercise the actual *frozen* updater in CLI mode after both
        # instances have stopped. The original installation must survive.
        updater = root / "Taverna-Updater.exe"
        assert updater.is_file(), "Portable ZIP omitted the manual updater"
        upgraded = Path(outside) / "Обновлённая Таверна"
        old_exe_sha = hashlib.sha256((root / "Taverna.exe").read_bytes()).hexdigest()
        try:
            upgrade = subprocess.run(
                [
                    str(updater), "--source", str(root),
                    "--archive", str(archive.resolve()),
                    "--destination", str(upgraded),
                    "--sha256", hashlib.sha256(archive.read_bytes()).hexdigest(),
                ],
                cwd=root,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=90,
            )
        except subprocess.TimeoutExpired as exc:
            print("Updater phase trace:", (exc.stderr or b"")[-4000:].decode("utf-8", errors="replace"))
            raise
        assert upgrade.returncode == 0, (
            "Frozen manual updater failed: "
            + upgrade.stderr.decode("utf-8", errors="replace")[-3000:]
        )
        assert (upgraded / "Taverna.exe").is_file()
        assert (upgraded / "Taverna-PluginHost.exe").is_file()
        assert (upgraded / "app/web/dist/index.html").is_file()
        assert (upgraded / "data/config/store-manifest.json").is_file()
        assert (root / "Taverna.exe").is_file()
        assert hashlib.sha256((root / "Taverna.exe").read_bytes()).hexdigest() == old_exe_sha
        preserved_backups = list((root / "backups").glob("taverna-pre-update-*.zip"))
        assert preserved_backups
        restored = Path(outside) / "Восстановленные данные"
        recovery = subprocess.run(
            [
                str(updater),
                "--restore-archive", str(preserved_backups[0]),
                "--restore-destination", str(restored),
            ],
            cwd=root,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=120,
        )
        assert recovery.returncode == 0, "Frozen updater recovery mode failed"
        assert (restored / "data/config/store-manifest.json").is_file()
        assert (root / "data/config/store-manifest.json").is_file()
        print("Taverna-Updater.exe PASS: new-folder upgrade and non-destructive recovery")


if __name__ == "__main__":
    main()
