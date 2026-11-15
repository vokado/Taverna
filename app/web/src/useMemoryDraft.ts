import { useEffect, useRef, useState } from "react";
import { request } from "./api";
import type { SavedDraft } from "./MemoryPanel";
type Editor = SavedDraft["payload"];
export function useMemoryDraft(
  base: string,
  branchId: string,
  editor: Editor,
  dirty: boolean,
  busy: boolean,
) {
  const [id, setId] = useState<string>(() => crypto.randomUUID()),
    [drafts, setDrafts] = useState<SavedDraft[]>([]),
    [savedId, setSavedId] = useState(""),
    [status, setStatus] = useState("");
  const versions = useRef<Record<string, number>>({}),
    committed = useRef<Record<string, string>>({}),
    writes = useRef<Promise<void>>(Promise.resolve()),
    alive = useRef(true);
  const current = useRef({ id, editor, dirty });
  current.current = { id, editor, dirty };
  const fingerprint = JSON.stringify(editor);
  async function refresh() {
    const value = await request<{ drafts: SavedDraft[]; errors: unknown[] }>(
      base + `/draft?branch_id=${branchId}`,
    );
    for (const row of value.drafts) versions.current[row.id] = row.revision;
    if (alive.current) {
      setDrafts(value.drafts);
      setSavedId((old) =>
        old && value.drafts.some((d) => d.id === old)
          ? old
          : value.drafts[0]?.id || "",
      );
      if (value.errors.length)
        setStatus(
          "Некоторые черновики повреждены. Проверьте целостность данных.",
        );
    }
  }
  function persist(snapshot: typeof current.current) {
    if (!snapshot.dirty) return writes.current.catch(() => {});
    const body = JSON.stringify(snapshot.editor),
      draftId = snapshot.id;
    const next = writes.current
      .catch(() => {})
      .then(async () => {
        if (committed.current[draftId] === body) return;
        if (alive.current) setStatus("Сохраняем черновик памяти…");
        const value = await request<{ draft: SavedDraft; revision: number }>(
          base + "/draft",
          "PUT",
          {
            ...snapshot.editor,
            draft_id: draftId,
            branch_id: branchId,
            expected_revision: versions.current[draftId] || 0,
          },
        );
        versions.current[draftId] = value.revision;
        committed.current[draftId] = body;
        if (alive.current) {
          setDrafts((old) => [
            value.draft,
            ...old.filter((d) => d.id !== draftId),
          ]);
          setSavedId(draftId);
          if (current.current.id === draftId)
            setStatus(
              JSON.stringify(current.current.editor) === body
                ? "Черновик памяти автосохранён."
                : "Сохраняем следующую редакцию черновика…",
            );
        }
      });
    writes.current = next;
    return next;
  }
  function saveNow() {
    return persist({
      ...current.current,
      editor: JSON.parse(JSON.stringify(current.current.editor)) as Editor,
    });
  }
  useEffect(() => {
    alive.current = true;
    void refresh().catch((e) => setStatus((e as Error).message));
    return () => {
      alive.current = false;
      void saveNow().catch(() => {});
    };
  }, []);
  useEffect(() => {
    if (!dirty || busy) return;
    const snapshot = { id, editor: JSON.parse(fingerprint) as Editor, dirty };
    const timer = setTimeout(
      () =>
        void persist(snapshot).catch((e) => {
          if (alive.current)
            setStatus("Автосохранение не выполнено: " + (e as Error).message);
        }),
      1000,
    );
    return () => clearTimeout(timer);
  }, [id, fingerprint, dirty, busy]);
  function create() {
    const next = crypto.randomUUID();
    current.current = { ...current.current, id: next, dirty: false };
    setId(next);
    setStatus("");
  }
  function adopt(value: SavedDraft) {
    versions.current[value.id] = value.revision;
    current.current = { ...current.current, id: value.id };
    setId(value.id);
    setStatus("");
  }
  async function remove(draftId: string) {
    await writes.current.catch(() => {});
    await request(base + "/draft", "DELETE", {
      branch_id: branchId,
      draft_id: draftId,
      expected_revision: versions.current[draftId] || 0,
    });
    delete versions.current[draftId];
    delete committed.current[draftId];
    if (current.current.id === draftId) current.current.dirty = false;
    if (alive.current) {
      setDrafts((old) => old.filter((d) => d.id !== draftId));
      setSavedId((old) => (old === draftId ? "" : old));
      setStatus("");
    }
  }
  async function saveSeparate() {
    const next = crypto.randomUUID();
    const snapshot = { ...current.current, id: next, dirty: true };
    current.current = snapshot;
    setId(next);
    await persist(snapshot);
  }
  return {
    id,
    drafts,
    saved: drafts.find((d) => d.id === savedId) || null,
    savedId,
    setSavedId,
    status,
    setStatus,
    refresh,
    saveNow,
    create,
    adopt,
    remove,
    saveSeparate,
  };
}
