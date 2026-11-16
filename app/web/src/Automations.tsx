import { useEffect, useState } from "react";
import { request } from "./api";

type PluginRow = {
  id: string;
  payload: {
    enabled: boolean;
    manifest: { name: string; runtime: string };
  };
};
type Rule = {
  id: string;
  revision: number;
  payload: {
    name: string;
    plugin_id: string;
    enabled: boolean;
    trigger: "manual" | "interval";
    interval_minutes: number | null;
    max_runs: number;
    runs_started: number;
    next_due_at: string | null;
  };
};
type Event = {
  id: string;
  payload: {
    trigger: "manual" | "interval";
    state: string;
    scheduled_at: string;
    error_code: string | null;
  };
  job_state: string | null;
  proposal_id: string | null;
};

export function Automations({ visible }: { visible: boolean }) {
  const [rules, setRules] = useState<Rule[]>([]);
  const [plugins, setPlugins] = useState<PluginRow[]>([]);
  const [pluginId, setPluginId] = useState("");
  const [name, setName] = useState("");
  const [trigger, setTrigger] = useState<"manual" | "interval">("manual");
  const [minutes, setMinutes] = useState(60);
  const [maxRuns, setMaxRuns] = useState(10);
  const [parameters, setParameters] = useState("{}");
  const [confirmed, setConfirmed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [history, setHistory] = useState<Record<string, Event[]>>({});

  async function refresh() {
    const [r, p] = await Promise.all([
      request<{ items: Rule[] }>("/automations"),
      request<{ items: PluginRow[] }>("/plugins"),
    ]);
    setRules(r.items);
    setPlugins(p.items);
  }

  useEffect(() => {
    if (visible) void refresh().catch((error) => setMessage(error.message));
  }, [visible]);

  async function act(work: () => Promise<void>) {
    if (busy) return;
    setBusy(true);
    try {
      await work();
      await refresh();
    } catch (error) {
      setMessage((error as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function create() {
    const input: unknown = JSON.parse(parameters);
    if (input === null || Array.isArray(input) || typeof input !== "object")
      throw Error("Параметры должны быть JSON-объектом.");
    const selected = plugins.find((p) => p.id === pluginId);
    if (!selected || !selected.payload.enabled)
      throw Error("Выберите включённое расширение.");
    if (selected.payload.manifest.runtime === "trusted_python")
      throw Error("Фоновое выполнение Python с полным доступом запрещено.");
    await request("/automations", "POST", {
      name,
      plugin_id: pluginId,
      trigger,
      interval_minutes: trigger === "interval" ? minutes : null,
      parameters: input,
      max_runs: maxRuns,
      confirmed,
    });
    setConfirmed(false);
    setName("");
    setMessage("Правило сохранено. Сформированные планы требуют подтверждения перед изменением данных.");
  }

  async function loadHistory(ruleId: string) {
    const result = await request<{ items: Event[] }>(
      `/automations/${ruleId}/history`,
    );
    setHistory((old) => ({ ...old, [ruleId]: result.items }));
  }

  return (
    <section aria-label="Правила и расписания">
      <h2>Автоматизация и макросы</h2>
      <p>
        Расписание работает только при запущенном приложении. Оно создаёт
        проверяемые планы декларативных и WASM-расширений. Изменения JSON
        применяются после просмотра в разделе «Расширения», а внешние
        программы требуют отдельного подтверждения. Доверенный Python
        автоматически не запускается.
      </p>
      <p role="status">{message}</p>
      <fieldset disabled={busy}>
        <legend>Создать правило</legend>
        <label>
          Название
          <input value={name} maxLength={120} onChange={(e) => setName(e.target.value)} />
        </label>
        <label>
          Расширение
          <select
            aria-label="Расширение для правила"
            value={pluginId}
            onChange={(e) => setPluginId(e.target.value)}
          >
            <option value="">Выберите расширение</option>
            {plugins
              .filter((p) => p.payload.enabled && p.payload.manifest.runtime !== "trusted_python")
              .map((p) => (
                <option key={p.id} value={p.id}>{p.payload.manifest.name}</option>
              ))}
          </select>
        </label>
        <label>
          Тип запуска
          <select
            aria-label="Тип запуска правила"
            value={trigger}
            onChange={(e) => setTrigger(e.target.value as "manual" | "interval")}
          >
            <option value="manual">Ручной запуск макроса</option>
            <option value="interval">По интервалу (пока приложение работает)</option>
          </select>
        </label>
        {trigger === "interval" && (
          <label>
            Интервал, минут (15–10080)
            <input
              type="number"
              min={15}
              max={10080}
              value={minutes}
              onChange={(e) => setMinutes(Number(e.target.value))}
            />
          </label>
        )}
        <label>
          Максимум запусков
          <input
            type="number"
            min={1}
            max={10000}
            value={maxRuns}
            onChange={(e) => setMaxRuns(Number(e.target.value))}
          />
        </label>
        <label>
          Параметры расширения (JSON)
          <textarea
            rows={4}
            aria-label="Параметры автоматизации"
            value={parameters}
            onChange={(e) => setParameters(e.target.value)}
          />
        </label>
        <label>
          <input
            type="checkbox"
            checked={confirmed}
            onChange={(e) => setConfirmed(e.target.checked)}
          />
          Подтверждаю привязку к текущей версии расширения и его разрешениям
        </label>
        <button
          disabled={!name.trim() || !pluginId || !confirmed || busy}
          onClick={() => void act(create)}
        >
          Сохранить правило
        </button>
      </fieldset>

      <h3>Сохранённые правила</h3>
      <button disabled={busy} onClick={() => void act(async () => {})}>
        Обновить список
      </button>
      <ul>
        {rules.map((rule) => (
          <li key={rule.id}>
            <article aria-label={"Правило " + rule.payload.name}>
              <strong>{rule.payload.name}</strong>
              <p>
                {rule.payload.enabled ? "Включено" : "Отключено"} ·
                {rule.payload.trigger === "interval"
                  ? ` раз в ${rule.payload.interval_minutes} мин`
                  : " ручное"} ·
                запусков {rule.payload.runs_started} из {rule.payload.max_runs}
              </p>
              {rule.payload.next_due_at && (
                <p>Следующая проверка: {new Date(rule.payload.next_due_at).toLocaleString()}</p>
              )}
              <button
                disabled={busy || !rule.payload.enabled}
                onClick={() =>
                  void act(async () => {
                    await request(`/automations/${rule.id}/run`, "POST", {
                      confirmed: true,
                    }, { "Idempotency-Key": crypto.randomUUID() });
                    setMessage("Макрос поставлен в очередь. Проверьте план в разделе «Расширения».");
                    await loadHistory(rule.id);
                  })
                }
              >
                Запустить макрос
              </button>
              <button
                disabled={busy || rule.payload.runs_started >= rule.payload.max_runs &&
                  !rule.payload.enabled}
                onClick={() =>
                  void act(async () => {
                    await request(`/automations/${rule.id}`, "PATCH", {
                      expected_revision: rule.revision,
                      enabled: !rule.payload.enabled,
                      confirmed: true,
                    });
                  })
                }
              >
                {rule.payload.enabled ? "Отключить" : "Включить"}
              </button>
              <button
                disabled={busy}
                onClick={() => void act(() => loadHistory(rule.id))}
              >
                История запусков
              </button>
              {history[rule.id] && (
                <ul>
                  {history[rule.id].map((event) => (
                    <li key={event.id}>
                      {new Date(event.payload.scheduled_at).toLocaleString()} ·
                      {event.payload.trigger} · {event.job_state || event.payload.state}
                      {event.payload.error_code ? " · " + event.payload.error_code : ""}
                      {event.proposal_id ? " · план подготовлен для проверки" : ""}
                    </li>
                  ))}
                </ul>
              )}
            </article>
          </li>
        ))}
      </ul>
    </section>
  );
}
