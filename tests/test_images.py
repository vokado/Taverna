import asyncio
import io
import json
import threading
from uuid import uuid4

import httpx
import pytest
from PIL import Image
from taverna.core import AppError
from taverna.image_engine import ComfyAdapter
from taverna.image_models import ImageRequest, ImageSettings
from taverna.images import ImageService
from taverna.llm import LmStudioAdapter
from taverna.media import MediaService
from taverna.worlds import WorldService
from test_chat import setup_chat


def png(width=512, height=512):
    out = io.BytesIO()
    Image.new("RGB", (width, height), "navy").save(out, format="PNG")
    return out.getvalue()


class Engine:
    def __init__(self, mode="completed"):
        self.mode = mode
        self.requests = []
        self.jobs = {}
        self.submitting = asyncio.Event()
        self.ack = asyncio.Event()
        self.on_status = None

    async def handle(self, request):
        path = request.url.path
        method = request.method
        self.requests.append((method, path, request.content))
        if path.startswith("/object_info/"):
            node = path.split("/")[-1]
            required = {}
            if node == "CheckpointLoaderSimple":
                required = {"ckpt_name": [["local.safetensors"]]}
            if node == "KSampler":
                required = {"sampler_name": [["euler"]], "scheduler": [["normal"]]}
            return httpx.Response(200, json={node: {"input": {"required": required}}})
        if path == "/system_stats":
            return httpx.Response(
                200,
                json={
                    "system": {"comfyui_version": "test-contract"},
                    "devices": [
                        {
                            "name": "Fixture GPU",
                            "type": "cuda",
                            "vram_total": 12 * 1024**3,
                            "vram_free": 3 * 1024**3,
                        }
                    ],
                },
            )
        if path == "/api/jobs":
            return httpx.Response(
                404 if self.mode == "old_api" else 200, json={"jobs": [], "pagination": {"total": 0}}
            )
        if path == "/prompt":
            body = json.loads(request.content)
            pid = body["prompt_id"]
            assert body["client_id"] == "taverna-" + pid
            assert set(n["class_type"] for n in body["prompt"].values()) == {
                "CheckpointLoaderSimple",
                "CLIPTextEncode",
                "EmptyLatentImage",
                "KSampler",
                "VAEDecode",
                "SaveImage",
            }
            assert body["prompt"]["7"]["inputs"]["filename_prefix"] == "Taverna/" + pid
            if self.mode == "rejected":
                return httpx.Response(400, json={"error": "invalid prompt"})
            self.jobs[pid] = "in_progress"
            self.submitting.set()
            if self.mode == "blocked_ack":
                await self.ack.wait()
            if self.mode == "unknown_submit":
                raise httpx.ReadTimeout("test lost acknowledgement")
            return httpx.Response(200, json={"prompt_id": pid, "number": 1, "node_errors": {}})
        if path.startswith("/api/jobs/"):
            parts = path.split("/")
            pid = parts[3]
            if method == "POST" and parts[-1] == "cancel":
                if pid not in self.jobs:
                    return httpx.Response(200, json={"cancelled": False})
                if self.mode == "pending":
                    del self.jobs[pid]
                else:
                    self.jobs[pid] = "cancelled"
                return httpx.Response(200, json={"cancelled": True})
            if self.mode == "offline":
                raise httpx.ConnectError("test disconnected")
            if self.on_status:
                self.on_status()
                self.on_status = None
            state = self.jobs.get(pid)
            if state is None:
                return httpx.Response(404, json={"error": "missing"})
            if state != "cancelled":
                state = (
                    "pending"
                    if self.mode == "pending"
                    else "in_progress"
                    if self.mode in ("running", "blocked_ack")
                    else "failed"
                    if self.mode == "oom"
                    else "completed"
                )
            self.jobs[pid] = state
            result = {"id": pid, "status": state}
            if self.mode == "oom":
                result["execution_error"] = {
                    "exception_type": "torch.OutOfMemoryError",
                    "exception_message": "CUDA out of memory",
                }
            return httpx.Response(200, json=result)
        if path.startswith("/history/"):
            pid = path.split("/")[-1]
            name = "../secret.png" if self.mode == "bad_output" else pid + "_00001_.png"
            return httpx.Response(
                200,
                json={
                    pid: {
                        "outputs": {
                            "7": {"images": [{"filename": name, "subfolder": "Taverna", "type": "output"}]}
                        }
                    }
                },
            )
        if path == "/view":
            return httpx.Response(200, content=png(), headers={"content-type": "image/png"})
        raise AssertionError((method, path))


