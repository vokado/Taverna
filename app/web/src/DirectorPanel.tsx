import { useEffect, useRef, useState } from "react";
import { request } from "./api";
import { useTemplateDraft } from "./useTemplateDraft";

type Actor = { id: string; name: string };
type Emotion = {
  label: string;
  intensity: number;
  valence: number;
  arousal: number;
  confidence: number | null;
};
type NPC = {
  mode: "fixed" | "flexible" | "developing";
  emotions_enabled: boolean;
  relationships_enabled: boolean;
  emotion: Emotion;
  traits: Record<string, number>;
  adaptations: Record<string, number>;
};
type Policy = {
  enabled: boolean;
  before_reply: boolean;
  after_every_messages: number;
  pre_timeout_seconds: number;
  max_actions: number;
  max_output_tokens: number;
  model_id: string | null;
  profile_id: string | null;
  auto_apply: boolean;
  auto_initiative: boolean;
  offscene: boolean;
  actor_entities: Record<string, string>;
  actor_attribute_keys: Record<string, string[]>;
};
type Intention = {
  id: string;
  text: string;
  status: string;
  scene_id: string;
  source_ids: string[];
};
type Action = {
  kind: string;
  reason: string;
  emotion?: Emotion;
  trait?: string;
  delta?: number;
  intent?: string;
  record?: { text: string };
  effect?: { op: string; entity_id: string | null; value: unknown };
};
type Proposal = {
  id: string;
  revision: number;
  created_at: string;
  payload: {
    character_id: string;
    base_revision: number;
    status: string;
    actions: Action[];
    notes: string;
    error: { message_ru: string } | null;
  };
};
type Snapshot = {
  policy: Policy;
  branch_revision: number;
  characters: Record<
    string,
    NPC & { history: unknown[]; intentions: Intention[] }
  >;
  director: {
    arcs?: Record<
      string,
      {
        id: string;
        description: string;
        status: string;
        resolution: string | null;
        knower_ids: string[];
      }
    >;
    audit?: unknown[];
  };
};
type Editor = {
  actor_id: string;
  policy: Policy;
  npc: NPC;
  reason: string;
  dirty_policy: boolean;
  dirty_npc: boolean;
  dirty_raw: boolean;
};
const traitLabels: Record<string, string> = {
  trustfulness: "Доверчивость",
  courage: "Смелость",
  patience: "Терпение",
  assertiveness: "Решительность",
  curiosity: "Любознательность",
  empathy: "Эмпатия",
  optimism: "Оптимизм",
  discipline: "Дисциплина",
};
const emotions = [
  "спокойствие",
  "радость",
  "печаль",
  "страх",
  "гнев",
  "интерес",
  "удивление",
  "отвращение",
  "смущение",
  "надежда",
];
const actionLabels: Record<string, string> = {
  emotion: "Эмоция",
  adaptation: "Ситуативная адаптация",
  evolution: "Развитие характера",
  relationship: "Отношения",
  knowledge: "Знание",
  world_action: "Действие в мире",
  initiative: "Инициатива",
  arc: "Сюжетная линия",
};
function npcForm(value: NPC): NPC {
  return {
    mode: value.mode,
    emotions_enabled: value.emotions_enabled,
    relationships_enabled: value.relationships_enabled,
    emotion: value.emotion,
    traits: value.traits,
    adaptations: value.adaptations,
  };
}
export function DirectorPanel({
  storyId,
  branchId,
  revision,
  actors,
  disabled,
  onChanged,
  registerFlush,
}: {
  storyId: string;
  branchId: string;
  revision: number;
  actors: Actor[];
  disabled: boolean;
  onChanged: () => Promise<void>;
  registerFlush: (flush: () => Promise<void>) => void;
}) {
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null),
    [editor, setEditor] = useState<Editor | null>(null),
    [baseRevision, setBaseRevision] = useState(revision),
    [raw, setRaw] = useState("");
  const [proposals, setProposals] = useState<Proposal[]>([]),
    [models, setModels] = useState<{ id: string; name: string }[]>([]),
    [entities, setEntities] = useState<
      { id: string; kind: string; name: string }[]
    >([]);
  const [busy, setBusy] = useState(false),
    [status, setStatus] = useState(""),
    [job, setJob] = useState(""),
    [jobState, setJobState] = useState(""),
    [pollAttempt, setPollAttempt] = useState(0),
    [proposalLimit, setProposalLimit] = useState(20),
    [consents, setConsents] = useState<Record<string, number>>({});
  const latest = useRef({ editor, baseRevision, raw });
  latest.current = { editor, baseRevision, raw };
  const alive = useRef(true);
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);
  const epoch = useRef(0),
    scope = useRef(storyId + "/" + branchId);
  scope.current = storyId + "/" + branchId;
  const dirty =
    !!editor && (editor.dirty_policy || editor.dirty_npc || editor.dirty_raw);
  const draft = useTemplateDraft(
    "director",
    {
      template_id: editor?.actor_id || null,
      source_revision: baseRevision,
      raw,
      payload: (editor || {}) as Record<string, unknown>,
    },
    dirty,
    busy,
    `/stories/${storyId}/director/drafts/${branchId}`,
  );
  const base = `/stories/${storyId}/director`;
  function replace(
    value: Editor | null,
    version = latest.current.baseRevision,
    nextRaw = latest.current.raw,
  ) {
    latest.current = { editor: value, baseRevision: version, raw: nextRaw };
    setEditor(value);
    setBaseRevision(version);
    setRaw(nextRaw);
  }
  function change(patch: Partial<Editor>) {
    if (latest.current.editor) replace({ ...latest.current.editor, ...patch });
  }
  function policy(patch: Partial<Policy>) {
    const e = latest.current.editor;
    if (e) change({ policy: { ...e.policy, ...patch }, dirty_policy: true });
  }
  function npc(patch: Partial<NPC>) {
    const e = latest.current.editor;
    if (e) change({ npc: { ...e.npc, ...patch }, dirty_npc: true });
  }
  function changeRaw(value: string) {
    const e = latest.current.editor;
    if (e)
      replace({ ...e, dirty_raw: true }, latest.current.baseRevision, value);
  }
  async function load(reset = false, actorId?: string) {
    const currentEpoch = ++epoch.current;
    const [data, proposed, world] = await Promise.all([
      request<Snapshot>(base + `?branch_id=${branchId}`),
      request<{ items: Proposal[] }>(base + `/proposals?branch_id=${branchId}`),
      request<{
        world: {
          entities?: Record<string, { id: string; kind: string; name: string }>;
        };
      }>(`/stories/${storyId}/world?branch_id=${branchId}`),
    ]);
    if (!alive.current || currentEpoch !== epoch.current) return;
    setSnapshot(data);
    setProposals(
      proposed.items.sort((a, b) => b.created_at.localeCompare(a.created_at)),
    );
    setEntities(Object.values(world.world.entities || {}));
    const old = latest.current.editor,
      id = actorId || old?.actor_id || actors[0]?.id;
    if (!id || !data.characters[id]) return;
    if (reset || !old || !(old.dirty_policy || old.dirty_npc || old.dirty_raw))
      replace(
        {
          actor_id: id,
          policy: data.policy,
          npc: npcForm(data.characters[id]),
          reason: "Ручное изменение владельца",
          dirty_policy: false,
          dirty_npc: false,
          dirty_raw: false,
        },
        data.branch_revision,
        "",
      );
  }
  useEffect(() => {
    void load().catch((e) => setStatus((e as Error).message));
    return () => {
      epoch.current++;
    };
  }, [storyId, branchId, revision]);
  useEffect(() => {
    registerFlush(draft.flush);
    return () => {
      registerFlush(async () => {});
    };
  }, []);
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
  async function reload() {
    await draft.flush();
    draft.create();
    await load(true);
    setStatus(
      "Загружена текущая ревизия. Предыдущие изменения сохранены в черновиках.",
    );
  }
  async function publish(kind: "policy" | "npc") {
    const old = latest.current.editor;
    if (!old) return;
    await draft.flush();
    const result = await request<{ branch_revision: number }>(
      kind === "policy" ? base + "/policy" : base + "/npcs/" + old.actor_id,
      "PUT",
      {
        branch_id: branchId,
        expected_revision: latest.current.baseRevision,
        reason: old.reason,
        ...(kind === "policy" ? { policy: old.policy } : { patch: old.npc }),
      },
    );
    if (!alive.current) return;
    const next = {
      ...old,
      ...(kind === "policy" ? { dirty_policy: false } : { dirty_npc: false }),
    };
    replace(next, result.branch_revision);
    if (!next.dirty_policy && !next.dirty_npc && !next.dirty_raw)
      await draft.remove();
    await onChanged();
    await load();
    setStatus(
      kind === "policy"
        ? "Настройки режиссёра сохранены."
        : "Состояние NPC сохранено в этой ветви.",
    );
  }
  useEffect(() => {
    if (!job) return;
    let active = true;
    const currentScope = scope.current;
    async function poll() {
      try {
        const data = await request<{
          state: string;
          error?: { message_ru: string };
          result?: { initiative_generation_id?: string };
        }>(`/generations/${job}`);
        if (!active || scope.current !== currentScope) return;
        setJobState(data.state);
        if (
          ["completed", "failed", "cancelled", "interrupted"].includes(
            data.state,
          )
        ) {
          setStatus(
            data.error?.message_ru ||
              (data.state === "completed"
                ? "Вспомогательная задача завершена. Проверьте предложения и журнал."
                : "Задача остановлена; сохранённые данные доступны."),
          );
          await onChanged();
          if (active) await load();
          return;
        }
        timer = setTimeout(() => void poll(), 500);
      } catch (e) {
        if (active) {
          setJobState("connection_error");
          setStatus("Связь с задачей потеряна: " + (e as Error).message);
        }
      }
    }
    let timer: ReturnType<typeof setTimeout>;
    void poll();
    return () => {
      active = false;
      clearTimeout(timer);
    };
  }, [job, pollAttempt]);
  async function analyze() {
    const e = latest.current.editor;
    if (!e) return;
    await draft.flush();
    const q = await request<{ generation_id: string }>(
      base + "/analyze",
      "POST",
      {
        branch_id: branchId,
        expected_revision: snapshot!.branch_revision,
        character_id: e.actor_id,
        model_id: e.policy.model_id,
        profile_id: e.policy.profile_id,
      },
      { "Idempotency-Key": crypto.randomUUID() },
    );
    if (!alive.current) return;
    setJob(q.generation_id);
    setJobState("queued");
    setStatus(
      "Анализ поставлен в общую очередь. Изменения применяются по сохранённой политике.",
    );
  }
  async function review(value: Proposal, accept: boolean) {
    await draft.flush();
    await request(base + `/proposals/${value.id}/review`, "POST", {
      branch_id: branchId,
      expected_revision: snapshot!.branch_revision,
      proposal_revision: value.revision,
      accept,
      confirmed: true,
      rebase: consents[value.id] === snapshot!.branch_revision,
    });
    await onChanged();
    await load();
    setStatus(
      accept ? "Предложение проверено и применено." : "Предложение отклонено.",
    );
  }
  async function initiative(intent: Intention) {
    const e = latest.current.editor!;
    await draft.flush();
    const q = await request<{ generation_id: string }>(
      base + "/initiative",
      "POST",
      {
        branch_id: branchId,
        expected_revision: snapshot!.branch_revision,
        character_id: e.actor_id,
        intention_id: intent.id,
        model_id: e.policy.model_id,
        confirmed: true,
      },
      { "Idempotency-Key": crypto.randomUUID() },
    );
    if (!alive.current) return;
    setJob(q.generation_id);
    setJobState("queued");
  }
  async function dismiss(targetId: string, kind: "arc" | "intention") {
    const e = latest.current.editor;
    if (!e || !snapshot) return;
    await draft.flush();
    await request(base + "/dismiss", "POST", {
      branch_id: branchId,
      expected_revision: snapshot.branch_revision,
      character_id: e.actor_id,
      target_id: targetId,
      kind,
      reason: e.reason,
    });
    if (!alive.current) return;
    await onChanged();
    await load();
    setStatus(
      "Решение владельца сохранено. История изменений доступна в журнале.",
    );
  }
  const current = snapshot?.characters[editor?.actor_id || ""];
  return (
    <details aria-label="Режиссёр и состояния NPC">
      <summary>Режиссёр, характер и эмоции</summary>
      <p role="status">{[status, draft.status].filter(Boolean).join(" ")}</p>
      {job && (
        <div>
          <p>
            Задача:{" "}
            {{
              queued: "в очереди",
              running: "выполняется",
              completed: "завершена",
              failed: "ошибка",
              cancelled: "отменена",
              interrupted: "прервана",
              connection_error: "состояние неизвестно — нет связи",
            }[jobState] || jobState}
          </p>
          {jobState === "connection_error" && (
            <button onClick={() => setPollAttempt((n) => n + 1)}>
              Повторить проверку задачи
            </button>
          )}
          <button
            disabled={[
              "completed",
              "failed",
              "cancelled",
              "interrupted",
            ].includes(jobState)}
            onClick={() =>
              void action(async () => {
                await request(`/generations/${job}/cancel`, "POST");
                setStatus("Запрос остановки принят.");
              })
            }
          >
            Остановить задачу режиссёра
          </button>
        </div>
      )}
      {!editor || !snapshot ? (
        <button onClick={() => void action(() => load(true))}>
          Повторить загрузку режиссёра
        </button>
      ) : (
        <fieldset disabled={busy || disabled}>
          {baseRevision !== snapshot.branch_revision && dirty && (
            <p>
              Ветвь изменилась после начала редактирования. Черновик сохранится,
              публикация со старой ревизией будет отклонена.
            </p>
          )}
          <button onClick={() => void action(reload)}>
            Перечитать состояния NPC
          </button>
          <button onClick={() => void action(draft.saveSeparate)}>
            Сохранить отдельный черновик режиссёра
          </button>
          <label>
            Редактируемый NPC
            <select
              value={editor.actor_id}
              onChange={(e) => {
                const target = e.target.value;
                void action(async () => {
                  await draft.flush();
                  draft.create();
                  await load(true, target);
                });
              }}
            >
              {actors.map((actor) => (
                <option key={actor.id} value={actor.id}>
                  {actor.name}
                </option>
              ))}
            </select>
          </label>
          <label>
            Причина изменения NPC
            <input
              value={editor.reason}
              maxLength={2000}
              onChange={(e) =>
                change({ reason: e.target.value, dirty_npc: true })
              }
            />
          </label>
          <label>
            Режим характера в ветви
            <select
              value={editor.npc.mode}
              onChange={(e) => npc({ mode: e.target.value as NPC["mode"] })}
            >
              <option value="fixed">Фиксированный</option>
              <option value="flexible">Гибкий</option>
              <option value="developing">Развивающийся</option>
            </select>
          </label>
          <p>
            Фиксированный сохраняет основу карточки; гибкий допускает адаптацию
            сцены; развивающийся — постепенные подтверждённые сдвиги. Карточка
            библиотеки не меняется.
          </p>
          <label>
            <input
              type="checkbox"
              checked={editor.npc.emotions_enabled}
              onChange={(e) => npc({ emotions_enabled: e.target.checked })}
            />
            Учитывать эмоциональное состояние
          </label>
          <label>
            <input
              type="checkbox"
              checked={editor.npc.relationships_enabled}
              onChange={(e) => npc({ relationships_enabled: e.target.checked })}
            />
            Учитывать модуль отношений
          </label>
          <label>
            Текущая эмоция
            <select
              value={editor.npc.emotion.label}
              onChange={(e) =>
                npc({
                  emotion: { ...editor.npc.emotion, label: e.target.value },
                })
              }
            >
              {emotions.map((value) => (
                <option key={value}>{value}</option>
              ))}
            </select>
          </label>
          {(["intensity", "valence", "arousal"] as const).map((key, i) => (
            <label key={key}>
              {["Сила эмоции", "Тон эмоции (−1…1)", "Возбуждение (0…1)"][i]}
              <input
                type="number"
                min={key === "valence" ? -1 : 0}
                max={1}
                step={0.1}
                value={editor.npc.emotion[key]}
                onChange={(e) =>
                  npc({
                    emotion: {
                      ...editor.npc.emotion,
                      [key]: Number(e.target.value),
                    },
                  })
                }
              />
            </label>
          ))}
          <label>
            Самооценка уверенности (пусто: не задана)
            <input
              type="number"
              min={0}
              max={1}
              step={0.1}
              value={editor.npc.emotion.confidence ?? ""}
              onChange={(e) =>
                npc({
                  emotion: {
                    ...editor.npc.emotion,
                    confidence:
                      e.target.value === "" ? null : Number(e.target.value),
                  },
                })
              }
            />
          </label>
          <details>
            <summary>Тенденции и адаптация</summary>
            <p>
              Числа −1…1 задают относительные тенденции, не психологический
              диагноз. Модель ограничена сдвигом0.1 на предложение; ручное
              изменение фиксируется в истории.
            </p>
            {Object.entries(traitLabels).map(([key, label]) => (
              <div key={key}>
                <label>
                  {label}: устойчивая тенденция
                  <input
                    type="number"
                    min={-1}
                    max={1}
                    step={0.1}
                    value={editor.npc.traits[key] ?? 0}
                    onChange={(e) =>
                      npc({
                        traits: {
                          ...editor.npc.traits,
                          [key]: Number(e.target.value),
                        },
                      })
                    }
                  />
                </label>
                <label>
                  {label}: адаптация сцены
                  <input
                    type="number"
                    min={-1}
                    max={1}
                    step={0.1}
                    value={editor.npc.adaptations[key] ?? 0}
                    onChange={(e) =>
                      npc({
                        adaptations: {
                          ...editor.npc.adaptations,
                          [key]: Number(e.target.value),
                        },
                      })
                    }
                  />
                </label>
              </div>
            ))}
          </details>
          <button
            disabled={!editor.reason.trim()}
            onClick={() => void action(() => publish("npc"))}
          >
            Сохранить состояние NPC
          </button>
          <details>
            <summary>Автономность и настройки режиссёра</summary>
            {(
              [
                "enabled",
                "before_reply",
                "auto_apply",
                "auto_initiative",
                "offscene",
              ] as const
            ).map((key, i) => (
              <label key={key}>
                <input
                  type="checkbox"
                  checked={editor.policy[key]}
                  onChange={(e) => policy({ [key]: e.target.checked })}
                />
                {
                  [
                    "Включить режиссёра для ветви",
                    "Готовить предложения до ответа",
                    "Автоматически применять прошедшие проверки предложения",
                    "Разрешить одну самостоятельную реплику NPC на внешний триггер",
                    "Анализировать NPC вне сцены",
                  ][i]
                }
              </label>
            ))}
            <p>
              Автоприменение записывает изменения после структурных проверок.
              Смысловая оценка модели может ошибаться; изменения и источники
              остаются в журнале. Инициатива не создаёт реплик пользователя.
            </p>
            <button
              onClick={() =>
                void action(async () => {
                  const r = await request<{
                    models: Array<{ id: string; name: string; type: string }>;
                  }>("/models");
                  setModels(r.models.filter((m) => m.type === "llm"));
                  setStatus(
                    "Показаны только уже загруженные LLM. Моделями управляйте в LM Studio.",
                  );
                })
              }
            >
              Обновить модели режиссёра
            </button>
            <label>
              Модель режиссёра
              <select
                value={editor.policy.model_id || ""}
                onChange={(e) => policy({ model_id: e.target.value || null })}
              >
                <option value="">
                  Наследовать служебный профиль / модель диалога
                </option>
                {editor.policy.model_id &&
                  !models.some((m) => m.id === editor.policy.model_id) && (
                    <option value={editor.policy.model_id}>
                      {editor.policy.model_id} (доступность не проверена)
                    </option>
                  )}
                {models.map((m) => (
                  <option key={m.id} value={m.id}>
                    {m.name}
                  </option>
                ))}
              </select>
            </label>
            {(
              [
                "after_every_messages",
                "pre_timeout_seconds",
                "max_actions",
                "max_output_tokens",
              ] as const
            ).map((key, i) => (
              <label key={key}>
                {
                  [
                    "Анализировать каждые N сообщений",
                    "Лимит модельной подготовки, секунд",
                    "Максимум действий в предложении",
                    "Резерв токенов режиссёра",
                  ][i]
                }
                <input
                  type="number"
                  min={key === "max_output_tokens" ? 128 : 1}
                  max={
                    key === "max_actions"
                      ? 16
                      : key === "max_output_tokens"
                        ? 2048
                        : key === "pre_timeout_seconds"
                          ? 60
                          : 100
                  }
                  value={editor.policy[key]}
                  onChange={(e) => policy({ [key]: Number(e.target.value) })}
                />
              </label>
            ))}
            <label>
              UUID служебного профиля (пусто: наследование)
              <input
                value={editor.policy.profile_id || ""}
                onChange={(e) => policy({ profile_id: e.target.value || null })}
              />
            </label>
            <label>
              Сущность мира для выбранного NPC
              <select
                value={editor.policy.actor_entities[editor.actor_id] || ""}
                onChange={(e) => {
                  const next = { ...editor.policy.actor_entities };
                  if (e.target.value) next[editor.actor_id] = e.target.value;
                  else delete next[editor.actor_id];
                  policy({ actor_entities: next });
                }}
              >
                <option value="">Не связана</option>
                {entities
                  .filter((entity) =>
                    ["person", "faction"].includes(entity.kind),
                  )
                  .map((entity) => (
                    <option key={entity.id} value={entity.id}>
                      {entity.name}
                    </option>
                  ))}
              </select>
            </label>
            <label>
              Разрешённые атрибуты своей сущности, по одному в строке
              <textarea
                value={(
                  editor.policy.actor_attribute_keys[editor.actor_id] || []
                ).join("\n")}
                onChange={(e) =>
                  policy({
                    actor_attribute_keys: {
                      ...editor.policy.actor_attribute_keys,
                      [editor.actor_id]: e.target.value
                        .split("\n")
                        .filter(Boolean),
                    },
                  })
                }
              />
            </label>
            <button
              disabled={!editor.reason.trim()}
              onClick={() => void action(() => publish("policy"))}
            >
              Сохранить настройки режиссёра
            </button>
          </details>
          <button
            disabled={
              !snapshot.policy.enabled ||
              ["queued", "running"].includes(jobState)
            }
            onClick={() => void action(analyze)}
          >
            Предложить изменения для NPC
          </button>
          <details>
            <summary>Предложения и решения</summary>
            {proposals.filter((p) => p.payload.character_id === editor.actor_id)
              .length === 0 && <p>Предложений для выбранного NPC пока нет.</p>}
            {proposals
              .filter((p) => p.payload.character_id === editor.actor_id)
              .slice(0, proposalLimit)
              .map((value) => (
                <article key={value.id}>
                  <strong>
                    {
                      (
                        {
                          pending: "Ожидает проверки",
                          accepted: "Применено",
                          rejected: "Отклонено",
                          invalid: "Не прошло проверку",
                        } as Record<string, string>
                      )[value.payload.status]
                    }
                  </strong>
                  <p>
                    Исходная ревизия: {value.payload.base_revision}; текущая:{" "}
                    {snapshot.branch_revision}.
                  </p>
                  {value.payload.error && (
                    <p>{value.payload.error.message_ru}</p>
                  )}
                  <ul>
                    {value.payload.actions.map((item, i) => (
                      <li key={i}>
                        <strong>
                          {actionLabels[item.kind] || item.kind}:{" "}
                        </strong>
                        {item.emotion?.label ||
                          item.intent ||
                          item.record?.text ||
                          (item.trait
                            ? `${traitLabels[item.trait] || item.trait}: ${item.delta}`
                            : item.effect
                              ? `${item.effect.op}: ${String(item.effect.value)}`
                              : "")}
                        <p>{item.reason}</p>
                      </li>
                    ))}
                  </ul>
                  <LazyJSON
                    title="Служебный план и источники"
                    value={value.payload}
                  />
                  {value.payload.status === "pending" && (
                    <>
                      {value.payload.base_revision !==
                        snapshot.branch_revision && (
                        <label>
                          <input
                            type="checkbox"
                            checked={
                              consents[value.id] === snapshot.branch_revision
                            }
                            onChange={(e) =>
                              setConsents((old) => ({
                                ...old,
                                [value.id]: e.target.checked
                                  ? snapshot.branch_revision
                                  : -1,
                              }))
                            }
                          />
                          Подтверждаю повторную привязку к ревизии{" "}
                          {snapshot.branch_revision} при неизменных источниках и
                          состоянии NPC
                        </label>
                      )}
                      <button
                        disabled={
                          value.payload.base_revision !==
                            snapshot.branch_revision &&
                          consents[value.id] !== snapshot.branch_revision
                        }
                        onClick={() => void action(() => review(value, true))}
                      >
                        Применить предложение режиссёра
                      </button>
                      <button
                        onClick={() => void action(() => review(value, false))}
                      >
                        Отклонить предложение режиссёра
                      </button>
                    </>
                  )}
                </article>
              ))}
            {proposals.filter((p) => p.payload.character_id === editor.actor_id)
              .length > proposalLimit && (
              <button onClick={() => setProposalLimit((n) => n + 20)}>
                Показать ещё 20 предложений
              </button>
            )}
          </details>
          <details>
            <summary>Намерения, линии и история изменений</summary>
            {current?.intentions.map((intent) => (
              <article key={intent.id}>
                <p>
                  {intent.text} —{" "}
                  {intent.status === "proposed_action"
                    ? "возможное намерение"
                    : intent.status === "dismissed"
                      ? "отменено владельцем"
                      : "реплика уже сформирована"}
                </p>
                <button
                  disabled={intent.status !== "proposed_action"}
                  onClick={() => void action(() => initiative(intent))}
                >
                  Дать реплику по инициативе NPC
                </button>
                {intent.status === "proposed_action" && (
                  <button
                    onClick={() =>
                      void action(() => dismiss(intent.id, "intention"))
                    }
                  >
                    Отменить намерение NPC
                  </button>
                )}
              </article>
            ))}
            {Object.values(snapshot.director.arcs || {})
              .filter((arc) => arc.knower_ids.includes(editor.actor_id))
              .map((arc) => (
                <p key={arc.id}>
                  {arc.description} —{" "}
                  {arc.status === "open"
                    ? "открытая линия"
                    : "разрешённая линия"}
                </p>
              ))}
            <LazyJSON
              title="История выбранного NPC"
              value={current?.history || []}
            />
            <LazyJSON
              title="Журнал режиссёра"
              value={snapshot.director.audit || []}
            />
          </details>
          <details>
            <summary>JSON состояний и черновики режиссёра</summary>
            <button
              onClick={() => {
                changeRaw(
                  JSON.stringify(
                    { policy: editor.policy, npc: editor.npc },
                    null,
                    2,
                  ),
                );
              }}
            >
              Заполнить JSON состояний
            </button>
            <label>
              JSON редактора режиссёра
              <textarea
                value={raw}
                onChange={(e) => {
                  changeRaw(e.target.value);
                }}
                rows={12}
              />
            </label>
            <button
              onClick={() =>
                void action(async () => {
                  const valid = await request<{ policy: Policy; npc: NPC }>(
                    base + "/validate",
                    "POST",
                    JSON.parse(raw),
                  );
                  replace({
                    ...editor,
                    ...valid,
                    dirty_policy: true,
                    dirty_npc: true,
                    dirty_raw: false,
                  });
                  setStatus(
                    "JSON проверен и применён к форме. Публикация выполняется отдельными кнопками.",
                  );
                })
              }
            >
              Проверить JSON состояний
            </button>
            {draft.items.map((item) => (
              <div key={item.id}>
                <span>
                  {actors.find((a) => a.id === item.payload.template_id)
                    ?.name || "NPC"}
                </span>
                <button
                  onClick={() =>
                    void action(async () => {
                      await draft.flush();
                      draft.adopt(item);
                      replace(
                        item.payload.payload as Editor,
                        item.payload.source_revision,
                        item.payload.raw,
                      );
                      setStatus(
                        "Черновик восстановлен. Проверьте исходную ревизию перед публикацией.",
                      );
                    })
                  }
                >
                  Восстановить черновик режиссёра
                </button>
              </div>
            ))}
          </details>
        </fieldset>
      )}
    </details>
  );
}

function LazyJSON({ title, value }: { title: string; value: unknown }) {
  const [open, setOpen] = useState(false);
  return (
    <details onToggle={(event) => setOpen(event.currentTarget.open)}>
      <summary>{title}</summary>
      {open && <pre>{JSON.stringify(value, null, 2)}</pre>}
    </details>
  );
}
