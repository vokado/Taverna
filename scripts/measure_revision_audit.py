"""Reproducible local I/O/latency measurements; never calls an LLM or GPU.

Synthetic UTF-8 histories use the real JSON importer, storage, queue and reducers.
Counters measure application atomic-write bytes, not physical device writes.
Run alone (no pytest/browser workload) for comparable before/after timings.
"""

from __future__ import annotations

import argparse
import asyncio
import copy
import json
import os
import platform
import statistics
import subprocess
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

from taverna import storage
from taverna.cards import new_card
from taverna.characters import CharacterRequest, CharacterService
from taverna.chat import ChatService
from taverna.chat_import import ChatImport, import_chat
from taverna.generation_api import GenerationService
from taverna.jobs import JobQueue
from taverna.memory import MemoryService
from taverna.memory_index import MemoryIndex
from taverna.prompts import PromptBuilder
from taverna.storage import JsonStore, Mutation, document


def footprint(store):
    files = [p for p in store.data.rglob("*") if p.is_file()]
    manifest = json.loads(store.pointer.read_bytes())
    return {
        "files": len(files),
        "total_bytes": sum(p.stat().st_size for p in files),
        "journal_bytes": sum(p.stat().st_size for p in files if "transactions" in p.parts),
        "manifest_bytes": store.pointer.stat().st_size,
        "live_entries": len(manifest["entries"]),
        "index_entries": sum(k.startswith("indexes/memory/") for k in manifest["entries"]),
        "intent_files": sum(p.name == "intent.json" for p in files),
        "object_files": sum(
            p.name.endswith(".json") and p not in (store.pointer,) and "transactions" not in p.parts
            for p in files
        ),
    }


@contextmanager
def counters(store):
    values = {
        "atomic_writes": 0,
        "atomic_write_bytes": 0,
        "fsync_calls": 0,
        "object_reads": 0,
        "recovery_calls": 0,
    }
    write, fsync, read, recover = storage.atomic_write, os.fsync, store._read, store._recover

    def tracked_write(path, raw):
        values["atomic_writes"] += 1
        values["atomic_write_bytes"] += len(raw)
        return write(path, raw)

    def tracked_fsync(fd):
        values["fsync_calls"] += 1
        return fsync(fd)

    def tracked_read(entry):
        values["object_reads"] += 1
        return read(entry)

    def tracked_recover():
        values["recovery_calls"] += 1
        return recover()

    storage.atomic_write, os.fsync, store._read, store._recover = (
        tracked_write,
        tracked_fsync,
        tracked_read,
        tracked_recover,
    )
    started = time.perf_counter()
    try:
        yield values
    finally:
        values["seconds"] = time.perf_counter() - started
        storage.atomic_write, os.fsync, store._read, store._recover = write, fsync, read, recover


def timed(store, operation, repeats=3):
    timings = []
    counter_rows = []
    for _ in range(repeats):
        with counters(store) as row:
            operation()
        timings.append(row["seconds"])
        counter_rows.append(row.copy())
    return {
        "seconds": timings,
        "median_seconds": statistics.median(timings),
        "max_seconds": max(timings),
        "counters": counter_rows,
    }


def seeded(root, count):
    store = JsonStore(root)
    generation = GenerationService(store)
    chars = CharacterService(store, generation)
    cid = chars.save(CharacterRequest(expected_revision=0, character=new_card("Мира")))["id"]
    chat = ChatService(store, generation)
    rows = [
        {
            "name": "Игрок",
            "is_user": True,
            "mes": f"Сообщение {i}: " + ("гавань дождь тихий вечер. " * 8),
            "send_date": "2026-01-01T00:00:00Z",
        }
        for i in range(count)
    ]
    imported = import_chat(
        chat,
        ChatImport(
            format="json",
            text=json.dumps(rows, ensure_ascii=False),
            name="Измерение",
            character_ids=[cid],
            confirmed=True,
        ),
    )
    return chat, imported["story"]["id"], cid


def history_case(root, count):
    chat, sid, cid = seeded(root, count)
    store = chat.store
    branch = chat.branch(sid)
    prefix = timed(store, lambda: chat._prefix(sid, branch["id"]))
    history = chat._prefix(sid, branch["id"])
    story = chat.story(sid)
    state = chat.state(sid)
    resolved = chat.generation.profiles.resolve(character_id=cid, story_id=sid)
    built = []
    prompt = timed(
        store,
        lambda: built.append(
            PromptBuilder().build(
                story=story, branch=branch, state=state, character_id=cid, history=history, resolved=resolved
            )
        ),
    )
    append = timed(
        store,
        lambda: chat.append(
            sid, "Следующее сообщение", role="user", expected_revision=chat.branch(sid)["revision"]
        ),
        repeats=1,
    )
    fork = timed(store, lambda: chat.fork(sid, expected_revision=chat.branch(sid)["revision"]), repeats=1)
    return {
        "messages": count,
        "prefix": prefix,
        "prompt_builder": prompt,
        "append_one": append,
        "fork": fork,
        "footprint": footprint(store),
        "prompt_output_messages": len(built[-1]["messages"]),
    }


