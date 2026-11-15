import { useEffect, useRef, useState } from "react";
import { request } from "./api";
import { OwnerReferences, type OwnerReference } from "./OwnerReferences";
import { useTemplateDraft, type TemplateDraft } from "./useTemplateDraft";
import { StoryWorld } from "./StoryWorld";
type Kind = "note" | "bookmark" | "dictionary" | "name_generator" | "template";
type Entry = {
  format: 1;
  kind: Kind;
  title: string;
  text: string;
  references: OwnerReference[];
  links: { label: string; target: string }[];
  archived: boolean;
  generator: {
    prefixes: string[];
    middles: string[];
    suffixes: string[];
  } | null;
  template: {
    action: "note" | "chapter";
    text: string;
    variables: string[];
    grants: string[];
  } | null;
};
type Row = { id: string; revision: number; payload: Entry };
type Preview = {
  id: string;
  revision: number;
  payload: {
    status: string;
    title: string;
    text: string;
    action: string;
    required_permission: string;
    permission_granted: boolean;
  };
};
const labels: Record<Kind, string> = {
  note: "Заметка",
  bookmark: "Закладка",
  dictionary: "Словарь мира",
  name_generator: "Генератор имён",
  template: "Пользовательский шаблон",
};
const blank = (kind: Kind): Entry => ({
  format: 1,
  kind,
  title: labels[kind],
  text: "",
  references: [],
  links: [],
  archived: false,
  generator:
    kind === "name_generator"
      ? {
          prefixes: ["Аль", "Мир", "Вел"],
          middles: ["а", "и", ""],
          suffixes: ["на", "дар", "ис"],
        }
      : null,
  template:
    kind === "template"
      ? {
          action: "note",
          text: "Привет, {{имя}}!",
          variables: ["имя"],
          grants: [],
        }
      : null,
});
export function Tools({ visible }: { visible: boolean }) {
  const [rows, setRows] = useState<Row[]>([]),
    [entry, setEntry] = useState<Entry>(() => blank("note")),
    [id, setId] = useState<string | null>(null),
    [revision, setRevision] = useState(0),
    [raw, setRaw] = useState(JSON.stringify(blank("note"), null, 2)),
    [dirty, setDirty] = useState(false),
    [query, setQuery] = useState(""),
    [status, setStatus] = useState(""),
    [busy, setBusy] = useState(false),
    [archived, setArchived] = useState(false);
  const [seed, setSeed] = useState("Таверна"),
    [count, setCount] = useState(10),
    [names, setNames] = useState<string[]>([]),
    [inputs, setInputs] = useState<Record<string, string>>({}),
    [books, setBooks] = useState<
      { id: string; revision: number; payload: { title: string } }[]
    >([]),
    [book, setBook] = useState(""),
    [chapterTitle, setChapterTitle] = useState("Новая глава"),
    [previews, setPreviews] = useState<Preview[]>([]);
  const [stories, setStories] = useState<
      { id: string; payload: { name: string } }[]
    >([]),
    [story, setStory] = useState(""),
    [branch, setBranch] = useState<{ id: string; revision: number } | null>(
      null,
    );
  const drafts = useTemplateDraft(
    "tool",
    {
      template_id: id,
      source_revision: revision,
      raw,
      payload: entry as unknown as Record<string, unknown>,
    },
    dirty,
    busy,
    "/owner-drafts/tool",
  );
  const [bookQuery, setBookQuery] = useState("");
  const working = useRef(false);
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
          "/tools?query=" + encodeURIComponent(query) + "&archived=" + archived,
        )
      ).items,
    );
    setPreviews((await request<{ items: Preview[] }>("/tools/previews")).items);
  }
  useEffect(() => {
    if (visible) void refresh().catch((e) => setStatus(e.message));
  }, [visible]);
  function edit(next: Entry) {
    setEntry(next);
    setRaw(JSON.stringify(next, null, 2));
    setDirty(true);
  }
  function load(row: Row) {
    setId(row.id || null);
    setRevision(row.revision);
    setEntry(row.payload);
    setRaw(JSON.stringify(row.payload, null, 2));
    setDirty(false);
    setNames([]);
    setInputs({});
    drafts.create();
  }
  async function restore(d: TemplateDraft) {
    await drafts.flush();
    setId(d.payload.template_id);
    setRevision(d.payload.source_revision);
    setEntry(d.payload.payload as unknown as Entry);
    setRaw(d.payload.raw);
    setDirty(true);
    drafts.adopt(d);
  }
  async function loadBranch(sid: string) {
    const r = await request<{ branch: { id: string; revision: number } }>(
      "/stories/" + sid,
    );
    setStory(sid);
    setBranch(r.branch);
  }
  return (
    <section aria-label="Заметки и инструменты">
      <h2>Заметки и инструменты</h2>
      <p role="status">{status}</p>
      <p>{drafts.status}</p>
      <label>
        Поиск в инструментах
        <input value={query} onChange={(e) => setQuery(e.target.value)} />
      </label>
      <label>
        <input
          type="checkbox"
          checked={archived}
          onChange={(e) => setArchived(e.target.checked)}
        />
        Показывать архивные записи
      </label>
      <button disabled={busy} onClick={() => void act(refresh)}>
        Найти записи
      </button>
      <div className="toolbar">
        {Object.entries(labels).map(([kind, label]) => (
          <button
            key={kind}
            disabled={busy}
            onClick={() =>
              void act(async () => {
                await drafts.flush();
                load({ id: "", revision: 0, payload: blank(kind as Kind) });
              })
            }
          >
            Создать: {label}
          </button>
        ))}
      </div>
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
              {labels[row.payload.kind]}: {row.payload.title}
            </button>
          </li>
        ))}
      </ul>
      {!!drafts.items.length && (
        <details>
          <summary>Черновики инструментов</summary>
          {drafts.items.map((d) => (
            <button key={d.id} onClick={() => void act(() => restore(d))}>
              Восстановить инструмент {d.id.slice(0, 8)}
            </button>
          ))}
        </details>
      )}
      <fieldset disabled={busy}>
        <legend>
          {labels[entry.kind]} · версия {revision}
        </legend>
        <label>
          Название записи
          <input
            value={entry.title}
            onChange={(e) => edit({ ...entry, title: e.target.value })}
          />
        </label>
        <label>
          {entry.kind === "dictionary"
            ? "Определение и пояснения"
            : "Текст записи"}
          <textarea
            value={entry.text}
            onChange={(e) => edit({ ...entry, text: e.target.value })}
          />
        </label>
        <OwnerReferences
          value={entry.references}
          onChange={(references) => edit({ ...entry, references })}
        />
        <h4>Ссылки записи</h4>
        {entry.links.map((link, i) => (
          <div key={i}>
            <label>
              Подпись закладки
              <input
                value={link.label}
                onChange={(e) =>
                  edit({
                    ...entry,
                    links: entry.links.map((v, n) =>
                      n === i ? { ...v, label: e.target.value } : v,
                    ),
                  })
                }
              />
            </label>
            <label>
              URL закладки
              <input
                value={link.target}
                onChange={(e) =>
                  edit({
                    ...entry,
                    links: entry.links.map((v, n) =>
                      n === i ? { ...v, target: e.target.value } : v,
                    ),
                  })
                }
              />
            </label>
            <button
              onClick={() =>
                edit({ ...entry, links: entry.links.filter((_, n) => n !== i) })
              }
            >
              Убрать закладку
            </button>
            <a
              href={
                link.target.startsWith("https://") ||
                link.target.startsWith("http://")
                  ? link.target
                  : undefined
              }
              target="_blank"
              rel="noopener noreferrer"
            >
              Открыть внешнюю ссылку
            </a>
          </div>
        ))}
        <button
          onClick={() =>
            edit({
              ...entry,
              links: [
                ...entry.links,
                { label: "Закладка", target: "https://example.org" },
              ],
            })
          }
        >
          Добавить URL
        </button>
        {entry.generator && (
          <div>
            {(["prefixes", "middles", "suffixes"] as const).map((key, i) => (
              <label key={key}>
                {["Начала имён", "Середины имён", "Окончания имён"][i]}, по
                строкам
                <textarea
                  value={entry.generator![key].join("\n")}
                  onChange={(e) =>
                    edit({
                      ...entry,
                      generator: {
                        ...entry.generator!,
                        [key]: e.target.value.split("\n"),
                      },
                    })
                  }
                />
              </label>
            ))}
          </div>
        )}
        {entry.template && (
          <div>
            <label>
              Действие шаблона
              <select
                value={entry.template.action}
                onChange={(e) =>
                  edit({
                    ...entry,
                    template: {
                      ...entry.template!,
                      action: e.target.value as "note" | "chapter",
                    },
                  })
                }
              >
                <option value="note">Создать заметку</option>
                <option value="chapter">Добавить главу в рукопись</option>
              </select>
            </label>
            <label>
              Текст шаблона, переменные в двойных фигурных скобках
              <textarea
                value={entry.template.text}
                onChange={(e) =>
                  edit({
                    ...entry,
                    template: { ...entry.template!, text: e.target.value },
                  })
                }
              />
            </label>
            <label>
              Объявленные переменные, по строкам
              <textarea
                value={entry.template.variables.join("\n")}
                onChange={(e) =>
                  edit({
                    ...entry,
                    template: {
                      ...entry.template!,
                      variables: e.target.value.split("\n"),
                    },
                  })
                }
              />
            </label>
            <p>
              Разрешения этого шаблона выдаёт владелец. При изменении/отзыве
              старый предпросмотр использовать нельзя.
            </p>
            {["notes.write", "manuscripts.write"].map((permission) => (
              <label key={permission}>
                <input
                  type="checkbox"
                  checked={entry.template!.grants.includes(permission)}
                  onChange={(e) =>
                    edit({
                      ...entry,
                      template: {
                        ...entry.template!,
                        grants: e.target.checked
                          ? [...entry.template!.grants, permission]
                          : entry.template!.grants.filter(
                              (p) => p !== permission,
                            ),
                      },
                    })
                  }
                />
                {permission === "notes.write"
                  ? "Разрешить создание заметки"
                  : "Разрешить добавление главы"}
              </label>
            ))}
          </div>
        )}
        <label>
          <input
            type="checkbox"
            checked={entry.archived}
            onChange={(e) => edit({ ...entry, archived: e.target.checked })}
          />
          Архивировать запись
        </label>
        <details>
          <summary>JSON записи инструмента</summary>
          <textarea
            aria-label="JSON записи инструмента"
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
              const saved = await request<Row>(
                "/tools/" + (id || crypto.randomUUID()),
                "PUT",
                { expected_revision: revision, payload: JSON.parse(raw) },
              );
              await drafts.remove();
              load(saved);
              await refresh();
              setStatus("Запись инструмента опубликована.");
            })
          }
        >
          Сохранить запись инструмента
        </button>
      </fieldset>
      {entry.generator && (
        <fieldset disabled={busy || dirty || !id}>
          <legend>Запустить генератор имён</legend>
          <label>
            Seed генератора
            <input value={seed} onChange={(e) => setSeed(e.target.value)} />
          </label>
          <label>
            Количество имён
            <input
              type="number"
              min={1}
              max={100}
              value={count}
              onChange={(e) => setCount(Number(e.target.value))}
            />
          </label>
          <button
            onClick={() =>
              void act(async () => {
                const run = await request<{ payload: { names: string[] } }>(
                  "/tools/" + id + "/names",
                  "POST",
                  { expected_revision: revision, seed, count },
                );
                setNames(run.payload.names);
                setStatus("Имена и исходные данные записаны в журнал.");
              })
            }
          >
            Сгенерировать имена
          </button>
          <pre aria-label="Результат генератора имён">{names.join("\n")}</pre>
        </fieldset>
      )}
      {entry.template && (
        <fieldset disabled={busy || dirty || !id}>
          <legend>Визуальный запуск шаблона</legend>
          {entry.template.variables.map((name) => (
            <label key={name}>
              Переменная: {name}
              <input
                value={inputs[name] || ""}
                onChange={(e) =>
                  setInputs({ ...inputs, [name]: e.target.value })
                }
              />
            </label>
          ))}
          {entry.template.action === "chapter" && (
            <div>
              <label>
                Поиск целевой рукописи
                <input
                  value={bookQuery}
                  onChange={(e) => setBookQuery(e.target.value)}
                />
              </label>
              <button
                onClick={() =>
                  void act(async () =>
                    setBooks(
                      (
                        await request<{ items: typeof books }>(
                          "/manuscripts?query=" + encodeURIComponent(bookQuery),
                        )
                      ).items,
                    ),
                  )
                }
              >
                Получить рукописи для шаблона
              </button>
              <label>
                Целевая рукопись
                <select value={book} onChange={(e) => setBook(e.target.value)}>
                  <option value="">Выберите рукопись</option>
                  {books.map((b) => (
                    <option key={b.id} value={b.id}>
                      {b.payload.title} · версия {b.revision}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                Название создаваемой главы
                <input
                  value={chapterTitle}
                  onChange={(e) => setChapterTitle(e.target.value)}
                />
              </label>
            </div>
          )}
          <button
            onClick={() =>
              void act(async () => {
                const target = books.find((b) => b.id === book);
                const p = await request<Preview>(
                  "/tools/" + id + "/preview",
                  "POST",
                  {
                    expected_revision: revision,
                    inputs: Object.fromEntries(
                      entry.template!.variables.map((name) => [
                        name,
                        inputs[name] || "",
                      ]),
                    ),
                    manuscript_id:
                      entry.template!.action === "chapter"
                        ? target?.id || null
                        : null,
                    manuscript_revision:
                      entry.template!.action === "chapter"
                        ? target?.revision || null
                        : null,
                    chapter_title: chapterTitle,
                  },
                );
                setPreviews((old) => [p, ...old.filter((v) => v.id !== p.id)]);
                setStatus("Предпросмотр сохранён. Действие ещё не выполнено.");
              })
            }
          >
            Предпросмотр действия шаблона
          </button>
        </fieldset>
      )}
      <h3>Предпросмотры пользовательских действий</h3>
      {previews.map((p) => (
        <details key={p.id}>
          <summary>
            {p.payload.title} ·{" "}
            {{ pending: "Ожидает подтверждения", executed: "Выполнено" }[
              p.payload.status
            ] || "Неизвестный статус"}
          </summary>
          <p>
            Действие:{" "}
            {p.payload.action === "note"
              ? "создание заметки"
              : "добавление главы"}
            . Право: {p.payload.required_permission}; выдано в момент
            предпросмотра: {p.payload.permission_granted ? "да" : "нет"}.
          </p>
          <pre>{p.payload.text}</pre>
          <button
            disabled={busy || p.payload.status !== "pending"}
            onClick={() =>
              void act(async () => {
                await request("/tools/previews/" + p.id + "/execute", "POST", {
                  expected_revision: p.revision,
                  confirmed: true,
                });
                await refresh();
                setStatus("Подтверждённое действие выполнено один раз.");
              })
            }
          >
            Подтвердить действие с указанным правом
          </button>
        </details>
      ))}
      <details>
        <summary>Редактор событий выбранной истории</summary>
        <button
          onClick={() =>
            void act(async () =>
              setStories(
                (await request<{ stories: typeof stories }>("/stories"))
                  .stories,
              ),
            )
          }
        >
          Получить истории для событий
        </button>
        <label>
          История событий
          <select
            value={story}
            onChange={(e) => void act(() => loadBranch(e.target.value))}
          >
            <option value="">Выберите историю</option>
            {stories.map((s) => (
              <option key={s.id} value={s.id}>
                {s.payload.name}
              </option>
            ))}
          </select>
        </label>
        {branch && (
          <StoryWorld
            key={story + branch.id}
            storyId={story}
            branchId={branch.id}
            revision={branch.revision}
            disabled={busy}
            onChanged={() => loadBranch(story)}
          />
        )}
      </details>
    </section>
  );
}
