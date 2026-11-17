"""Regressions for measured I/O growth; real files, fsync, recovery and queue."""

import asyncio
import json
import os
import subprocess
import sys
import threading
from uuid import uuid4

import pytest
from taverna.core import AppError
from taverna.jobs import JobQueue
from taverna.storage import JsonStore, Mutation, digest, document, encoded


def seed_many(store):
    store.transaction([Mutation(f"config/seed-{i}.json", document("test", {"i": i}), 0) for i in range(10)])


def seed_queue(tmp_path):
    store = JsonStore(tmp_path)
    queue = JobQueue(store)
    job = document(
        "job",
        {"kind": "test", "request": {}, "state": "running", "event_count": 0, "result": None, "error": None},
    )
    store.transaction([Mutation(queue.key(job["id"]), job, 0)])
    return store, queue, job["id"]


@pytest.mark.asyncio
async def test_stream_batch_keeps_text_reasoning_metadata_and_contiguous_replay_ids(tmp_path):
    store, queue, jid = seed_queue(tmp_path)
    complete = []
    store.fault = lambda phase: complete.append(phase) if phase == "complete" else None
    for kind in ("reasoning", "delta"):
        for i in range(300):
            await queue.emit(jid, kind, {"text": f"я{i} "})
    await queue.emit(jid, "quality", {"flags": []})
    await queue.emit(jid, "completed", {}, state="completed", result={"text": "done"})
    events = await queue.events(jid, limit=200)
    for kind in ("reasoning", "delta"):
        assert "".join(e["data"]["text"] for e in events if e["type"] == "generation." + kind) == "".join(
            f"я{i} " for i in range(300)
        )
    assert [e["sequence"] for e in events] == list(range(1, len(events) + 1))
    assert [e["type"] for e in events[-2:]] == ["generation.quality", "generation.completed"]
    assert len(complete) < 40  # 600 transport fragments cannot create 600 transactions.
    assert await queue.events(jid, after=events[-2]["sequence"]) == events[-1:]
    assert (await queue.get(jid))["payload"]["state"] == "completed"
    assert store.integrity()["ok"]


@pytest.mark.asyncio
async def test_idle_stream_flushes_before_sse_replay_without_waiting_for_next_fragment(tmp_path):
    _, queue, jid = seed_queue(tmp_path)
    await queue.emit(jid, "delta", {"text": "Первый "})
    await queue.emit(jid, "delta", {"text": "второй"})
    # Windows CI under concurrent disk load can exceed a fixed 60ms timer.
    # Check an eventual idle flush, while still failing if it never occurs.
    async with asyncio.timeout(1.5):
        while True:
            events = await queue.events(jid)
            if "".join(e["data"]["text"] for e in events) == "Первый второй":
                break
            await asyncio.sleep(0.01)
    await queue.emit(jid, "cancelled", {}, state="cancelled")
    assert (await queue.events(jid))[-1]["type"] == "generation.cancelled"


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal", [False, True])
async def test_cancel_during_actual_commit_never_duplicates_buffer_or_clobbers_terminal(tmp_path, terminal):
    store, queue, jid = seed_queue(tmp_path)
    await queue.emit(jid, "delta", {"text": "Привет "})
    await queue.emit(jid, "delta", {"text": "мир"})
    entered, release = threading.Event(), threading.Event()

    def block(phase):
        if phase == "published":
            entered.set()
            if not release.wait(10):
                raise RuntimeError("test gate timed out")

    store.fault = block
    task = asyncio.create_task(
        queue.emit(jid, "completed" if terminal else "quality", {}, state="completed" if terminal else None)
    )
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
    finally:
        release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    store.fault = lambda phase: None
    await queue.emit(jid, "interrupted", {}, state="interrupted")
    events = await queue.events(jid)
    assert "".join(e["data"].get("text", "") for e in events) == "Привет мир"
    assert (await queue.get(jid))["payload"]["state"] == ("completed" if terminal else "interrupted")
    assert store.integrity()["ok"]