def distinct_states_case(root, count):
    chat, sid, _ = seeded(root, count)
    store = chat.store
    branch = chat.branch(sid)
    head = store.get(chat.segments._key(branch["id"]))
    base = chat.state(sid)
    changes = []
    last = None
    for key in head["payload"]["segments"]:
        segment = store.get(key)
        for message in segment["payload"]["messages"]:
            state = document("branch_state", copy.deepcopy(base["payload"]))
            changes.append(Mutation(chat.state_key(sid, state["id"]), state, 0))
            message["state_id"] = state["id"]
            last = state["id"]
        changes.append(Mutation(key, segment, segment["revision"]))
    branch["payload"]["state_id"] = last
    branch["refs"] = list(dict.fromkeys([*branch["refs"], chat.state_key(sid, last)]))
    store.transaction([*changes, Mutation(chat.branch_key(sid, branch["id"]), branch, branch["revision"])])
    return {
        "messages_and_distinct_states": count,
        "fork": timed(store, lambda: chat.fork(sid, expected_revision=chat.branch(sid)["revision"]), 1),
        "footprint": footprint(store),
    }


def index_case(root, count, updates):
    chat, sid, _ = seeded(root, count)
    service = MemoryService(chat)
    index = MemoryIndex(service)
    store = chat.store
    bid = chat.branch(sid)["id"]
    first = timed(store, lambda: index.rebuild(service.snapshot(sid, bid)), 1)
    before = footprint(store)
    rows = []
    for i in range(updates):
        chat.append(
            sid,
            f"Новый индексируемый эпизод {i}",
            role="user",
            expected_revision=chat.branch(sid)["revision"],
        )
        snap = service.snapshot(sid, bid)
        with counters(store) as row:
            index.rebuild(snap)
        rows.append(row.copy())
    return {
        "initial_messages": count,
        "updates": updates,
        "initial_build": first,
        "updates_counters": rows,
        "before": before,
        "after": footprint(store),
    }


async def event_case(root, count):
    store = JsonStore(root)
    queue = JobQueue(store)
    # A storage-only queue record; no LLM adapter and no synthetic inference result.
    job = document(
        "job",
        {
            "kind": "measurement",
            "request": {},
            "state": "running",
            "event_count": 0,
            "result": None,
            "error": None,
        },
    )
    store.transaction([Mutation(queue.key(job["id"]), job, 0)])
    samples = []
    started = time.perf_counter()
    with counters(store) as total:
        for i in range(count):
            await queue.emit(job["id"], "delta", {"text": "Таверна "})
            if (i + 1) in {100, 500, 1000, count}:
                samples.append(
                    {
                        "fragments": i + 1,
                        "elapsed_seconds": time.perf_counter() - started,
                        "footprint": footprint(store),
                    }
                )
        await queue.emit(job["id"], "completed", {}, state="completed", result={"measurement": True})
    return {
        "fragments": count,
        "source_utf8_bytes": count * len("Таверна ".encode()),
        "counters": total,
        "samples": samples,
        "after": footprint(store),
    }


def growth_case(root, count, unique):
    store = JsonStore(root)
    samples = []
    with counters(store) as total:
        started = time.perf_counter()
        for i in range(count):
            key = f"config/measurement-{i if unique else 0}.json"
            value = document("measurement", {"counter": i, "text": "данные" * 16})
            store.transaction([Mutation(key, value, 0 if unique else i)])
            if (i + 1) in {100, 500, 1000, count}:
                samples.append(
                    {
                        "operations": i + 1,
                        "elapsed_seconds": time.perf_counter() - started,
                        "footprint": footprint(store),
                        "read": timed(store, lambda key=key: store.get(key), 3),
                    }
                )
    return {
        "operations": count,
        "unique_keys": unique,
        "counters": total,
        "samples": samples,
        "after": footprint(store),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--part", choices=["all", "events", "growth", "history", "index", "fork"], default="all"
    )
    parser.add_argument("--operations", type=int, default=2000)
    parser.add_argument("--fragments", type=int, default=1000)
    parser.add_argument("--index-updates", type=int, default=20)
    parser.add_argument("--histories", type=int, nargs="+", default=[1000, 5000, 10000])
    args = parser.parse_args()
    result = {
        "commit": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        "platform": platform.platform(),
        "python": platform.python_version(),
        "note": "Synthetic local corpus, real storage/queue/importer; no LM Studio/GPU; bytes are application atomic-write bytes, not physical disk writes. Run without concurrent local tests.",
        "cases": {},
    }

    def save(name, value):
        result["cases"][name] = value
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"case": name, "done": True}, ensure_ascii=False), flush=True)

    with tempfile.TemporaryDirectory(prefix="taverna-revision-audit-") as folder:
        root = Path(folder)
        if args.part in ("all", "events"):
            save("events", asyncio.run(event_case(root / "events", args.fragments)))
        if args.part in ("all", "growth"):
            for unique in (False, True):
                save(
                    "growth-unique-" + str(unique),
                    growth_case(root / ("growth-" + str(unique)), args.operations, unique),
                )
        if args.part in ("all", "history"):
            for count in args.histories:
                save("history-" + str(count), history_case(root / ("history-" + str(count)), count))
        if args.part in ("all", "fork"):
            save("distinct-state-fork", distinct_states_case(root / "states", 1000))
        if args.part in ("all", "index"):
            save("index", index_case(root / "index", 10000, args.index_updates))


if __name__ == "__main__":
    main()
