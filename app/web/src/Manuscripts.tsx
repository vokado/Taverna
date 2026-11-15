import { useEffect, useRef, useState } from "react";
import { downloadGet, request } from "./api";
import { useTemplateDraft, type TemplateDraft } from "./useTemplateDraft";

type Link = { label: string; target: string };
type Chapter = {
  id: string;
  title: string;
  text: string;
  links: Link[];
  media: { asset_id: string; caption: string }[];
};
type Book = {
  format: 1;
  title: string;
  author: string;
  language: string;
  chapters: Chapter[];
};
type Row = { id: string; revision: number; payload: Book };
type Proposal = {
  id: string;
  revision: number;
  payload: {
    status: string;
    request: { operation: string };
    before: string;
    after: string;
    diff: string;
    payload: Book | null;
  };
};
type Version = { id: string; payload: { number: number; reason: string } };
type BookSummary = {
  id: string;
  revision: number;
  payload: Pick<Book, "title" | "author" | "language">;
  chapter_count: number;
};
type Story = { id: string; payload: { name: string } };
const empty = (): Book => ({
  format: 1,
  title: "Новая рукопись",
  author: "",
  language: "ru",
  chapters: [],
});
const operations = {
  rewrite: "Переработать",
  style: "Изменить стиль",
  shorten: "Сократить",
  expand: "Расширить",
  coherence: "Проверить связность",
};
export function Manuscripts({ visible }: { visible: boolean }) {
  const [books, setBooks] = useState<BookSummary[]>([]),
    [book, setBook] = useState<Book>(empty),
    [mid, setMid] = useState<string | null>(null),
    [revision, setRevision] = useState(0);
  const [selected, setSelected] = useState(""),
    [query, setQuery] = useState(""),
    [raw, setRaw] = useState(JSON.stringify(empty(), null, 2)),
    [dirty, setDirty] = useState(false);
  const [status, setStatus] = useState(""),
    [busy, setBusy] = useState(false),
    [reason, setReason] = useState("Редактирование рукописи"),
    [versions, setVersions] = useState<Version[]>([]);
  const [proposals, setProposals] = useState<Proposal[]>([]),
    [job, setJob] = useState(""),
    [jobState, setJobState] = useState("");
  const [operation, setOperation] =
      useState<keyof typeof operations>("rewrite"),
    [instruction, setInstruction] = useState(""),
    [model, setModel] = useState("");
  const [models, setModels] = useState<
      { id: string; name: string; type: string }[]
    >([]),
    [selection, setSelection] = useState({ start: 0, end: 0 });
  const [stories, setStories] = useState<Story[]>([]),
    [source, setSource] = useState(""),
    [sourceBranch, setSourceBranch] = useState<{
      id: string;
      revision: number;
    } | null>(null),
    [messages, setMessages] = useState<{ id: string; text: string }[]>([]),
    [chosen, setChosen] = useState<string[]>([]),
    [sourceOffset, setSourceOffset] = useState(0),
    [sourceTotal, setSourceTotal] = useState(0);
  const chapter = book.chapters.find((c) => c.id === selected);
  const drafts = useTemplateDraft(
    "manuscript",
    {
      template_id: mid,
      source_revision: revision,
      raw,
      payload: book as unknown as Record<string, unknown>,
    },
    dirty,
    busy,
    "/manuscripts/drafts",
  );
  const [bookOffset, setBookOffset] = useState(0),
    [bookTotal, setBookTotal] = useState(0);
  const seq = useRef(0),
    working = useRef(false);
  async function act(work: () => Promise<void>) {
    if (working.current) return;
    working.current = true;
    setBusy(true);
    try {
      await work();
    } catch (e) {
      setStatus((e as Error).message);
    } finally {
      working.current = false;
      setBusy(false);
    }
  }
  async function refresh(offset = bookOffset) {
    const r = await request<{
      items: BookSummary[];
      total: number;
      offset: number;
    }>("/manuscripts?query=" + encodeURIComponent(query) + "&offset=" + offset);
    setBooks(r.items);
    setBookOffset(r.offset);
    setBookTotal(r.total);
    setProposals(
      (await request<{ items: Proposal[] }>("/manuscripts/proposals")).items,
    );
  }
  useEffect(() => {
    if (visible) void refresh().catch((e) => setStatus(e.message));
  }, [visible]);
  function edit(next: Book) {
    setBook(next);
    setRaw(JSON.stringify(next, null, 2));
    setDirty(true);
  }
  function updateChapter(patch: Partial<Chapter>) {
    if (chapter)
      edit({
        ...book,
        chapters: book.chapters.map((c) =>
          c.id === chapter.id ? { ...c, ...patch } : c,
        ),
      });
  }
  function load(row: Row) {
    seq.current++;
    setMid(row.id);
    setRevision(row.revision);
    setBook(row.payload);
    setRaw(JSON.stringify(row.payload, null, 2));
    setSelected(row.payload.chapters[0]?.id || "");
    setDirty(false);
    setVersions([]);
    setSelection({ start: 0, end: 0 });
    drafts.create();
  }
  async function open(summary: BookSummary) {
    const row = await request<Row>("/manuscripts/" + summary.id);
    await drafts.flush();
    load(row);
    setStatus("Открыта опубликованная редакция " + row.revision);
  }
  async function save() {
    await drafts.flush();
    const parsed = JSON.parse(raw) as Book;
    const id = mid || crypto.randomUUID();
    const row = await request<Row>("/manuscripts/" + id, "PUT", {
      expected_revision: revision,
      payload: parsed,
      reason,
    });
    await drafts.remove();
    load(row);
    await refresh();
    setStatus("Рукопись опубликована. Версия " + row.revision);
  }
  async function assistant(convert = false) {
    if (!convert && (dirty || !mid || !chapter))
      throw Error("Сначала опубликуйте рукопись и выберите главу.");
    if (convert && (!sourceBranch || !chosen.length))
      throw Error("Выберите сообщения источника.");
    const end =
      selection.end > selection.start
        ? selection.end
        : Array.from(chapter?.text || "").length;
    const start = selection.end > selection.start ? selection.start : 0;
    const payload = convert
      ? {
          operation: "chat_to_prose",
          title: book.title,
          story_id: source,
          branch_id: sourceBranch!.id,
          branch_revision: sourceBranch!.revision,
          message_ids: chosen,
          instruction,
          model_id: model || null,
        }
      : {
          operation,
          manuscript_id: mid,
          expected_revision: revision,
          chapter_id: chapter!.id,
          start,
          end,
          instruction,
          model_id: model || null,
        };
    const r = await request<{ generation_id: string }>(
      "/manuscripts/assistant",
      "POST",
      payload,
      { "Idempotency-Key": crypto.randomUUID() },
    );
    setJob(r.generation_id);
    setJobState("В очереди");
    setStatus("Оригинал сохранён; ожидается отдельное предложение.");
  }
  useEffect(() => {
    if (!job || !visible) return;
    let live = true,
      timer: ReturnType<typeof setTimeout>;
    async function poll() {
      try {
        const r = await request<{
          state: string;
          error?: { message_ru: string };
        }>("/generations/" + job);
        if (!live) return;
        setJobState(
          (
            {
              queued: "В очереди",
              running: "Редактор работает",
              completed: "Предложение готово",
              failed: "Ошибка",
              cancelled: "Отменено",
              interrupted: "Прервано",
            } as Record<string, string>
          )[r.state],
        );
        if (
          ["completed", "failed", "cancelled", "interrupted"].includes(r.state)
        ) {
          if (r.error) setStatus(r.error.message_ru);
          await refresh();
          setJob("");
        } else timer = setTimeout(poll, 600);
      } catch (e) {
        if (live) {
          setStatus((e as Error).message);
          timer = setTimeout(poll, 1500);
        }
      }
    }
    void poll();
    return () => {
      live = false;
      clearTimeout(timer);
    };
  }, [job, visible]);
  async function review(p: Proposal, accept: boolean) {
    if (accept) await drafts.flush();
    const r = await request<{ manuscript: Row | null }>(
      "/manuscripts/proposals/" + p.id + "/review",
      "POST",
      { proposal_revision: p.revision, accept, confirmed: accept },
    );
    if (r.manuscript) load(r.manuscript);
    await refresh();
    setStatus(
      accept
        ? "Предложение применено в новую версию."
        : "Предложение отклонено, оригинал сохранён.",
    );
  }
  async function sourcePage(sid: string, offset = 0) {
    const turn = ++seq.current;
    const r = await request<{ branch: { id: string; revision: number } }>(
      "/stories/" + sid,
    );
    const page = await request<{
      items: { id: string; text: string }[];
      total: number;
    }>(
      `/stories/${sid}/messages?branch_id=${r.branch.id}&offset=${offset}&limit=100`,
    );
    if (turn !== seq.current) return;
    setSource(sid);
    setSourceBranch(r.branch);
    setMessages(page.items);
    setSourceOffset(offset);
    setSourceTotal(page.total);
    setChosen([]);
  }
  async function restore(d: TemplateDraft) {
    await drafts.flush();
    setMid(d.payload.template_id);
    setRevision(d.payload.source_revision);
    setBook(d.payload.payload as unknown as Book);
    setRaw(d.payload.raw);
    setSelected((d.payload.payload as unknown as Book).chapters[0]?.id || "");
    setDirty(true);
    drafts.adopt(d);
    setStatus(
      "Черновик восстановлен с исходной ревизией; публикация отдельно.",
    );
  }
  return (
    <section aria-label="Литературный редактор">
      <h2>Литературный редактор</h2>
      <p>
        Рукопись хранится отдельно от истории. Текст главы — обычный текст;
        ссылки и медиа задаются явно.
      </p>
      <p role="status">{status}</p>
      <p>{drafts.status}</p>
      <div className="toolbar">
        <label>
          Поиск по рукописям
          <input value={query} onChange={(e) => setQuery(e.target.value)} />
        </label>
        <button onClick={() => void act(() => refresh(0))}>
          Найти рукопись
        </button>
        <button
          disabled={busy}
          onClick={() =>
            void act(async () => {
              await drafts.flush();
              load({ id: "", revision: 0, payload: empty() });
              setMid(null);
              setStatus("Новая рукопись");
            })
          }
        >
          Новая рукопись
        </button>
      </div>
      <ul>
        {books.map((row) => (
          <li key={row.id}>
            <button disabled={busy} onClick={() => void act(() => open(row))}>
              {row.payload.title} · версия {row.revision}
            </button>
          </li>
        ))}
      </ul>
      <p>
        Найдено рукописей: {bookTotal}; страница{" "}
        {Math.floor(bookOffset / 50) + 1}
      </p>
      <button
        disabled={busy || !bookOffset}
        onClick={() => void act(() => refresh(Math.max(0, bookOffset - 50)))}
      >
        Предыдущие рукописи
      </button>
      <button
        disabled={busy || bookOffset + 50 >= bookTotal}
        onClick={() => void act(() => refresh(bookOffset + 50))}
      >
        Следующие рукописи
      </button>
      {!!drafts.items.length && (
        <details>
          <summary>Черновики рукописи</summary>
          {drafts.items.map((d) => (
            <button key={d.id} onClick={() => void act(() => restore(d))}>
              Восстановить черновик {d.id.slice(0, 8)}
            </button>
          ))}
        </details>
      )}
      <fieldset disabled={busy}>
        <legend>Рукопись · версия {revision || "не опубликована"}</legend>
        <label>
          Название книги
          <input
            value={book.title}
            onChange={(e) => edit({ ...book, title: e.target.value })}
          />
        </label>
        <label>
          Автор
          <input
            value={book.author}
            onChange={(e) => edit({ ...book, author: e.target.value })}
          />
        </label>
        <label>
          Язык
          <input
            value={book.language}
            onChange={(e) => edit({ ...book, language: e.target.value })}
          />
        </label>
        <h3>Структура глав</h3>
        <ol>
          {book.chapters.map((c, index) => (
            <li key={c.id}>
              <button
                onClick={() => {
                  setSelected(c.id);
                  setSelection({ start: 0, end: 0 });
                }}
              >
                {c.title}
              </button>
              <button
                disabled={!index}
                aria-label={"Поднять " + c.title}
                onClick={() => {
                  const next = [...book.chapters];
                  [next[index - 1], next[index]] = [
                    next[index],
                    next[index - 1],
                  ];
                  edit({ ...book, chapters: next });
                }}
              >
                ↑
              </button>
              <button
                disabled={index === book.chapters.length - 1}
                aria-label={"Опустить " + c.title}
                onClick={() => {
                  const next = [...book.chapters];
                  [next[index + 1], next[index]] = [
                    next[index],
                    next[index + 1],
                  ];
                  edit({ ...book, chapters: next });
                }}
              >
                ↓
              </button>
              <button
                onClick={() =>
                  edit({
                    ...book,
                    chapters: book.chapters.filter((v) => v.id !== c.id),
                  })
                }
              >
                Убрать главу в этой редакции
              </button>
            </li>
          ))}
        </ol>
        <button
          onClick={() => {
            const c: Chapter = {
              id: crypto.randomUUID(),
              title: "Новая глава",
              text: "",
              links: [],
              media: [],
            };
            edit({ ...book, chapters: [...book.chapters, c] });
            setSelected(c.id);
          }}
        >
          Добавить главу
        </button>
        {chapter && (
          <div>
            <label>
              Название главы
              <input
                value={chapter.title}
                onChange={(e) => updateChapter({ title: e.target.value })}
              />
            </label>
            <label>
              Текст главы
              <textarea
                rows={18}
                value={chapter.text}
                onChange={(e) => updateChapter({ text: e.target.value })}
                onSelect={(e) => {
                  const el = e.currentTarget;
                  setSelection({
                    start: Array.from(el.value.slice(0, el.selectionStart))
                      .length,
                    end: Array.from(el.value.slice(0, el.selectionEnd)).length,
                  });
                }}
              />
            </label>
            <p>
              Найдено в главе:{" "}
              {query
                ? chapter.text
                    .toLocaleLowerCase()
                    .split(query.toLocaleLowerCase()).length - 1
                : 0}
            </p>
            <p>
              ID главы: {chapter.id}; внутренняя ссылка: #chapter-{chapter.id}
            </p>
            <h4>Ссылки главы</h4>
            {chapter.links.map((link, i) => (
              <div key={i}>
                <label>
                  Подпись ссылки
                  <input
                    value={link.label}
                    onChange={(e) =>
                      updateChapter({
                        links: chapter.links.map((v, n) =>
                          n === i ? { ...v, label: e.target.value } : v,
                        ),
                      })
                    }
                  />
                </label>
                <label>
                  Адрес ссылки
                  <input
                    value={link.target}
                    onChange={(e) =>
                      updateChapter({
                        links: chapter.links.map((v, n) =>
                          n === i ? { ...v, target: e.target.value } : v,
                        ),
                      })
                    }
                  />
                </label>
                <button
                  onClick={() =>
                    updateChapter({
                      links: chapter.links.filter((_, n) => n !== i),
                    })
                  }
                >
                  Удалить ссылку
                </button>
              </div>
            ))}
            <button
              onClick={() =>
                updateChapter({
                  links: [
                    ...chapter.links,
                    { label: "Ссылка", target: "https://example.org" },
                  ],
                })
              }
            >
              Добавить ссылку
            </button>
            <h4>Встроенные медиа</h4>
            {chapter.media.map((m, i) => (
              <div key={i}>
                <label>
                  ID медиаресурса
                  <input
                    value={m.asset_id}
                    onChange={(e) =>
                      updateChapter({
                        media: chapter.media.map((v, n) =>
                          n === i ? { ...v, asset_id: e.target.value } : v,
                        ),
                      })
                    }
                  />
                </label>
                <label>
                  Подпись медиа
                  <input
                    value={m.caption}
                    onChange={(e) =>
                      updateChapter({
                        media: chapter.media.map((v, n) =>
                          n === i ? { ...v, caption: e.target.value } : v,
                        ),
                      })
                    }
                  />
                </label>
                <button
                  onClick={() =>
                    updateChapter({
                      media: chapter.media.filter((_, n) => n !== i),
                    })
                  }
                >
                  Убрать медиа
                </button>
              </div>
            ))}
            <button
              onClick={() =>
                updateChapter({
                  media: [...chapter.media, { asset_id: "", caption: "" }],
                })
              }
            >
              Добавить медиаресурс по ID из галереи
            </button>
          </div>
        )}
        <label>
          Причина новой версии
          <input value={reason} onChange={(e) => setReason(e.target.value)} />
        </label>
        <details>
          <summary>JSON рукописи и незавершённый ввод</summary>
          <textarea
            aria-label="JSON рукописи"
            rows={12}
            value={raw}
            onChange={(e) => {
              setRaw(e.target.value);
              setDirty(true);
            }}
          />
        </details>
        <button onClick={() => void act(save)}>Опубликовать рукопись</button>
      </fieldset>
      <details>
        <summary>Версии и экспорт</summary>
        <button
          disabled={!mid || busy}
          onClick={() =>
            void act(async () =>
              setVersions(
                (
                  await request<{ items: Version[] }>(
                    "/manuscripts/" + mid + "/history",
                  )
                ).items,
              ),
            )
          }
        >
          Показать историю изменений
        </button>
        {versions.map((v) => (
          <p key={v.id}>
            Версия {v.payload.number}: {v.payload.reason}{" "}
            <button
              disabled={busy}
              onClick={() =>
                void act(async () => {
                  await drafts.flush();
                  const r = await request<Row>(
                    "/manuscripts/" + mid + "/restore",
                    "POST",
                    {
                      expected_revision: revision,
                      number: v.payload.number,
                      confirmed: true,
                    },
                  );
                  load(r);
                  await refresh();
                  setStatus("Историческая версия восстановлена как новая.");
                })
              }
            >
              Восстановить в новую версию
            </button>
          </p>
        ))}
        <div className="toolbar">
          {(["txt", "markdown", "html", "epub"] as const).map((format) => (
            <button
              key={format}
              disabled={!mid || dirty || busy}
              onClick={() =>
                void act(() =>
                  downloadGet(
                    `/manuscripts/${mid}/export?format=${format}&expected_revision=${revision}`,
                  ),
                )
              }
            >
              Скачать{" "}
              {format === "markdown"
                ? "Markdown + медиа ZIP"
                : format.toUpperCase()}
            </button>
          ))}
        </div>
        <p>
          EPUB: изображения и MP3. WebP преобразуется в PNG. Другие аудиоформаты
          отклоняются до экспорта.
        </p>
      </details>
      <details>
        <summary>ИИ-редактор и преобразование чата</summary>
        <button
          onClick={() =>
            void act(async () =>
              setModels(
                (
                  await request<{ models: typeof models }>("/models")
                ).models.filter((m) => m.type === "llm"),
              ),
            )
          }
        >
          Обновить уже загруженные модели
        </button>
        <label>
          Модель редактора
          <select value={model} onChange={(e) => setModel(e.target.value)}>
            <option value="">Из профиля editor</option>
            {models.map((m) => (
              <option key={m.id} value={m.id}>
                {m.name}
              </option>
            ))}
          </select>
        </label>
        <label>
          Операция
          <select
            value={operation}
            onChange={(e) =>
              setOperation(e.target.value as keyof typeof operations)
            }
          >
            {Object.entries(operations).map(([id, label]) => (
              <option key={id} value={id}>
                {label}
              </option>
            ))}
          </select>
        </label>
        <label>
          Указание редактору
          <textarea
            value={instruction}
            onChange={(e) => setInstruction(e.target.value)}
          />
        </label>
        <p>
          Выделите фрагмент в тексте главы; без выделения отправится вся глава.
          Результат появится отдельно.
        </p>
        <button
          disabled={busy || !!job || dirty || !mid || !chapter}
          onClick={() => void act(() => assistant())}
        >
          Получить предпросмотр правки
        </button>
        <h4>Чат → отдельная рукопись</h4>
        <button
          onClick={() =>
            void act(async () =>
              setStories(
                (await request<{ stories: Story[] }>("/stories")).stories,
              ),
            )
          }
        >
          Выбрать историю для преобразования
        </button>
        <label>
          Источник
          <select
            value={source}
            onChange={(e) => void act(() => sourcePage(e.target.value))}
          >
            <option value="">Выберите историю</option>
            {stories.map((s) => (
              <option key={s.id} value={s.id}>
                {s.payload.name}
              </option>
            ))}
          </select>
        </label>
        {messages.map((m) => (
          <label key={m.id}>
            <input
              type="checkbox"
              checked={chosen.includes(m.id)}
              onChange={(e) =>
                setChosen(
                  e.target.checked
                    ? [...chosen, m.id]
                    : chosen.filter((id) => id !== m.id),
                )
              }
            />
            {m.text.slice(0, 300)}
          </label>
        ))}
        <p>
          Сообщения {sourceOffset + 1}–
          {Math.min(sourceOffset + 100, sourceTotal)} из {sourceTotal}; выбрано{" "}
          {chosen.length}
        </p>
        <button
          disabled={!source || sourceOffset === 0 || busy}
          onClick={() =>
            void act(() => sourcePage(source, Math.max(0, sourceOffset - 100)))
          }
        >
          Предыдущие сообщения источника
        </button>
        <button
          disabled={!source || sourceOffset + 100 >= sourceTotal || busy}
          onClick={() => void act(() => sourcePage(source, sourceOffset + 100))}
        >
          Следующие сообщения источника
        </button>
        <button
          disabled={busy || !!job || !chosen.length}
          onClick={() => void act(() => assistant(true))}
        >
          Предпросмотр преобразования выбранных сообщений
        </button>
        <p>{jobState}</p>
        {job && (
          <button
            onClick={() =>
              void act(async () => {
                await request("/generations/" + job + "/cancel", "POST", {});
                setStatus("Отмена запрошена; ждём завершения задачи.");
              })
            }
          >
            Остановить редактор
          </button>
        )}
      </details>
      <h3>Предложения редактора</h3>
      <button onClick={() => void act(refresh)}>Обновить предложения</button>
      {proposals.map((p) => (
        <details key={p.id}>
          <summary>
            {p.payload.request.operation === "chat_to_prose"
              ? "Преобразование чата"
              : operations[
                  p.payload.request.operation as keyof typeof operations
                ] || "Правка"}{" "}
            ·{" "}
            {{
              pending: "Ожидает решения",
              accepted: "Принято",
              rejected: "Отклонено",
            }[p.payload.status] || "Неизвестный статус"}{" "}
            · {p.id.slice(0, 8)}
          </summary>
          <h4>Оригинал</h4>
          <pre>{p.payload.before}</pre>
          <h4>Предложение</h4>
          <pre>{p.payload.after}</pre>
          <h4>Различия</h4>
          <pre>{p.payload.diff}</pre>
          <button
            disabled={
              busy || p.payload.status !== "pending" || !p.payload.payload
            }
            onClick={() => void act(() => review(p, true))}
          >
            Подтвердить и применить эту правку
          </button>
          <button
            disabled={busy || p.payload.status !== "pending"}
            onClick={() => void act(() => review(p, false))}
          >
            Отклонить без изменения рукописи
          </button>
        </details>
      ))}
    </section>
  );
}
