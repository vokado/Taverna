import { useEffect, useRef, useState } from "react";
import { request } from "./api";
export type TemplateEditor = {
  template_id: string | null;
  source_revision: number;
  raw: string;
  payload: Record<string, unknown>;
};
export type TemplateDraft = {
  id: string;
  revision: number;
  payload: TemplateEditor;
};
export function useTemplateDraft(
  kind: string,
  editor: TemplateEditor,
  dirty: boolean,
  busy: boolean,
) {
  const [id, setId] = useState<string>(() => crypto.randomUUID());
  const [items, setItems] = useState<TemplateDraft[]>([]),
    [status, setStatus] = useState("");
  const versions = useRef<Record<string, number>>({}),
    committed = useRef<Record<string, string>>({});
  const writes = useRef<Promise<void>>(Promise.resolve());
  const current = useRef({ id, editor, dirty });
  current.current = { id, editor, dirty };
  const base = `/template-drafts/${kind}`;
  async function refresh() {
    const result = await request<{ items: TemplateDraft[]; errors: unknown[] }>(
      base,
    );
    for (const row of result.items) versions.current[row.id] = row.revision;
    setItems(result.items);
    if (result.errors.length)
      setStatus("Некоторые черновики повреждены. Проверьте целостность.");
  }
  function persist(snapshot: typeof current.current) {
    if (!snapshot.dirty) return writes.current;
    const fingerprint = JSON.stringify(snapshot.editor);
    const next = writes.current
      .catch(() => {})
      .then(async () => {
        if (committed.current[snapshot.id] === fingerprint) return;
        const saved = await request<TemplateDraft>(
          base + "/" + snapshot.id,
          "PUT",
          {
            ...snapshot.editor,
            expected_revision: versions.current[snapshot.id] || 0,
          },
        );
        versions.current[snapshot.id] = saved.revision;
        committed.current[snapshot.id] = fingerprint;
        setItems((old) => [saved, ...old.filter((d) => d.id !== saved.id)]);
        setStatus(
          JSON.stringify(current.current.editor) === fingerprint
            ? "Черновик шаблона автосохранён."
            : "Сохраняем следующую редакцию черновика…",
        );
      });
    writes.current = next;
    return next;
  }
  function flush() {
    return persist(JSON.parse(JSON.stringify(current.current)));
  }
  function create() {
    const next = crypto.randomUUID();
    current.current = { ...current.current, id: next, dirty: false };
    setId(next);
  }
  function adopt(saved: TemplateDraft) {
    versions.current[saved.id] = saved.revision;
    current.current = { ...current.current, id: saved.id };
    setId(saved.id);
  }
  async function remove() {
    const draftId = current.current.id;
    await writes.current;
    if (versions.current[draftId])
      await request(
        base + `/${draftId}?expected_revision=${versions.current[draftId]}`,
        "DELETE",
      );
    delete versions.current[draftId];
    delete committed.current[draftId];
    current.current.dirty = false;
    setItems((old) => old.filter((d) => d.id !== draftId));
    create();
  }
  useEffect(() => {
    void refresh().catch((e) => setStatus((e as Error).message));
  }, []);
  useEffect(() => {
    if (!dirty || busy) return;
    setStatus("Сохраняем черновик шаблона…");
    const snapshot = JSON.parse(JSON.stringify({ id, editor, dirty }));
    const timer = setTimeout(
      () =>
        void persist(snapshot).catch((e) =>
          setStatus("Автосохранение не выполнено: " + (e as Error).message),
        ),
      1000,
    );
    return () => clearTimeout(timer);
  }, [id, JSON.stringify(editor), dirty, busy]);
  useEffect(() => {
    const warn = (e: BeforeUnloadEvent) => {
      if (
        current.current.dirty &&
        committed.current[current.current.id] !==
          JSON.stringify(current.current.editor)
      ) {
        e.preventDefault();
        void flush().catch(() => {});
      }
    };
    window.addEventListener("beforeunload", warn);
    return () => {
      window.removeEventListener("beforeunload", warn);
      void flush().catch(() => {});
    };
  }, []);
  return { id, items, status, flush, create, adopt, remove, refresh };
}
