import { useEffect, useRef, useState } from "react";
import type { components } from "./api-types";
import { request } from "./api";
import { useTemplateDraft } from "./useTemplateDraft";
type Settings = components["schemas"]["RpgSettings"];
type Action = components["schemas"]["RpgAction"];
type Module =
  | "inventory"
  | "stats"
  | "skills"
  | "dice"
  | "economy"
  | "conflicts"
  | "random_events";
type Editor = { settings: Settings; reason: string; seed: string };
type Sheet = {
  attributes: Record<string, number>;
  skills: Record<string, number>;
  inventory: Record<string, number>;
  balances: Record<string, number>;
  health: number;
  max_health: number;
  capacity_grams: number | null;
};
type Receipt = {
  id: string;
  kind: string;
  reason: string;
  result?: {
    rolls?: number[];
    total?: number;
    success?: boolean;
    outcome?: { text: string };
  };
  seed?: string;
  counter_before?: number;
  counter_after?: number;
};
type Snapshot = {
  branch_revision: number;
  player_id: string;
  actors: Record<string, string>;
  rpg: {
    settings: Settings;
    actors: Record<string, Sheet>;
    seed: string;
    counter: number;
    audit: Receipt[];
    conflicts: Record<
      string,
      { id: string; status: string; turn: string; round: number }
    >;
  };
};
const modules: Record<Module, string> = {
  inventory: "Инвентарь",
  stats: "Характеристики",
  skills: "Навыки",
  dice: "Кубики",
  economy: "Экономика",
  conflicts: "Конфликты",
  random_events: "Случайные события",
};
const actions: Record<Action["kind"], string> = {
  set_attribute: "Задать характеристику",
  set_skill: "Задать навык",
  set_health: "Исправить здоровье",
  set_capacity: "Задать грузоподъёмность",
  adjust_item: "Исправить количество предмета",
  transfer_item: "Передать предмет",
  adjust_currency: "Исправить валютный остаток",
  transfer_currency: "Передать валюту",
  trade: "Купить предмет",
  roll: "Бросить кубики",
  check: "Проверить навык",
  start_conflict: "Начать конфликт",
  attack: "Выполнить ход конфликта",
  end_conflict: "Завершить конфликт",
  random_event: "Разыграть событие",
};
const actionModules: Record<Action["kind"], Module> = {
  set_attribute: "stats",
  set_skill: "skills",
  set_health: "stats",
  set_capacity: "inventory",
  adjust_item: "inventory",
  transfer_item: "inventory",
  adjust_currency: "economy",
  transfer_currency: "economy",
  trade: "economy",
  roll: "dice",
  check: "skills",
  start_conflict: "conflicts",
  attack: "conflicts",
  end_conflict: "conflicts",
  random_event: "random_events",
};
const copy = <T,>(v: T): T => JSON.parse(JSON.stringify(v));
function Num({
  label,
  value,
  onChange,
}: {
  label: string;
  value: number;
  onChange: (v: number) => void;
}) {
  return (
    <label>
      {label}
      <input
        type="number"
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
      />
    </label>
  );
}
function Text({
  label,
  value,
  onChange,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
}) {
  return (
    <label>
      {label}
      <input value={value} onChange={(e) => onChange(e.target.value)} />
    </label>
  );
}
function JsonDetails({ label, value }: { label: string; value: unknown }) {
  const [open, setOpen] = useState(false);
  return (
    <details
      onToggle={(e) => {
        if (e.target === e.currentTarget) setOpen(e.currentTarget.open);
      }}
    >
      <summary>{label}</summary>
      {open && <pre>{JSON.stringify(value, null, 2)}</pre>}
    </details>
  );
}

