"""Local-IP plugin network effects: real HTTP, explicit consent, revocation, no replay."""

import asyncio
import base64
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from uuid import uuid4

import pytest
from taverna.core import AppError, Config
from taverna.main import create_app
from taverna.plugin_actions import NetworkRequest, PluginCommandDecision, PluginRun
from test_plugin_packages import package
from test_plugin_registry import configure, install


class LocalHandler(BaseHTTPRequestHandler):
    count = 0
    request_bodies = []

    def log_message(self, *_args):
        pass

    def do_GET(self):
        type(self).count += 1
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(b"one")

    def do_POST(self):
        type(self).count += 1
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n)
        type(self).request_bodies.append(json.loads(body))
        self.send_response(201)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"ok":true}')


@pytest.fixture
def local_http():
    LocalHandler.count = 0
    LocalHandler.request_bodies = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), LocalHandler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/effect"
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)


def install_network(tmp_path, url, *, granted=True, method="GET", payload=None):
    app = create_app(Config(tmp_path))
    service = app.state.plugins
    action = {
        "type": "network.request",
        "url": url,
        "method": method,
        "payload": payload,
        "timeout_seconds": 5,
    }
    plan = {"format": 1, "actions": [action], "message": "Запрос только после подтверждения"}
    archive = package(
        {
            "runtime": "declarative",
            "entry": "main.json",
            "requests": [
                {"capability": "network.request", "reason": "Точный локальный HTTP адрес", "scopes": [url]}
            ],
        },
        json.dumps(plan).encode("utf-8"),
    )
    row = install(service.registry, archive)
    row = configure(
        service.registry,
        row,
        grants=[{"capability": "network.request", "scopes": [url]}] if granted else [],
    )
    return service, row


async def finished(service, job):
    async with asyncio.timeout(15):
        while True:
            current = await service.queue.get(job["id"])
            if current["payload"]["state"] not in ("queued", "running"):
                return current
            await asyncio.sleep(0.02)


async def preview(service, row):
    body = PluginRun(
        catalog_revision=service.registry.list()["catalog_revision"],
        plugin_revision=row["revision"],
        permission_epoch=row["payload"]["permission_epoch"],
        package_sha256=row["payload"]["package_sha256"],
    )
    job = await service.enqueue(row["id"], body, str(uuid4()))
    outcome = await finished(service, job)
    if outcome["payload"]["state"] != "completed":
        return outcome
    pid = outcome["payload"]["result"]["proposal_id"]
    return service.store.get(service.proposal_key(pid))


def approve(preview):
    return PluginCommandDecision(
        expected_revision=preview["revision"],
        confirmed=True,
        full_access_warning_ack=True,
        fingerprint=preview["payload"]["metadata"]["external_network"]["fingerprint"],
    )


@pytest.mark.asyncio
async def test_explicit_local_post_request_once_only_and_no_implicit_json_effects(tmp_path, local_http):
    service, row = install_network(tmp_path, local_http, method="POST", payload={"action": "onlyonce"})
    try:
        item = await preview(service, row)
        assert item["payload"]["status"] == "pending"
        assert LocalHandler.count == 0
        with pytest.raises(AppError):
            service.decide(item["id"], item["revision"], True)
        with pytest.raises(AppError):
            await service.approve_external(item["id"], approve(item))  # command endpoint must not confirm network
        assert LocalHandler.count == 0
        request = approve(item)
        first = await service.approve_external(item["id"], request, action_type="network.request")
        second = await service.approve_external(item["id"], request, action_type="network.request")
        assert first["id"] == second["id"]
        final = await finished(service, first)
        assert final["payload"]["state"] == "completed", final["payload"]
        assert LocalHandler.count == 1
        assert LocalHandler.request_bodies == [{"action": "onlyonce"}]
        receipt = service.store.get(service.proposal_key(item["id"]))
        assert receipt["payload"]["status"] == "observed"
        assert receipt["payload"]["observed"]["status_code"] == 201
        assert base64.b64decode(receipt["payload"]["observed"]["body_base64"]) == b'{"ok":true}'
        third = await service.approve_external(item["id"], request, action_type="network.request")
        assert third["id"] == first["id"]
        assert LocalHandler.count == 1
    finally:
        await service.queue.close()


@pytest.mark.asyncio
async def test_grants_revoke_and_previews_are_checked_before_any_network_io(tmp_path, local_http):
    service, row = install_network(tmp_path, local_http, granted=False)
    try:
        result = await preview(service, row)
        assert result["payload"]["state"] == "failed"
        assert result["payload"]["error"]["code"] == "plugin_scope_denied"
        assert LocalHandler.count == 0
    finally:
        await service.queue.close()

    other, row = install_network(tmp_path / "other", local_http)
    try:
        item = await preview(other, row)
        row = configure(other.registry, row, grants=[])
        with pytest.raises(AppError):
            await other.approve_external(item["id"], approve(item), action_type="network.request")
        assert LocalHandler.count == 0
    finally:
        await other.queue.close()


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com:8080/path",
        "https://8.8.8.8:443/data",
        "http://127.0.0.1:80@evil.invalid/a",
        "http://127.0.0.1/path",
        "http://127.0.0.1:8080/x#fragment",
        "http://169.254.169.254:80/meta",
        "file:///etc/passwd",
        "http://0.0.0.0:4567/a",
    ],
)
def test_network_plan_rejects_public_dns_implicit_ports_and_unsafe_addresses(url):
    with pytest.raises(ValueError):
        NetworkRequest(type="network.request", url=url)


def test_only_bounded_json_post_or_empty_get():
    with pytest.raises(ValueError):
        NetworkRequest(type="network.request", url="http://127.0.0.1:1234/a", method="GET", payload={})
    with pytest.raises(ValueError):
        NetworkRequest(
            type="network.request",
            url="http://127.0.0.1:1234/a",
            method="POST", payload={"large": "x" * 20000},
        )
