import asyncio
import json

import httpx
import pytest
from fastapi.testclient import TestClient
from taverna.core import AppError, Config
from taverna.jobs import JobQueue
from taverna.llm import LmStudioAdapter, local_endpoint
from taverna.main import create_app
from taverna.storage import JsonStore, Mutation, document

CATALOG = {
    "models": [
        {
            "key": "model-key",
            "type": "llm",
            "display_name": "Тестовая модель протокола",
            "loaded_instances": [{"id": "loaded-instance", "config": {"context_length": 8192}}],
        },
        {"key": "unloaded", "type": "llm", "loaded_instances": []},
    ]
}


def adapter(calls, *, broken=False):
    def transport(request):
        calls.append((request.method, request.url.path))
        if request.url.path == "/api/v1/models":
            return httpx.Response(200, json=CATALOG)
        assert request.url.path == "/v1/chat/completions"
        payload = json.loads(request.content)
        assert payload["model"] == "loaded-instance"
        chunks = [
            json.dumps({"choices": [{"delta": {"reasoning_content": "Отдельно"}}]}),
            json.dumps({"choices": [{"delta": {"content": "Привет"}}]}),
        ]
        if not broken:
            chunks += [
                json.dumps(
                    {"choices": [{"delta": {}, "finish_reason": "stop"}], "usage": {"completion_tokens": 2}}
                ),
                "[DONE]",
            ]
        return httpx.Response(
            200,
            text="".join("data: " + x + "\n\n" for x in chunks),
            headers={"Content-Type": "text/event-stream"},
        )

    return LmStudioAdapter(transport=httpx.MockTransport(transport))


@pytest.mark.parametrize(
    "url",
    [
        "https://127.0.0.1:1234",
        "http://example.com:1234",
        "http://127.0.0.1:1234/a",
        "http://user:password@127.0.0.1:1234",
        "http://127.0.0.1:1234#x",
    ],
)
def test_endpoint_rejects_nonlocal_and_credentials(url):
    with pytest.raises(AppError):
        local_endpoint(url)


@pytest.mark.asyncio
async def test_loaded_only_and_reasoning_separated():
    calls = []
    llm = adapter(calls)
    with pytest.raises(AppError) as error:
        async for _ in llm.stream("unloaded", [{"role": "user", "content": "Тест"}], {}, asyncio.Event()):
            pass
    assert error.value.code == "model_not_loaded"
    assert calls == [("GET", "/api/v1/models")]
    events = [
        e
        async for e in llm.stream(
            "loaded-instance", [{"role": "user", "content": "Тест"}], {"max_tokens": 32}, asyncio.Event()
        )
    ]
    assert [e[0] for e in events] == ["reasoning", "delta", "metrics"]
    assert events[1][1]["text"] == "Привет"
    metrics = events[-1][1]
    assert metrics["usage"]["completion_tokens"] == 2
    if metrics["duration_seconds"] > 0:
        assert metrics["tokens_per_second"] == 2 / metrics["duration_seconds"]
    else:
        assert metrics["tokens_per_second"] is None
    assert not any(path.endswith(("/load", "/unload", "/download")) for _, path in calls)


@pytest.mark.asyncio
async def test_unknown_native_catalog_fails_closed():
    llm = LmStudioAdapter(transport=httpx.MockTransport(lambda request: httpx.Response(404)))
    with pytest.raises(AppError) as error:
        await llm.require_loaded("x")
    assert error.value.code == "loaded_status_unavailable"


async def terminal(queue, job_id):
    for _ in range(300):
        job = await queue.get(job_id)
        if job["payload"]["state"] in {"completed", "failed", "cancelled", "interrupted"}:
            return job
        await asyncio.sleep(0.01)
    raise AssertionError("Задача не завершилась")