class LanguageModel:
    def __init__(self, engine):
        self.requests = []
        self.engine = engine

    def handle(self, request):
        self.requests.append((request.method, request.url.path))
        if request.method == "GET" and request.url.path == "/api/v1/models":
            return httpx.Response(
                200,
                json={
                    "models": [
                        {
                            "type": "llm",
                            "key": "local-model",
                            "display_name": "Loaded fixture",
                            "loaded_instances": [{"id": "loaded-lm", "config": {"context_length": 8192}}],
                        }
                    ]
                },
            )
        if request.method == "POST" and request.url.path == "/v1/chat/completions":
            assert all(v in ("cancelled", "completed", "failed") for v in self.engine.jobs.values())
            return httpx.Response(
                200,
                text="data: "
                + json.dumps(
                    {"choices": [{"delta": {"content": "Чат продолжается"}, "finish_reason": None}]},
                    ensure_ascii=False,
                )
                + "\n\ndata: [DONE]\n\n",
                headers={"content-type": "text/event-stream"},
            )
        raise AssertionError((request.method, request.url.path))


def setup_images(tmp_path, mode="completed"):
    chat, sid, ids = setup_chat(tmp_path)
    engine = Engine(mode)
    lm = LanguageModel(engine)
    chat.generation.adapter = LmStudioAdapter(transport=httpx.MockTransport(lm.handle))
    service = ImageService(MediaService(WorldService(chat)), transport=httpx.MockTransport(engine.handle))
    service.save_settings(ImageSettings(enabled=True, cancel_wait_seconds=1), 0)
    return service, chat, sid, ids, engine, lm


async def wait(service, job_id):
    async with asyncio.timeout(20):
        while True:
            job = await service.generation.queue.get(job_id)
            if job["payload"]["state"] in ("completed", "failed", "cancelled", "interrupted"):
                return job["payload"]
            await asyncio.sleep(0.01)


def body(**extra):
    return ImageRequest(
        settings_revision=1,
        name="Гавань",
        prompt="Harbor at night",
        checkpoint="local.safetensors",
        confirmed=True,
        **extra,
    )


@pytest.mark.asyncio
async def test_actual_image_adapter_job_import_idempotence_and_no_lm_lifecycle_calls(tmp_path):
    service, chat, sid, ids, engine, lm = setup_images(tmp_path)
    try:
        request = body(
            story_id=sid, branch_id=chat.branch(sid)["id"], expected_revision=chat.branch(sid)["revision"]
        )
        before = chat.state(sid)
        queued = await service.enqueue(request, "same-image")
        result = await wait(service, queued["id"])
        assert result["state"] == "completed", result
        value = result["result"]
        asset = service.media.get(value["asset_id"])
        assert (
            asset["payload"]["type"] == "image/png"
            and asset["payload"]["provenance"]["request"]["prompt"] == request.prompt
        )
        assert service.media.checked_file(asset["id"])[1].read_bytes() == png()
        assert chat.state(sid) == before and value["source_current"]
        assert (
            value["lm_before"] == value["lm_after"] == ["loaded-lm"]
            and value["lm_instances_changed"] is False
        )
        assert (
            service.lease()["payload"]["state"] == "terminal"
            and service.lease()["payload"]["result_asset_id"] == asset["id"]
        )
        assert (await service.enqueue(request, "same-image"))["id"] == queued["id"]
        assert len([r for r in engine.requests if r[:2] == ("POST", "/prompt")]) == 1
        assert all(method == "GET" and path == "/api/v1/models" for method, path in lm.requests)
        assert not list(tmp_path.rglob("*.sqlite*"))
    finally:
        await service.generation.queue.close()


@pytest.mark.asyncio
async def test_foreground_preempts_image_only_after_addressed_external_stop(tmp_path):
    service, chat, sid, ids, engine, lm = setup_images(tmp_path, "running")
    try:
        image = await service.enqueue(body(), "image-preempt")
        await asyncio.wait_for(engine.submitting.wait(), 5)
        # Ensure the running job has acknowledged its external identifier.
        while not service.lease()["payload"]["acknowledged"]:
            await asyncio.sleep(0.01)
        main = await service.generation.queue.enqueue(
            "llm",
            {
                "model_id": "loaded-lm",
                "messages": [{"role": "user", "content": "Продолжай"}],
                "parameters": {"max_tokens": 32},
            },
            priority=0,
            idempotency_key="foreground",
        )
        image_result = await wait(service, image["id"])
        main_result = await wait(service, main["id"])
        assert image_result["state"] == "cancelled" and main_result["state"] == "completed", main_result
        assert main_result["result"]["text"] == "Чат продолжается"
        assert service.lease()["payload"]["external_status"] == "cancelled"
        assert not any(path in ("/interrupt", "/free", "/unload") for _, path, _ in engine.requests)
        assert any(method == "POST" and path.endswith("/cancel") for method, path, _ in engine.requests)
        assert not service.media.list()["items"]
    finally:
        await service.generation.queue.close()


