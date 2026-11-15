import { useEffect, useRef, useState } from "react";
import { request, upload, downloadGet } from "./api";
import { OwnerReferences, type OwnerReference } from "./OwnerReferences";
import { useTemplateDraft, type TemplateDraft } from "./useTemplateDraft";
type Metadata = {
  name: string;
  description: string;
  tags: string[];
  references: OwnerReference[];
  archived: boolean;
};
type Row = {
  id: string;
  revision: number;
  payload: Metadata & {
    filename: string;
    text: string;
    probe: string;
    checksum: string;
  };
};
const blank = (): Metadata => ({
  name: "Новый материал",
  description: "",
  tags: [],
  references: [],
  archived: false,
});
export function Materials({ visible }: { visible: boolean }) {
  const [rows, setRows] = useState<Row[]>([]),
    [current, setCurrent] = useState<Row | null>(null),
    [metadata, setMetadata] = useState<Metadata>(blank),
    [sourceRevision, setSourceRevision] = useState(0),
    [sourceId, setSourceId] = useState<string | null>(null),
    [raw, setRaw] = useState(JSON.stringify(blank(), null, 2)),
    [dirty, setDirty] = useState(false),
    [query, setQuery] = useState(""),
    [archived, setArchived] = useState(false),
    [file, setFile] = useState<File | null>(null),
    [status, setStatus] = useState(""),
    [busy, setBusy] = useState(false);
  const working = useRef(false);
  const drafts = useTemplateDraft(
    "material",
    {
      template_id: sourceId,
      source_revision: sourceRevision,
      raw,
      payload: metadata as unknown as Record<string, unknown>,
    },
    dirty,
    busy,
    "/owner-drafts/material",
  );
  async function act(fn: () => Promise<void>) {
    if (working.current) return;
    working.current = true;
    setBusy(true);
    try {
      await fn();
    } catch (e) {
      setStatus((e as Error).message);
    } finally {
      working.current = false;
      setBusy(false);
    }
  }
  async function refresh() {
    setRows(
      (
        await request<{ items: Row[] }>(
          "/library?query=" +
            encodeURIComponent(query) +
            "&archived=" +
            archived,
        )
      ).items,
    );
  }
  useEffect(() => {
    if (visible) void refresh().catch((e) => setStatus(e.message));
  }, [visible]);
  function edit(patch: Partial<Metadata>) {
    const next = { ...metadata, ...patch };
    setMetadata(next);
    setRaw(JSON.stringify(next, null, 2));
    setDirty(true);
  }
  function load(row: Row) {
    setCurrent(row);
    setSourceId(row.id);
    setSourceRevision(row.revision);
    const { name, description, tags, references, archived } = row.payload;
    const next = { name, description, tags, references, archived };
    setMetadata(next);
    setRaw(JSON.stringify(next, null, 2));
    setDirty(false);
    drafts.create();
  }
  async function restore(d: TemplateDraft) {
    await drafts.flush();
    setSourceId(d.payload.template_id);
    setSourceRevision(d.payload.source_revision);
    setMetadata(d.payload.payload as unknown as Metadata);
    setRaw(d.payload.raw);
    setDirty(true);
    setCurrent(
      d.payload.template_id
        ? await request<Row>("/library/" + d.payload.template_id)
        : null,
    );
    drafts.adopt(d);
    setStatus("Черновик восстановлен с исходной ревизией.");
  }
  return (
    <section aria-label="Библиотека материалов">
      <h2>Библиотека документов и справочников</h2>
      <p>
        TXT/Markdown/JSON в UTF-8, PDF и DOCX без активных частей. Исходный файл
        скачивается как вложение; текстовый предпросмотр не исполняет разметку.
        Изображения доступны в медиабиблиотеке.
      </p>
      <p role="status">{status}</p>
      <p>{drafts.status}</p>
      <label>
        Поиск в материалах
        <input value={query} onChange={(e) => setQuery(e.target.value)} />
      </label>
      <label>
        <input
          type="checkbox"
          checked={archived}
          onChange={(e) => setArchived(e.target.checked)}
        />
        Включая архив
      </label>
      <button disabled={busy} onClick={() => void act(refresh)}>
        Найти материалы
      </button>
      <ul>
        {rows.map((row) => (
          <li key={row.id}>
            <button
              disabled={busy}
              onClick={() =>
                void act(async () => {
                  await drafts.flush();
                  load(row);
                })
              }
            >
              {row.payload.name} · {row.payload.filename}
            </button>
          </li>
        ))}
      </ul>
      <fieldset disabled={busy}>
        <legend>Импорт документа</legend>
        <input
          aria-label="Файл документа"
          type="file"
          accept=".txt,.md,.json,.pdf,.docx"
          onChange={(e) => setFile(e.target.files?.[0] || null)}
        />
        <button
          disabled={!file}
          onClick={() =>
            void act(async () => {
              await drafts.flush();
              const row = await upload<Row>(
                "/library/import?filename=" +
                  encodeURIComponent(file!.name) +
                  "&name=" +
                  encodeURIComponent(file!.name) +
                  "&confirmed=true",
                file!,
              );
              load(row);
              await refresh();
              setStatus("Документ импортирован с проверкой типа и checksum.");
            })
          }
        >
          Подтвердить импорт документа
        </button>
      </fieldset>
      {!!drafts.items.length && (
        <details>
          <summary>Черновики материалов</summary>
          {drafts.items.map((d) => (
            <button key={d.id} onClick={() => void act(() => restore(d))}>
              Восстановить материал {d.id.slice(0, 8)}
            </button>
          ))}
        </details>
      )}
      {sourceId && (
        <fieldset disabled={busy}>
          <legend>Метаданные материала · версия {sourceRevision}</legend>
          <label>
            Название материала
            <input
              value={metadata.name}
              onChange={(e) => edit({ name: e.target.value })}
            />
          </label>
          <label>
            Описание материала
            <textarea
              value={metadata.description}
              onChange={(e) => edit({ description: e.target.value })}
            />
          </label>
          <label>
            Теги материала, по строкам
            <textarea
              value={metadata.tags.join("\n")}
              onChange={(e) => edit({ tags: e.target.value.split("\n") })}
            />
          </label>
          <OwnerReferences
            value={metadata.references}
            onChange={(references) => edit({ references })}
          />
          <label>
            <input
              type="checkbox"
              checked={metadata.archived}
              onChange={(e) => edit({ archived: e.target.checked })}
            />
            Архивировать материал, сохранив файл
          </label>
          <details>
            <summary>JSON метаданных материала</summary>
            <textarea
              aria-label="JSON метаданных материала"
              value={raw}
              onChange={(e) => {
                setRaw(e.target.value);
                setDirty(true);
              }}
            />
          </details>
          <button
            onClick={() =>
              void act(async () => {
                await drafts.flush();
                const row = await request<Row>(
                  "/library/" + sourceId,
                  "PATCH",
                  { ...JSON.parse(raw), expected_revision: sourceRevision },
                );
                await drafts.remove();
                load(row);
                await refresh();
                setStatus("Метаданные материала сохранены.");
              })
            }
          >
            Сохранить метаданные материала
          </button>
          <button
            onClick={() =>
              void act(() => downloadGet("/library/" + sourceId + "/file"))
            }
          >
            Скачать исходный документ
          </button>
          <p>
            Проверка: {current?.payload.probe}; SHA256:{" "}
            {current?.payload.checksum}
          </p>
          <pre>
            {current?.payload.text ||
              "У PDF доступна проверка структуры и скачивание исходника; встроенный PDF viewer не запускается."}
          </pre>
        </fieldset>
      )}
    </section>
  );
}
