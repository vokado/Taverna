import { useEffect, useState } from "react";
import { download, request } from "./api";
import { useTemplateDraft } from "./useTemplateDraft";

type Item = { id: string; revision: number; payload: Data };
type Entity = {
  id: string;
  kind: string;
  name: string;
  description: string;
  parent_id?: string | null;
  location_id?: string | null;
  attributes: Record<string, unknown>;
  [key: string]: unknown;
};
type Lore = {
  id: string;
  name: string;
  text: string;
  keys: string[];
  secondary_keys?: string[];
  selective?: boolean;
  enabled: boolean;
  constant: boolean;
  public: boolean;
  knower_ids: string[];
  [key: string]: unknown;
};
type Rule = {
  id: string;
  name: string;
  on: string;
  condition: Record<string, unknown>;
  effects: Array<Record<string, unknown>>;
  [key: string]: unknown;
};
type Start = {
  id: string;
  name: string;
  text: string;
  location_id: string | null;
  minutes: number;
  [key: string]: unknown;
};
type Data = {
  name: string;
  description?: string;
  entities?: Entity[];
  links?: Array<{
    source_id: string;
    target_id: string;
    kind: string;
    description?: string;
  }>;
  lore?: Lore[];
  rules?: Rule[];
  world_id?: string;
  premise?: string;
  goals?: string[];
  starts?: Start[];
  calendar?: {
    name: string;
    months: { name: string; days: number }[];
    hours_per_day: number;
    minutes_per_hour: number;
    epoch_year: number;
  };
  flags?: Record<string, unknown>;
  resources?: Record<string, number>;
  extras?: Record<string, unknown>;
  [key: string]: unknown;
};
const fresh = (kind: string): Data =>
  kind === "worlds"
    ? {
        name: "Новый мир",
        description: "",
        entities: [],
        links: [],
        lore: [],
        rules: [],
        flags: {},
        resources: {},
        extras: {},
        calendar: {
          name: "Местный календарь",
          months: [{ name: "Месяц", days: 30 }],
          hours_per_day: 24,
          minutes_per_hour: 60,
          epoch_year: 1,
        },
      }
    : {
        name: "Новый сценарий",
        world_id: "",
        premise: "",
        goals: [],
        starts: [],
        rules: [],
        extras: {},
      };