@pytest.mark.asyncio
async def test_cancel_during_submission_waits_for_ack_then_cancels_owned_uuid(tmp_path):
    service, chat, sid, ids, engine, lm = setup_images(tmp_path, "blocked_ack")
    try:
        image = await service.enqueue(body(), "blocked-ack")
        await asyncio.wait_for(engine.submitting.wait(), 5)
        await service.generation.queue.cancel(image["id"])
        engine.ack.set()
        result = await wait(service, image["id"])
        assert result["state"] == "cancelled"
        lease = service.lease()["payload"]
        assert (
            lease["acknowledged"] and lease["state"] == "terminal" and lease["external_status"] == "cancelled"
        )
        assert not service.media.list()["items"]
    finally:
        engine.ack.set()
        await service.generation.queue.close()


@pytest.mark.asyncio
async def test_cancel_after_lease_publication_before_network_never_submits(tmp_path, monkeypatch):
    service, chat, sid, ids, engine, lm = setup_images(tmp_path)
    real = service.acquire
    claimed = threading.Event()
    release = threading.Event()

    def delayed(*args):
        real(*args)
        claimed.set()
        assert release.wait(5)

    monkeypatch.setattr(service, "acquire", delayed)
    try:
        job = await service.enqueue(body(), "lease-cancel")
        assert await asyncio.to_thread(claimed.wait, 5)
        await service.generation.queue.cancel(job["id"])
        release.set()
        assert (await wait(service, job["id"]))["state"] == "cancelled"
        assert service.lease()["payload"]["external_status"] == "not_submitted"
        assert not any(method == "POST" and path == "/prompt" for method, path, _ in engine.requests)
    finally:
        release.set()
        await service.generation.queue.close()


@pytest.mark.asyncio
async def test_unknown_submission_blocks_inference_until_real_reconciliation_and_recovers_image(tmp_path):
    service, chat, sid, ids, engine, lm = setup_images(tmp_path, "unknown_submit")
    try:
        job = await service.enqueue(body(), "unknown")
        assert (await wait(service, job["id"]))["state"] == "failed"
        assert service.lease()["payload"]["state"] == "uncertain"
        with pytest.raises(AppError, match="не подтверждён"):
            await service.resource_guard("llm", {})
        await service.resource_guard("memory_search", {"embedding_model_id": None})
        engine.mode = "completed"
        recovered = await service.reconcile()
        assert recovered["lease"]["payload"]["state"] == "terminal"
        asset_id = recovered["lease"]["payload"]["result_asset_id"]
        assert asset_id
        service.media.update(
            asset_id,
            service.media.get(asset_id)["revision"],
            {"name": "Моя исправленная подпись", "archived": True},
        )
        await service.reconcile()
        assert service.media.get(asset_id)["payload"]["name"] == "Моя исправленная подпись"
        assert len(service.media.list(archived=True)["items"]) == 1
        await service.resource_guard("llm", {})
    finally:
        await service.generation.queue.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mode,code",
    [
        ("oom", "image_oom"),
        ("old_api", "image_capability_missing"),
        ("bad_output", "image_output_invalid"),
        ("rejected", "image_engine_rejected"),
    ],
)
async def test_engine_failures_do_not_publish_fake_image_or_change_story(tmp_path, mode, code):
    service, chat, sid, ids, engine, lm = setup_images(tmp_path, mode)
    try:
        before = chat.state(sid)
        job = await service.enqueue(body(), "failure-" + mode)
        result = await wait(service, job["id"])
        assert result["state"] == "failed" and result["error"]["code"] == code, result
        assert not service.media.list()["items"] and chat.state(sid) == before
        if mode == "old_api":
            assert not service.lease() and not engine.jobs
        else:
            assert service.lease()["payload"]["state"] == "terminal"
    finally:
        await service.generation.queue.close()


@pytest.mark.asyncio
async def test_late_image_is_library_resource_without_mutating_new_branch(tmp_path):
    service, chat, sid, ids, engine, lm = setup_images(tmp_path)
    original = chat.branch(sid)
    request = body(story_id=sid, branch_id=original["id"], expected_revision=original["revision"])

    def fork():
        chat.fork(sid, expected_revision=chat.branch(sid)["revision"], before_all=True)

    engine.on_status = fork
    try:
        job = await service.enqueue(request, "late-image")
        result = await wait(service, job["id"])
        assert result["state"] == "completed" and not result["result"]["source_current"]
        assert chat.branch(sid)["id"] != original["id"] and not chat.state(sid)["payload"].get("media")
        asset = service.media.get(result["result"]["asset_id"])
        assert asset["payload"]["provenance"]["request"]["branch_id"] == original["id"]
    finally:
        await service.generation.queue.close()


