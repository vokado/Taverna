import { useState } from "react";
import { request } from "./api";
type EventRecord = {
  id: string;
  name?: string;
  description?: string;
  [key: string]: unknown;
};
type Branch = { id: string; revision: number; payload: { name: string } };
type Transfer = {
  operation_id: string;
  branch_id: string;
  expected_revision: number;
  source_story_id: string;
  source_branch_id: string;
  source_revision: number;
  event_ids: string[];
  confirmed: boolean;
};
export function EventTransferPanel({
  stories,
  target,
  disabled,
  onApplied,
}: {
  stories: Array<{ id: string; payload: { name: string } }>;
  target: { storyId: string; branchId: string; revision: number };
  disabled: boolean;
  onApplied: () => Promise<void>;
}) {
  const [source, setSource] = useState("");
  const [branches, setBranches] = useState<Branch[]>([]);
  const [branch, setBranch] = useState("");
  const [revision, setRevision] = useState(0);
  const [events, setEvents] = useState<EventRecord[]>([]);
  const [selected, setSelected] = useState<string[]>([]);
  const [pending, setPending] = useState<Transfer | null>(null);
  const [preview, setPreview] = useState<EventRecord[] | null>(null);
  const [status, setStatus] = useState("");
  const [busy, setBusy] = useState(false);
  async function chooseStory(id: string) {
    setSource(id);
    setPending(null);
    setPreview(null);
    setEvents([]);
    setSelected([]);
    try {
      const result = await request<{ branches: Branch[] }>(
        `/stories/${id}/branches`,
      );
      setBranches(result.branches);
      setBranch("");
    } catch (e) {
      setStatus((e as Error).message);
    }
  }
  async function chooseBranch(id: string) {
    setBranch(id);
    setPending(null);
    setPreview(null);
    setSelected([]);
    try {
      const result = await request<{ events: EventRecord[]; revision: number }>(
        `/stories/${source}/transfer-events?branch_id=${id}`,
      );
      setEvents(result.events);
      setRevision(result.revision);
      setStatus(
        result.events.length
          ? "Выберите события для переноса."
          : "В выбранной ветви пока нет записанных событий.",
      );
    } catch (e) {
      setStatus((e as Error).message);
    }
  }
  async function prepare() {
    setBusy(true);
    try {
      const body: Transfer = {
        operation_id: crypto.randomUUID(),
        branch_id: target.branchId,
        expected_revision: target.revision,
        source_story_id: source,
        source_branch_id: branch,
        source_revision: revision,
        event_ids: selected,
        confirmed: false,
      };
      const result = await request<{ events: EventRecord[]; warning: string }>(
        `/stories/${target.storyId}/transfer-events`,
        "POST",
        body,
      );
      setPending(body);
      setPreview(result.events);
      setStatus(result.warning);
    } catch (e) {
      setStatus((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function confirm() {
    if (!pending) return;
    setBusy(true);
    try {
      await request(`/stories/${target.storyId}/transfer-events`, "POST", {
        ...pending,
        confirmed: true,
      });
      setPending(null);
      setPreview(null);
      setStatus(
        "Выбранные записи событий перенесены. Память и отношения не изменены.",
      );
      await onApplied();
    } catch (e) {
      setStatus((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <details>
      <summary>Явный выборочный перенос событий</summary>
      <p>
        Перенос записей не применяет последствия мира автоматически и не делает
        события известными всем NPC.
      </p>
      <fieldset disabled={disabled || busy}>
        <label>
          История-источник
          <select
            value={source}
            onChange={(e) => void chooseStory(e.target.value)}
          >
            <option value="">Выберите историю</option>
            {stories.map((s) => (
              <option key={s.id} value={s.id}>
                {s.payload.name}
              </option>
            ))}
          </select>
        </label>
        <label>
          Ветвь-источник
          <select
            value={branch}
            onChange={(e) => void chooseBranch(e.target.value)}
          >
            <option value="">Выберите ветвь</option>
            {branches
              .filter((b) => b.id !== target.branchId)
              .map((b) => (
                <option key={b.id} value={b.id}>
                  {b.payload.name}
                </option>
              ))}
          </select>
        </label>
        {events.map((event) => (
          <label className="inline" key={event.id}>
            <input
              type="checkbox"
              checked={selected.includes(event.id)}
              onChange={(e) => {
                setSelected((ids) =>
                  e.target.checked
                    ? [...ids, event.id]
                    : ids.filter((id) => id !== event.id),
                );
                setPreview(null);
                setPending(null);
              }}
            />
            {event.name || event.description || "Событие"}
          </label>
        ))}
        <button onClick={prepare} disabled={!selected.length}>
          Предпросмотр переноса событий
        </button>
        {preview && (
          <>
            <pre className="reply">{JSON.stringify(preview, null, 2)}</pre>
            <button onClick={confirm}>Подтвердить перенос событий</button>
            <button
              onClick={() => {
                setPending(null);
                setPreview(null);
              }}
            >
              Отказаться от переноса
            </button>
          </>
        )}
      </fieldset>
      <p role="status">{status}</p>
    </details>
  );
}
