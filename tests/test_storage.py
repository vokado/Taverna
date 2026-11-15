import errno
import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4
from zipfile import ZipFile

import pytest
from taverna.core import AppError
from taverna.migrations import checkpoint, migrate, verify_checkpoint
from taverna.segments import SegmentStore
from taverna.storage import JsonStore, Mutation, document, safe_path


@pytest.mark.parametrize("invalid", [float("nan"), "\ud800", {1, 2}])
def test_invalid_json_data_does_not_publish(tmp_path, invalid):
    store = JsonStore(tmp_path)
    before = store.snapshot()
    with pytest.raises(AppError) as error:
        store.transaction([Mutation("memory/invalid.json", document("test", {"invalid": invalid}), 0)])
    assert error.value.code == "invalid_json_data" and error.value.status == 422
    assert store.snapshot() == before


def seed(store):
    first, second = document("test", {"n": 0}), document("test", {"n": 0})
    store.transaction([Mutation("characters/a.json", first, 0), Mutation("characters/b.json", second, 0)])
    return first, second


@pytest.mark.parametrize(
    "phase,expected",
    [("object_written", 0), ("prepared", 0), ("committed", 1), ("published", 1), ("complete", 1)],
)
def test_kill_and_recovery_every_phase(tmp_path, phase, expected):
    store = JsonStore(tmp_path)
    seed(store)
    script = """
import os,sys
from pathlib import Path
from taverna.storage import JsonStore,Mutation
store=JsonStore(Path(sys.argv[1]))
a=store.get('characters/a.json');b=store.get('characters/b.json')
a['payload']['n']=1;b['payload']['n']=1
store.fault=lambda phase:os._exit(88) if phase==sys.argv[2] else None
store.transaction([Mutation('characters/a.json',a,1),Mutation('characters/b.json',b,1)])
"""
    result = subprocess.run([sys.executable, "-c", script, str(tmp_path), phase], timeout=20)
    assert result.returncode == 88
    recovered = JsonStore(tmp_path)
    assert recovered.get("characters/a.json")["payload"]["n"] == expected
    assert recovered.get("characters/b.json")["payload"]["n"] == expected
    assert JsonStore(tmp_path).integrity()["ok"]


def test_revision_concurrent_clients_and_references(tmp_path):
    store = JsonStore(tmp_path)
    a, b = seed(store)
    b["refs"] = ["characters/a.json"]
    store.transaction([Mutation("characters/b.json", b, 1)])
    with pytest.raises(AppError, match="несуществующую"):
        store.transaction([Mutation("characters/a.json", None, 1)])

    def update(number):
        client = JsonStore(tmp_path)
        value = client.get("characters/a.json")
        value["payload"]["n"] = number
        try:
            client.transaction([Mutation("characters/a.json", value, 1)])
            return "ok"
        except AppError as exc:
            return exc.code

    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(update, [1, 2]))
    assert sorted(results) == ["ok", "storage_conflict"]


def test_idempotency_schema_validation_and_migration(tmp_path):
    store = JsonStore(tmp_path)
    value = document("test", {"n": 1})
    txid = str(uuid4())
    mutation = Mutation("characters/a.json", value, 0)
    one = store.transaction([mutation], transaction_id=txid)
    assert store.transaction([mutation], transaction_id=txid) == one
    value["payload"]["n"] = 99
    with pytest.raises(AppError) as conflict:
        store.transaction([mutation], transaction_id=txid)
    assert conflict.value.code == "idempotency_conflict"
    store.register("test", 1, {"type": "object", "required": ["n"], "properties": {"n": {"type": "integer"}}})
    store.register(
        "test", 2, {"type": "object", "required": ["number"], "properties": {"number": {"type": "integer"}}}
    )
    with pytest.raises(AppError):
        migrate(store, "test", 1, 2, lambda payload: {"number": "bad"})
    assert store.get("characters/a.json")["schema_version"] == 1
    result = migrate(store, "test", 1, 2, lambda payload: {"number": payload["n"]})
    assert result["migrated"] == 1 and result["backup"]
    assert store.get("characters/a.json")["payload"] == {"number": 1}
    assert migrate(store, "test", 1, 2, lambda payload: payload)["migrated"] == 0
    assert verify_checkpoint(tmp_path / "backups" / result["backup"])["files"]