def test_image_settings_numeric_loopback_pixels_and_model_path_validation():
    for endpoint in [
        "https://127.0.0.1:8188",
        "http://localhost:8188",
        "http://127.0.0.1:8188/a",
        "http://user:secret@127.0.0.1:8188",
        "http://example.org:8188",
    ]:
        with pytest.raises((AppError, ValueError)):
            ImageSettings(endpoint=endpoint)
    with pytest.raises(ValueError):
        body(width=513)
    cap = {"checkpoints": ["../outside.safetensors"], "samplers": ["euler"], "schedulers": ["normal"]}
    request = body().model_dump(mode="json")
    request["checkpoint"] = "../outside.safetensors"
    with pytest.raises(AppError):
        ComfyAdapter.graph(request, cap, str(uuid4()))


@pytest.mark.asyncio
async def test_cancel_after_external_completion_records_release_before_lm_diagnostics(tmp_path, monkeypatch):
    service, chat, sid, ids, engine, lm = setup_images(tmp_path)
    real = service.loaded_lm
    calls = 0
    diagnostics = asyncio.Event()

    async def delayed():
        nonlocal calls
        calls += 1
        if calls == 2:
            assert service.lease()["payload"]["state"] == "terminal"
            diagnostics.set()
            await asyncio.Event().wait()
        return await real()

    monkeypatch.setattr(service, "loaded_lm", delayed)
    try:
        image = await service.enqueue(body(), "completed-before-diagnostics")
        await asyncio.wait_for(diagnostics.wait(), 5)
        main = await service.generation.queue.enqueue(
            "llm",
            {
                "model_id": "loaded-lm",
                "messages": [{"role": "user", "content": "Продолжай"}],
                "parameters": {"max_tokens": 32},
            },
            priority=0,
            idempotency_key="after-image-completed",
        )
        assert (await wait(service, image["id"]))["state"] == "cancelled"
        assert (await wait(service, main["id"]))["state"] == "completed"
        assert service.lease()["payload"]["external_status"] == "completed"
    finally:
        await service.generation.queue.close()


@pytest.mark.asyncio
async def test_actual_free_vram_threshold_rejects_before_any_prompt_submission(tmp_path):
    service, chat, sid, ids, engine, lm = setup_images(tmp_path)
    cfg = service.save_settings(ImageSettings(enabled=True, minimum_free_vram_mib=4096), 1)
    try:
        request = body().model_copy(update={"settings_revision": cfg["revision"]})
        job = await service.enqueue(request, "low-vram")
        result = await wait(service, job["id"])
        assert result["state"] == "failed" and result["error"]["code"] == "image_vram_low"
        assert not service.lease() and not any(path == "/prompt" for _, path, _ in engine.requests)
    finally:
        await service.generation.queue.close()


def test_image_api_offline_validation_drafts_and_config_cas(tmp_path):
    from fastapi.testclient import TestClient
    from taverna.core import Config
    from taverna.main import create_app

    app = create_app(Config(tmp_path))
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        csrf = client.post(
            "/api/v1/session/bootstrap", json={"token": app.state.sessions.bootstrap()}
        ).json()["csrf"]
        client.headers.update({"X-Taverna-CSRF": csrf})
        config = client.get("/api/v1/images/settings").json()["config"]
        assert config["revision"] == 0 and not config["payload"]["enabled"]
        invalid = {**config["payload"], "endpoint": "http://example.org:8188"}
        assert (
            client.put(
                "/api/v1/images/settings", json={"expected_revision": 0, "settings": invalid}
            ).status_code
            == 400
        )
        settings = {**config["payload"], "enabled": True}
        saved = client.put("/api/v1/images/settings", json={"expected_revision": 0, "settings": settings})
        assert saved.status_code == 200 and saved.json()["revision"] == 1
        assert (
            client.put(
                "/api/v1/images/settings", json={"expected_revision": 0, "settings": settings}
            ).status_code
            == 409
        )
        request = body().model_dump(mode="json")
        request["width"] = 513.5
        draft_id = str(uuid4())
        draft = {
            "template_id": None,
            "source_revision": 0,
            "expected_revision": 0,
            "raw": "",
            "payload": {"settings": invalid, "request": request, "dirty_settings": True},
        }
        response = client.put("/api/v1/images/drafts/" + draft_id, json=draft)
        assert response.status_code == 200, response.text
        assert response.json()["payload"]["payload"]["request"]["width"] == 513.5
        assert (
            client.post(
                "/api/v1/images/generate", headers={"Idempotency-Key": "invalid"}, json=request
            ).status_code
            == 422
        )
        assert not app.state.images.lease() and not app.state.images.recent_jobs()["items"]
        assert client.put("/api/v1/images/drafts/" + draft_id, json=draft).status_code == 409
        assert client.delete("/api/v1/images/drafts/" + draft_id + "?expected_revision=1").status_code == 200


