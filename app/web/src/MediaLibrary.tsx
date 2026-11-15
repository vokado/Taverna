import { useEffect, useRef, useState } from "react";
import type { components } from "./api-types";
import { request, upload } from "./api";
import { useTemplateDraft } from "./useTemplateDraft";
import { ImageGeneratePanel } from "./ImageGeneratePanel";
export type MediaMetadata = components["schemas"]["AssetMetadata"];
export type MediaAsset = {
  id: string;
  revision: number;
  payload: MediaMetadata & {
    type: string;
    checksum: string;
    relative_path: string;
    info: Record<string, unknown>;
    provenance: Record<string, unknown>;
  };
};
export const mediaCategories: Record<
  MediaMetadata["category"] & string,
  string
> = {
  portrait: "Портрет",
  illustration: "Иллюстрация",
  expression: "Выражение",
  costume: "Костюм",
  animation: "Анимация",
  background: "Фон",
  music: "Музыка",
  ambient: "Окружение",
};
function metadata(asset: MediaAsset): MediaMetadata {
  return {
    name: asset.payload.name,
    description: asset.payload.description || "",
    category: asset.payload.category || "portrait",
    tags: asset.payload.tags || [],
    archived: asset.payload.archived || false,
  };
}
export function MediaLibrary({ visible = true }: { visible?: boolean }) {
  const element = useRef<HTMLElement>(null);
  useEffect(() => {
    if (!visible)
      element.current?.querySelectorAll("audio").forEach((a) => a.pause());
  }, [visible]);
  const [items, setItems] = useState<MediaAsset[]>([]),
    [total, setTotal] = useState(0),
    [offset, setOffset] = useState(0),
    [query, setQuery] = useState(""),
    [tag, setTag] = useState(""),
    [category, setCategory] = useState(""),
    [archived, setArchived] = useState(false);
  const [selected, setSelected] = useState<MediaAsset | null>(null),
    [form, setForm] = useState<MediaMetadata | null>(null),
    [raw, setRaw] = useState(""),
    [dirty, setDirty] = useState(false),
    [sourceRevision, setSourceRevision] = useState(1),
    [busy, setBusy] = useState(false),
    [status, setStatus] = useState("");
  const [file, setFile] = useState<File | null>(null),
    [importCategory, setImportCategory] =
      useState<NonNullable<MediaMetadata["category"]>>("illustration"),
    [importName, setImportName] = useState(""),
    [importTags, setImportTags] = useState("");
  useEffect(() => {
    const nodes = Array.from(element.current?.querySelectorAll("audio") || []);
    return () => nodes.forEach((a) => a.pause());
  }, [selected?.id, visible]);
  const latest = useRef({ form, raw, dirty, selected, sourceRevision });
  latest.current = { form, raw, dirty, selected, sourceRevision };
  const seq = useRef(0);
  const draft = useTemplateDraft(
    "media",
    {
      template_id: selected?.id || null,
      source_revision: sourceRevision,
      raw,
      payload: (form || {}) as Record<string, unknown>,
    },
    dirty,
    busy,
    "/media/drafts",
  );
  function replace(
    asset: MediaAsset,
    value: MediaMetadata,
    version: number,
    text = "",
    isDirty = false,
  ) {
    latest.current = {
      form: value,
      raw: text,
      dirty: isDirty,
      selected: asset,
      sourceRevision: version,
    };
    setSelected(asset);
    setForm(value);
    setSourceRevision(version);
    setRaw(text);
    setDirty(isDirty);
  }
  function edit(patch: Partial<MediaMetadata>) {
    const old = latest.current;
    if (old.selected && old.form)
      replace(
        old.selected,
        { ...old.form, ...patch },
        old.sourceRevision,
        old.raw,
        true,
      );
  }
  async function load(at = offset) {
    const ticket = ++seq.current;
    const params = new URLSearchParams({
      query,
      offset: String(at),
      limit: "50",
      archived: String(archived),
    });
    if (tag) params.set("tag", tag);
    if (category) params.set("category", category);
    const r = await request<{
      items: MediaAsset[];
      total: number;
      errors: unknown[];
    }>("/media?" + params);
    if (ticket !== seq.current) return;
    setItems(r.items);
    setTotal(r.total);
    setOffset(at);
    if (r.errors.length)
      setStatus("Некоторые записи повреждены; проверьте целостность данных.");
  }
  useEffect(() => {
    if (visible) void load().catch((e) => setStatus((e as Error).message));
  }, [visible]);
  async function action(fn: () => Promise<void>) {
    setBusy(true);
    setStatus("");
    try {
      await fn();
    } catch (e) {
      setStatus((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function choose(asset: MediaAsset) {
    await draft.flush();
    draft.create();
    replace(asset, metadata(asset), asset.revision);
  }
  async function save() {
    const old = latest.current;
    if (!old.form || !old.selected) return;
    await draft.flush();
    const saved = await request<MediaAsset>(
      `/media/${old.selected.id}`,
      "PATCH",
      { ...old.form, expected_revision: old.sourceRevision },
    );
    replace(
      saved,
      metadata(saved),
      saved.revision,
      old.raw,
      Boolean(old.raw.trim()),
    );
    if (!old.raw.trim()) await draft.remove();
    await load();
    setStatus(
      "Метаданные медиа сохранены. Файл и исторические ссылки сохранены.",
    );
  }
  return (
    <section ref={element} aria-label="Медиабиблиотека">
      <p role="status">{[status, draft.status].filter(Boolean).join(" ")}</p>
      <p>
        Локальные изображения и аудио. Архивирование убирает ресурс из обычной
        галереи, сохраняя файл и ссылки истории. Изображения в списке показаны
        статическими миниатюрами.
      </p>
      <ImageGeneratePanel
        visible={visible}
        onGenerated={() => load(0)}
        onResult={choose}
      />
      <fieldset disabled={busy}>
        <details>
          <summary>Импорт собственного медиа</summary>
          <label>
            Медиафайл
            <input
              type="file"
              accept=".png,.jpg,.jpeg,.webp,.gif,.apng,.wav,.mp3,.ogg,.flac"
              onChange={(e) => {
                const f = e.target.files?.[0] || null;
                setFile(f);
                if (f) setImportName(f.name);
              }}
            />
          </label>
          <label>
            Название импортируемого медиа
            <input
              value={importName}
              onChange={(e) => setImportName(e.target.value)}
              maxLength={300}
            />
          </label>
          <label>
            Категория импортируемого медиа
            <select
              value={importCategory}
              onChange={(e) =>
                setImportCategory(e.target.value as typeof importCategory)
              }
            >
              {Object.entries(mediaCategories).map(([k, v]) => (
                <option key={k} value={k}>
                  {v}
                </option>
              ))}
            </select>
          </label>
          <label>
            Теги импортируемого медиа через запятую
            <input
              value={importTags}
              onChange={(e) => setImportTags(e.target.value)}
            />
          </label>
          <button
            disabled={!file || !importName.trim()}
            onClick={() =>
              void action(async () => {
                if (!file) return;
                const params = new URLSearchParams({
                  name: importName,
                  category: importCategory,
                  confirmed: "true",
                });
                importTags
                  .split(",")
                  .map((t) => t.trim())
                  .filter(Boolean)
                  .forEach((t) => params.append("tags", t));
                const asset = await upload<MediaAsset>(
                  "/media/import?" + params,
                  file,
                );
                await choose(asset);
                await load(0);
                setStatus("Медиа импортировано и проверено.");
              })
            }
          >
            Подтвердить импорт медиа
          </button>
          <p>
            До12МиБ; PNG/JPEG/WebP/GIF/APNG и WAV/MP3/Ogg/FLAC. SVG/HTML не
            принимаются.
          </p>
        </details>
        <label>
          Поиск медиа
          <input value={query} onChange={(e) => setQuery(e.target.value)} />
        </label>
        <label>
          Точный тег медиа
          <input value={tag} onChange={(e) => setTag(e.target.value)} />
        </label>
        <label>
          Фильтр категории медиа
          <select
            value={category}
            onChange={(e) => setCategory(e.target.value)}
          >
            <option value="">Все категории</option>
            {Object.entries(mediaCategories).map(([k, v]) => (
              <option key={k} value={k}>
                {v}
              </option>
            ))}
          </select>
        </label>
        <label>
          <input
            type="checkbox"
            checked={archived}
            onChange={(e) => setArchived(e.target.checked)}
          />
          Включая архив медиа
        </label>
        <button onClick={() => void action(() => load(0))}>Найти медиа</button>
        <p>
          Найдено: {total}; показано {items.length}.
        </p>
        <div className="media-grid">
          {items.map((asset) => (
            <article key={asset.id}>
              <strong>{asset.payload.name}</strong>
              {asset.payload.type.startsWith("image/") ? (
                <img
                  loading="lazy"
                  className="media-thumbnail"
                  src={`/api/v1/media/${asset.id}/thumbnail`}
                  alt={asset.payload.name}
                />
              ) : (
                <p>
                  Аудио: {String(asset.payload.info.duration_seconds || "?")} с
                </p>
              )}
              <p>
                {mediaCategories[asset.payload.category || "portrait"]};{" "}
                {asset.payload.tags?.join(", ")}
                {asset.payload.archived ? "; в архиве" : ""}
              </p>
              <button onClick={() => void action(() => choose(asset))}>
                Открыть медиа {asset.payload.name}
              </button>
            </article>
          ))}
        </div>
        <button
          disabled={offset === 0}
          onClick={() => void action(() => load(Math.max(0, offset - 50)))}
        >
          Предыдущие 50 медиа
        </button>
        <button
          disabled={offset + 50 >= total}
          onClick={() => void action(() => load(offset + 50))}
        >
          Следующие 50 медиа
        </button>
        {selected && form && (
          <article aria-label="Редактор медиаресурса">
            <h3>{selected.payload.name}</h3>
            <p>
              Исходная ревизия: {sourceRevision};{" "}
              {dirty
                ? "есть неопубликованные изменения"
                : "изменения опубликованы"}
              .
            </p>
            {selected.payload.type.startsWith("image/") ? (
              <img
                className="media-preview"
                src={`/api/v1/media/${selected.id}/file`}
                alt={selected.payload.name}
              />
            ) : (
              <audio
                controls
                preload="metadata"
                src={`/api/v1/media/${selected.id}/file`}
              />
            )}
            <label>
              Название медиаресурса
              <input
                value={form.name}
                maxLength={300}
                onChange={(e) => edit({ name: e.target.value })}
              />
            </label>
            <label>
              Описание медиаресурса
              <textarea
                value={form.description || ""}
                onChange={(e) => edit({ description: e.target.value })}
              />
            </label>
            <label>
              Категория медиаресурса
              <select
                value={form.category}
                onChange={(e) =>
                  edit({
                    category: e.target.value as MediaMetadata["category"],
                  })
                }
              >
                {Object.entries(mediaCategories).map(([k, v]) => (
                  <option key={k} value={k}>
                    {v}
                  </option>
                ))}
              </select>
            </label>
            <Tags value={form.tags || []} onChange={(tags) => edit({ tags })} />
            <label>
              <input
                type="checkbox"
                checked={form.archived}
                onChange={(e) => edit({ archived: e.target.checked })}
              />
              Убрать из обычной галереи
            </label>
            <button onClick={() => void action(save)}>
              Сохранить метаданные медиа
            </button>
            <details>
              <summary>Метаданные файла и происхождение</summary>
              <pre>
                {JSON.stringify(
                  {
                    info: selected.payload.info,
                    checksum: selected.payload.checksum,
                    provenance: selected.payload.provenance,
                  },
                  null,
                  2,
                )}
              </pre>
            </details>
            <details>
              <summary>JSON и черновики медиаресурса</summary>
              <button
                onClick={() => {
                  setRaw(JSON.stringify(form, null, 2));
                  setDirty(true);
                }}
              >
                Заполнить JSON метаданных
              </button>
              <label>
                JSON метаданных медиа
                <textarea
                  rows={8}
                  value={raw}
                  onChange={(e) => {
                    setRaw(e.target.value);
                    setDirty(true);
                  }}
                />
              </label>
              <button
                onClick={() =>
                  void action(async () => {
                    const valid = await request<MediaMetadata>(
                      "/media/drafts/validate",
                      "POST",
                      JSON.parse(raw),
                    );
                    edit(valid);
                    setStatus("JSON проверен и помещён в форму.");
                  })
                }
              >
                Проверить JSON метаданных
              </button>
              <button onClick={() => void action(draft.saveSeparate)}>
                Сохранить отдельный черновик медиа
              </button>
            </details>
          </article>
        )}
        <details>
          <summary>Сохранённые черновики медиабиблиотеки</summary>
          {draft.items.map((d) => (
            <p key={d.id}>
              <button
                onClick={() =>
                  void action(async () => {
                    const asset = await request<MediaAsset>(
                      `/media/${d.payload.template_id}/metadata`,
                    );
                    await draft.flush();
                    draft.adopt(d);
                    replace(
                      asset,
                      d.payload.payload as MediaMetadata,
                      d.payload.source_revision,
                      d.payload.raw,
                      true,
                    );
                    setStatus(
                      "Черновик медиа восстановлен с исходной ревизией.",
                    );
                  })
                }
              >
                Восстановить черновик медиа {d.id.slice(0, 8)}
              </button>
            </p>
          ))}
        </details>
      </fieldset>
    </section>
  );
}

function Tags({
  value,
  onChange,
}: {
  value: string[];
  onChange: (tags: string[]) => void;
}) {
  const [text, setText] = useState(value.join(", "));
  useEffect(() => setText(value.join(", ")), [JSON.stringify(value)]);
  return (
    <label>
      Теги медиаресурса через запятую
      <input
        value={text}
        onChange={(e) => {
          setText(e.target.value);
          onChange(
            e.target.value
              .split(",")
              .map((t) => t.trim())
              .filter(Boolean),
          );
        }}
      />
    </label>
  );
}
