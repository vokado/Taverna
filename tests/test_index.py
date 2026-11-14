import json

from taverna.storage import JsonStore, Mutation, document


def test_index_corruption_rebuild_does_not_change_sources(tmp_path):
    store = JsonStore(tmp_path)
    store.transaction([Mutation("characters/a.json", document("test", {"text": "Имя"}), 0)])
    source = store.get("characters/a.json")
    store.rebuild_index("names", "characters/", lambda doc: doc["payload"])
    manifest = json.loads(store.pointer.read_bytes())
    index_path = tmp_path / "data" / manifest["entries"]["indexes/names.json"]["object"]
    index_path.write_bytes(b"broken")
    index = store.rebuild_index("names", "characters/", lambda doc: doc["payload"])
    assert index["payload"]["rows"][0]["projection"] == {"text": "Имя"}
    assert store.get("characters/a.json") == source
    assert store.integrity()["ok"]
