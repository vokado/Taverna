import { useEffect, useRef, useState } from "react";
import { download, request } from "./api";
import { MemoryResults } from "./MemoryResults";
import { useMemoryDraft } from "./useMemoryDraft";
type Data = {
  level: string;
  nature: string;
  text: string;
  knower_ids: string[];
  subject_ids: string[];
  source_ids: string[];
  source_fact_versions: Record<string, number>;
  confidence: number;
  salience: number;
  pinned: boolean;
  status: string;
  claim_key: string;
  story_time: string;
  location_id: string | null;
  cause_ids: string[];
  relation: {
    parties: string[];
    metrics: Record<string, number>;
    episode_ids: string[];
  } | null;
  extras: Record<string, unknown>;
};
export type Row = Data & { id: string; version: number; history: unknown[] };
type Proposal = {
  id: string;
  revision: number;
  payload: { status: string; base_revision: number; records: Data[] };
};
type Props = {
  storyId: string;
  branchId: string;
  revision: number;
  actors: Array<{ id: string; name: string }>;
  messages: Array<{ id: string; text: string }>;
  disabled: boolean;
  onChanged: () => Promise<void>;
  onMessage: (id: string) => Promise<void>;
  registerFlush: (flush: (() => Promise<void>) | null) => void;
};
const levels = {
  operational: "Оперативная",
  episodic: "Эпизодическая",
  personal: "Личная память NPC",
  relationship: "Отношения",
  world: "Состояние мира",
  plot: "Сюжетная",
};
const natures = {
  fact: "Факт",
  observation: "Наблюдение",
  report: "Полученные сведения",
  rumor: "Слух",
  belief: "Убеждение / заблуждение",
  secret: "Тайна",
  summary: "Сводка",
};
function blank(actor: string): Data {
  return {
    level: "personal",
    nature: "fact",
    text: "",
    knower_ids: actor ? [actor] : [],
    subject_ids: [],
    source_ids: [],
    source_fact_versions: {},
    confidence: 1,
    salience: 0.5,
    pinned: false,
    status: "active",
    claim_key: "",
    story_time: "",
    location_id: null,
    cause_ids: [],
    relation: null,
    extras: {},
  };
}
type Policy = {
  auto_extract: boolean;
  every_messages: number;
  model_id: string | null;
  auto_archive: boolean;
  archive_min_salience: number;
  archive_after_messages: number;
};
export type SavedDraft = {
  id: string;
  revision: number;
  payload: {
    record_id: string | null;
    record_version: number;
    source_revision: number;
    form: Data;
    raw: string;
    applied_raw: string;
    reason: string;
    conflict_policy: string | null;
    policy_draft?: Policy | null;
  };
};
export function MemoryPanel(p: Props) {
  const base = `/stories/${p.storyId}/memories`,
    scopeKey = p.storyId + "/" + p.branchId;
  const scope = useRef(scopeKey);
  scope.current = scopeKey;
  const [opened, setOpened] = useState(false);
  const liveRevision = useRef(p.revision);
  liveRevision.current = Math.max(liveRevision.current, p.revision);
  const [player, setPlayer] = useState("");
  const [rows, setRows] = useState<Row[]>([]),
    [proposals, setProposals] = useState<Proposal[]>([]),
    [legacy, setLegacy] = useState<unknown[]>([]);
  const [revision, setRevision] = useState(p.revision),
    [selected, setSelected] = useState<Row | null>(null),
    [draft, setDraft] = useState<Data>(() => blank(p.actors[0]?.id || ""));
  const [raw, setRaw] = useState(""),
    [applied, setApplied] = useState(""),
    [reason, setReason] = useState("Ручная запись владельца"),
    [conflict, setConflict] = useState("");
  const [busy, setBusy] = useState(false),
    [status, setStatus] = useState(""),
    [output, setOutput] = useState(""),
    [job, setJob] = useState("");
  const [actor, setActor] = useState(p.actors[0]?.id || ""),
    [viewActor, setViewActor] = useState(""),
    [query, setQuery] = useState("");
  const [models, setModels] = useState<
      Array<{ id: string; name: string; type: string }>
    >([]),
    [model, setModel] = useState(""),
    [embedding, setEmbedding] = useState("");
  const [summaryFacts, setSummaryFacts] = useState<string[]>([]),
    [rebase, setRebase] = useState(false),
    [keepBoth, setKeepBoth] = useState(false);
  const [filters, setFilters] = useState({
      level: "",
      nature: "",
      subject_id: "",
      source_id: "",
      location_id: "",
      time_from: "",
      time_to: "",
    }),
    [messages, setMessages] = useState(false);
  const [policy, setPolicy] = useState({
    auto_extract: true,
    every_messages: 8,
    model_id: null as string | null,
    auto_archive: true,
    archive_min_salience: 0.2,
    archive_after_messages: 100,
  });
  const [importText, setImportText] = useState(""),
    [actorMap, setActorMap] = useState("{}"),
    [sourceMap, setSourceMap] = useState("{}"),
    [external, setExternal] = useState(false),
    [preview, setPreview] = useState("");
  const [dirty, setDirty] = useState(false);
  const [policyDirty, setPolicyDirty] = useState(false);
  const policyDirtyRef = useRef(false),
    loadEpoch = useRef(0),
    view = useRef(viewActor);
  view.current = viewActor;
  function editPolicy(value: Policy) {
    policyDirtyRef.current = true;
    setPolicyDirty(true);
    setPolicy(value);
  }
  const [sourceRevision, setSourceRevision] = useState(p.revision);
  const draftPayload = {
    record_id: selected?.id || null,
    record_version: selected?.version || 0,
    source_revision: sourceRevision,
    form: draft,
    raw,
    applied_raw: applied,
    reason,
    conflict_policy: conflict || null,
    policy_draft: policyDirty ? policy : null,
  };
  const drafts = useMemoryDraft(
    base,
    p.branchId,
    draftPayload,
    dirty || policyDirty,
    busy,
  );
  const savedDraft = drafts.saved,
    draftStatus = drafts.status;
  useEffect(() => {
    p.registerFlush(drafts.saveNow);
    return () => p.registerFlush(null);
  }, []);
  useEffect(() => {
    const listener = (e: BeforeUnloadEvent) => {
      if (dirty || policyDirty) {
        e.preventDefault();
        e.returnValue = "";
      }
    };
    window.addEventListener("beforeunload", listener);
    return () => window.removeEventListener("beforeunload", listener);
  }, [dirty, policyDirty]);
  async function restoreDraft() {
    if (!savedDraft) return;
    try {
      await drafts.saveNow();
      const value = savedDraft.payload;
      const current = rows.find((row) => row.id === value.record_id);
      if (value.record_id && !current)
        throw Error(
          "Запись черновика отсутствует в текущем списке. Откройте просмотр владельца и обновите память.",
        );
      const form = await request<Data>(base + "/draft-validate", "POST", {
        record: value.form,
      });
      setSelected(
        current ? { ...current, version: value.record_version } : null,
      );
      drafts.adopt(savedDraft);
      change(form);
      setRaw(value.raw);
      setApplied(value.applied_raw);
      setReason(value.reason);
      if (value.policy_draft) {
        const restored = value.policy_draft;
        if (
          typeof restored.auto_extract !== "boolean" ||
          typeof restored.auto_archive !== "boolean" ||
          typeof restored.every_messages !== "number" ||
          typeof restored.archive_min_salience !== "number" ||
          typeof restored.archive_after_messages !== "number" ||
          (restored.model_id !== null && typeof restored.model_id !== "string")
        )
          throw Error(
            "Черновик политики повреждён; исходные данные доступны в подробностях.",
          );
        editPolicy(restored);
      }
      setConflict(value.conflict_policy || "");
      setSourceRevision(value.source_revision);
      setRevision(value.source_revision);
      setStatus(
        "Черновик восстановлен. При конфликте исходной ревизии обновите память и проверьте изменения.",
      );
    } catch (e) {
      setStatus((e as Error).message);
    }
  }
  async function selectRecord(value: Row | null) {
    try {
      await drafts.saveNow();
      if (value) {
        const loaded = await request<{ record: Row; branch_revision: number }>(
          base + `/${value.id}?branch_id=${p.branchId}`,
        );
        setViewActor("");
        setRevision(loaded.branch_revision);
        choose(loaded.record, loaded.branch_revision);
      } else choose(null);
    } catch (e) {
      setStatus("Сначала сохраните черновик: " + (e as Error).message);
    }
  }
  const locked = busy || p.disabled,
    ready = !locked && raw === applied;
  const body = () => ({
    branch_id: p.branchId,
    expected_revision: liveRevision.current,
  });
  function change(value: Data) {
    setDirty(true);
    setDraft(value);
    const text = JSON.stringify(value, null, 2);
    setRaw(text);
    setApplied(text);
  }
  function choose(
    value: Row | null,
    sourceRev = revision,
    defaultActor = viewActor || p.actors[0]?.id || "",
  ) {
    drafts.create();
    setSelected(value);
    change(
      value
        ? (Object.fromEntries(
            Object.keys(blank("")).map((k) => [k, value[k as keyof Row]]),
          ) as Data)
        : blank(defaultActor),
    );
    setSourceRevision(sourceRev);
    setDirty(false);
  }
  async function load() {
    const key = scope.current,
      epoch = ++loadEpoch.current,
      actorFilter = view.current;
    try {
      const [r, a, s] = await Promise.all([
        request<{
          records: Row[];
          legacy_records: unknown[];
          branch_revision: number;
          player_id: string;
        }>(
          base +
            `?branch_id=${p.branchId}${actorFilter ? `&character_id=${actorFilter}` : ""}`,
        ),
        request<{ proposals: Proposal[] }>(
          base + `/proposals?branch_id=${p.branchId}`,
        ),
        request<{ settings: typeof policy }>(
          base + `/settings?branch_id=${p.branchId}`,
        ),
      ]);
      if (
        key !== scope.current ||
        epoch !== loadEpoch.current ||
        actorFilter !== view.current ||
        r.branch_revision < liveRevision.current
      )
        return;
      setPlayer(r.player_id);
      setRows(r.records);
      setLegacy(r.legacy_records);
      liveRevision.current = r.branch_revision;
      setRevision(r.branch_revision);
      setProposals(a.proposals);
      if (!policyDirtyRef.current) setPolicy(s.settings);
    } catch (e) {
      if (key === scope.current) setStatus((e as Error).message);
    }
  }
  useEffect(
    () => () => {
      scope.current = "";
    },
    [],
  );
  useEffect(() => {
    choose(null);
    setActor(p.actors[0]?.id || "");
    setOutput("");
    setJob("");
    setBusy(false);
    setPreview("");
    setSummaryFacts([]);
  }, [scopeKey]);
  useEffect(() => {
    if (opened) void load();
  }, [scopeKey, p.revision, viewActor, opened]);
  async function changed() {
    await p.onChanged();
    await load();
  }
  async function mutate(
    path: string,
    method: string,
    value: unknown,
    message: string,
  ) {
    const key = scope.current,
      editingDraftId = drafts.id;
    setBusy(true);
    setStatus("Применяем изменения памяти…");
    try {
      await drafts.saveNow();
      const r = await request<{
        record?: Row;
        branch_revision?: number;
        already_imported?: boolean;
      }>(base + path, method, value);
      if (key !== scope.current) return;
      if (r.branch_revision) {
        liveRevision.current = Math.max(
          liveRevision.current,
          r.branch_revision,
        );
        setRevision(liveRevision.current);
      }
      setOutput("");
      if (r.record) {
        try {
          if (
            !policyDirty &&
            ((method === "POST" && path === "") || path === `/${selected?.id}`)
          )
            await drafts.remove(editingDraftId);
        } catch (e) {
          drafts.setStatus(
            "Запись сохранена; старый черновик требует проверки: " +
              (e as Error).message,
          );
        }
        choose(r.record, r.branch_revision || revision);
      }
      if (path === "/settings") {
        policyDirtyRef.current = false;
        setPolicyDirty(false);
        if (!dirty) {
          try {
            await drafts.remove(editingDraftId);
          } catch (e) {
            drafts.setStatus((e as Error).message);
          }
          drafts.create();
        }
      }
      await changed();
      setStatus(
        r.already_imported
          ? "Этот пакет уже был импортирован; повторных записей не создано."
          : message,
      );
    } catch (e) {
      setStatus((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function inspect(path: string) {
    try {
      setOutput(
        JSON.stringify(
          await request(base + path + `?branch_id=${p.branchId}`),
          null,
          2,
        ),
      );
    } catch (e) {
      setStatus((e as Error).message);
    }
  }
  async function run(path: string, value: unknown) {
    const key = scope.current;
    setBusy(true);
    try {
      const q = await request<{ generation_id: string }>(
        base + path,
        "POST",
        value,
        { "Idempotency-Key": crypto.randomUUID() },
      );
      setJob(q.generation_id);
      setStatus("Задача в общей очереди…");
      while (key === scope.current) {
        const r = await request<{
          state: string;
          result: unknown;
          error?: { message_ru: string };
        }>(`/generations/${q.generation_id}`);
        if (key !== scope.current) return;
        if (
          ["completed", "failed", "cancelled", "interrupted"].includes(r.state)
        ) {
          setOutput(
            JSON.stringify(r.result || r.error || { state: r.state }, null, 2),
          );
          setStatus(
            r.state === "completed"
              ? "Задача завершена."
              : r.error?.message_ru || "Задача остановлена.",
          );
          await load();
          break;
        }
        await new Promise((resolve) => setTimeout(resolve, 200));
      }
    } catch (e) {
      if (key === scope.current) setStatus((e as Error).message);
    } finally {
      if (key === scope.current) setBusy(false);
    }
  }
  function importBody() {
    return {
      ...body(),
      text: importText,
      actor_map: JSON.parse(actorMap),
      source_map: JSON.parse(sourceMap),
      allow_external_sources: external,
      conflict_policy: keepBoth ? "keep_both" : null,
    };
  }
  const options = (items: Record<string, string>) =>
    Object.entries(items).map(([id, name]) => (
      <option key={id} value={id}>
        {name}
      </option>
    ));
  return (
    <details
      className="panel"
      aria-label="Память истории"
      onToggle={(e) => {
        if (e.target === e.currentTarget) setOpened(e.currentTarget.open);
      }}
    >
      <summary>Память, знания и отношения</summary>
      <button
        onClick={() =>
          void drafts
            .refresh()
            .catch((e) => drafts.setStatus((e as Error).message))
        }
      >
        Обновить черновики памяти
      </button>
      <label>
        Сохранённый черновик памяти
        <select
          value={drafts.savedId}
          onChange={(e) => drafts.setSavedId(e.target.value)}
        >
          <option value="">Выберите черновик</option>
          {drafts.drafts.map((d) => (
            <option key={d.id} value={d.id}>
              {typeof d.payload.form.text === "string"
                ? d.payload.form.text.slice(0, 60)
                : "Незавершённый черновик"}{" "}
              · {d.id}
            </option>
          ))}
        </select>
      </label>
      <button
        disabled={!dirty || locked}
        onClick={() =>
          void drafts
            .saveSeparate()
            .catch((e) => drafts.setStatus((e as Error).message))
        }
      >
        Сохранить конфликтный черновик отдельно
      </button>
      {savedDraft && (
        <>
          <details>
            <summary>Просмотреть сохранённый черновик памяти</summary>
            <pre className="diagnostic">
              {JSON.stringify(savedDraft.payload, null, 2)}
            </pre>
          </details>
          <button onClick={() => void restoreDraft()}>
            Восстановить черновик памяти
          </button>
          <button
            disabled={locked}
            onClick={async () => {
              if (!confirm("Удалить выбранный сохранённый черновик памяти?"))
                return;
              setBusy(true);
              try {
                await drafts.remove(savedDraft.id);
                if (drafts.id === savedDraft.id) choose(null);
              } catch (e) {
                drafts.setStatus((e as Error).message);
              } finally {
                setBusy(false);
              }
            }}
          >
            Удалить черновик памяти
          </button>
        </>
      )}
      {draftStatus && <p role="status">{draftStatus}</p>}
      <p>
        Память принадлежит выбранной ветви. Модель предлагает изменения для
        просмотра; владелец подтверждает знания и их источники.
      </p>
      <label>
        Просмотр памяти
        <select
          value={viewActor}
          onChange={async (e) => {
            const value = e.target.value;
            try {
              await drafts.saveNow();
              choose(null, revision, value || p.actors[0]?.id || "");
              setViewActor(value);
            } catch (error) {
              setStatus((error as Error).message);
            }
          }}
        >
          <option value="">Владелец — все записи</option>
          {p.actors.map((a) => (
            <option key={a.id} value={a.id}>
              {a.name}
            </option>
          ))}
        </select>
      </label>
      <button disabled={locked} onClick={() => void load()}>
        Обновить память
      </button>
      <label>
        Сохранённая запись памяти
        <select
          value={selected?.id || ""}
          onChange={(e) =>
            void selectRecord(rows.find((r) => r.id === e.target.value) || null)
          }
        >
          <option value="">Новая запись</option>
          {rows.map((r) => (
            <option key={r.id} value={r.id}>
              {r.text.slice(0, 70)} · v{r.version} · {r.status}
            </option>
          ))}
        </select>
      </label>
      <button disabled={locked} onClick={() => void selectRecord(null)}>
        Новая запись памяти
      </button>
      <fieldset disabled={!ready}>
        <legend>Редактор памяти</legend>
        <label>
          Уровень памяти
          <select
            value={draft.level}
            onChange={(e) =>
              change({
                ...draft,
                level: e.target.value,
                relation:
                  e.target.value === "relationship"
                    ? draft.relation || {
                        parties: [p.actors[0]?.id, player].filter(Boolean),
                        metrics: {},
                        episode_ids: [],
                      }
                    : null,
              })
            }
          >
            {options(levels)}
          </select>
        </label>
        <label>
          Тип знания
          <select
            value={draft.nature}
            onChange={(e) => change({ ...draft, nature: e.target.value })}
          >
            {options(natures)}
          </select>
        </label>
        <label>
          Текст памяти
          <textarea
            rows={4}
            value={draft.text}
            onChange={(e) => change({ ...draft, text: e.target.value })}
          />
        </label>
        <fieldset>
          <legend>Кто знает эту запись</legend>
          {p.actors.map((a) => (
            <label key={a.id}>
              <input
                type="checkbox"
                checked={draft.knower_ids.includes(a.id)}
                onChange={(e) =>
                  change({
                    ...draft,
                    knower_ids: e.target.checked
                      ? [...draft.knower_ids, a.id]
                      : draft.knower_ids.filter((id) => id !== a.id),
                  })
                }
              />
              {a.name}
            </label>
          ))}
        </fieldset>
        {(
          [
            ["confidence", "Уверенность"],
            ["salience", "Значимость"],
          ] as const
        ).map(([key, label]) => (
          <label key={key}>
            {label}
            <input
              type="number"
              min={0}
              max={1}
              step="0.05"
              value={draft[key]}
              onChange={(e) =>
                change({ ...draft, [key]: Number(e.target.value) })
              }
            />
          </label>
        ))}
        <label>
          <input
            type="checkbox"
            checked={draft.pinned}
            onChange={(e) => change({ ...draft, pinned: e.target.checked })}
          />
          Закрепить воспоминание
        </label>
        <label>
          Состояние записи
          <select
            value={draft.status}
            onChange={(e) => change({ ...draft, status: e.target.value })}
          >
            {options({
              active: "Действующая",
              archived: "Архивная",
              superseded: "Заменённая",
              deleted: "Удалённая",
            })}
          </select>
        </label>
        {(
          [
            ["claim_key", "Ключ утверждения для конфликтов"],
            ["story_time", "Время в истории"],
          ] as const
        ).map(([key, label]) => (
          <label key={key}>
            {label}
            <input
              value={draft[key]}
              onChange={(e) => change({ ...draft, [key]: e.target.value })}
            />
          </label>
        ))}
        <label>
          Локация записи
          <input
            value={draft.location_id || ""}
            onChange={(e) =>
              change({ ...draft, location_id: e.target.value || null })
            }
          />
        </label>
        <details>
          <summary>Источники из текущей страницы чата</summary>
          {p.messages.map((m) => (
            <label key={m.id}>
              <input
                type="checkbox"
                checked={draft.source_ids.includes(m.id)}
                onChange={(e) =>
                  change({
                    ...draft,
                    source_ids: e.target.checked
                      ? [...draft.source_ids, m.id]
                      : draft.source_ids.filter((id) => id !== m.id),
                  })
                }
              />
              {m.text.slice(0, 100)}
            </label>
          ))}
        </details>
        {draft.relation && (
          <fieldset>
            <legend>Отношения</legend>
            {[0, 1].map((i) => (
              <label key={i}>
                Участник {i + 1}
                <select
                  value={draft.relation!.parties[i] || ""}
                  onChange={(e) => {
                    const parties = [...draft.relation!.parties];
                    parties[i] = e.target.value;
                    change({
                      ...draft,
                      relation: { ...draft.relation!, parties },
                    });
                  }}
                >
                  {p.actors.map((a) => (
                    <option key={a.id} value={a.id}>
                      {a.name}
                    </option>
                  ))}
                  <option value="">Выберите участника</option>
                  {player && <option value={player}>Вы</option>}
                </select>
              </label>
            ))}
            {Object.entries(draft.relation.metrics).map(([name, value]) => (
              <label key={name}>
                {name}
                <input
                  type="number"
                  min={-100}
                  max={100}
                  value={value}
                  onChange={(e) =>
                    change({
                      ...draft,
                      relation: {
                        ...draft.relation!,
                        metrics: {
                          ...draft.relation!.metrics,
                          [name]: Number(e.target.value),
                        },
                      },
                    })
                  }
                />
              </label>
            ))}
            <button
              onClick={() => {
                const name = prompt("Название показателя отношений");
                if (name)
                  change({
                    ...draft,
                    relation: {
                      ...draft.relation!,
                      metrics: { ...draft.relation!.metrics, [name]: 0 },
                    },
                  });
              }}
            >
              Добавить показатель отношений
            </button>
            <details>
              <summary>Важные эпизоды отношений</summary>
              {rows
                .filter((r) => r.level === "episodic" && r.status === "active")
                .map((r) => (
                  <label key={r.id}>
                    <input
                      type="checkbox"
                      checked={draft.relation!.episode_ids.includes(r.id)}
                      onChange={(e) =>
                        change({
                          ...draft,
                          relation: {
                            ...draft.relation!,
                            episode_ids: e.target.checked
                              ? [...draft.relation!.episode_ids, r.id]
                              : draft.relation!.episode_ids.filter(
                                  (id) => id !== r.id,
                                ),
                          },
                        })
                      }
                    />
                    {r.text.slice(0, 100)}
                  </label>
                ))}
            </details>
          </fieldset>
        )}
      </fieldset>
      <details>
        <summary>Полные поля записи JSON</summary>
        <label>
          Данные записи памяти
          <textarea
            rows={14}
            value={raw}
            disabled={locked}
            onChange={(e) => {
              setRaw(e.target.value);
              setDirty(true);
            }}
          />
        </label>
        <button
          disabled={locked}
          onClick={async () => {
            setBusy(true);
            try {
              const value = JSON.parse(raw);
              if (!value || typeof value !== "object" || Array.isArray(value))
                throw Error("Нужен JSON-объект.");
              change(
                await request<Data>(base + "/validate", "POST", {
                  record: value,
                }),
              );
            } catch (e) {
              setStatus((e as Error).message);
            } finally {
              setBusy(false);
            }
          }}
        >
          Применить JSON памяти к форме
        </button>
      </details>
      {raw !== applied && <p>Примените изменённый JSON перед сохранением.</p>}
      <label>
        Обоснование изменения памяти
        <input value={reason} onChange={(e) => setReason(e.target.value)} />
      </label>
      <label>
        При конфликте утверждений
        <select value={conflict} onChange={(e) => setConflict(e.target.value)}>
          {options({
            "": "Остановиться для проверки",
            keep_both: "Явно сохранить обе версии",
            supersede: "Заменить действующее утверждение",
          })}
        </select>
      </label>
      <button
        disabled={
          !ready ||
          !draft.text.trim() ||
          !draft.knower_ids.length ||
          !reason.trim()
        }
        onClick={() =>
          void mutate(
            selected ? `/${selected.id}` : "",
            selected ? "PATCH" : "POST",
            {
              ...body(),
              expected_revision: sourceRevision,
              record: draft,
              expected_version: selected?.version || 0,
              reason,
              conflict_policy: conflict || null,
            },
            "Запись памяти сохранена.",
          )
        }
      >
        Сохранить память
      </button>
      <button
        disabled={locked || !selected}
        onClick={() => {
          if (
            selected &&
            confirm("Удалить запись памяти, сохранив историю версий?")
          )
            void mutate(
              `/${selected.id}`,
              "DELETE",
              {
                ...body(),
                expected_version: selected.version,
                confirmed: true,
                reason,
              },
              "Запись отмечена удалённой.",
            );
        }}
      >
        Удалить запись памяти
      </button>
      {selected && (
        <button onClick={() => void inspect(`/${selected.id}/sources`)}>
          Источники и история изменений
        </button>
      )}
      <h3>Извлечение и сводки</h3>
      <label>
        NPC для извлечения и поиска
        <select value={actor} onChange={(e) => setActor(e.target.value)}>
          {p.actors.map((a) => (
            <option key={a.id} value={a.id}>
              {a.name}
            </option>
          ))}
        </select>
      </label>
      <button
        disabled={locked}
        onClick={async () => {
          try {
            setModels(
              (await request<{ models: typeof models }>("/models")).models,
            );
          } catch (e) {
            setStatus((e as Error).message);
          }
        }}
      >
        Проверить загруженные модели памяти
      </button>
      <label>
        Модель памяти
        <select value={model} onChange={(e) => setModel(e.target.value)}>
          <option value="">Из служебного профиля</option>
          {models
            .filter((m) => m.type === "llm")
            .map((m) => (
              <option key={m.id} value={m.id}>
                {m.name}
              </option>
            ))}
        </select>
      </label>
      <p>
        Без отмеченных сообщений используются последние 50 видимых этому NPC.
        Для сводки можно выбрать известные ему факты.
      </p>
      <details>
        <summary>Факты для сводки</summary>
        {rows
          .filter((r) => r.status === "active" && r.knower_ids.includes(actor))
          .map((r) => (
            <label key={r.id}>
              <input
                type="checkbox"
                checked={summaryFacts.includes(r.id)}
                onChange={(e) =>
                  setSummaryFacts(
                    e.target.checked
                      ? [...summaryFacts, r.id]
                      : summaryFacts.filter((id) => id !== r.id),
                  )
                }
              />
              {r.text.slice(0, 100)}
            </label>
          ))}
      </details>
      {(
        [
          ["extract", "Извлечь факты"],
          ["summarize", "Создать сводку"],
        ] as const
      ).map(([operation, label]) => (
        <button
          key={operation}
          disabled={locked || !actor}
          onClick={() =>
            void run("/extract", {
              ...body(),
              character_id: actor,
              model_id: model || null,
              source_ids: draft.source_ids,
              source_fact_ids: summaryFacts,
              operation,
            })
          }
        >
          {label}
        </button>
      ))}
      <label>
        <input
          type="checkbox"
          checked={rebase}
          onChange={(e) => setRebase(e.target.checked)}
        />
        Подтверждаю повторную привязку к текущей ревизии при неизменных
        источниках
      </label>
      <label>
        <input
          type="checkbox"
          checked={keepBoth}
          onChange={(e) => setKeepBoth(e.target.checked)}
        />
        В предложении / импорте явно сохранить обе версии конфликтующих
        утверждений
      </label>
      {proposals.map((q) => (
        <details key={q.id}>
          <summary>
            Предложение {q.id} · {q.payload.status} · ревизия{" "}
            {q.payload.base_revision}
          </summary>
          <pre className="diagnostic">
            {JSON.stringify(q.payload.records, null, 2)}
          </pre>
          {q.payload.status === "pending" &&
            [true, false].map((accept) => (
              <button
                key={String(accept)}
                disabled={locked}
                onClick={() => {
                  if (
                    confirm(
                      accept
                        ? "Внести предложенные факты в память выбранного NPC?"
                        : "Отклонить предложение памяти?",
                    )
                  )
                    void mutate(
                      `/proposals/${q.id}/review`,
                      "POST",
                      {
                        ...body(),
                        proposal_revision: q.revision,
                        accept,
                        confirmed: true,
                        rebase,
                        conflict_policy: keepBoth ? "keep_both" : null,
                      },
                      accept
                        ? "Предложение принято."
                        : "Предложение отклонено.",
                    );
                }}
              >
                {accept
                  ? "Принять предложение памяти"
                  : "Отклонить предложение памяти"}
              </button>
            ))}
        </details>
      ))}
      <h3>Поиск и хронология</h3>
      <label>
        Поиск памяти
        <input value={query} onChange={(e) => setQuery(e.target.value)} />
      </label>
      <label>
        Уровень поиска
        <select
          value={filters.level}
          onChange={(e) => setFilters({ ...filters, level: e.target.value })}
        >
          <option value="">Все уровни</option>
          {options(levels)}
        </select>
      </label>
      <label>
        Тип поиска
        <select
          value={filters.nature}
          onChange={(e) => setFilters({ ...filters, nature: e.target.value })}
        >
          <option value="">Все типы</option>
          {options(natures)}
        </select>
      </label>
      {(
        [
          ["subject_id", "Фильтр сущности (ID)"],
          ["source_id", "Фильтр источника (ID)"],
          ["location_id", "Фильтр локации"],
          ["time_from", "Время от"],
          ["time_to", "Время до"],
        ] as const
      ).map(([key, label]) => (
        <label key={key}>
          {label}
          <input
            value={filters[key]}
            onChange={(e) => setFilters({ ...filters, [key]: e.target.value })}
          />
        </label>
      ))}
      <label>
        <input
          type="checkbox"
          checked={messages}
          onChange={(e) => setMessages(e.target.checked)}
        />
        Искать также в видимых сообщениях
      </label>
      <label>
        Embedding-модель
        <select
          value={embedding}
          onChange={(e) => setEmbedding(e.target.value)}
        >
          <option value="">Только текстовый поиск</option>
          {models
            .filter((m) => m.type === "embedding")
            .map((m) => (
              <option key={m.id} value={m.id}>
                {m.name}
              </option>
            ))}
        </select>
      </label>
      <button
        disabled={locked || !actor}
        onClick={() =>
          void run("/search", {
            ...body(),
            character_id: actor,
            query,
            include_messages: messages,
            embedding_model_id: embedding || null,
            ...Object.fromEntries(
              Object.entries(filters).map(([k, v]) => [k, v || null]),
            ),
          })
        }
      >
        Найти в памяти
      </button>
      <button disabled={locked} onClick={() => void run("/index", body())}>
        Перестроить индекс памяти
      </button>
      <button
        disabled={locked}
        onClick={() => {
          if (
            confirm(
              "Сбросить производный кеш смыслового поиска? Векторы будут пересчитаны при следующем запросе.",
            )
          )
            void run("/semantic-cache/reset", body());
        }}
      >
        Сбросить кеш смыслового поиска
      </button>
      <button onClick={() => void inspect("/timeline")}>
        Хронология памяти
      </button>
      <button onClick={() => void inspect("/conflicts")}>
        Проверить конфликты памяти
      </button>
      <h3>Политика памяти</h3>
      <label>
        <input
          type="checkbox"
          checked={policy.auto_extract}
          onChange={(e) =>
            editPolicy({ ...policy, auto_extract: e.target.checked })
          }
        />
        Автоматически предлагать факты
      </label>
      <label>
        Интервал извлечения (сообщений)
        <input
          type="number"
          min={1}
          max={100}
          value={policy.every_messages}
          onChange={(e) =>
            editPolicy({ ...policy, every_messages: Number(e.target.value) })
          }
        />
      </label>
      <label>
        Модель автоматической памяти
        <select
          value={policy.model_id || ""}
          onChange={(e) =>
            editPolicy({ ...policy, model_id: e.target.value || null })
          }
        >
          <option value="">Служебный профиль / модель текущего ответа</option>
          {models
            .filter((m) => m.type === "llm")
            .map((m) => (
              <option key={m.id} value={m.id}>
                {m.name}
              </option>
            ))}
        </select>
      </label>
      <label>
        <input
          type="checkbox"
          checked={policy.auto_archive}
          onChange={(e) =>
            editPolicy({ ...policy, auto_archive: e.target.checked })
          }
        />
        Архивировать малозначительные незакреплённые записи
      </label>
      <label>
        Порог значимости для архива
        <input
          type="number"
          min={0}
          max={1}
          step="0.05"
          value={policy.archive_min_salience}
          onChange={(e) =>
            editPolicy({
              ...policy,
              archive_min_salience: Number(e.target.value),
            })
          }
        />
      </label>
      <label>
        Возраст для архива (сообщений)
        <input
          type="number"
          min={1}
          value={policy.archive_after_messages}
          onChange={(e) =>
            editPolicy({
              ...policy,
              archive_after_messages: Number(e.target.value),
            })
          }
        />
      </label>
      <button
        disabled={locked}
        onClick={() =>
          void mutate(
            "/settings",
            "PUT",
            { ...body(), settings: policy },
            "Политика памяти сохранена.",
          )
        }
      >
        Сохранить политику памяти
      </button>
      <button
        disabled={locked}
        onClick={() =>
          void mutate(
            "/archive",
            "POST",
            {
              ...body(),
              min_salience: policy.archive_min_salience,
              min_age_messages: policy.archive_after_messages,
            },
            "Политика архива применена.",
          )
        }
      >
        Применить политику архива сейчас
      </button>
      <details>
        <summary>Экспорт и импорт памяти</summary>
        <button
          disabled={locked}
          onClick={() =>
            void download(base + "/export", body()).catch((e) =>
              setStatus((e as Error).message),
            )
          }
        >
          Экспорт памяти JSON
        </button>
        <label>
          Пакет памяти
          <input
            type="file"
            accept=".json"
            onChange={async (e) => {
              try {
                const f = e.target.files?.[0];
                if (!f) return;
                if (f.size > 12 * 1024 * 1024)
                  throw Error("Пакет слишком велик.");
                setImportText(
                  new TextDecoder("utf-8", { fatal: true }).decode(
                    await f.arrayBuffer(),
                  ),
                );
                setPreview("");
              } catch (err) {
                setStatus((err as Error).message);
              }
            }}
          />
        </label>
        <label>
          Соответствие NPC (JSON: исходный ID → целевой ID)
          <textarea
            value={actorMap}
            onChange={(e) => {
              setActorMap(e.target.value);
              setPreview("");
            }}
          />
        </label>
        <label>
          Соответствие сообщений (JSON: исходный ID → целевой ID)
          <textarea
            value={sourceMap}
            onChange={(e) => {
              setSourceMap(e.target.value);
              setPreview("");
            }}
          />
        </label>
        <label>
          <input
            type="checkbox"
            checked={external}
            onChange={(e) => {
              setExternal(e.target.checked);
              setPreview("");
            }}
          />
          Сохранить отсутствующие сообщения как внешнее происхождение
        </label>
        <button
          disabled={locked || !importText}
          onClick={async () => {
            try {
              setPreview(
                JSON.stringify(
                  await request(base + "/import-preview", "POST", importBody()),
                  null,
                  2,
                ),
              );
            } catch (e) {
              setStatus((e as Error).message);
            }
          }}
        >
          Предпросмотр импорта памяти
        </button>
        {preview && (
          <>
            <pre className="diagnostic">{preview}</pre>
            <button
              disabled={locked}
              onClick={() => {
                if (
                  confirm(
                    "Импортировать просмотренную память и назначенные знания NPC?",
                  )
                )
                  void mutate(
                    "/import",
                    "POST",
                    { ...importBody(), confirmed: true },
                    "Память импортирована.",
                  );
              }}
            >
              Подтвердить импорт памяти
            </button>
          </>
        )}
      </details>
      {legacy.length > 0 && (
        <details>
          <summary>Сохранённые записи неподтверждённого формата</summary>
          <pre className="diagnostic">{JSON.stringify(legacy, null, 2)}</pre>
        </details>
      )}
      {output && (
        <MemoryResults
          text={output}
          onChoose={(row) => {
            setViewActor("");
            choose(row);
          }}
          onMessage={(id) => void p.onMessage(id)}
          onResolve={(row, decision) => {
            if (confirm("Подтвердить это разрешение конфликта памяти?"))
              void mutate(
                "/conflicts/resolve",
                "POST",
                {
                  ...body(),
                  record_id: row.id,
                  expected_version: row.version,
                  decision,
                  reason: "Ручное разрешение конфликта: " + reason,
                },
                "Конфликт разрешён с сохранением истории.",
              );
          }}
        />
      )}
      {busy && job && (
        <button
          onClick={() =>
            void request(`/generations/${job}/cancel`, "POST").catch((e) =>
              setStatus((e as Error).message),
            )
          }
        >
          Остановить задачу памяти
        </button>
      )}
      <p role="status">{status}</p>
    </details>
  );
}