export function Worlds() {
  const [kind, setKind] = useState("worlds");
  return (
    <section aria-label="Миры и сценарии">
      <label>
        Тип конструктора
        <select value={kind} onChange={(e) => setKind(e.target.value)}>
          <option value="worlds">Миры</option>
          <option value="scenarios">Сценарии</option>
        </select>
      </label>
      <div hidden={kind !== "worlds"}>
        <TemplateEditor kind="worlds" />
      </div>
      <div hidden={kind !== "scenarios"}>
        <TemplateEditor kind="scenarios" />
      </div>
    </section>
  );
}
function TemplateEditor({ kind }: { kind: string }) {
  const [data, setData] = useState<Data>(() => fresh(kind)),
    [id, setId] = useState(""),
    [revision, setRevision] = useState(0),
    [raw, setRaw] = useState("");
  const [items, setItems] = useState<Item[]>([]),
    [worlds, setWorlds] = useState<Item[]>([]),
    [dirty, setDirty] = useState(false),
    [busy, setBusy] = useState(false),
    [status, setStatus] = useState("");
  const [entityId, setEntityId] = useState(""),
    [loreId, setLoreId] = useState(""),
    [startId, setStartId] = useState("");
  const [file, setFile] = useState(""),
    [preview, setPreview] = useState<Record<string, unknown> | null>(null),
    [isPublic, setPublic] = useState(false),
    [importType, setImportType] = useState("lore");
  const [instruction, setInstruction] = useState(""),
    [modelId, setModelId] = useState(""),
    [jobId, setJobId] = useState(""),
    [proposal, setProposal] = useState<{
      id: string;
      revision: number;
      payload: {
        payload: Data;
        base_revision: number;
        template_id: string | null;
        status: string;
      };
    } | null>(null);
  const [rulePath, setRulePath] = useState("clock.minutes"),
    [ruleValue, setRuleValue] = useState("60"),
    [ruleOp, setRuleOp] = useState("gte"),
    [effectOp, setEffectOp] = useState("set_flag"),
    [effectKey, setEffectKey] = useState("событие"),
    [effectValue, setEffectValue] = useState("true"),
    [effectEntity, setEffectEntity] = useState(""),
    [eventOn, setEventOn] = useState("time_advance");
  const [resourceKey, setResourceKey] = useState(""),
    [resourceAmount, setResourceAmount] = useState(0),
    [attributeKey, setAttributeKey] = useState(""),
    [attributeValue, setAttributeValue] = useState("null"),
    [linkSource, setLinkSource] = useState(""),
    [linkTarget, setLinkTarget] = useState(""),
    [linkKind, setLinkKind] = useState("связь");
  const draft = useTemplateDraft(
    kind,
    { template_id: id || null, source_revision: revision, raw, payload: data },
    dirty,
    busy,
  );
  async function refresh() {
    const [a, b] = await Promise.all([
      request<{ items: Item[] }>(`/templates/${kind}`),
      request<{ items: Item[] }>("/templates/worlds"),
    ]);
    setItems(a.items);
    setWorlds(b.items);
  }
  useEffect(() => {
    void refresh().catch((e) => setStatus((e as Error).message));
  }, []);
  function change(patch: Partial<Data>) {
    setData((old) => ({ ...old, ...patch }));
    setDirty(true);
  }
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
  async function choose(value: string) {
    await draft.flush();
    draft.create();
    setDirty(false);
    setId(value);
    setRaw("");
    const item = value
      ? await request<Item>(`/templates/${kind}/${value}`)
      : null;
    setData(item?.payload || fresh(kind));
    setRevision(item?.revision || 0);
    setEntityId("");
    setLoreId("");
    setStartId("");
  }
  async function save() {
    await draft.flush();
    const saved = await request<Item>(
      `/templates/${kind}` + (id ? "/" + id : ""),
      id ? "PUT" : "POST",
      { expected_revision: revision, payload: data },
    );
    setDirty(false);
    if (raw && raw !== JSON.stringify(data, null, 2)) draft.create();
    else await draft.remove();
    setId(saved.id);
    setRevision(saved.revision);
    setData(saved.payload);
    setRaw("");
    await refresh();
    setStatus(
      "Шаблон сохранён. Существующие истории используют собственные снимки." +
        (raw && raw !== JSON.stringify(data, null, 2)
          ? " Отдельная редакция JSON сохранена в черновике."
          : ""),
    );
  }
  const entities = data.entities || [],
    lore = data.lore || [],
    starts = data.starts || [],
    rules = data.rules || [];
  const entity = entities.find((e) => e.id === entityId),
    entry = lore.find((e) => e.id === loreId),
    start = starts.find((s) => s.id === startId);
  const locations =
    kind === "worlds"
      ? entities.filter((e) => ["region", "location"].includes(e.kind))
      : (
          worlds.find((w) => w.id === data.world_id)?.payload.entities || []
        ).filter((e) => ["region", "location"].includes(e.kind));
  function patchEntity(patch: Partial<Entity>) {
    change({
      entities: entities.map((e) =>
        e.id === entityId ? { ...e, ...patch } : e,
      ),
    });
  }
  function patchLore(patch: Partial<Lore>) {
    change({
      lore: lore.map((e) => (e.id === loreId ? { ...e, ...patch } : e)),
    });
  }
  function patchStart(patch: Partial<Start>) {
    change({
      starts: starts.map((e) => (e.id === startId ? { ...e, ...patch } : e)),
    });
  }
  async function readFile(file: File | undefined) {
    if (!file) return;
    if (file.size > 12 * 1024 * 1024) throw new Error("Файл превышает12МиБ.");
    setFile(await file.text());
    setPreview(null);
  }
  return (
    <article
      aria-label={
        kind === "worlds" ? "Конструктор мира" : "Конструктор сценария"
      }
    >
      <p role="status">{[status, draft.status].filter(Boolean).join(" ")}</p>
      <fieldset disabled={busy}>
        <label>
          Сохранённый шаблон
          <select
            value={id}
            onChange={(e) => void action(() => choose(e.target.value))}
          >
            <option value="">Новый шаблон</option>
            {items.map((i) => (
              <option key={i.id} value={i.id}>
                {i.payload.name}
              </option>
            ))}
          </select>
        </label>
        <button onClick={() => void action(() => choose(""))}>
          Новый шаблон
        </button>
        <button onClick={() => void action(refresh)}>Обновить шаблоны</button>
        <button
          onClick={() =>
            void action(async () => {
              await draft.flush();
              draft.create();
              setId("");
              setRevision(0);
              setData((old) => ({ ...old, name: old.name + " — копия" }));
              setDirty(true);
            })
          }
        >
          Создать копию шаблона
        </button>
        <label>
          Название шаблона
          <input
            value={data.name}
            onChange={(e) => change({ name: e.target.value })}
            maxLength={200}
          />
        </label>
        {kind === "scenarios" ? (
          <>
            <label>
              Мир сценария
              <select
                value={data.world_id || ""}
                onChange={(e) => change({ world_id: e.target.value })}
              >
                <option value="">Выберите мир</option>
                {worlds.map((w) => (
                  <option value={w.id} key={w.id}>
                    {w.payload.name}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Замысел сценария
              <textarea
                value={data.premise || ""}
                onChange={(e) => change({ premise: e.target.value })}
              />
            </label>
            <label>
              Возможные цели, по одной в строке
              <textarea
                value={(data.goals || []).join("\n")}
                onChange={(e) =>
                  change({ goals: e.target.value.split("\n").filter(Boolean) })
                }
              />
            </label>
            <label>
              Начало сценария
              <select
                value={startId}
                onChange={(e) => setStartId(e.target.value)}
              >
                <option value="">Выберите начало</option>
                {starts.map((s) => (
                  <option value={s.id} key={s.id}>
                    {s.name}
                  </option>
                ))}
              </select>
            </label>
            <button
              onClick={() => {
                const s: Start = {
                  id: crypto.randomUUID(),
                  name: "Новое начало",
                  text: "",
                  location_id: null,
                  minutes: 0,
                };
                change({ starts: [...starts, s] });
                setStartId(s.id);
              }}
            >
              Добавить начало
            </button>
            {start && (
              <>
                <label>
                  Название начала
                  <input
                    value={start.name}
                    onChange={(e) => patchStart({ name: e.target.value })}
                  />
                </label>
                <label>
                  Описание начала (общедоступное)
                  <textarea
                    value={start.text}
                    onChange={(e) => patchStart({ text: e.target.value })}
                  />
                </label>
                <label>
                  Начальная локация
                  <select
                    value={start.location_id || ""}
                    onChange={(e) =>
                      patchStart({ location_id: e.target.value || null })
                    }
                  >
                    <option value="">Не задана</option>
                    {locations.map((l) => (
                      <option key={l.id} value={l.id}>
                        {l.name}
                      </option>
                    ))}
                  </select>
                </label>
                <label>
                  Начальные игровые минуты
                  <input
                    type="number"
                    min={0}
                    value={start.minutes}
                    onChange={(e) =>
                      patchStart({ minutes: Number(e.target.value) })
                    }
                  />
                </label>
                <button
                  onClick={() => {
                    change({ starts: starts.filter((s) => s.id !== startId) });
                    setStartId("");
                  }}
                >
                  Удалить начало
                </button>
              </>
            )}
          </>
        ) : (
          <>
            <label>
              Описание мира
              <textarea
                value={data.description || ""}
                onChange={(e) => change({ description: e.target.value })}
              />
            </label>
            <details open>
              <summary>География, фракции, история и культура</summary>
              <label>
                Сущность мира
                <select
                  value={entityId}
                  onChange={(e) => setEntityId(e.target.value)}
                >
                  <option value="">Выберите сущность</option>
                  {entities.map((e) => (
                    <option value={e.id} key={e.id}>
                      {e.name} ({e.kind})
                    </option>
                  ))}
                </select>
              </label>
              <button
                onClick={() => {
                  const e: Entity = {
                    id: crypto.randomUUID(),
                    name: "Новое место",
                    kind: "location",
                    description: "",
                    attributes: {},
                  };
                  change({ entities: [...entities, e] });
                  setEntityId(e.id);
                }}
              >
                Добавить сущность
              </button>
              {entity && (
                <>
                  <label>
                    Название сущности
                    <input
                      value={entity.name}
                      onChange={(e) => patchEntity({ name: e.target.value })}
                    />
                  </label>
                  <label>
                    Тип сущности
                    <select
                      value={entity.kind}
                      onChange={(e) => patchEntity({ kind: e.target.value })}
                    >
                      {[
                        ["region", "Регион"],
                        ["location", "Место"],
                        ["faction", "Фракция"],
                        ["person", "Персона мира"],
                        ["resource", "Ресурс"],
                        ["culture", "Культура"],
                        ["history", "История"],
                      ].map(([v, l]) => (
                        <option key={v} value={v}>
                          {l}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label>
                    Описание сущности
                    <textarea
                      value={entity.description}
                      onChange={(e) =>
                        patchEntity({ description: e.target.value })
                      }
                    />
                  </label>
                  <label>
                    Родительская сущность
                    <select
                      value={entity.parent_id || ""}
                      onChange={(e) =>
                        patchEntity({ parent_id: e.target.value || null })
                      }
                    >
                      <option value="">Нет</option>
                      {entities
                        .filter((e) => e.id !== entityId)
                        .map((e) => (
                          <option value={e.id} key={e.id}>
                            {e.name}
                          </option>
                        ))}
                    </select>
                  </label>
                  <label>
                    Локация сущности
                    <select
                      value={entity.location_id || ""}
                      onChange={(e) =>
                        patchEntity({ location_id: e.target.value || null })
                      }
                    >
                      <option value="">Не задана</option>
                      {locations.map((l) => (
                        <option value={l.id} key={l.id}>
                          {l.name}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label>
                    Ключ атрибута сущности
                    <input
                      value={attributeKey}
                      onChange={(e) => setAttributeKey(e.target.value)}
                    />
                  </label>
                  <label>
                    Значение атрибута JSON
                    <input
                      value={attributeValue}
                      onChange={(e) => setAttributeValue(e.target.value)}
                    />
                  </label>
                  <button
                    disabled={!attributeKey}
                    onClick={() =>
                      void action(async () =>
                        patchEntity({
                          attributes: {
                            ...entity.attributes,
                            [attributeKey]: JSON.parse(attributeValue),
                          },
                        }),
                      )
                    }
                  >
                    Установить атрибут
                  </button>
                  <pre>{JSON.stringify(entity.attributes, null, 2)}</pre>
                  <button
                    onClick={() => {
                      change({
                        entities: entities.filter((e) => e.id !== entityId),
                      });
                      setEntityId("");
                    }}
                  >
                    Удалить сущность
                  </button>
                </>
              )}
            </details>
            <details>
              <summary>Связи сущностей и ресурсы</summary>
              <label>
                Источник связи
                <select
                  value={linkSource}
                  onChange={(e) => setLinkSource(e.target.value)}
                >
                  <option value="">Выберите сущность</option>
                  {entities.map((e) => (
                    <option value={e.id} key={e.id}>
                      {e.name}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                Цель связи
                <select
                  value={linkTarget}
                  onChange={(e) => setLinkTarget(e.target.value)}
                >
                  <option value="">Выберите сущность</option>
                  {entities.map((e) => (
                    <option value={e.id} key={e.id}>
                      {e.name}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                Тип связи
                <input
                  value={linkKind}
                  onChange={(e) => setLinkKind(e.target.value)}
                />
              </label>
              <button
                disabled={!linkSource || !linkTarget || !linkKind}
                onClick={() =>
                  change({
                    links: [
                      ...(data.links || []),
                      {
                        source_id: linkSource,
                        target_id: linkTarget,
                        kind: linkKind,
                      },
                    ],
                  })
                }
              >
                Добавить связь
              </button>
              {(data.links || []).map((link, i) => (
                <div key={i}>
                  {entities.find((e) => e.id === link.source_id)?.name} —{" "}
                  {link.kind} →{" "}
                  {entities.find((e) => e.id === link.target_id)?.name}
                  <button
                    onClick={() =>
                      change({ links: data.links!.filter((_, j) => j !== i) })
                    }
                  >
                    Удалить связь
                  </button>
                </div>
              ))}
              <label>
                Ключ ресурса
                <input
                  value={resourceKey}
                  onChange={(e) => setResourceKey(e.target.value)}
                />
              </label>
              <label>
                Начальное количество ресурса
                <input
                  type="number"
                  value={resourceAmount}
                  onChange={(e) => setResourceAmount(Number(e.target.value))}
                />
              </label>
              <button
                disabled={!resourceKey}
                onClick={() =>
                  change({
                    resources: {
                      ...data.resources,
                      [resourceKey]: resourceAmount,
                    },
                  })
                }
              >
                Установить ресурс
              </button>
              {Object.entries(data.resources || {}).map(([key, count]) => (
                <div key={key}>
                  {key}: {count}
                  <button
                    onClick={() => {
                      const next = { ...data.resources };
                      delete next[key];
                      change({ resources: next });
                    }}
                  >
                    Удалить ресурс
                  </button>
                </div>
              ))}
            </details>
            <details>
              <summary>Календарь</summary>
              {data.calendar && (
                <>
                  <label>
                    Название календаря
                    <input
                      value={data.calendar.name}
                      onChange={(e) =>
                        change({
                          calendar: { ...data.calendar!, name: e.target.value },
                        })
                      }
                    />
                  </label>
                  {data.calendar.months.map((m, i) => (
                    <div key={i}>
                      <label>
                        Название месяца
                        <input
                          value={m.name}
                          onChange={(e) =>
                            change({
                              calendar: {
                                ...data.calendar!,
                                months: data.calendar!.months.map((n, j) =>
                                  j === i ? { ...n, name: e.target.value } : n,
                                ),
                              },
                            })
                          }
                        />
                      </label>
                      <label>
                        Дней в месяце
                        <input
                          type="number"
                          min={1}
                          value={m.days}
                          onChange={(e) =>
                            change({
                              calendar: {
                                ...data.calendar!,
                                months: data.calendar!.months.map((n, j) =>
                                  j === i
                                    ? { ...n, days: Number(e.target.value) }
                                    : n,
                                ),
                              },
                            })
                          }
                        />
                      </label>
                      <button
                        onClick={() =>
                          change({
                            calendar: {
                              ...data.calendar!,
                              months: data.calendar!.months.filter(
                                (_, j) => j !== i,
                              ),
                            },
                          })
                        }
                      >
                        Удалить месяц
                      </button>
                    </div>
                  ))}
                  <button
                    onClick={() =>
                      change({
                        calendar: {
                          ...data.calendar!,
                          months: [
                            ...data.calendar!.months,
                            { name: "Новый месяц", days: 30 },
                          ],
                        },
                      })
                    }
                  >
                    Добавить месяц
                  </button>
                  {(
                    ["hours_per_day", "minutes_per_hour", "epoch_year"] as const
                  ).map((key, i) => (
                    <label key={key}>
                      {["Часов в дне", "Минут в часе", "Год начала"][i]}
                      <input
                        type="number"
                        value={data.calendar![key]}
                        onChange={(e) =>
                          change({
                            calendar: {
                              ...data.calendar!,
                              [key]: Number(e.target.value),
                            },
                          })
                        }
                      />
                    </label>
                  ))}
                </>
              )}
            </details>
            <details>
              <summary>Лорбук и доступ к знаниям</summary>
              <p>
                Объективный факт не становится знанием всех NPC. Общедоступность
                назначается явно.
              </p>
              <label>
                Запись лорбука
                <select
                  value={loreId}
                  onChange={(e) => setLoreId(e.target.value)}
                >
                  <option value="">Выберите запись</option>
                  {lore.map((e) => (
                    <option value={e.id} key={e.id}>
                      {e.name}
                    </option>
                  ))}
                </select>
              </label>
              <button
                onClick={() => {
                  const e: Lore = {
                    id: crypto.randomUUID(),
                    name: "Новая запись",
                    text: "",
                    keys: [],
                    enabled: true,
                    constant: false,
                    public: false,
                    knower_ids: [],
                  };
                  change({ lore: [...lore, e] });
                  setLoreId(e.id);
                }}
              >
                Добавить лор
              </button>
              {entry && (
                <>
                  <label>
                    Название лора
                    <input
                      value={entry.name}
                      onChange={(e) => patchLore({ name: e.target.value })}
                    />
                  </label>
                  <label>
                    Текст лора
                    <textarea
                      value={entry.text}
                      onChange={(e) => patchLore({ text: e.target.value })}
                    />
                  </label>
                  <label>
                    Ключи, по одному в строке
                    <textarea
                      value={entry.keys.join("\n")}
                      onChange={(e) =>
                        patchLore({
                          keys: e.target.value.split("\n").filter(Boolean),
                        })
                      }
                    />
                  </label>
                  {(["enabled", "constant", "public"] as const).map((k, i) => (
                    <label key={k}>
                      <input
                        type="checkbox"
                        checked={entry[k]}
                        onChange={(e) => patchLore({ [k]: e.target.checked })}
                      />
                      {
                        [
                          "Включена",
                          "Всегда активна для знающих",
                          "Явно общедоступное знание",
                        ][i]
                      }
                    </label>
                  ))}
                  <label>
                    UUID знающих NPC, по одному в строке
                    <textarea
                      value={entry.knower_ids.join("\n")}
                      onChange={(e) =>
                        patchLore({
                          knower_ids: e.target.value
                            .split("\n")
                            .filter(Boolean),
                        })
                      }
                    />
                  </label>
                  <button
                    onClick={() => {
                      change({ lore: lore.filter((l) => l.id !== loreId) });
                      setLoreId("");
                    }}
                  >
                    Удалить лор
                  </button>
                </>
              )}
            </details>
          </>
        )}
        <details>
          <summary>Правила событий</summary>
          <p>
            Последствия выполняются один раз на событие. По умолчанию правило
            однократное; сложные условия редактируются в проверяемом JSON.
          </p>
          <label>
            Событие-триггер
            <select
              value={eventOn}
              onChange={(e) => setEventOn(e.target.value)}
            >
              {[
                ["time_advance", "Пропуск времени"],
                ["move", "Перемещение"],
                ["message", "Реплика"],
                ["manual", "Ручное событие"],
                ["any", "Любое событие"],
              ].map(([v, l]) => (
                <option key={v} value={v}>
                  {l}
                </option>
              ))}
            </select>
          </label>
          <label>
            Путь условия
            <input
              value={rulePath}
              onChange={(e) => setRulePath(e.target.value)}
            />
          </label>
          <label>
            Сравнение
            <select value={ruleOp} onChange={(e) => setRuleOp(e.target.value)}>
              {["eq", "ne", "gt", "gte", "lt", "lte", "exists"].map((op) => (
                <option key={op}>{op}</option>
              ))}
            </select>
          </label>
          <label>
            Значение условия JSON
            <input
              value={ruleValue}
              onChange={(e) => setRuleValue(e.target.value)}
            />
          </label>
          <label>
            Тип последствия
            <select
              value={effectOp}
              onChange={(e) => setEffectOp(e.target.value)}
            >
              {[
                "set_flag",
                "change_resource",
                "move_entity",
                "set_attribute",
                "add_arc",
                "resolve_arc",
                "set_clock",
              ].map((op) => (
                <option key={op}>{op}</option>
              ))}
            </select>
          </label>
          <label>
            Ключ последствия
            <input
              value={effectKey}
              onChange={(e) => setEffectKey(e.target.value)}
            />
          </label>
          <label>
            UUID сущности последствия
            <input
              value={effectEntity}
              onChange={(e) => setEffectEntity(e.target.value)}
            />
          </label>
          <label>
            Значение последствия JSON
            <input
              value={effectValue}
              onChange={(e) => setEffectValue(e.target.value)}
            />
          </label>
          <button
            onClick={() =>
              void action(async () => {
                const condition = {
                  op: ruleOp,
                  path: rulePath,
                  ...(ruleOp === "exists"
                    ? {}
                    : { value: JSON.parse(ruleValue) }),
                };
                const effect = {
                  op: effectOp,
                  key: effectKey,
                  entity_id: effectEntity || null,
                  value: JSON.parse(effectValue),
                };
                const payload = {
                  ...data,
                  rules: [
                    ...rules,
                    {
                      id: crypto.randomUUID(),
                      name: "Правило " + (rules.length + 1),
                      on: eventOn,
                      condition,
                      effects: [effect],
                    },
                  ],
                };
                const valid = await request<{ payload: Data }>(
                  `/templates/${kind}/validate`,
                  "POST",
                  { expected_revision: revision, payload },
                );
                change(valid.payload);
              })
            }
          >
            Добавить проверенное правило
          </button>
          {rules.map((r) => (
            <div key={r.id}>
              <strong>{r.name}</strong>
              <details>
                <summary>Условие и последствия</summary>
                <pre>{JSON.stringify(r, null, 2)}</pre>
              </details>
              <button
                onClick={() =>
                  change({ rules: rules.filter((x) => x.id !== r.id) })
                }
              >
                Удалить правило
              </button>
            </div>
          ))}
        </details>
        <details>
          <summary>JSON полного шаблона</summary>
          <button
            onClick={() => {
              setRaw(JSON.stringify(data, null, 2));
              setDirty(true);
            }}
          >
            Заполнить JSON из формы
          </button>
          <label>
            JSON шаблона
            <textarea
              value={raw}
              onChange={(e) => {
                setRaw(e.target.value);
                setDirty(true);
              }}
              rows={14}
            />
          </label>
          <button
            onClick={() =>
              void action(async () => {
                const r = await request<{ payload: Data }>(
                  `/templates/${kind}/validate`,
                  "POST",
                  { expected_revision: revision, payload: JSON.parse(raw) },
                );
                change(r.payload);
                setStatus(
                  "JSON проверен и применён к форме. Сохраните шаблон.",
                );
              })
            }
          >
            Проверить и применить JSON
          </button>
        </details>
        <button onClick={() => void action(save)} disabled={!data.name.trim()}>
          Сохранить шаблон
        </button>
        <details>
          <summary>Черновики шаблонов</summary>
          {draft.items.map((d) => (
            <div key={d.id}>
              <span>
                {String(d.payload.payload.name || "Незавершённый шаблон")}
              </span>
              <button
                onClick={() =>
                  void action(async () => {
                    await draft.flush();
                    draft.adopt(d);
                    setId(d.payload.template_id || "");
                    setRevision(d.payload.source_revision);
                    setData(d.payload.payload as Data);
                    setRaw(d.payload.raw);
                    setDirty(true);
                  })
                }
              >
                Восстановить черновик
              </button>
            </div>
          ))}
        </details>
        {kind === "worlds" && (
          <details>
            <summary>Обмен мирами и импорт лорбука</summary>
            <button
              disabled={!id}
              onClick={() =>
                void action(() =>
                  download(`/templates/worlds/${id}/export`, {}),
                )
              }
            >
              Экспорт пакета мира
            </button>
            <label>
              Тип импортируемого файла
              <select
                value={importType}
                onChange={(e) => {
                  setImportType(e.target.value);
                  setPreview(null);
                }}
              >
                <option value="lore">Лорбук SillyTavern</option>
                <option value="package">Пакет мира Таверны</option>
              </select>
            </label>
            <label>
              Файл импорта
              <input
                type="file"
                accept=".json"
                onChange={(e) =>
                  void action(() => readFile(e.target.files?.[0]))
                }
              />
            </label>
            <label>
              <input
                type="checkbox"
                checked={isPublic}
                onChange={(e) => {
                  setPublic(e.target.checked);
                  setPreview(null);
                }}
              />
              Импортируемый лор явно общедоступен
            </label>
            <button
              disabled={!file}
              onClick={() =>
                void action(async () =>
                  setPreview(
                    await request(
                      importType === "lore"
                        ? "/lorebooks/preview"
                        : "/world-packages/preview",
                      "POST",
                      {
                        text: file,
                        ...(importType === "lore" ? { public: isPublic } : {}),
                      },
                    ),
                  ),
                )
              }
            >
              Предпросмотр импорта мира
            </button>
            {preview && (
              <>
                <pre>{JSON.stringify(preview, null, 2)}</pre>
                <button
                  disabled={importType === "lore" && !id}
                  onClick={() =>
                    void action(async () => {
                      const r = await request<{ world?: Item }>(
                        importType === "lore"
                          ? `/templates/worlds/${id}/lore-import`
                          : "/world-packages/import",
                        "POST",
                        {
                          text: file,
                          confirmed: true,
                          ...(importType === "lore"
                            ? { public: isPublic, expected_revision: revision }
                            : {}),
                        },
                      );
                      if (r.world) {
                        setData(r.world.payload);
                        setRevision(r.world.revision);
                      }
                      setPreview(null);
                      await refresh();
                      setStatus("Импорт подтверждён.");
                    })
                  }
                >
                  Подтвердить импорт мира
                </button>
              </>
            )}
          </details>
        )}
        <details>
          <summary>ИИ-мастер мира и сценария</summary>
          <label>
            Задание помощнику
            <textarea
              value={instruction}
              onChange={(e) => setInstruction(e.target.value)}
            />
          </label>
          <label>
            ID уже загруженной модели
            <input
              value={modelId}
              onChange={(e) => setModelId(e.target.value)}
              placeholder="Пусто: профиль служебной роли «Мир»"
            />
          </label>
          <button
            disabled={!instruction.trim()}
            onClick={() =>
              void action(async () => {
                await draft.flush();
                const r = await request<{ generation_id: string }>(
                  "/world-assistant",
                  "POST",
                  {
                    kind,
                    instruction,
                    template_id: id || null,
                    expected_revision: revision,
                    world_id:
                      kind === "scenarios" ? data.world_id || null : null,
                    model_id: modelId || null,
                  },
                  { "Idempotency-Key": crypto.randomUUID() },
                );
                setJobId(r.generation_id);
                setStatus("Задание поставлено в общую очередь.");
              })
            }
          >
            Запустить помощника
          </button>
          {jobId && (
            <>
              <button
                onClick={() =>
                  void action(async () => {
                    const r = await request<{
                      state: string;
                      result?: { proposal?: typeof proposal };
                      error?: { message_ru: string };
                    }>(`/generations/${jobId}`);
                    if (r.result?.proposal) setProposal(r.result.proposal);
                    setStatus(r.error?.message_ru || "Задача: " + r.state);
                  })
                }
              >
                Проверить результат помощника
              </button>
              <button
                onClick={() =>
                  void action(async () => {
                    await request(`/generations/${jobId}/cancel`, "POST");
                    setStatus("Запрошена отмена.");
                  })
                }
              >
                Отменить помощника
              </button>
            </>
          )}
          {proposal && (
            <>
              <pre>{JSON.stringify(proposal.payload.payload, null, 2)}</pre>
              <button
                onClick={() =>
                  void action(async () => {
                    await request(
                      `/world-proposals/${proposal.id}/review`,
                      "POST",
                      {
                        expected_revision: proposal.payload.base_revision,
                        proposal_revision: proposal.revision,
                        accept: true,
                        confirmed: true,
                      },
                    );
                    setProposal(null);
                    await refresh();
                    setStatus("Предложение подтверждено и сохранено.");
                  })
                }
              >
                Подтвердить предложение мира
              </button>
              <button
                onClick={() =>
                  void action(async () => {
                    await request(
                      `/world-proposals/${proposal.id}/review`,
                      "POST",
                      {
                        expected_revision: proposal.payload.base_revision,
                        proposal_revision: proposal.revision,
                        accept: false,
                        confirmed: true,
                      },
                    );
                    setProposal(null);
                  })
                }
              >
                Отклонить предложение мира
              </button>
              <button
                onClick={() => {
                  change(proposal.payload.payload);
                  setStatus(
                    "Предложение помещено в форму для редактирования. Сохраните отдельным действием.",
                  );
                }}
              >
                Редактировать предложенный шаблон
              </button>
            </>
          )}
        </details>
      </fieldset>
    </article>
  );
}