@pytest.mark.parametrize(
    "path",
    [
        "../a.json",
        "/tmp/a.json",
        "characters/CON.json",
        "characters/a:b.json",
        "characters/a\\b.json",
        "characters/x./a.json",
    ],
)
def test_paths_and_symlink_escape(tmp_path, path):
    with pytest.raises(AppError):
        safe_path(tmp_path, path)


def test_corrupt_resource_isolation_snapshot_and_disk_full(tmp_path, monkeypatch):
    import taverna.storage as storage

    store = JsonStore(tmp_path)
    seed(store)
    backup = checkpoint(store, "test")
    with ZipFile(backup) as z:
        assert json.loads(z.read("data/config/store-manifest.json"))["generation"] == 1
    manifest = json.loads(store.pointer.read_bytes())
    path = tmp_path / "data" / manifest["entries"]["characters/a.json"]["object"]
    path.write_text("corrupt")
    rows, errors = store.scan("characters/", tolerate_corrupt=True)
    assert len(rows) == 1 and errors == ["characters/a.json"]
    assert store.get("characters/b.json")
    assert not store.integrity()["ok"]
    real = storage.atomic_write

    def fail(path, data):
        if path.name == "commit.json":
            raise OSError(errno.ENOSPC, "test disk full")
        real(path, data)

    monkeypatch.setattr(storage, "atomic_write", fail)
    with pytest.raises(AppError, match="недостаточно места"):
        store.transaction([Mutation("characters/c.json", document("test", {}), 0)])
    monkeypatch.setattr(storage, "atomic_write", real)
    assert store.get("characters/c.json") is None
    assert store.get("characters/b.json")


def test_ten_thousand_messages_bounded_append_and_pagination(tmp_path):
    store = JsonStore(tmp_path)
    segments = SegmentStore(store)
    story = str(uuid4())
    messages = [{"id": str(uuid4()), "text": f"Сообщение {n}"} for n in range(10000)]
    segments.append(story, messages)
    assert segments.page(story, 9990, 20)["items"] == messages[9990:]
    before = json.loads(store.pointer.read_bytes())
    segments.append(story, [{"id": str(uuid4()), "text": "Следующее"}])
    after = json.loads(store.pointer.read_bytes())
    changed = [k for k in after["entries"] if before["entries"].get(k) != after["entries"][k]]
    assert len(changed) == 2  # one bounded tail + head; never rewrite all 10k
    assert len([k for k in after["entries"] if "/messages/" in k]) == 79
    assert segments.page(story, 0, 10)["items"] == messages[:10]
    assert store.integrity()["ok"]


def test_consistent_reader_during_multi_file_publication(tmp_path):
    store = JsonStore(tmp_path)
    seed(store)

    def writer():
        for n in range(1, 15):
            a, b = store.get("characters/a.json"), store.get("characters/b.json")
            a["payload"]["n"] = b["payload"]["n"] = n
            store.transaction(
                [
                    Mutation("characters/a.json", a, a["revision"]),
                    Mutation("characters/b.json", b, b["revision"]),
                ]
            )

    with ThreadPoolExecutor(1) as pool:
        future = pool.submit(writer)
        for _ in range(50):
            with store.view() as view:
                assert view.get("characters/a.json")["payload"] == view.get("characters/b.json")["payload"]
        future.result()


def test_cancellation_before_commit_marker_keeps_multi_document_snapshot_and_recovers(tmp_path):
    store = JsonStore(tmp_path)
    a, b = seed(store)
    a["payload"]["n"] = b["payload"]["n"] = 1
    cancelled = False

    def fault(phase):
        nonlocal cancelled
        if phase == "prepared":
            cancelled = True

    def guard():
        if cancelled:
            raise AppError("task_cancelled", "Остановлено до фиксации.", 409)

    store.fault = fault
    with pytest.raises(AppError) as error:
        store.transaction(
            [Mutation("characters/a.json", a, 1), Mutation("characters/b.json", b, 1)], before_commit=guard
        )
    assert error.value.code == "task_cancelled"
    assert store.get("characters/a.json")["payload"]["n"] == 0
    assert store.get("characters/b.json")["payload"]["n"] == 0
    recovered = JsonStore(tmp_path)
    assert recovered.integrity()["ok"] and recovered.get("characters/a.json")["revision"] == 1
    recovered.transaction([Mutation("characters/a.json", a, 1), Mutation("characters/b.json", b, 1)])
    assert recovered.get("characters/a.json")["payload"]["n"] == 1