export function RpgPanel({
  storyId,
  branchId,
  revision,
  disabled,
  onChanged,
  registerFlush,
}: {
  storyId: string;
  branchId: string;
  revision: number;
  disabled: boolean;
  onChanged: () => Promise<void>;
  registerFlush: (flush: () => Promise<void>) => void;
}) {
  const [opened, setOpened] = useState(false),
    [snapshot, setSnapshot] = useState<Snapshot | null>(null),
    [editor, setEditor] = useState<Editor | null>(null),
    [baseRevision, setBaseRevision] = useState(revision),
    [dirty, setDirty] = useState(false),
    [raw, setRaw] = useState("");
  const [busy, setBusy] = useState(false),
    [status, setStatus] = useState(""),
    [receipt, setReceipt] = useState<Receipt | null>(null),
    [auditLimit, setAuditLimit] = useState(20);
  const [form, setForm] = useState<Partial<Action> & { kind: Action["kind"] }>({
    kind: "roll",
    key: "",
    amount: 1,
    quantity: 1,
    price: 1,
    currency: "",
    attribute: "",
    skill: "",
    difficulty: 10,
    max_health: 10,
    dice: { count: 1, sides: 20, modifier: 0 },
    participants: [],
  });
  const [locations, setLocations] = useState<{ id: string; name: string }[]>(
      [],
    ),
    [flagKey, setFlagKey] = useState(""),
    [flagValue, setFlagValue] = useState("true");
  const latest = useRef({ editor, baseRevision, dirty, raw });
  latest.current = { editor, baseRevision, dirty, raw };
  const alive = useRef(true),
    sequence = useRef(0),
    base = `/stories/${storyId}/rpg`,
    draftBase = base + `/drafts/${branchId}`;
  const draft = useTemplateDraft(
    "rpg",
    {
      template_id: null,
      source_revision: baseRevision,
      raw,
      payload: (editor || {}) as Record<string, unknown>,
    },
    dirty,
    busy,
    draftBase,
  );
  function replace(e: Editor, v: number, text: string, d: boolean) {
    latest.current = { editor: e, baseRevision: v, raw: text, dirty: d };
    setEditor(e);
    setBaseRevision(v);
    setRaw(text);
    setDirty(d);
  }
  function edit(fn: (s: Settings) => void) {
    const old = latest.current;
    if (!old.editor) return;
    const next = copy(old.editor);
    fn(next.settings);
    replace(next, old.baseRevision, old.raw, true);
  }
  function patch(p: Partial<Action>) {
    setForm((f) => ({ ...f, ...p }));
  }
  async function load(reset = false) {
    const ticket = ++sequence.current;
    const [data, world] = await Promise.all([
      request<Snapshot>(base + `?branch_id=${branchId}`),
      request<{
        world: {
          entities?: Record<string, { id: string; name: string; kind: string }>;
        };
      }>(`/stories/${storyId}/world?branch_id=${branchId}`),
    ]);
    if (!alive.current || ticket !== sequence.current) return;
    setSnapshot(data);
    setForm((f) => ({ ...f, actor_id: f.actor_id || data.player_id }));
    setLocations(
      Object.values(world.world.entities || {}).filter(
        (e) => e.kind === "location",
      ),
    );
    if (reset || !latest.current.editor || !latest.current.dirty)
      replace(
        {
          settings: data.rpg.settings,
          reason: "Ручное действие владельца",
          seed: "",
        },
        data.branch_revision,
        "",
        false,
      );
  }
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
      sequence.current++;
    };
  }, []);
  useEffect(() => {
    if (opened) void load().catch((e) => setStatus((e as Error).message));
  }, [revision, opened]);
  useEffect(() => {
    registerFlush(draft.flush);
    return () => registerFlush(async () => {});
  }, []);
  async function action(fn: () => Promise<void>) {
    setBusy(true);
    setStatus("");
    try {
      await fn();
    } catch (e) {
      if (alive.current) setStatus((e as Error).message);
    } finally {
      if (alive.current) setBusy(false);
    }
  }
  async function save() {
    const old = latest.current;
    if (!old.editor) return;
    await draft.flush();
    const result = await request<{ branch_revision: number }>(
      base + "/settings",
      "PUT",
      {
        branch_id: branchId,
        expected_revision: old.baseRevision,
        settings: old.editor.settings,
        reason: old.editor.reason,
        seed: old.editor.seed || null,
      },
    );
    if (!alive.current) return;
    replace(
      { ...old.editor, seed: "" },
      result.branch_revision,
      old.raw,
      Boolean(old.raw.trim()),
    );
    if (!old.raw.trim()) await draft.remove();
    await onChanged();
    await load();
    setStatus("Настройки RPG сохранены.");
  }
  async function perform() {
    if (!snapshot || !editor) return;
    await draft.flush();
    const result = await request<{ receipt: Receipt }>(
      base + "/actions",
      "POST",
      {
        ...form,
        branch_id: branchId,
        expected_revision: snapshot.branch_revision,
        event_id: crypto.randomUUID(),
        target_id: form.target_id || null,
        conflict_id: form.conflict_id || null,
        actor_id: form.actor_id || snapshot.player_id,
        reason: editor.reason,
        confirmed: true,
        max_health: form.kind === "set_health" ? form.max_health : null,
      },
    );
    if (!alive.current) return;
    setReceipt(result.receipt);
    await onChanged();
    await load();
    setStatus("Игровая операция сохранена.");
  }
  const kind = form.kind,
    actor = form.actor_id || snapshot?.player_id || "",
    sheet = snapshot?.rpg.actors[actor];
  return (
    <details
      aria-label="Игровые модули RPG"
      onToggle={(e) => {
        if (e.target === e.currentTarget) setOpened(e.currentTarget.open);
      }}
    >
      <summary>Игровые модули RPG</summary>
      <p role="status">{[status, draft.status].filter(Boolean).join(" ")}</p>
      {!editor || !snapshot ? (
        <button onClick={() => void action(() => load())}>Загрузить RPG</button>
      ) : (
        <fieldset disabled={busy || disabled}>
          <p>
            Модули выключены по умолчанию. Операции используют опубликованные
            настройки; выключение сохраняет данные и журнал.
          </p>
          {dirty && baseRevision !== snapshot.branch_revision && (
            <p>
              Ветвь изменилась: черновик остаётся на исходной ревизии{" "}
              {baseRevision}.
            </p>
          )}
          <button
            onClick={() =>
              void action(async () => {
                await draft.flush();
                await load(true);
              })
            }
          >
            Перечитать настройки RPG
          </button>
          <Text
            label="Причина игровой операции"
            value={editor.reason}
            onChange={(v) =>
              replace({ ...editor, reason: v }, baseRevision, raw, true)
            }
          />
          <details>
            <summary>Настройки и каталоги RPG</summary>
            {Object.entries(modules).map(([m, label]) => (
              <label key={m}>
                <input
                  type="checkbox"
                  checked={!!editor.settings.enabled?.[m]}
                  onChange={(e) =>
                    edit((s) => {
                      s.enabled = { ...s.enabled, [m]: e.target.checked };
                    })
                  }
                />
                {label}: включить модуль
              </label>
            ))}
            <details>
              <summary>Политика мира для игровых действий</summary>
              {Object.entries(modules).map(([m, label]) => (
                <label key={m}>
                  <input
                    type="checkbox"
                    checked={editor.settings.world_policy!.allowed_modules!.includes(
                      m as Module,
                    )}
                    onChange={(e) =>
                      edit((s) => {
                        s.world_policy!.allowed_modules = e.target.checked
                          ? [...s.world_policy!.allowed_modules!, m as Module]
                          : s.world_policy!.allowed_modules!.filter(
                              (x) => x !== m,
                            );
                      })
                    }
                  />
                  Мир разрешает: {label}
                </label>
              ))}
              <label>
                <input
                  type="checkbox"
                  checked={editor.settings.world_policy!.allow_offscene}
                  onChange={(e) =>
                    edit((s) => {
                      s.world_policy!.allow_offscene = e.target.checked;
                    })
                  }
                />
                Разрешить действия вне сцены
              </label>
              <p>
                Без отмеченных локаций нет локального ограничения места.
                Политика прикреплённого мира extras.rpg_policy также
                обязательна.
              </p>
              {locations.map((l) => (
                <label key={l.id}>
                  <input
                    type="checkbox"
                    checked={editor.settings.world_policy!.allowed_locations!.includes(
                      l.id,
                    )}
                    onChange={(e) =>
                      edit((s) => {
                        s.world_policy!.allowed_locations = e.target.checked
                          ? [...s.world_policy!.allowed_locations!, l.id]
                          : s.world_policy!.allowed_locations!.filter(
                              (x) => x !== l.id,
                            );
                      })
                    }
                  />
                  Разрешённая локация: {l.name}
                </label>
              ))}
              {Object.entries(snapshot.actors).map(([id, name]) => (
                <label key={id}>
                  <input
                    type="checkbox"
                    checked={editor.settings.world_policy!.blocked_actor_ids!.includes(
                      id,
                    )}
                    onChange={(e) =>
                      edit((s) => {
                        s.world_policy!.blocked_actor_ids = e.target.checked
                          ? [...s.world_policy!.blocked_actor_ids!, id]
                          : s.world_policy!.blocked_actor_ids!.filter(
                              (x) => x !== id,
                            );
                      })
                    }
                  />
                  Запретить игровые действия: {name}
                </label>
              ))}
              <Text
                label="Обязательный флаг мира"
                value={flagKey}
                onChange={setFlagKey}
              />
              <Text
                label="Значение флага"
                value={flagValue}
                onChange={setFlagValue}
              />
              <button
                disabled={!flagKey.trim()}
                onClick={() =>
                  edit((s) => {
                    s.world_policy!.required_flags![flagKey] =
                      flagValue === "true"
                        ? true
                        : flagValue === "false"
                          ? false
                          : flagValue;
                  })
                }
              >
                Добавить условие флага
              </button>
              {Object.entries(
                editor.settings.world_policy!.required_flags || {},
              ).map(([k, v]) => (
                <p key={k}>
                  {k}: {String(v)}
                  <button
                    onClick={() =>
                      edit((s) => {
                        delete s.world_policy!.required_flags![k];
                      })
                    }
                  >
                    Удалить условие {k}
                  </button>
                </p>
              ))}
            </details>
            <Catalogs settings={editor.settings} edit={edit} />
            <Text
              label="Новый seed RNG (пусто: оставить прежний)"
              value={editor.seed}
              onChange={(v) =>
                replace({ ...editor, seed: v }, baseRevision, raw, true)
              }
            />
            <button onClick={() => void action(save)}>
              Сохранить настройки RPG
            </button>
          </details>
          <details>
            <summary>Игровые операции</summary>
            <label>
              Участник RPG
              <select
                value={actor}
                onChange={(e) => patch({ actor_id: e.target.value })}
              >
                {Object.entries(snapshot.actors).map(([id, name]) => (
                  <option key={id} value={id}>
                    {name}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Игровая операция
              <select
                value={kind}
                onChange={(e) =>
                  patch({ kind: e.target.value as Action["kind"] })
                }
              >
                {Object.entries(actions).map(([k, label]) => (
                  <option key={k} value={k}>
                    {label}
                  </option>
                ))}
              </select>
            </label>
            {["trade", "transfer_item", "transfer_currency", "attack"].includes(
              kind,
            ) && (
              <label>
                Другой участник RPG
                <select
                  value={form.target_id || ""}
                  onChange={(e) => patch({ target_id: e.target.value })}
                >
                  <option value="">Выберите</option>
                  {Object.entries(snapshot.actors)
                    .filter(([id]) => id !== actor)
                    .map(([id, name]) => (
                      <option key={id} value={id}>
                        {name}
                      </option>
                    ))}
                </select>
              </label>
            )}
            {[
              "set_attribute",
              "set_skill",
              "adjust_item",
              "transfer_item",
              "adjust_currency",
              "transfer_currency",
              "trade",
              "random_event",
            ].includes(kind) && (
              <Text
                label="Ключ игрового параметра"
                value={form.key || ""}
                onChange={(v) => patch({ key: v })}
              />
            )}
            {[
              "set_attribute",
              "set_skill",
              "set_health",
              "set_capacity",
              "adjust_item",
              "adjust_currency",
            ].includes(kind) && (
              <Num
                label="Значение или поправка RPG"
                value={form.amount || 0}
                onChange={(v) => patch({ amount: v })}
              />
            )}
            {kind === "set_capacity" && (
              <p>Грузоподъёмность в граммах; 0 снимает ограничение.</p>
            )}
            {kind === "set_health" && (
              <Num
                label="Максимальное здоровье"
                value={form.max_health ?? 10}
                onChange={(v) => patch({ max_health: v })}
              />
            )}
            {["transfer_item", "transfer_currency", "trade"].includes(kind) && (
              <Num
                label="Количество передачи"
                value={form.quantity ?? 1}
                onChange={(v) => patch({ quantity: v })}
              />
            )}
            {kind === "trade" && (
              <>
                <label>
                  Валюта покупки
                  <select
                    value={form.currency || ""}
                    onChange={(e) => patch({ currency: e.target.value })}
                  >
                    <option value="">Выберите</option>
                    {Object.entries(snapshot.rpg.settings.currencies || {}).map(
                      ([k, v]) => (
                        <option key={k} value={k}>
                          {v.name}
                        </option>
                      ),
                    )}
                  </select>
                </label>
                <Num
                  label="Цена за предмет"
                  value={form.price ?? 1}
                  onChange={(v) => patch({ price: v })}
                />
              </>
            )}
            {["roll", "attack"].includes(kind) &&
              (["count", "sides", "modifier"] as const).map((k, i) => (
                <Num
                  key={k}
                  label={
                    ["Число кубиков", "Граней кубика", "Модификатор броска"][i]
                  }
                  value={form.dice?.[k] || 0}
                  onChange={(v) =>
                    patch({
                      dice: {
                        count: form.dice?.count ?? 1,
                        sides: form.dice?.sides ?? 20,
                        modifier: form.dice?.modifier ?? 0,
                        [k]: v,
                      },
                    })
                  }
                />
              ))}
            {["check", "attack"].includes(kind) && (
              <>
                <Text
                  label="Характеристика проверки (необязательно)"
                  value={form.attribute || ""}
                  onChange={(v) => patch({ attribute: v })}
                />
                <Text
                  label="Навык проверки (необязательно)"
                  value={form.skill || ""}
                  onChange={(v) => patch({ skill: v })}
                />
              </>
            )}
            {kind === "check" && (
              <Num
                label="Сложность проверки"
                value={form.difficulty || 0}
                onChange={(v) => patch({ difficulty: v })}
              />
            )}
            {kind === "start_conflict" &&
              Object.entries(snapshot.actors).map(([id, name]) => (
                <label key={id}>
                  <input
                    type="checkbox"
                    checked={form.participants?.includes(id) || false}
                    onChange={(e) =>
                      patch({
                        participants: e.target.checked
                          ? [...(form.participants || []), id]
                          : (form.participants || []).filter((x) => x !== id),
                      })
                    }
                  />
                  Участник конфликта: {name}
                </label>
              ))}
            {["attack", "end_conflict"].includes(kind) && (
              <label>
                Активный конфликт
                <select
                  value={form.conflict_id || ""}
                  onChange={(e) => patch({ conflict_id: e.target.value })}
                >
                  <option value="">Выберите</option>
                  {Object.values(snapshot.rpg.conflicts)
                    .filter((c) => c.status === "active")
                    .map((c) => (
                      <option key={c.id} value={c.id}>
                        Раунд {c.round}, ход {snapshot.actors[c.turn]} — {c.id}
                      </option>
                    ))}
                </select>
              </label>
            )}
            <button
              disabled={!snapshot.rpg.settings.enabled?.[actionModules[kind]]}
              onClick={() => void action(perform)}
            >
              Подтвердить игровую операцию
            </button>
            {receipt && (
              <p role="status">
                Результат:{" "}
                {receipt.result?.outcome?.text ||
                  (receipt.result?.total !== undefined
                    ? `Броски: ${receipt.result.rolls?.join(", ")}; сумма: ${receipt.result.total}${receipt.result.success === undefined ? "" : receipt.result.success ? "; успех" : "; неудача"}`
                    : "изменение сохранено")}
                . Seed: {receipt.seed}; счётчик {receipt.counter_before} →{" "}
                {receipt.counter_after}.
              </p>
            )}
            <h4>Параметры выбранного участника</h4>
            <p>
              Здоровье: {sheet?.health ?? 10}/{sheet?.max_health ?? 10};
              грузоподъёмность: {sheet?.capacity_grams ?? "не ограничена"} г.
            </p>
            {(["attributes", "skills", "inventory", "balances"] as const).map(
              (field, i) => (
                <div key={field}>
                  <strong>
                    {["Характеристики", "Навыки", "Инвентарь", "Валюты"][i]}
                  </strong>
                  <ul>
                    {Object.entries(sheet?.[field] || {}).map(([k, v]) => (
                      <li key={k}>
                        {snapshot.rpg.settings.items?.[k]?.name ||
                          snapshot.rpg.settings.currencies?.[k]?.name ||
                          k}
                        : {v}
                      </li>
                    ))}
                  </ul>
                </div>
              ),
            )}
          </details>
          <details>
            <summary>Журнал игровых операций</summary>
            <p>
              sha256-counter-v1; seed:{" "}
              {snapshot.rpg.seed || "будет создан при первой операции"};
              счётчик: {snapshot.rpg.counter}.
            </p>
            {snapshot.rpg.audit
              .slice()
              .reverse()
              .slice(0, auditLimit)
              .map((r) => (
                <JsonDetails
                  key={r.id}
                  label={`${actions[r.kind as Action["kind"]] || "Настройки"}: ${r.reason}`}
                  value={r}
                />
              ))}
            {snapshot.rpg.audit.length > auditLimit && (
              <button onClick={() => setAuditLimit((n) => n + 20)}>
                Ещё 20 игровых операций
              </button>
            )}
          </details>
          <details>
            <summary>JSON и черновики RPG</summary>
            <button
              onClick={() =>
                replace(
                  editor,
                  baseRevision,
                  JSON.stringify(editor.settings, null, 2),
                  true,
                )
              }
            >
              Заполнить JSON настроек RPG
            </button>
            <label>
              JSON настроек RPG
              <textarea
                rows={10}
                value={raw}
                onChange={(e) =>
                  replace(editor, baseRevision, e.target.value, true)
                }
              />
            </label>
            <button
              onClick={() =>
                void action(async () => {
                  const valid = await request<Settings>(
                    draftBase + "/validate",
                    "POST",
                    JSON.parse(raw),
                  );
                  replace(
                    { ...editor, settings: valid },
                    baseRevision,
                    raw,
                    true,
                  );
                  setStatus("JSON проверен, настройки ещё не опубликованы.");
                })
              }
            >
              Проверить JSON RPG
            </button>
            <button onClick={() => void action(draft.saveSeparate)}>
              Сохранить отдельный черновик RPG
            </button>
            {draft.items.map((d) => (
              <p key={d.id}>
                <button
                  onClick={() =>
                    void action(async () => {
                      await draft.flush();
                      draft.adopt(d);
                      replace(
                        d.payload.payload as Editor,
                        d.payload.source_revision,
                        d.payload.raw,
                        true,
                      );
                      setStatus(
                        "Черновик RPG восстановлен с исходной ревизией.",
                      );
                    })
                  }
                >
                  Восстановить черновик RPG {d.id.slice(0, 8)}
                </button>
              </p>
            ))}
          </details>
        </fieldset>
      )}
    </details>
  );
}

function Catalogs({
  settings,
  edit,
}: {
  settings: Settings;
  edit: (fn: (s: Settings) => void) => void;
}) {
  const [key, setKey] = useState(""),
    [name, setName] = useState(""),
    [weight, setWeight] = useState(0),
    [decimals, setDecimals] = useState(0),
    [description, setDescription] = useState("");
  const [tableKey, setTableKey] = useState(""),
    [tableName, setTableName] = useState(""),
    [outcomeName, setOutcomeName] = useState(""),
    [text, setText] = useState(""),
    [chance, setChance] = useState(1);
  const [withReward, setWithReward] = useState(false),
    [rewardKind, setRewardKind] = useState<
      "item" | "currency" | "health" | "attribute"
    >("currency"),
    [rewardKey, setRewardKey] = useState(""),
    [rewardAmount, setRewardAmount] = useState(1);
  return (
    <>
      <details>
        <summary>Предметы и валюты</summary>
        <Text label="Ключ каталога RPG" value={key} onChange={setKey} />
        <Text
          label="Название записи каталога"
          value={name}
          onChange={setName}
        />
        <Text
          label="Описание предмета"
          value={description}
          onChange={setDescription}
        />
        <Num
          label="Вес предмета в граммах"
          value={weight}
          onChange={setWeight}
        />
        <button
          disabled={!key.trim() || !name.trim()}
          onClick={() =>
            edit((s) => {
              s.items = {
                ...s.items,
                [key]: { name, weight_grams: weight, description },
              };
            })
          }
        >
          Добавить или обновить предмет
        </button>
        <Num
          label="Десятичных знаков валюты"
          value={decimals}
          onChange={setDecimals}
        />
        <button
          disabled={!key.trim() || !name.trim()}
          onClick={() =>
            edit((s) => {
              s.currencies = { ...s.currencies, [key]: { name, decimals } };
            })
          }
        >
          Добавить или обновить валюту
        </button>
        <p>
          Все счета/цены — целые минимальные единицы: при двух знаках125
          означает1,25.
        </p>
        {Object.entries(settings.items || {}).map(([k, v]) => (
          <p key={k}>
            {v.name} ({k}), {v.weight_grams} г
            <button
              onClick={() => {
                setKey(k);
                setName(v.name);
                setWeight(v.weight_grams || 0);
                setDescription(v.description || "");
              }}
            >
              Редактировать предмет {v.name}
            </button>
            <button
              onClick={() =>
                edit((s) => {
                  delete s.items![k];
                })
              }
            >
              Удалить предмет {v.name}
            </button>
          </p>
        ))}
        {Object.entries(settings.currencies || {}).map(([k, v]) => (
          <p key={k}>
            {v.name} ({k})
            <button
              onClick={() => {
                setKey(k);
                setName(v.name);
                setDecimals(v.decimals || 0);
              }}
            >
              Редактировать валюту {v.name}
            </button>
            <button
              onClick={() =>
                edit((s) => {
                  delete s.currencies![k];
                })
              }
            >
              Удалить валюту {v.name}
            </button>
          </p>
        ))}
      </details>
      <details>
        <summary>Таблицы случайных событий</summary>
        <Text label="Ключ таблицы" value={tableKey} onChange={setTableKey} />
        <Text
          label="Название таблицы"
          value={tableName}
          onChange={setTableName}
        />
        <Text
          label="Название исхода"
          value={outcomeName}
          onChange={setOutcomeName}
        />
        <label>
          Текст события
          <textarea value={text} onChange={(e) => setText(e.target.value)} />
        </label>
        <Num label="Вес исхода" value={chance} onChange={setChance} />
        <label>
          <input
            type="checkbox"
            checked={withReward}
            onChange={(e) => setWithReward(e.target.checked)}
          />
          Добавить последствие исхода
        </label>
        {withReward && (
          <>
            <label>
              Тип последствия
              <select
                value={rewardKind}
                onChange={(e) =>
                  setRewardKind(e.target.value as typeof rewardKind)
                }
              >
                <option value="item">Предмет</option>
                <option value="currency">Валюта</option>
                <option value="health">Здоровье</option>
                <option value="attribute">Характеристика</option>
              </select>
            </label>
            <Text
              label="Ключ последствия"
              value={rewardKey}
              onChange={setRewardKey}
            />
            <Num
              label="Изменение последствия"
              value={rewardAmount}
              onChange={setRewardAmount}
            />
          </>
        )}
        <button
          disabled={
            !tableKey.trim() ||
            !tableName.trim() ||
            !outcomeName.trim() ||
            !text.trim()
          }
          onClick={() =>
            edit((s) => {
              s.tables = {
                ...s.tables,
                [tableKey]: {
                  name: tableName,
                  outcomes: [
                    ...(s.tables?.[tableKey]?.outcomes || []),
                    {
                      name: outcomeName,
                      text,
                      weight: chance,
                      rewards: withReward
                        ? [
                            {
                              kind: rewardKind,
                              key: rewardKey,
                              amount: rewardAmount,
                            },
                          ]
                        : [],
                    },
                  ],
                },
              };
            })
          }
        >
          Добавить исход в таблицу
        </button>
        {Object.entries(settings.tables || {}).map(([k, v]) => (
          <article key={k}>
            <strong>
              {v.name} ({k})
            </strong>
            <button
              onClick={() => {
                setTableKey(k);
                setTableName(v.name);
              }}
            >
              Выбрать таблицу {v.name}
            </button>
            <button
              onClick={() =>
                edit((s) => {
                  delete s.tables![k];
                })
              }
            >
              Удалить таблицу {v.name}
            </button>
            {v.outcomes.map((o, i) => (
              <p key={i}>
                {o.name}: {o.text}, вес {o.weight}
                <button
                  onClick={() =>
                    edit((s) => {
                      s.tables![k].outcomes.splice(i, 1);
                    })
                  }
                >
                  Удалить исход {i + 1} из {v.name}
                </button>
              </p>
            ))}
          </article>
        ))}
      </details>
    </>
  );
}
