import copy
import json
from uuid import uuid4

import pytest
from taverna.chat_import import ChatImport, import_chat
from taverna.core import AppError
from taverna.memory_index import MemoryIndex
from taverna.storage import Mutation, document
from test_chat import append
from test_memory import save, setup_memory


def history(tmp_path, count=600):
    service, chat, _, ids = setup_memory(tmp_path)
    rows = [
        {"name": "Игрок", "is_user": True, "mes": f"Гавань {i}", "send_date": "2026-01-01T00:00:00Z"}
        for i in range(count)
    ]
    result = import_chat(
        chat,
        ChatImport(format="json", text=json.dumps(rows), name="История", character_ids=ids, confirmed=True),
    )
    return service, chat, result["story"]["id"], ids


def object_hashes(store, keys):
    with store.view() as view:
        return {key: view.manifest["entries"][key]["sha256"] for key in keys}


def test_index_reuses_unchanged_tokens_and_shards_and_removes_legacy_generations(tmp_path, monkeypatch):
    import taverna.memory_index as module

    service, chat, sid, ids = history(tmp_path)
    store = chat.store
    bid = chat.branch(sid)["id"]
    index = MemoryIndex(service)
    head = index.rebuild(service.snapshot(sid, bid))
    keys = head["payload"]["shards"]
    original = object_hashes(store, keys)
    calls = []
    words = module.words
    monkeypatch.setattr(module, "words", lambda text: (calls.append(text), words(text))[1])
    append(chat, sid, "Новый эпизод")
    snap = service.snapshot(sid, bid)
    head = index.rebuild(snap)
    assert len(calls) == 1 and calls[0] == "Новый эпизод"
    hashes = object_hashes(store, keys)
    assert sum(original[k] != hashes[k] for k in keys) == 1
    calls.clear()
    save(service, sid, ids, "Доступный факт")
    head = index.rebuild(service.snapshot(sid, bid))
    assert calls == ["Доступный факт"]
    assert object_hashes(store, keys) == hashes
    # Insert a genuine old-format generation and point the index head at it.
    legacy_key = f"indexes/memory/{bid}/legacy-fingerprint/shard-000000.json"
    legacy = document("memory_index_shard", {"entries": []})
    current = store.get(index.key(bid))
    current["payload"]["fingerprint"] = "legacy"
    current["payload"]["shards"] = [legacy_key]
    current["refs"] = [legacy_key]
    store.transaction(
        [Mutation(legacy_key, legacy, 0), Mutation(index.key(bid), current, current["revision"])]
    )
    before = chat._prefix(sid, bid)
    repaired = index.rebuild(service.snapshot(sid, bid))
    assert store.get(legacy_key) is None
    assert repaired["payload"]["format_version"] == 2
    assert len(store.scan(f"indexes/memory/{bid}/")[0]) == len(repaired["payload"]["shards"]) + 1
    generation = json.loads(store.pointer.read_bytes())["generation"]
    assert index.rebuild(service.snapshot(sid, bid)) == repaired
    assert json.loads(store.pointer.read_bytes())["generation"] == generation
    assert chat._prefix(sid, bid) == before and store.integrity()["ok"]


def test_cache_head_and_shard_are_read_from_one_snapshot_and_corruption_falls_back(tmp_path):
    service, chat, sid, ids = history(tmp_path, 260)
    store = chat.store
    bid = chat.branch(sid)["id"]
    index = MemoryIndex(service)
    snap = service.snapshot(sid, bid)
    head = index.rebuild(snap)
    shard = store.get(head["payload"]["shards"][0])
    shard["payload"]["entries"] = [{"id": str(uuid4()), "kind": "message", "tokens": [7]}]
    store.transaction([Mutation(head["payload"]["shards"][0], shard, shard["revision"])])
    assert index.search(snap, "Гавань", ids[0], include_messages=True)["total"] == 260
    index.rebuild(snap)
    assert index.search(snap, "259", ids[0], include_messages=True)["total"] == 1


def test_fork_many_distinct_states_uses_bounded_manifest_reads_and_preserves_scope(tmp_path, monkeypatch):
    _, chat, sid, ids = history(tmp_path, 120)
    store = chat.store
    parent = chat.branch(sid)
    state = chat.state(sid)
    head = store.get(chat.segments._key(parent["id"]))
    changes = []
    for key in head["payload"]["segments"]:
        segment = store.get(key)
        for index, message in enumerate(segment["payload"]["messages"]):
            payload = copy.deepcopy(state["payload"])
            payload["world"] = {"value": index}
            snapshot = document("branch_state", payload)
            changes.append(Mutation(chat.state_key(sid, snapshot["id"]), snapshot, 0))
            message["state_id"] = snapshot["id"]
        changes.append(Mutation(key, segment, segment["revision"]))
    store.transaction(changes)
    calls = []
    recover = store._recover

    def count():
        calls.append(True)
        return recover()

    monkeypatch.setattr(store, "_recover", count)
    branch = chat.fork(sid, expected_revision=parent["revision"])["branch"]
    assert len(calls) < 25
    original = chat._prefix(sid, parent["id"])
    copied = chat._prefix(sid, branch["id"])
    assert len(copied) == len(original) == 120
    assert not {m["id"] for m in original} & {m["id"] for m in copied}
    assert all(m["branch_id"] == branch["id"] for m in copied)
    assert all(original[i]["id"] in m["source_message_ids"] for i, m in enumerate(copied))
    assert store.integrity()["ok"]


def test_fork_checks_captured_source_revisions_before_publication(tmp_path, monkeypatch):
    _, chat, sid, _ = history(tmp_path, 2)
    store = chat.store
    branch = chat.branch(sid)
    original = store.transaction
    entered = False
    key = chat.state_key(sid, branch["payload"]["initial_state_id"])

    def concurrent_update(changes, **kwargs):
        nonlocal entered
        if not entered:
            entered = True
            row = store.get(key)
            row["payload"]["world"] = {"changed": True}
            original([Mutation(key, row, row["revision"])])
        return original(changes, **kwargs)

    monkeypatch.setattr(store, "transaction", concurrent_update)
    with pytest.raises(AppError) as error:
        chat.fork(sid, expected_revision=branch["revision"])
    assert error.value.code == "storage_conflict"
    assert chat.branch(sid)["id"] == branch["id"]
    assert store.get(key)["payload"]["world"] == {"changed": True}


@pytest.mark.parametrize("entries", [[{"kind": "message", "tokens": ["Гавань"]}], {}])
def test_malformed_cached_entry_shape_never_disables_source_search(tmp_path, entries):
    service, chat, sid, ids = history(tmp_path, 2)
    store = chat.store
    bid = chat.branch(sid)["id"]
    index = MemoryIndex(service)
    snap = service.snapshot(sid, bid)
    head = index.rebuild(snap)
    key = head["payload"]["shards"][0]
    row = store.get(key)
    row["payload"]["entries"] = entries
    store.transaction([Mutation(key, row, row["revision"])])
    assert index.search(snap, "Гавань", ids[0], include_messages=True)["total"] == 2
    index.rebuild(snap)
    assert index.search(snap, "Гавань", ids[0], include_messages=True)["total"] == 2
