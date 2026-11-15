import { useState } from "react";
import { request } from "./api";
type Card = { id: string; payload: { fields: Record<string, unknown> } };
type Preview = {
  message_count: number;
  warnings: string[];
  unresolved_authors: string[];
  messages: Array<{ name: string; role: string; text: string }>;
};
export function ChatImportPanel({
  cards,
  onImported,
}: {
  cards: Card[];
  onImported: (id: string) => Promise<void>;
}) {
  const [text, setText] = useState("");
  const [format, setFormat] = useState("jsonl");
  const [name, setName] = useState("");
  const [selected, setSelected] = useState<string[]>([]);
  const [mapping, setMapping] = useState<Record<string, string>>({});
  const [preview, setPreview] = useState<Preview | null>(null);
  const [status, setStatus] = useState("");
  const [busy, setBusy] = useState(false);
  const [changed, setChanged] = useState(false);
  async function read(file: File) {
    try {
      if (file.size > 8 * 1024 * 1024) throw Error("Файл превышает 8 МиБ.");
      const decoded = new TextDecoder("utf-8", { fatal: true }).decode(
        await file.arrayBuffer(),
      );
      setText(decoded);
      setFormat(file.name.toLowerCase().endsWith(".jsonl") ? "jsonl" : "json");
      setName(file.name.replace(/\.[^.]+$/, ""));
      setPreview(null);
      setMapping({});
      setStatus("Файл прочитан. Выберите персонажей и выполните предпросмотр.");
    } catch {
      setStatus(
        "Не удалось прочитать файл. Требуется корректный UTF-8 JSON/JSONL размером до 8 МиБ.",
      );
    }
  }
  async function validate(confirmed = false) {
    setBusy(true);
    try {
      const result = await request<Preview & { story?: { id: string } }>(
        `/chat-import`,
        "POST",
        {
          format,
          text,
          name,
          character_ids: selected,
          author_map: mapping,
          confirmed,
        },
      );
      if (confirmed && result.story) {
        await onImported(result.story.id);
        setPreview(null);
        setText("");
        setStatus(
          "История импортирована независимо от существующих прохождений.",
        );
      } else {
        setPreview(result);
        setChanged(false);
        setStatus("Предпросмотр готов. Сохранение ещё не выполнялось.");
      }
    } catch (e) {
      setStatus((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <details className="chat-import">
      <summary>Импорт истории SillyTavern</summary>
      <p>
        JSONL или JSON-массив с полями mes/is_user. Неизвестные метаданные
        сохраняются; импорт не запускает модель или команды.
      </p>
      <label>
        Файл истории
        <input
          type="file"
          accept=".jsonl,.json"
          disabled={busy}
          onChange={(e) => {
            const file = e.target.files?.[0];
            if (file) void read(file);
            e.target.value = "";
          }}
        />
      </label>
      <label>
        Название импортированной истории
        <input
          value={name}
          onChange={(e) => {
            setName(e.target.value);
            setChanged(true);
          }}
          maxLength={200}
          disabled={busy}
        />
      </label>
      <fieldset disabled={busy}>
        <legend>Персонажи для сопоставления авторов</legend>
        {cards.map((card) => (
          <label className="inline" key={card.id}>
            <input
              type="checkbox"
              checked={selected.includes(card.id)}
              onChange={(e) => {
                setSelected((ids) =>
                  e.target.checked
                    ? [...ids, card.id]
                    : ids.filter((id) => id !== card.id),
                );
                setChanged(true);
              }}
            />
            {String(card.payload.fields.name)}
          </label>
        ))}
      </fieldset>
      <button
        onClick={() => void validate()}
        disabled={busy || !text || !name.trim() || !selected.length}
      >
        Предпросмотр истории
      </button>
      <p role="status">{status}</p>
      {preview && (
        <div>
          <h3>Сообщений: {preview.message_count}</h3>
          {preview.warnings.map((w) => (
            <p key={w}>{w}</p>
          ))}
          {preview.unresolved_authors.map((author) => (
            <label key={author}>
              Автор «{author}»
              <select
                value={mapping[author] || ""}
                onChange={(e) => {
                  setMapping((m) => ({ ...m, [author]: e.target.value }));
                  setChanged(true);
                }}
              >
                <option value="">Выберите персонажа</option>
                {cards
                  .filter((c) => selected.includes(c.id))
                  .map((c) => (
                    <option key={c.id} value={c.id}>
                      {String(c.payload.fields.name)}
                    </option>
                  ))}
              </select>
            </label>
          ))}
          {preview.messages.map((message, i) => (
            <blockquote key={i}>
              <strong>{message.name || "Автор"}:</strong>
              <pre className="reply">{message.text}</pre>
            </blockquote>
          ))}
          {changed && (
            <p>
              Параметры изменены. Повторите предпросмотр перед подтверждением.
            </p>
          )}
          <button
            onClick={() => void validate(true)}
            disabled={busy || changed || preview.unresolved_authors.length > 0}
          >
            Подтвердить импорт истории
          </button>
          <button onClick={() => setPreview(null)} disabled={busy}>
            Отказаться от импорта истории
          </button>
        </div>
      )}
    </details>
  );
}
