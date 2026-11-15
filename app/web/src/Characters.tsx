import { useEffect, useRef, useState } from "react";
import { download, request } from "./api";
import type { components } from "./api-types";
type Payload = {
  source_format: string;
  original: Record<string, unknown>;
  fields: Record<string, unknown>;
  taverna: Record<string, unknown>;
  folder: string;
  portrait_id: string | null;
  compatibility_notes: string[];
};
type Character = {
  id: string;
  revision: number;
  payload: Payload;
  updated_at: string;
};
type Import = {
  format: "json" | "png" | "package";
  content_base64: string;
  confirmed: boolean;
};
type DraftRecord = {
  id: string;
  revision: number;
  payload: {
    title: string;
    character: Payload;
    raw_text: string;
    source_id: string | null;
    source_revision: number;
    raw_applied: boolean;
    editor: string;
  };
};
type Report = { unsupported_fields: string[]; warnings: string[] };
const modes: Record<string, string> = {
  fixed:
    "Фиксированный: постоянные свойства сохраняются; изменения только вручную.",
  flexible:
    "Гибкий: поведение адаптируется в пределах карточки и текущей истории.",
  developing:
    "Развивающийся: допускаются проверяемые изменения с историей причин.",
};
const fields = [
  ["name", "Имя"],
  ["description", "Описание"],
  ["personality", "Характер"],
  ["scenario", "Стартовая ситуация"],
  ["first_mes", "Первое приветствие"],
  ["mes_example", "Примеры диалогов"],
  ["system_prompt", "Системная инструкция"],
  ["post_history_instructions", "Инструкция после истории"],
  ["creator_notes", "Заметки автора"],
];
const details = [
  ["biography", "Биография"],
  ["motivation", "Мотивация"],
  ["habits", "Привычки"],
  ["fears", "Страхи"],
  ["speech_style", "Стиль речи"],
];
export function Characters() {
  const [cards, setCards] = useState<Character[]>([]);
  const [query, setQuery] = useState("");
  const [folder, setFolder] = useState("");
  const [tag, setTag] = useState("");
  const [sort, setSort] = useState("name");
  const [view, setView] = useState("grid");
  const [current, setCurrent] = useState<Character | null>(null);
  const [draft, setDraft] = useState<Payload | null>(null);
  const [dirty, setDirty] = useState(false);
  const [status, setStatus] = useState("");
  const [busy, setBusy] = useState(false);
  const [rawApplied, setRawApplied] = useState(true);
  const [editor, setEditor] = useState("wizard");
  const [step, setStep] = useState(0);
  const [raw, setRaw] = useState("");
  const [sectionName, setSectionName] = useState("");
  const [sectionText, setSectionText] = useState("");
  const [pendingImport, setPendingImport] = useState<Import | null>(null);
  const [importPreview, setImportPreview] = useState<Payload | null>(null);
  const [exportReport, setExportReport] = useState<Report | null>(null);
  const [format, setFormat] = useState("json");
  const [version, setVersion] = useState("v3");
  const [models, setModels] = useState<Array<{ id: string; name: string }>>([]);
  const [model, setModel] = useState("");
  const [operation, setOperation] = useState("create");
  const [instructions, setInstructions] = useState("");
  const [aiText, setAiText] = useState("");
  const [aiPreview, setAiPreview] = useState<Payload | null>(null);
  const aiStream = useRef<EventSource | null>(null);
  const draftInfo = useRef<{ id: string; revision: number }>({
    id: crypto.randomUUID(),
    revision: 0,
  });
  const draftWrites = useRef<Promise<void>>(Promise.resolve());
  const [autosave, setAutosave] = useState("");
  const [recoverable, setRecoverable] = useState<DraftRecord[]>([]);
  async function refreshDrafts() {
    try {
      setRecoverable(
        (await request<{ drafts: DraftRecord[] }>("/characters/drafts")).drafts,
      );
    } catch (e) {
      setAutosave((e as Error).message);
    }
  }
  useEffect(() => {
    void refreshDrafts();
  }, []);
  useEffect(() => {
    if (!dirty || !draft || busy) return;
    setAutosave("Черновик изменён; ожидает автосохранения.");
    const info = draftInfo.current;
    const payload = {
      title: String(draft.fields.name || "Без имени"),
      character: draft,
      raw_text: raw,
      source_id: current?.id || null,
      source_revision: current?.revision || 0,
      raw_applied: rawApplied,
      editor,
    };
    const timer = setTimeout(() => {
      draftWrites.current = draftWrites.current.then(async () => {
        try {
          const saved = await request<DraftRecord>(
            `/characters/drafts/${info.id}`,
            "PUT",
            { ...payload, expected_revision: info.revision },
          );
          info.revision = saved.revision;
          if (draftInfo.current === info)
            setAutosave(
              "Черновик сохранён автоматически. Для публикации в библиотеке нажмите «Сохранить карточку».",
            );
          await refreshDrafts();
        } catch (e) {
          setAutosave("Автосохранение не выполнено: " + (e as Error).message);
        }
      });
    }, 1000);
    return () => clearTimeout(timer);
  }, [draft, raw, rawApplied, dirty, busy, editor, current]);
  async function recoverDraft(value: DraftRecord) {
    if (
      dirty &&
      !confirm(
        "Заменить текущие несохранённые правки восстановленным черновиком?",
      )
    )
      return;
    let source: Character | null = null;
    if (value.payload.source_id) {
      try {
        source = await request<Character>(
          "/characters/" + value.payload.source_id,
        );
        source.revision = value.payload.source_revision;
      } catch {
        source = null;
      }
    }
    setCurrent(source);
    setDraft(value.payload.character);
    setRaw(value.payload.raw_text);
    setRawApplied(value.payload.raw_applied);
    setEditor(value.payload.editor);
    draftInfo.current = { id: value.id, revision: value.revision };
    setDirty(true);
    setAutosave("Черновик восстановлен. Исходная карточка не изменена.");
  }
  async function refresh() {
    try {
      const params = new URLSearchParams({ query, sort });
      if (folder) params.set("folder", folder);
      if (tag) params.set("tag", tag);
      const result = await request<{
        characters: Character[];
        errors: string[];
      }>("/characters?" + params);
      setCards(result.characters);
      if (result.errors.length)
        setStatus(
          `Некоторые карточки повреждены: ${result.errors.length}. Остальные доступны.`,
        );
    } catch (e) {
      setStatus((e as Error).message);
    }
  }
  useEffect(() => {
    void refresh();
  }, [query, folder, tag, sort]);
  useEffect(() => {
    const listener = (e: BeforeUnloadEvent) => {
      if (dirty) {
        e.preventDefault();
        e.returnValue = "";
      }
    };
    window.addEventListener("beforeunload", listener);
    return () => window.removeEventListener("beforeunload", listener);
  }, [dirty]);
  useEffect(() => () => aiStream.current?.close(), []);
  useEffect(() => {
    document.documentElement.dataset.characterDirty = String(dirty);
    return () => {
      delete document.documentElement.dataset.characterDirty;
    };
  }, [dirty]);
  function select(value: Character | null, payload: Payload) {
    if (dirty && !confirm("Есть несохранённые изменения. Отказаться от них?"))
      return;
    draftInfo.current = { id: crypto.randomUUID(), revision: 0 };
    setAutosave("");
    setRawApplied(true);
    setCurrent(value);
    setDraft(structuredClone(payload));
    setRaw(JSON.stringify(payload, null, 2));
    setDirty(false);
    setStep(0);
    setAiPreview(null);
    setExportReport(null);
    setStatus("Карточка открыта.");
  }
  async function create() {
    try {
      const result = await request<{ character: Payload }>("/characters/new");
      select(null, result.character);
    } catch (e) {
      setStatus((e as Error).message);
    }
  }
  function change(group: "fields" | "taverna", key: string, value: unknown) {
    if (!draft) return;
    const next = { ...draft, [group]: { ...draft[group], [key]: value } };
    setRawApplied(true);
    setDraft(next);
    setRaw(JSON.stringify(next, null, 2));
    setDirty(true);
    setStatus("Есть несохранённые изменения.");
  }
  async function save() {
    if (!draft) return;
    if (!rawApplied) {
      setStatus("Примените изменённый JSON к черновику перед сохранением.");
      return;
    }
    setBusy(true);
    setStatus("Сохраняем карточку…");
    try {
      const body: components["schemas"]["CharacterRequest"] = {
        expected_revision: current?.revision || 0,
        character: draft,
      };
      const saved = await request<Character>(
        current ? `/characters/${current.id}` : "/characters",
        current ? "PATCH" : "POST",
        body,
      );
      setCurrent(saved);
      setDraft(saved.payload);
      setDirty(false);
      setStatus("Карточка сохранена.");
      await draftWrites.current;
      const info = draftInfo.current;
      if (info.revision) {
        try {
          await request(`/characters/drafts/${info.id}`, "DELETE", {
            expected_revision: info.revision,
          });
          draftInfo.current = { id: crypto.randomUUID(), revision: 0 };
          await refreshDrafts();
        } catch {
          setAutosave(
            "Карточка сохранена; контрольный черновик оставлен для восстановления.",
          );
        }
      }
      await refresh();
    } catch (e) {
      setStatus((e as Error).message + " Изменения не сохранены.");
    } finally {
      setBusy(false);
    }
  }
  async function importFile(file: File) {
    setBusy(true);
    try {
      if (file.size > 12 * 1024 * 1024)
        throw Error("Файл слишком большой. Лимит — 12 МиБ.");
      const data = new Uint8Array(await file.arrayBuffer());
      let binary = "";
      for (const byte of data) binary += String.fromCharCode(byte);
      const format = file.name.toLowerCase().endsWith(".png")
        ? "png"
        : file.name.toLowerCase().endsWith(".tavcharacter")
          ? "package"
          : "json";
      const body: Import = {
        format,
        content_base64: btoa(binary),
        confirmed: false,
      };
      const result = await request<{ preview: Payload }>(
        "/characters/import",
        "POST",
        body,
      );
      setPendingImport(body);
      setImportPreview(result.preview);
      setStatus("Предпросмотр импорта. Данные ещё не сохранены.");
    } catch (e) {
      setStatus((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function confirmImport() {
    if (!pendingImport) return;
    setBusy(true);
    try {
      const result = await request<{ character: Character }>(
        "/characters/import",
        "POST",
        { ...pendingImport, confirmed: true },
      );
      setPendingImport(null);
      setImportPreview(null);
      select(result.character, result.character.payload);
      await refresh();
      setStatus("Карточка импортирована.");
    } catch (e) {
      setStatus((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function previewExport() {
    if (!current) return;
    try {
      const result = await request<{ report: Report }>(
        `/characters/${current.id}/export`,
        "POST",
        { format, version, confirmed: false },
      );
      setExportReport(result.report);
    } catch (e) {
      setStatus((e as Error).message);
    }
  }
  async function exportFile() {
    if (!current) return;
    try {
      await download(`/characters/${current.id}/export`, {
        format,
        version,
        confirmed: true,
      });
      setExportReport(null);
      setStatus("Файл экспорта подготовлен.");
    } catch (e) {
      setStatus((e as Error).message);
    }
  }
  async function applyRaw() {
    try {
      const result = await request<{ character: Payload }>(
        "/characters/ai-preview",
        "POST",
        { text: raw },
      );
      setDraft(result.character);
      setRaw(JSON.stringify(result.character, null, 2));
      setRawApplied(true);
      setDirty(true);
      setStatus("JSON применён к черновику. Сохраните карточку.");
    } catch (e) {
      setStatus((e as Error).message);
    }
  }
  async function loadModels() {
    try {
      const result = await request<{
        models: Array<{ id: string; name: string; type: string }>;
      }>("/models");
      const loaded = result.models.filter((m) => m.type === "llm");
      setModels(loaded);
      setModel(loaded[0]?.id || "");
      if (!loaded.length) setStatus("Загрузите модель в LM Studio вручную.");
    } catch (e) {
      setStatus((e as Error).message);
    }
  }
  async function aiDraft() {
    if (!draft) return;
    setBusy(true);
    setAiText("");
    setAiPreview(null);
    try {
      const result = await request<{ generation_id: string }>(
        "/characters/ai-draft",
        "POST",
        { model_id: model, instructions, operation, character: draft },
        { "Idempotency-Key": crypto.randomUUID() },
      );
      const source = new EventSource(
        `/api/v1/generations/${result.generation_id}/stream`,
      );
      aiStream.current = source;
      let text = "";
      let sequence = 0;
      source.addEventListener("generation.delta", (e) => {
        const value = JSON.parse((e as MessageEvent).data);
        if (value.sequence <= sequence) return;
        sequence = value.sequence;
        text += value.data.text || "";
        setAiText(text);
      });
      for (const end of ["completed", "failed", "cancelled", "interrupted"])
        source.addEventListener("generation." + end, (e) => {
          source.close();
          setBusy(false);
          const value = JSON.parse((e as MessageEvent).data);
          setStatus(
            end === "completed"
              ? "Предложение готово. Проверьте его перед применением."
              : value.data.message_ru ||
                  "Генерация остановлена. Частичный результат сохранён.",
          );
        });
      source.onerror = () => {
        setStatus(
          "Соединение прервано. Повторно читать задачу можно в сохранённых ответах.",
        );
        source.close();
        setBusy(false);
      };
    } catch (e) {
      setStatus((e as Error).message);
      setBusy(false);
    }
  }
  async function validateAi() {
    try {
      setAiPreview(
        (
          await request<{ character: Payload }>(
            "/characters/ai-preview",
            "POST",
            { text: aiText },
          )
        ).character,
      );
    } catch (e) {
      setStatus((e as Error).message + " Предложение не применено.");
    }
  }
  return (
    <div className="character-library">
      <section>
        <h2>Локальная библиотека персонажей</h2>
        <button onClick={create} disabled={busy}>
          Создать персонажа
        </button>
        <label className="import-control">
          Импорт PNG, JSON или пакета
          <input
            type="file"
            accept=".png,.json,.tavcharacter"
            disabled={busy}
            onChange={(e) => {
              const f = e.target.files?.[0];
              if (f) void importFile(f);
              e.target.value = "";
            }}
          />
        </label>
        <div className="library-filters">
          <label>
            Поиск персонажей
            <input value={query} onChange={(e) => setQuery(e.target.value)} />
          </label>
          <label>
            Папка
            <input value={folder} onChange={(e) => setFolder(e.target.value)} />
          </label>
          <label>
            Тег
            <input value={tag} onChange={(e) => setTag(e.target.value)} />
          </label>
          <label>
            Сортировка
            <select value={sort} onChange={(e) => setSort(e.target.value)}>
              <option value="name">По имени</option>
              <option value="updated">По изменению</option>
            </select>
          </label>
          <label>
            Вид библиотеки
            <select value={view} onChange={(e) => setView(e.target.value)}>
              <option value="grid">Плитки</option>
              <option value="list">Список</option>
            </select>
          </label>
        </div>
        <p role="status">
          {status || "Карточки хранятся локально. Публичного каталога нет."}
        </p>
        {cards.length === 0 ? (
          <p>Персонажи не найдены. Создайте карточку или импортируйте файл.</p>
        ) : (
          <div className={"cards " + view}>
            {cards.map((card) => (
              <button
                className="character-card"
                disabled={busy}
                key={card.id}
                onClick={() => select(card, card.payload)}
              >
                {card.payload.portrait_id && (
                  <img
                    src={`/api/v1/characters/${card.id}/portrait`}
                    alt=""
                    loading="lazy"
                  />
                )}
                <strong>{String(card.payload.fields.name)}</strong>
                <span>
                  {String(
                    card.payload.fields.description || "Без описания",
                  ).slice(0, 150)}
                </span>
              </button>
            ))}
          </div>
        )}
      </section>
      {recoverable.length > 0 && (
        <section>
          <h2>Автосохранённые черновики</h2>
          <p>Оригинальные карточки не изменены. Восстановите нужные правки.</p>
          {recoverable.map((value) => (
            <div key={value.id}>
              <strong>{value.payload.title}</strong>
              <button disabled={busy} onClick={() => void recoverDraft(value)}>
                Восстановить черновик
              </button>
            </div>
          ))}
        </section>
      )}
      {importPreview && (
        <section>
          <h2>Предпросмотр импорта</h2>
          <h3>{String(importPreview.fields.name)}</h3>
          <pre className="reply">{JSON.stringify(importPreview, null, 2)}</pre>
          <button onClick={confirmImport} disabled={busy}>
            Подтвердить импорт
          </button>
          <button
            onClick={() => {
              setImportPreview(null);
              setPendingImport(null);
            }}
          >
            Отказаться от импорта
          </button>
        </section>
      )}
      {draft && (
        <section>
          <h2>{current ? "Редактирование персонажа" : "Новый персонаж"}</h2>
          <fieldset disabled={busy} className="editor-fields">
            <label>
              Редактор
              <select
                value={editor}
                onChange={(e) => {
                  if (
                    !rawApplied &&
                    !confirm("Отказаться от неприменённых изменений JSON?")
                  )
                    return;
                  setRawApplied(true);
                  setRaw(JSON.stringify(draft, null, 2));
                  setEditor(e.target.value);
                }}
              >
                <option value="wizard">Пошаговый мастер</option>
                <option value="expert">Экспертный JSON-редактор</option>
              </select>
            </label>
            {editor === "expert" ? (
              <>
                <label>
                  Полные поля карточки и расширения
                  <textarea
                    rows={24}
                    value={raw}
                    onChange={(e) => {
                      setRaw(e.target.value);
                      setRawApplied(false);
                      setDirty(true);
                      setStatus(
                        "JSON изменён. Примените его к черновику, затем сохраните.",
                      );
                    }}
                  />
                </label>
                <button onClick={applyRaw} disabled={busy}>
                  Применить JSON к черновику
                </button>
                <p>
                  Неизвестные extensions и метаданные сохраняются. Данные не
                  исполняются как команды или HTML.
                </p>
              </>
            ) : (
              <>
                <nav className="wizard-steps">
                  {[
                    "Основные поля",
                    "Подробности",
                    "Приветствия и организация",
                  ].map((label, i) => (
                    <button
                      key={label}
                      onClick={() => setStep(i)}
                      aria-pressed={step === i}
                    >
                      {i + 1}. {label}
                    </button>
                  ))}
                </nav>
                {step === 0 &&
                  fields.map(([key, label]) => (
                    <label key={key}>
                      {label}
                      <textarea
                        rows={key === "name" ? 1 : 3}
                        value={String(draft.fields[key] || "")}
                        onChange={(e) => change("fields", key, e.target.value)}
                      />
                    </label>
                  ))}
                {step === 1 && (
                  <>
                    {details.map(([key, label]) => (
                      <label key={key}>
                        {label}
                        <textarea
                          rows={3}
                          value={String(draft.taverna[key] || "")}
                          onChange={(e) =>
                            change("taverna", key, e.target.value)
                          }
                        />
                      </label>
                    ))}
                    <label>
                      Режим характера
                      <select
                        value={String(
                          draft.taverna.personality_mode || "fixed",
                        )}
                        onChange={(e) =>
                          change("taverna", "personality_mode", e.target.value)
                        }
                      >
                        <option value="fixed">Фиксированный</option>
                        <option value="flexible">Гибкий</option>
                        <option value="developing">Развивающийся</option>
                      </select>
                    </label>
                    <p>
                      {modes[String(draft.taverna.personality_mode || "fixed")]}
                    </p>
                    <label>
                      Название пользовательской секции
                      <input
                        value={sectionName}
                        onChange={(e) => setSectionName(e.target.value)}
                      />
                    </label>
                    <label>
                      Содержимое секции
                      <textarea
                        value={sectionText}
                        onChange={(e) => setSectionText(e.target.value)}
                      />
                    </label>
                    <button
                      disabled={!sectionName.trim()}
                      onClick={() => {
                        change("taverna", "sections", {
                          ...((draft.taverna.sections as Record<
                            string,
                            unknown
                          >) || {}),
                          [sectionName]: sectionText,
                        });
                        setSectionName("");
                        setSectionText("");
                      }}
                    >
                      Добавить пользовательскую секцию
                    </button>
                  </>
                )}
                {step === 2 && (
                  <>
                    <label>
                      Папка карточки
                      <input
                        value={draft.folder}
                        onChange={(e) => {
                          setDraft({ ...draft, folder: e.target.value });
                          setDirty(true);
                        }}
                      />
                    </label>
                    <label>
                      Теги через запятую
                      <input
                        value={((draft.fields.tags as string[]) || []).join(
                          ", ",
                        )}
                        onChange={(e) =>
                          change(
                            "fields",
                            "tags",
                            e.target.value
                              .split(",")
                              .map((t) => t.trim())
                              .filter(Boolean),
                          )
                        }
                      />
                    </label>
                    {((draft.fields.alternate_greetings as string[]) || []).map(
                      (g, i) => (
                        <label key={i}>
                          Альтернативное приветствие {i + 1}
                          <textarea
                            rows={3}
                            value={g}
                            onChange={(e) =>
                              change(
                                "fields",
                                "alternate_greetings",
                                (
                                  draft.fields.alternate_greetings as string[]
                                ).map((v, n) => (n === i ? e.target.value : v)),
                              )
                            }
                          />
                        </label>
                      ),
                    )}
                    <button
                      onClick={() =>
                        change("fields", "alternate_greetings", [
                          ...((draft.fields.alternate_greetings as string[]) ||
                            []),
                          "",
                        ])
                      }
                    >
                      Добавить приветствие
                    </button>
                    <p>
                      Сценарии и пресеты полностью доступны в экспертном
                      редакторе; их применение к истории будет реализовано в
                      соответствующих модулях.
                    </p>
                  </>
                )}
              </>
            )}
            <button onClick={save} disabled={busy}>
              Сохранить карточку
            </button>
            <p role="status">{autosave}</p>
            {dirty && <p>Есть несохранённые изменения.</p>}
            {current && (
              <fieldset>
                <legend>Экспорт сохранённой версии</legend>
                <label>
                  Формат экспорта
                  <select
                    value={format}
                    onChange={(e) => {
                      setFormat(e.target.value);
                      setExportReport(null);
                    }}
                  >
                    <option value="json">JSON</option>
                    <option value="png">PNG</option>
                    <option value="package">Полный пакет Таверны</option>
                  </select>
                </label>
                {format !== "package" && (
                  <label>
                    Версия карточки
                    <select
                      value={version}
                      onChange={(e) => {
                        setVersion(e.target.value);
                        setExportReport(null);
                      }}
                    >
                      <option value="v1">V1</option>
                      <option value="v2">V2</option>
                      <option value="v3">V3</option>
                    </select>
                  </label>
                )}
                <button onClick={previewExport}>
                  Проверить совместимость экспорта
                </button>
                {exportReport && (
                  <div>
                    <p>
                      {exportReport.unsupported_fields.length
                        ? "Целевой стандарт не поддерживает следующие поля:"
                        : "Потерь поддерживаемых полей не обнаружено."}
                    </p>
                    <ul>
                      {exportReport.unsupported_fields.map((f) => (
                        <li key={f}>{f}</li>
                      ))}
                    </ul>
                    {exportReport.warnings.map((w) => (
                      <p key={w}>{w}</p>
                    ))}
                    <button onClick={exportFile}>Скачать после проверки</button>
                  </div>
                )}
              </fieldset>
            )}
            <fieldset>
              <legend>ИИ-помощник карточки</legend>
              <button onClick={loadModels} disabled={busy}>
                Проверить загруженные модели
              </button>
              {models.length > 0 && (
                <>
                  <label>
                    Модель помощника
                    <select
                      value={model}
                      onChange={(e) => setModel(e.target.value)}
                    >
                      {models.map((m) => (
                        <option key={m.id} value={m.id}>
                          {m.name}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label>
                    Действие помощника
                    <select
                      value={operation}
                      onChange={(e) => setOperation(e.target.value)}
                    >
                      <option value="create">
                        Создать и улучшить карточку
                      </option>
                      <option value="biography">Расширить биографию</option>
                      <option value="dialogue">Примеры диалогов</option>
                      <option value="contradictions">Найти противоречия</option>
                    </select>
                  </label>
                  <label>
                    Инструкции помощнику
                    <textarea
                      value={instructions}
                      onChange={(e) => setInstructions(e.target.value)}
                    />
                  </label>
                  <button
                    onClick={aiDraft}
                    disabled={busy || !instructions.trim()}
                  >
                    Подготовить предложение
                  </button>
                </>
              )}
              {aiText && (
                <>
                  <label>
                    Редактируемое предложение модели
                    <textarea
                      rows={12}
                      value={aiText}
                      onChange={(e) => setAiText(e.target.value)}
                    />
                  </label>
                  <button onClick={validateAi} disabled={busy}>
                    Проверить предложение
                  </button>
                </>
              )}
              {aiPreview && (
                <>
                  <pre className="reply">
                    {JSON.stringify(aiPreview, null, 2)}
                  </pre>
                  <button
                    onClick={() => {
                      setDraft(aiPreview);
                      setRaw(JSON.stringify(aiPreview, null, 2));
                      setDirty(true);
                      setAiPreview(null);
                      setStatus(
                        "Предложение применено только к черновику. Сохраните карточку.",
                      );
                    }}
                  >
                    Применить к черновику
                  </button>
                  <button onClick={() => setAiPreview(null)}>
                    Отклонить предложение
                  </button>
                </>
              )}
            </fieldset>
          </fieldset>
        </section>
      )}
    </div>
  );
}
