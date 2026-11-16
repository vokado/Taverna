import { useEffect, useRef, useState } from "react";
import { request } from "./api";

type Field = {
  key: string;
  label: string;
  type: "text" | "number" | "checkbox" | "select";
  required: boolean;
  options: string[];
};
type Row = {
  id: string;
  revision: number;
  payload: {
    manifest: { name: string; runtime: string; fields: Field[] };
    permission_epoch: number;
    package_sha256: string;
    enabled: boolean;
  };
};
type Read = {
  kind: "own" | "note" | "book";
  target: string;
  expected_revision: number;
};
type Source = {
  id: string;
  revision: number;
  payload: { title: string; kind?: string };
};
type Preview = {
  id: string;
  revision: number;
  payload: {
    status: string;
    plan: { message: string; actions: unknown[] };
    metadata: { runtime: string };
    identity: { package_sha256: string };
  };
};
type Job = {
  id: string;
  payload: {
    state: string;
    error?: { message_ru: string };
    result?: { proposal_id: string };
  };
};

export function PluginRunner({
  row,
  visible,
  catalogRevision,
}: {
  row: Row;
  visible: boolean;
  catalogRevision: number;
}) {
  const [parameters, setParameters] = useState<
      Record<string, string | number | boolean>
    >({}),
    [reads, setReads] = useState<Read[]>([]),
    [kind, setKind] = useState<Read["kind"]>("own"),
    [target, setTarget] = useState(""),
    [revision, setRevision] = useState(0),
    [sources, setSources] = useState<Source[]>([]),
    [query, setQuery] = useState(""),
    [offset, setOffset] = useState(0),
    [total, setTotal] = useState(0),
    [status, setStatus] = useState(""),
    [busy, setBusy] = useState(false),
    [ack, setAck] = useState(false),
    [job, setJob] = useState<Job | null>(null),
    [previews, setPreviews] = useState<Preview[]>([]),
    [confirmed, setConfirmed] = useState<Record<string, boolean>>({});
  const lock = useRef(false),
    retry = useRef<{ fingerprint: string; key: string } | null>(null);
  const identity = {
    catalog_revision: catalogRevision,
    plugin_revision: row.revision,
    permission_epoch: row.payload.permission_epoch,
    package_sha256: row.payload.package_sha256,
  };
  async function act(operation: () => Promise<void>) {
    if (lock.current) return;
    lock.current = true;
    setBusy(true);
    try {
      await operation();
    } catch (e) {
      setStatus((e as Error).message);
    } finally {
      lock.current = false;
      setBusy(false);
    }
  }
  async function refresh() {
    const r = await request<{ items: Preview[]; errors: string[] }>(
      `/plugins/${row.id}/previews`,
    );
    setPreviews(r.items.reverse());
    if (r.errors.length)
      setStatus("Некоторые планы повреждены; проверьте хранилище.");
  }
  useEffect(() => {
    if (visible) void refresh().catch((e) => setStatus(e.message));
  }, [visible, row.id]);
  useEffect(() => {
    if (!visible || !job || !["queued", "running"].includes(job.payload.state))
      return;
    let stopped = false,
      timer: ReturnType<typeof setTimeout>;
    async function poll() {
      try {
        const response = await request<
          Job["payload"] & { generation_id: string }
        >("/generations/" + job!.id);
        const next: Job = { id: response.generation_id, payload: response };
        if (stopped) return;
        setJob(next);
        if (!["queued", "running"].includes(next.payload.state)) {
          setStatus(
            next.payload.error?.message_ru ||
              (next.payload.state === "completed"
                ? "План подготовлен. Проверьте и подтвердите каждое изменение."
                : "Вычисление остановлено; изменения JSON не применены."),
          );
          await refresh();
          return;
        }
      } catch (e) {
        if (!stopped) setStatus((e as Error).message);
      }
      if (!stopped) timer = setTimeout(poll, 500);
    }
    timer = setTimeout(poll, 100);
    return () => {
      stopped = true;
      clearTimeout(timer);
    };
  }, [visible, job?.id, job?.payload.state]);
  const active = !!job && ["queued", "running"].includes(job.payload.state);
  async function sourcePage(nextOffset: number) {
    const r =
      kind === "note"
        ? await request<{ items: Source[] }>(
            "/tools?query=" + encodeURIComponent(query),
          )
        : await request<{ items: Source[]; total: number }>(
            `/manuscripts?query=${encodeURIComponent(query)}&offset=${nextOffset}&limit=50`,
          );
    const found =
      kind === "note"
        ? r.items.filter((s) =>
            ["note", "bookmark", "dictionary"].includes(s.payload.kind || ""),
          )
        : r.items;
    setSources(found);
    setTotal(
      "total" in r && typeof r.total === "number" ? r.total : found.length,
    );
    setOffset(nextOffset);
    setTarget("");
  }
  return (
    <section aria-label="Запуск выбранного расширения">
      <h3>Запуск и проверка плана</h3>
      <p>
        Данные передаются только из списка ниже и по отдельным правам. План не
        меняет JSON до подтверждения. Python с полным доступом способен
        обращаться к ОС непосредственно и выполнять действия уже во время
        вычисления.
      </p>
      <p role="status">{status}</p>
      <fieldset disabled={busy || active || !row.payload.enabled}>
        <legend>Параметры и выбранные источники</legend>
        {row.payload.manifest.fields.map((f) => (
          <label key={f.key}>
            {f.label}
            {f.required ? " (обязательно)" : ""}
            {f.type === "select" ? (
              <select
                aria-label={f.label}
                value={String(parameters[f.key] ?? "")}
                onChange={(e) =>
                  setParameters({ ...parameters, [f.key]: e.target.value })
                }
              >
                <option value="">Выберите</option>
                {f.options.map((v) => (
                  <option key={v}>{v}</option>
                ))}
              </select>
            ) : (
              <input
                aria-label={f.label}
                type={
                  f.type === "checkbox"
                    ? "checkbox"
                    : f.type === "number"
                      ? "number"
                      : "text"
                }
                {...(f.type === "checkbox"
                  ? { checked: parameters[f.key] === true }
                  : { value: String(parameters[f.key] ?? "") })}
                onChange={(e) => {
                  const next = { ...parameters };
                  if (f.type === "number" && e.target.value === "")
                    delete next[f.key];
                  else
                    next[f.key] =
                      f.type === "checkbox"
                        ? e.target.checked
                        : f.type === "number"
                          ? Number(e.target.value)
                          : e.target.value;
                  setParameters(next);
                }}
              />
            )}
          </label>
        ))}
        <label>
          Тип источника
          <select
            value={kind}
            onChange={(e) => {
              setKind(e.target.value as Read["kind"]);
              setTarget("");
              setSources([]);
              setOffset(0);
              setTotal(0);
            }}
          >
            <option value="own">Собственная запись расширения</option>
            <option value="note">Заметка, закладка или словарь</option>
            <option value="book">Рукопись</option>
          </select>
        </label>
        {kind === "own" ? (
          <>
            <label>
              Ключ собственной записи
              <input
                value={target}
                onChange={(e) => setTarget(e.target.value)}
              />
            </label>
            <button
              disabled={!target}
              onClick={() =>
                void act(async () => {
                  const r = await request<{
                    revision: number;
                    value: unknown;
                    deleted: boolean;
                  }>(`/plugins/${row.id}/data/${encodeURIComponent(target)}`);
                  setRevision(r.revision);
                  setStatus(
                    `Редакция ${r.revision}${r.deleted ? " · удалено" : ""}: ${JSON.stringify(r.value)}`,
                  );
                })
              }
            >
              Проверить редакцию собственной записи
            </button>
            <label>
              Ожидаемая редакция источника
              <input
                type="number"
                min={0}
                value={revision}
                onChange={(e) => setRevision(Number(e.target.value))}
              />
            </label>
          </>
        ) : (
          <>
            <label>
              Поиск источника
              <input value={query} onChange={(e) => setQuery(e.target.value)} />
            </label>
            <button onClick={() => void act(() => sourcePage(0))}>
              Найти источники
            </button>
            <select
              aria-label="Выбранный источник"
              value={target}
              onChange={(e) => {
                setTarget(e.target.value);
                setRevision(
                  sources.find((s) => s.id === e.target.value)?.revision || 0,
                );
              }}
            >
              <option value="">Выберите документ</option>
              {sources.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.payload.title} · редакция {s.revision}
                </option>
              ))}
            </select>
            {kind === "book" && (
              <>
                <button
                  disabled={offset === 0}
                  onClick={() =>
                    void act(() => sourcePage(Math.max(0, offset - 50)))
                  }
                >
                  Предыдущие источники
                </button>
                <button
                  disabled={offset + 50 >= total}
                  onClick={() => void act(() => sourcePage(offset + 50))}
                >
                  Следующие источники
                </button>
              </>
            )}
          </>
        )}
        <button
          disabled={!target || reads.length >= 32}
          onClick={() => {
            if (reads.some((r) => r.kind === kind && r.target === target)) {
              setStatus("Источник уже выбран.");
              return;
            }
            setReads([...reads, { kind, target, expected_revision: revision }]);
            setTarget("");
          }}
        >
          Добавить источник к запуску
        </button>
        <ul>
          {reads.map((r, i) => (
            <li key={r.kind + ":" + r.target}>
              {r.kind} · {r.target} · редакция {r.expected_revision}
              <button
                onClick={() =>
                  setReads(reads.filter((_, index) => index !== i))
                }
              >
                Убрать источник {i + 1}
              </button>
            </li>
          ))}
        </ul>
        {row.payload.manifest.runtime === "trusted_python" && (
          <label>
            <input
              type="checkbox"
              checked={ack}
              onChange={(e) => setAck(e.target.checked)}
            />
            Подтверждаю этот запуск Python с полным доступом к файлам, сети и
            командам
          </label>
        )}
        <button
          disabled={row.payload.manifest.runtime === "trusted_python" && !ack}
          onClick={() =>
            void act(async () => {
              const body = {
                ...identity,
                parameters,
                reads,
                confirmed_full_access_run: ack,
              };
              const fingerprint = JSON.stringify(body);
              if (retry.current?.fingerprint !== fingerprint)
                retry.current = { fingerprint, key: crypto.randomUUID() };
              const next = await request<Job>(
                `/plugins/${row.id}/run`,
                "POST",
                body,
                { "Idempotency-Key": retry.current.key },
              );
              setJob(next);
              retry.current = null;
              setAck(false);
              setStatus(
                "Запуск принят общей очередью. Изменения JSON ещё не применены.",
              );
            })
          }
        >
          Вычислить план расширения
        </button>
      </fieldset>
      {job && (
        <p>
          Задача {job.id}: {job.payload.state}
        </p>
      )}
      <button
        disabled={!active || busy}
        onClick={() =>
          void act(async () => {
            await request(`/generations/${job!.id}/cancel`, "POST");
            setStatus(
              "Отмена запрошена. Дождитесь подтверждённого состояния задачи.",
            );
          })
        }
      >
        Остановить вычисление расширения
      </button>
      <button disabled={busy} onClick={() => void act(refresh)}>
        Обновить планы расширения
      </button>
      {previews.map((p) => (
        <article key={p.id} aria-label={"План расширения " + p.id}>
          <h4>{p.payload.plan.message || "План действий"}</h4>
          <p>
            {p.payload.status}; код SHA256 {p.payload.identity.package_sha256}
          </p>
          <pre>{JSON.stringify(p.payload.plan.actions, null, 2)}</pre>
          {p.payload.status === "pending" && (
            <>
              <label>
                <input
                  type="checkbox"
                  checked={confirmed[p.id] || false}
                  onChange={(e) =>
                    setConfirmed({ ...confirmed, [p.id]: e.target.checked })
                  }
                />
                Подтверждаю перечисленные изменения JSON этого плана
              </label>
              <button
                disabled={busy || !confirmed[p.id]}
                onClick={() =>
                  void act(async () => {
                    await request(`/plugins/previews/${p.id}/accept`, "POST", {
                      expected_revision: p.revision,
                      confirmed: true,
                    });
                    setConfirmed({ ...confirmed, [p.id]: false });
                    await refresh();
                    setStatus(
                      "План применён атомарно; запись результата сохранена.",
                    );
                  })
                }
              >
                Применить план расширения
              </button>
              <button
                disabled={busy}
                onClick={() =>
                  void act(async () => {
                    await request(`/plugins/previews/${p.id}/reject`, "POST", {
                      expected_revision: p.revision,
                      confirmed: true,
                    });
                    await refresh();
                    setStatus("План отклонён. Изменения JSON не применены.");
                  })
                }
              >
                Отклонить план расширения
              </button>
            </>
          )}
        </article>
      ))}
    </section>
  );
}
