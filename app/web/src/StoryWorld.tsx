import { useEffect, useRef, useState } from "react";
import { request } from "./api";
type Template = {
  id: string;
  payload: {
    name: string;
    world_id?: string;
    starts?: { id: string; name: string }[];
  };
};
type Entity = {
  id: string;
  kind: string;
  name: string;
  location_id: string | null;
  attributes: Record<string, unknown>;
};
type Runtime = {
  format?: number;
  template?: { payload: { name: string } };
  start?: { name: string; text: string };
  entities?: Record<string, Entity>;
  resources?: Record<string, number>;
  flags?: Record<string, unknown>;
  arcs?: Record<string, unknown>;
  applied_events?: Record<string, unknown>;
};
export function StoryWorld({
  storyId,
  branchId,
  revision,
  disabled,
  onChanged,
}: {
  storyId: string;
  branchId: string;
  revision: number;
  disabled: boolean;
  onChanged: () => Promise<void>;
}) {
  const [worlds, setWorlds] = useState<Template[]>([]),
    [scenarios, setScenarios] = useState<Template[]>([]),
    [runtime, setRuntime] = useState<Runtime>({}),
    [time, setTime] = useState("");
  const [worldId, setWorldId] = useState(""),
    [scenarioId, setScenarioId] = useState(""),
    [startId, setStartId] = useState(""),
    [seed, setSeed] = useState(""),
    [minutes, setMinutes] = useState(60),
    [locationId, setLocationId] = useState("");
  const [status, setStatus] = useState(""),
    [busy, setBusy] = useState(false),
    [reason, setReason] = useState("Ручное изменение мира"),
    [op, setOp] = useState("set_flag"),
    [key, setKey] = useState(""),
    [value, setValue] = useState("true"),
    [entity, setEntity] = useState(""),
    [expected, setExpected] = useState(""),
    [receipt, setReceipt] = useState<unknown>(null);
  const loadEpoch = useRef(0),
    identity = useRef({ storyId, branchId, revision });
  identity.current = { storyId, branchId, revision };
  async function load() {
    const epoch = ++loadEpoch.current;
    const [w, s, current] = await Promise.all([
      request<{ items: Template[] }>("/templates/worlds"),
      request<{ items: Template[] }>("/templates/scenarios"),
      request<{
        world: Runtime;
        scene: { time?: string; location_id?: string };
      }>(`/stories/${storyId}/world?branch_id=${branchId}`),
    ]);
    if (epoch !== loadEpoch.current) return;
    setWorlds(w.items);
    setScenarios(s.items);
    setRuntime(current.world);
    setTime(current.scene.time || "");
    setLocationId(current.scene.location_id || "");
  }
  useEffect(() => {
    void load().catch((e) => setStatus((e as Error).message));
    return () => {
      loadEpoch.current++;
    };
  }, [storyId, branchId, revision]);
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
  async function event(operation: string, extra: Record<string, unknown>) {
    const source = { ...identity.current };
    const r = await request<unknown>(
      `/stories/${source.storyId}/world/events`,
      "POST",
      {
        branch_id: source.branchId,
        expected_revision: source.revision,
        event_id: crypto.randomUUID(),
        operation,
        reason,
        confirmed: true,
        ...extra,
      },
    );
    if (
      identity.current.storyId !== source.storyId ||
      identity.current.branchId !== source.branchId
    )
      return;
    setReceipt(r);
    await onChanged();
    await load();
    setStatus("Изменения мира применены и записаны в журнал.");
  }
  const entities = Object.values(runtime.entities || {}),
    starts = scenarios.find((s) => s.id === scenarioId)?.payload.starts || [];
  return (
    <details aria-label="Мир истории">
      <summary>Мир, время и события</summary>
      <p role="status">{status}</p>
      <fieldset disabled={busy || disabled}>
        <button onClick={() => void action(load)}>Обновить мир истории</button>
        {runtime.format !== 1 ? (
          <>
            <p>
              Подключение создаёт независимый снимок. Изменение шаблона не
              меняет прохождение.
            </p>
            <label>
              Шаблон мира
              <select
                value={worldId}
                onChange={(e) => {
                  setWorldId(e.target.value);
                  setScenarioId("");
                  setStartId("");
                }}
              >
                <option value="">Выберите мир</option>
                {worlds.map((w) => (
                  <option key={w.id} value={w.id}>
                    {w.payload.name}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Шаблон сценария
              <select
                value={scenarioId}
                onChange={(e) => {
                  setScenarioId(e.target.value);
                  setStartId("");
                }}
              >
                <option value="">Без сценария</option>
                {scenarios
                  .filter((s) => s.payload.world_id === worldId)
                  .map((s) => (
                    <option key={s.id} value={s.id}>
                      {s.payload.name}
                    </option>
                  ))}
              </select>
            </label>
            <label>
              Выбор начала
              <select
                value={startId}
                onChange={(e) => setStartId(e.target.value)}
              >
                <option value="">Случайное начало</option>
                {starts.map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.name}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Seed случайного начала
              <input
                value={seed}
                onChange={(e) => setSeed(e.target.value)}
                inputMode="numeric"
              />
            </label>
            <button
              disabled={!worldId}
              onClick={() =>
                void action(async () => {
                  await request(`/stories/${storyId}/world/attach`, "POST", {
                    branch_id: branchId,
                    expected_revision: revision,
                    world_id: worldId,
                    scenario_id: scenarioId || null,
                    start_id: startId || null,
                    seed: seed ? Number(seed) : null,
                    confirmed: true,
                  });
                  await onChanged();
                  await load();
                  setStatus("Независимый снимок мира подключён.");
                })
              }
            >
              Подтвердить подключение мира
            </button>
          </>
        ) : (
          <>
            <h3>{runtime.template?.payload.name}</h3>
            {runtime.start && (
              <p>
                Начало «{runtime.start.name}»: {runtime.start.text}
              </p>
            )}
            <p>Игровое время: {time}</p>
            <label>
              Причина изменения
              <input
                value={reason}
                onChange={(e) => setReason(e.target.value)}
              />
            </label>
            <label>
              Пропустить игровых минут
              <input
                type="number"
                min={1}
                max={1000000000}
                value={minutes}
                onChange={(e) => setMinutes(Number(e.target.value))}
              />
            </label>
            <button
              disabled={!reason.trim()}
              onClick={() =>
                void action(() => event("time_advance", { minutes }))
              }
            >
              Подтвердить пропуск времени
            </button>
            <label>
              Локация сцены
              <select
                value={locationId}
                onChange={(e) => setLocationId(e.target.value)}
              >
                <option value="">Выберите локацию</option>
                {entities
                  .filter((e) => ["region", "location"].includes(e.kind))
                  .map((e) => (
                    <option key={e.id} value={e.id}>
                      {e.name}
                    </option>
                  ))}
              </select>
            </label>
            <button
              disabled={!locationId || !reason.trim()}
              onClick={() =>
                void action(() => event("move", { location_id: locationId }))
              }
            >
              Подтвердить перемещение
            </button>
            <h4>Сущности и действия вне сцены</h4>
            <ul>
              {entities.map((e) => (
                <li key={e.id}>
                  <strong>{e.name}</strong>
                  {e.location_id
                    ? " — " +
                      (runtime.entities?.[e.location_id]?.name ||
                        "Локация не найдена")
                    : ""}
                  <details>
                    <summary>Атрибуты</summary>
                    <pre>{JSON.stringify(e.attributes, null, 2)}</pre>
                  </details>
                </li>
              ))}
            </ul>
            <details>
              <summary>Ресурсы, флаги и сюжетные линии</summary>
              <pre>
                {JSON.stringify(
                  {
                    resources: runtime.resources,
                    flags: runtime.flags,
                    arcs: runtime.arcs,
                  },
                  null,
                  2,
                )}
              </pre>
            </details>
            <details>
              <summary>Ручное последствие</summary>
              <label>
                Операция последствия
                <select value={op} onChange={(e) => setOp(e.target.value)}>
                  {[
                    ["set_flag", "Установить флаг"],
                    ["set_clock", "Исправить абсолютное время (минуты)"],
                    ["change_resource", "Изменить ресурс"],
                    ["move_entity", "Переместить сущность"],
                    ["set_attribute", "Атрибут сущности"],
                    ["add_arc", "Открыть сюжетную линию"],
                    ["resolve_arc", "Разрешить сюжетную линию"],
                  ].map(([v, l]) => (
                    <option key={v} value={v}>
                      {l}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                Сущность последствия
                <select
                  value={entity}
                  onChange={(e) => setEntity(e.target.value)}
                >
                  <option value="">Не задана</option>
                  {entities.map((e) => (
                    <option key={e.id} value={e.id}>
                      {e.name}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                Ключ свойства
                <input value={key} onChange={(e) => setKey(e.target.value)} />
              </label>
              <label>
                Новое значение или приращение JSON
                <input
                  value={value}
                  onChange={(e) => setValue(e.target.value)}
                />
              </label>
              <label>
                Ожидаемое предыдущее значение JSON (пусто: без проверки)
                <input
                  value={expected}
                  onChange={(e) => setExpected(e.target.value)}
                />
              </label>
              <button
                disabled={!reason.trim()}
                onClick={() =>
                  void action(() =>
                    event("manual", {
                      effects: [
                        {
                          op,
                          entity_id: entity || null,
                          key,
                          value: JSON.parse(value),
                          check_expected: !!expected,
                          expected: expected ? JSON.parse(expected) : null,
                        },
                      ],
                    }),
                  )
                }
              >
                Подтвердить ручное последствие
              </button>
            </details>
            <details>
              <summary>Журнал событий мира</summary>
              <pre>{JSON.stringify(runtime.applied_events, null, 2)}</pre>
            </details>
            {receipt !== null && (
              <details>
                <summary>Результат последней операции</summary>
                <pre>{JSON.stringify(receipt, null, 2)}</pre>
              </details>
            )}
          </>
        )}
      </fieldset>
    </details>
  );
}