@pytest.mark.asyncio
async def test_unknown_lease_manual_release_requires_explicit_reason_and_expected_revision(tmp_path):
    service, chat, sid, ids, engine, lm = setup_images(tmp_path, "unknown_submit")
    try:
        job = await service.enqueue(body(), "manual-release")
        await wait(service, job["id"])
        lease = service.lease()
        with pytest.raises(AppError, match="Подтвердите"):
            service.manual_release(lease["revision"], "Проверил движок", False)
        with pytest.raises(AppError):
            service.manual_release(0, "Проверил движок", True)
        released = service.manual_release(lease["revision"], "Движок остановлен владельцем вручную", True)
        assert released["payload"]["state"] == "manually_released" and released["payload"]["manual_reason"]
        await service.resource_guard("llm", {})
        assert not any(path in ("/interrupt", "/free") for _, path, _ in engine.requests)
    finally:
        await service.generation.queue.close()


@pytest.mark.asyncio
async def test_inference_guard_also_covers_nested_model_calls_from_future_job_kinds(tmp_path):
    service, chat, sid, ids, engine, lm = setup_images(tmp_path, "unknown_submit")

    async def emit(*args):
        raise AssertionError("Model generation must not start")

    try:
        job = await service.enqueue(body(), "nested-guard")
        await wait(service, job["id"])
        with pytest.raises(AppError, match="не подтверждён"):
            await service.generation.run(
                {
                    "model_id": "loaded-lm",
                    "messages": [{"role": "user", "content": "Do not submit while image is uncertain"}],
                },
                asyncio.Event(),
                emit,
            )
        assert not any(method == "POST" for method, path in lm.requests)
    finally:
        await service.generation.queue.close()


@pytest.mark.asyncio
async def test_pending_external_job_dequeue_404_is_confirmed_only_with_prior_pending_and_cancel_receipt(
    tmp_path,
):
    service, chat, sid, ids, engine, lm = setup_images(tmp_path, "pending")
    try:
        job = await service.enqueue(body(), "pending-dequeue")
        await asyncio.wait_for(engine.submitting.wait(), 5)
        while not service.lease()["payload"]["acknowledged"]:
            await asyncio.sleep(0.01)
        await service.generation.queue.cancel(job["id"])
        result = await wait(service, job["id"])
        assert result["state"] == "cancelled"
        assert (
            service.lease()["payload"]["state"] == "terminal"
            and service.lease()["payload"]["external_status"] == "cancelled"
        )
        assert not engine.jobs and not service.media.list()["items"]
        await service.resource_guard("llm", {})
    finally:
        await service.generation.queue.close()


@pytest.mark.asyncio
async def test_cancellation_during_error_cleanup_waits_for_external_terminal(tmp_path):
    service, chat, sid, ids, engine, lm = setup_images(tmp_path, "running")
    entered, release = asyncio.Event(), asyncio.Event()
    original = service.recover_cancel

    async def delayed_cleanup(*args):
        entered.set()
        await release.wait()
        return await original(*args)

    service.recover_cancel = delayed_cleanup

    def error_once():
        engine.on_status = None
        raise AppError("test_transport_abort", "Тестовый разрыв после отправки", 502)

    engine.on_status = error_once
    config = service.settings()
    payload = {
        "prompt_id": str(uuid4()),
        "job_id": str(uuid4()),
        "endpoint": config["payload"]["endpoint"],
        "settings": config["payload"],
        "request": body().model_dump(mode="json"),
    }

    async def emit(*args):
        pass

    task = asyncio.create_task(service.run(payload, asyncio.Event(), emit))
    try:
        await asyncio.wait_for(entered.wait(), 5)
        task.cancel()
        await asyncio.sleep(0.05)
        assert not task.done(), "Handler must finish addressed cleanup before giving the queue to inference"
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert service.lease()["payload"]["state"] == "terminal"
        assert service.lease()["payload"]["external_status"] == "cancelled"
        assert all(status == "cancelled" for status in engine.jobs.values())
    finally:
        release.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await service.generation.queue.close()