def test_delta_receipts_checkpoint_history_and_idempotent_old_result(tmp_path):
    store = JsonStore(tmp_path)
    seed_many(store)
    value = document("test", {"n": 0})
    txid = str(uuid4())
    first = store.transaction([Mutation("config/a.json", value, 0)], transaction_id=txid)
    original = json.loads(encoded(value))
    for n in range(1, 132):
        value["payload"]["n"] = n
        store.transaction([Mutation("config/a.json", value, n)])
    current = store.get("config/a.json")
    assert store.transaction([Mutation("config/a.json", original, 0)], transaction_id=txid.upper()) == first
    assert store.get("config/a.json") == current
    assert current["payload"]["n"] == 131
    assert len(list(store.journal.glob("checkpoints/*.json"))) >= 2
    assert not list(store.journal.glob("*/commit.json"))
    receipts = list(store.journal.glob("history/*/*/intent.json"))
    assert len(receipts) == 133
    assert sum(json.loads(p.read_bytes()).get("format") == 2 for p in receipts) == 132
    assert sum(p.stat().st_size for p in receipts) < 200000
    assert JsonStore(tmp_path).integrity()["ok"]


def test_historical_manifest_replay_covers_insert_update_delete_and_guard_conflict(tmp_path):
    store = JsonStore(tmp_path)
    a, b = document("test", {"n": 1}), document("test", {"n": 2})
    txid = str(uuid4())
    mutation = Mutation("config/a.json", a, 0)
    first = store.transaction([mutation], transaction_id=txid)
    store.transaction([Mutation("config/b.json", b, 0)], guards={"config/a.json": 1})
    store.transaction([Mutation("config/a.json", None, 1)])
    assert store.transaction([mutation], transaction_id=txid) == first
    assert store.get("config/a.json") is None
    with pytest.raises(AppError) as error:
        store.transaction([Mutation("config/c.json", document("test", {}), 0)], guards={"config/a.json": 1})
    assert error.value.code == "storage_conflict"
    with pytest.raises(AppError) as error:
        store.transaction([Mutation("config/b.json", b, 0)], transaction_id=txid)
    assert error.value.code == "idempotency_conflict"


@pytest.mark.parametrize(
    "phase,expected",
    [
        ("object_written", 0),
        ("prepared", 0),
        ("committed", 1),
        ("published", 1),
        ("complete", 1),
        ("archived", 1),
    ],
)
def test_delta_journal_real_process_kill_all_phases(tmp_path, phase, expected):
    store = JsonStore(tmp_path)
    seed_many(store)
    value = document("test", {"n": 0})
    store.transaction([Mutation("config/a.json", value, 0)])
    script = """
import os,sys
from pathlib import Path
from taverna.storage import JsonStore,Mutation
store=JsonStore(Path(sys.argv[1]));a=store.get('config/a.json');a['payload']['n']=1
store.fault=lambda phase:os._exit(88) if phase==sys.argv[2] else None
store.transaction([Mutation('config/a.json',a,1)])
"""
    killed = subprocess.run([sys.executable, "-c", script, str(tmp_path), phase], timeout=20)
    assert killed.returncode == 88
    recovered = JsonStore(tmp_path)
    assert recovered.get("config/a.json")["payload"]["n"] == expected
    assert recovered.integrity()["ok"]


def test_v1_recovery_then_v2_history_replay_and_missing_manifest_never_resets(tmp_path):
    store = JsonStore(tmp_path)
    seed_many(store)
    before = store.pointer.read_bytes()
    value = document("test", {"n": 1})
    txid = str(uuid4())
    after = store.transaction([Mutation("config/a.json", value, 0)], transaction_id=txid)
    directory = next(d for d in store.journal.glob("history/*/*") if d.name.endswith(txid))
    modern = json.loads((directory / "intent.json").read_bytes())
    legacy = {
        "before_sha256": digest(before),
        "after": after,
        "changed": modern["changed"],
        "fingerprint": modern["fingerprint"],
    }
    (directory / "intent.json").write_bytes(encoded(legacy))
    (directory / "commit.json").write_bytes(encoded({"intent_sha256": digest(encoded(legacy))}))
    (directory / "complete.json").unlink()
    active = store.journal / directory.name
    os.replace(directory, active)
    store.pointer.write_bytes(before)
    reopened = JsonStore(tmp_path)
    assert reopened.get("config/a.json")["payload"]["n"] == 1
    second = document("test", {"n": 2})
    second_id = str(uuid4())
    later = reopened.transaction([Mutation("config/b.json", second, 0)], transaction_id=second_id)
    assert reopened.transaction([Mutation("config/b.json", second, 0)], transaction_id=second_id) == later
    assert reopened.transaction([Mutation("config/a.json", value, 0)], transaction_id=txid) == after
    reopened.pointer.unlink()
    with pytest.raises(AppError) as error:
        JsonStore(tmp_path)
    assert error.value.code == "manifest_corrupt"