@pytest.mark.asyncio
async def test_queue_priority_cancel_idempotency_and_restart(tmp_path):
    store = JsonStore(tmp_path)
    queue = JobQueue(store)
    entered, release = asyncio.Event(), asyncio.Event()
    order = []

    async def handler(payload, cancel, emit):
        if payload["name"] == "block":
            entered.set()
            await release.wait()
        order.append(payload["name"])
        await emit("delta", {"text": payload["name"]})
        return {"text": payload["name"]}

    queue.handlers["test"] = handler
    await queue.enqueue("test", {"name": "block"}, idempotency_key="block")
    await entered.wait()
    low = await queue.enqueue("test", {"name": "low"}, priority=10, idempotency_key="low")
    high = await queue.enqueue("test", {"name": "high"}, priority=0, idempotency_key="high")
    duplicate = await queue.enqueue("test", {"name": "high"}, idempotency_key="high")
    assert duplicate["id"] == high["id"]
    with pytest.raises(AppError):
        await queue.enqueue("test", {"name": "other"}, idempotency_key="high")
    release.set()
    assert (await terminal(queue, low["id"]))["payload"]["state"] == "completed"
    assert order == ["block", "high", "low"]
    entries = await queue.events(high["id"])
    assert [e["sequence"] for e in entries] == [1, 2, 3]
    assert len(await queue.events(high["id"], after=2)) == 1
    await queue.close()
    pending = document("job", {"state": "running", "event_count": 0})
    store.transaction([Mutation(queue.key(pending["id"]), pending, 0)])
    restarted = JobQueue(store)
    await restarted.start()
    assert (await restarted.get(pending["id"]))["payload"]["state"] == "interrupted"
    assert (await restarted.events(pending["id"]))[-1]["type"] == "generation.interrupted"
    await restarted.close()


@pytest.mark.asyncio
async def test_context_guard_and_bounded_connection_retry():
    calls = []
    llm = adapter(calls)
    with pytest.raises(AppError) as error:
        async for _ in llm.stream(
            "loaded-instance", [{"role": "user", "content": "a" * 8192}], {}, asyncio.Event()
        ):
            pass
    assert error.value.code == "context_overflow"
    assert not any(method == "POST" for method, path in calls)
    attempts = 0

    def transport(request):
        nonlocal attempts
        if request.method == "GET":
            return httpx.Response(200, json=CATALOG)
        attempts += 1
        raise httpx.ConnectError("test", request=request)

    llm = LmStudioAdapter(transport=httpx.MockTransport(transport))
    with pytest.raises(AppError) as error:
        async for _ in llm.stream("loaded-instance", [{"role": "user", "content": "a"}], {}, asyncio.Event()):
            pass
    assert error.value.code == "generation_connection_failed"
    assert attempts == 2


@pytest.mark.asyncio
async def test_cancel_interrupts_blocked_handler(tmp_path):
    queue = JobQueue(JsonStore(tmp_path))
    entered = asyncio.Event()

    async def handler(payload, cancel, emit):
        entered.set()
        await asyncio.sleep(60)

    queue.handlers["test"] = handler
    job = await queue.enqueue("test", {}, idempotency_key="cancel")
    await entered.wait()
    await queue.cancel(job["id"])
    assert (await terminal(queue, job["id"]))["payload"]["state"] == "cancelled"
    await queue.close()


@pytest.mark.parametrize("broken", [False, True])
def test_api_stream_replay_and_partial(tmp_path, broken):
    app = create_app(Config(tmp_path))
    calls = []
    app.state.generation.adapter = adapter(calls, broken=broken)
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        csrf = client.post(
            "/api/v1/session/bootstrap", json={"token": app.state.sessions.bootstrap()}
        ).json()["csrf"]
        headers = {"X-Taverna-CSRF": csrf, "Idempotency-Key": "same"}
        body = {"model_id": "loaded-instance", "messages": [{"role": "user", "content": "Тест"}]}
        response = client.post("/api/v1/generations", json=body, headers=headers)
        assert response.status_code == 202
        job_id = response.json()["generation_id"]
        stream = client.get(f"/api/v1/generations/{job_id}/stream")
        assert "generation.delta" in stream.text and "Привет" in stream.text
        expected = "failed" if broken else "completed"
        job = client.get(f"/api/v1/generations/{job_id}").json()
        assert job["state"] == expected
        assert (
            client.post("/api/v1/generations", json=body, headers=headers).json()["generation_id"] == job_id
        )
        assert len([p for m, p in calls if m == "POST"]) == 1
        replay = client.get(f"/api/v1/generations/{job_id}/stream", headers={"Last-Event-ID": f"{job_id}:1"})
        assert "generation.started" not in replay.text