def test_corrupt_checkpoint_or_completed_delta_never_reexecutes_effect(tmp_path):
    store = JsonStore(tmp_path)
    seed_many(store)
    value = document("test", {"n": 7})
    txid = str(uuid4())
    mutation = Mutation("config/a.json", value, 0)
    store.transaction([mutation], transaction_id=txid)
    checkpoint = next(store.journal.glob("checkpoints/*.json"))
    original = checkpoint.read_bytes()
    checkpoint.write_bytes(b"{}")
    with pytest.raises(AppError) as error:
        store.transaction([mutation], transaction_id=txid)
    assert error.value.code == "transaction_recovery_required"
    assert store.get("config/a.json")["revision"] == 1
    checkpoint.write_bytes(original)
    receipt = next(d / "intent.json" for d in store.journal.glob("history/*/*") if d.name.endswith(txid))
    receipt.write_bytes(b"{}")
    with pytest.raises(AppError) as error:
        store.transaction([mutation], transaction_id=txid)
    assert error.value.code == "transaction_recovery_required"
    assert store.get("config/a.json")["revision"] == 1


def test_tiny_manifest_uses_existing_compact_receipt_without_space_regression(tmp_path):
    store = JsonStore(tmp_path)
    value = document("test", {"n": 0})
    for n in range(100):
        value["payload"]["n"] = n
        store.transaction([Mutation("config/a.json", value, n)])
    size = sum(p.stat().st_size for p in store.journal.rglob("*") if p.is_file())
    assert size < 80000
    assert store.get("config/a.json")["payload"]["n"] == 99
    assert store.integrity()["ok"]


def test_malformed_completed_directory_fails_with_recovery_error_not_raw_exception(tmp_path):
    store = JsonStore(tmp_path)
    before = store.pointer.read_bytes()
    bad = store.journal / "invalid-name"
    bad.mkdir()
    (bad / "commit.json").write_text("{}")
    (bad / "complete.json").write_text("{}")
    with pytest.raises(AppError) as error:
        store.get("config/absent.json")
    assert error.value.code == "transaction_recovery_required"
    assert store.pointer.read_bytes() == before


@pytest.mark.asyncio
async def test_invalid_model_unicode_cannot_poison_buffer_or_stop_queue(tmp_path):
    store = JsonStore(tmp_path)
    queue = JobQueue(store)

    async def invalid(request, cancel, emit):
        await emit("delta", {"text": "Сохранённый "})
        await emit("delta", {"text": "хвост"})
        await emit("delta", {"text": "\ud800"})
        return {"must_not_complete": True}

    async def healthy(request, cancel, emit):
        await emit("delta", {"text": "Следующая задача"})
        return {"ok": True}

    queue.handlers.update(invalid=invalid, healthy=healthy)

    async def terminal(jid):
        async with asyncio.timeout(10):
            while (row := await queue.get(jid))["payload"]["state"] in ("queued", "running"):
                await asyncio.sleep(0.01)
        return row

    try:
        job = await queue.enqueue("invalid", {}, idempotency_key="invalid-unicode")
        row = await terminal(job["id"])
        assert row["payload"]["state"] == "failed" and row["payload"]["error"]["code"] == "invalid_json_data"
        assert (
            "".join(e["data"].get("text", "") for e in await queue.events(job["id"])) == "Сохранённый хвост"
        )
        second = await queue.enqueue("healthy", {}, idempotency_key="healthy-after-invalid-unicode")
        assert (await terminal(second["id"]))["payload"]["state"] == "completed"
        assert store.integrity()["ok"]
    finally:
        await queue.close()
