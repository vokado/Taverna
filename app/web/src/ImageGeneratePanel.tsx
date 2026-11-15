import { useEffect, useRef, useState } from "react";
import type { components } from "./api-types";
import { request } from "./api";
import { useTemplateDraft } from "./useTemplateDraft";
import type { MediaAsset } from "./MediaLibrary";
type Settings = components["schemas"]["ImageSettings"];
type ImageRequest = components["schemas"]["ImageRequest"];
type Editor = {
  settings: Settings;
  request: ImageRequest;
  dirty_settings: boolean;
};
type Device = {
  name?: string;
  type?: string;
  vram_total?: number;
  vram_free?: number;
};
type Lease = {
  revision: number;
  payload: {
    state: string;
    prompt_id: string;
    job_id: string;
    external_status?: string | null;
    error_code?: string | null;
    result_asset_id?: string | null;
    devices_before: Device[];
    lm_before: string[] | null;
    lm_after: string[] | null;
  };
};
type Config = { revision: number; payload: Settings };
type Capabilities = {
  checkpoints: string[];
  samplers: string[];
  schedulers: string[];
  devices: Device[];
  engine_version?: string;
  scoped_cancel: boolean;
  hardware_verified: boolean;
  lm_loaded_instances: string[] | null;
};
type Job = {
  id: string;
  name: string;
  state: string;
  error?: { message_ru: string } | null;
  result?: {
    asset_id: string;
    source_current: boolean;
    lm_instances_changed: boolean | null;
  } | null;
};
const labels: Record<string, string> = {
  queued: "В очереди",
  running: "Выполняется",
  completed: "Завершена",
  failed: "Ошибка",
  cancelled: "Отменена",
  interrupted: "Прервана",
};
const terminal = (state: string) =>
  ["completed", "failed", "cancelled", "interrupted"].includes(state);
const copy = <T,>(v: T): T => JSON.parse(JSON.stringify(v));
const initialRequest = (revision: number): ImageRequest => ({
  settings_revision: revision,
  name: "Новая иллюстрация",
  prompt: "",
  negative_prompt: "",
  checkpoint: "",
  width: 512,
  height: 512,
  steps: 20,
  cfg: 7,
  seed: 0,
  sampler: "euler",
  scheduler: "normal",
  category: "illustration",
  tags: [],
  confirmed: false,
  story_id: null,
  branch_id: null,
  expected_revision: null,
});
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
        step="any"
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
      />
    </label>
  );
}
export function ImageGeneratePanel({
  visible = true,
  onGenerated,
  onResult,
}: {
  visible?: boolean;
  onGenerated: () => Promise<void>;
  onResult: (asset: MediaAsset) => Promise<void>;
}) {
  const [opened, setOpened] = useState(false),
    [editor, setEditor] = useState<Editor | null>(null),
    [config, setConfig] = useState<Config | null>(null),
    [baseRevision, setBaseRevision] = useState(0),
    [dirty, setDirty] = useState(false),
    [lease, setLease] = useState<Lease | null>(null),
    [cap, setCap] = useState<Capabilities | null>(null),
    [busy, setBusy] = useState(false),
    [status, setStatus] = useState("");
  const [jobs, setJobs] = useState<Job[]>([]),
    [jobId, setJobId] = useState(""),
    [jobState, setJobState] = useState(""),
    [retry, setRetry] = useState(0),
    [resultAsset, setResultAsset] = useState<MediaAsset | null>(null),
    [manualReason, setManualReason] = useState(""),
    [manualConfirmed, setManualConfirmed] = useState(false),
    [stories, setStories] = useState<
      { id: string; payload: { name: string } }[]
    >([]);
  const alive = useRef(true),
    epoch = useRef(0),
    latest = useRef({ editor, baseRevision, dirty });
  latest.current = { editor, baseRevision, dirty };
  const draft = useTemplateDraft(
    "image",
    {
      template_id: editor?.request.story_id || null,
      source_revision: baseRevision,
      raw: "",
      payload: (editor || {}) as Record<string, unknown>,
    },
    dirty,
    busy,
    "/images/drafts",
  );
  function replace(value: Editor, revision: number, isDirty: boolean) {
    latest.current = { editor: value, baseRevision: revision, dirty: isDirty };
    setEditor(value);
    setBaseRevision(revision);
    setDirty(isDirty);
  }
  function edit(fn: (e: Editor) => void) {
    const old = latest.current;
    if (!old.editor) return;
    const value = copy(old.editor);
    fn(value);
    replace(value, old.baseRevision, true);
  }
  async function recent() {
    const r = await request<{ items: Job[] }>("/images/jobs");
    if (alive.current) setJobs(r.items);
    return r.items;
  }
  async function load(reset = false) {
    const id = ++epoch.current;
    const [data, history, s] = await Promise.all([
      request<{ config: Config; lease: Lease | null }>("/images/settings"),
      recent(),
      request<{ stories: { id: string; payload: { name: string } }[] }>(
        "/stories",
      ),
    ]);
    if (!alive.current || id !== epoch.current) return;
    setConfig(data.config);
    setLease(data.lease);
    setStories(s.stories);
    if (reset || !latest.current.editor || !latest.current.dirty) {
      replace(
        {
          settings: data.config.payload,
          request: initialRequest(data.config.revision),
          dirty_settings: false,
        },
        data.config.revision,
        false,
      );
      setCap(null);
    }
    if (!jobId) {
      const current = history.find((j) => !terminal(j.state)) || history[0];
      if (current) {
        setJobId(current.id);
        setJobState(current.state);
      }
    }
  }
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
      epoch.current++;
    };
  }, []);
  useEffect(() => {
    if (visible && opened)
      void load().catch((e) => setStatus((e as Error).message));
  }, [visible, opened]);
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
  async function loadAsset(id: string, accept: () => boolean = () => true) {
    const asset = await request<MediaAsset>(`/media/${id}/metadata`);
    if (alive.current && accept()) setResultAsset(asset);
  }
  useEffect(() => {
    if (!jobId) return;
    let valid = true;
    let timer: ReturnType<typeof setTimeout>;
    async function poll() {
      try {
        const job = await request<{
          state: string;
          error?: { message_ru: string } | null;
          result?: Job["result"];
          lease: Lease | null;
        }>(`/images/jobs/${jobId}`);
        if (!valid || !alive.current) return;
        setJobState(job.state);
        setLease(job.lease);
        if (terminal(job.state)) {
          if (job.result?.asset_id) {
            await loadAsset(job.result.asset_id, () => valid);
            if (!valid) return;
            setStatus(
              job.result.lm_instances_changed === true
                ? "Изображение сохранено. Список загруженных экземпляров LM Studio изменился; проверьте его отдельно."
                : job.result.source_current
                  ? "Изображение сохранено в галерее. Привязка к сцене выполняется отдельно."
                  : "Изображение сохранено. Источник изменён или недоступен; автоматической записи в ветвь нет.",
            );
          } else
            setStatus(
              job.error?.message_ru ||
                "Задача остановлена. При неизвестном внешнем статусе выполните проверку ниже.",
            );
          await onGenerated();
          if (!valid) return;
          const data = await request<{ lease: Lease | null }>(
            "/images/settings",
          );
          if (valid) setLease(data.lease);
          await recent();
          return;
        }
        timer = setTimeout(() => void poll(), 500);
      } catch (e) {
        if (valid) {
          setJobState("connection_error");
          setStatus("Статус задачи неизвестен: " + (e as Error).message);
        }
      }
    }
    void poll();
    return () => {
      valid = false;
      clearTimeout(timer);
    };
  }, [jobId, retry]);
  async function saveSettings() {
    const old = latest.current;
    if (!old.editor) return;
    await draft.flush();
    const saved = await request<Config>("/images/settings", "PUT", {
      expected_revision: old.baseRevision,
      settings: old.editor.settings,
    });
    if (!alive.current) return;
    setConfig(saved);
    setCap(null);
    replace(
      {
        ...old.editor,
        settings: saved.payload,
        request: { ...old.editor.request, settings_revision: saved.revision },
        dirty_settings: false,
      },
      saved.revision,
      true,
    );
    setStatus("Настройки генератора сохранены. Проверьте возможности движка.");
  }
  async function probe() {
    const c = await request<Capabilities>("/images/capabilities");
    if (!alive.current) return;
    setCap(c);
    edit((e) => {
      if (!c.checkpoints.includes(e.request.checkpoint))
        e.request.checkpoint = c.checkpoints[0] || "";
      if (!c.samplers.includes(e.request.sampler || ""))
        e.request.sampler = c.samplers[0] || "";
      if (!c.schedulers.includes(e.request.scheduler || ""))
        e.request.scheduler = c.schedulers[0] || "";
    });
    setStatus(
      "API движка и адресная отмена доступны. Генерация и аппаратная конфигурация требуют отдельного испытания.",
    );
  }
  async function source(id: string) {
    if (!id) {
      edit((e) => {
        e.request.story_id = null;
        e.request.branch_id = null;
        e.request.expected_revision = null;
      });
      return;
    }
    const r = await request<{ branch: { id: string; revision: number } }>(
      `/stories/${id}`,
    );
    edit((e) => {
      e.request.story_id = id;
      e.request.branch_id = r.branch.id;
      e.request.expected_revision = r.branch.revision;
    });
  }
  async function generate() {
    const e = latest.current.editor;
    if (!e) return;
    await draft.flush();
    const q = await request<{ generation_id: string }>(
      "/images/generate",
      "POST",
      { ...e.request, confirmed: true },
      { "Idempotency-Key": crypto.randomUUID() },
    );
    if (!alive.current) return;
    setResultAsset(null);
    setJobId(q.generation_id);
    setJobState("queued");
    setStatus(
      "Изображение поставлено в общую очередь. Диалог имеет приоритет.",
    );
  }
  const activeJob = jobId && !terminal(jobState),
    lockedLease =
      lease && ["running", "uncertain"].includes(lease.payload.state);
  return (
    <details
      aria-label="Локальная генерация изображений"
      onToggle={(e) => {
        if (e.target === e.currentTarget) setOpened(e.currentTarget.open);
      }}
    >
      <summary>Локальная генерация изображений</summary>
      <p role="status">{[status, draft.status].filter(Boolean).join(" ")}</p>
      {!editor || !config ? (
        <button onClick={() => void action(() => load())}>
          Загрузить настройки генератора
        </button>
      ) : (
        <fieldset disabled={busy}>
          {((cap?.devices.some((d) => d.type === "cuda") &&
            Boolean(cap.lm_loaded_instances?.length)) ||
            (lockedLease &&
              lease?.payload.devices_before.some((d) => d.type === "cuda") &&
              Boolean(lease.payload.lm_before?.length))) && (
            <p role="alert">
              В LM Studio уже есть загруженные экземпляры, а генератор
              использует GPU. Возможна конкуренция VRAM. Модели LM Studio
              автоматически не выгружаются; доступны порог памяти и отмена.
            </p>
          )}
          <p>
            Подключается отдельно запущенный ComfyUI с современным API задач.
            Команды загрузки/выгрузки/скачивания моделей LM Studio не
            отправляются. Изображение сохраняется в галерее; изменение сцены
            требует отдельного действия.
          </p>
          <button
            onClick={() =>
              void action(async () => {
                await draft.flush();
                await load(true);
              })
            }
          >
            Перечитать настройки генератора
          </button>
          <details>
            <summary>Подключение и ограничения генератора</summary>
            <label>
              <input
                type="checkbox"
                checked={editor.settings.enabled}
                onChange={(e) =>
                  edit((v) => {
                    v.settings.enabled = e.target.checked;
                    v.dirty_settings = true;
                  })
                }
              />
              Разрешить локальную генерацию изображений
            </label>
            <label>
              Адрес генератора изображений
              <input
                value={editor.settings.endpoint}
                onChange={(e) =>
                  edit((v) => {
                    v.settings.endpoint = e.target.value;
                    v.dirty_settings = true;
                  })
                }
              />
            </label>
            {(
              [
                "timeout_seconds",
                "cancel_wait_seconds",
                "minimum_free_vram_mib",
                "max_pixels",
              ] as const
            ).map((k, i) => (
              <Num
                key={k}
                label={
                  [
                    "Лимит ожидания изображения, с",
                    "Ожидание подтверждения отмены, с",
                    "Минимум свободной VRAM, МиБ (0: без порога)",
                    "Предел пикселей изображения",
                  ][i]
                }
                value={editor.settings[k] || 0}
                onChange={(n) =>
                  edit((v) => {
                    v.settings[k] = n;
                    v.dirty_settings = true;
                  })
                }
              />
            ))}
            <button onClick={() => void action(saveSettings)}>
              Сохранить настройки генератора
            </button>
            <p>
              Порог VRAM проверяется по первому устройству движка. Снимок
              свободной памяти не гарантирует отсутствие OOM; основной чат
              прерывает вспомогательную задачу с проверкой внешней остановки.
            </p>
          </details>
          {editor.dirty_settings && (
            <p>Настройки подключения ещё не опубликованы.</p>
          )}
          <button
            disabled={editor.dirty_settings}
            onClick={() => void action(probe)}
          >
            Проверить локальный генератор
          </button>
          {cap && (
            <>
              <p>
                Версия движка: {cap.engine_version || "не указана"}. Локальных
                checkpoint: {cap.checkpoints.length}. Проверка отмены использует
                новый отсутствующий UUID и не создаёт изображение.
              </p>
              {cap.devices.map((d, i) => (
                <p key={i}>
                  {d.name || d.type || "Устройство"}; свободно{" "}
                  {d.vram_free === undefined
                    ? "неизвестно"
                    : Math.round(d.vram_free / 1024 ** 2)}{" "}
                  МиБ / всего{" "}
                  {d.vram_total === undefined
                    ? "неизвестно"
                    : Math.round(d.vram_total / 1024 ** 2)}{" "}
                  МиБ.
                </p>
              ))}
            </>
          )}
          <label>
            Название результата изображения
            <input
              value={editor.request.name}
              maxLength={300}
              onChange={(e) =>
                edit((v) => {
                  v.request.name = e.target.value;
                })
              }
            />
          </label>
          <label>
            Запрос изображения
            <textarea
              rows={5}
              value={editor.request.prompt}
              onChange={(e) =>
                edit((v) => {
                  v.request.prompt = e.target.value;
                })
              }
            />
          </label>
          <label>
            Нежелательные детали изображения
            <textarea
              rows={3}
              value={editor.request.negative_prompt || ""}
              onChange={(e) =>
                edit((v) => {
                  v.request.negative_prompt = e.target.value;
                })
              }
            />
          </label>
          <label>
            Локальный checkpoint изображения
            <select
              value={editor.request.checkpoint}
              onChange={(e) =>
                edit((v) => {
                  v.request.checkpoint = e.target.value;
                })
              }
            >
              <option value="">Проверьте движок и выберите</option>
              {editor.request.checkpoint &&
                !cap?.checkpoints.includes(editor.request.checkpoint) && (
                  <option value={editor.request.checkpoint}>
                    {editor.request.checkpoint} (не проверен)
                  </option>
                )}
              {cap?.checkpoints.map((x) => (
                <option key={x} value={x}>
                  {x}
                </option>
              ))}
            </select>
          </label>
          {(["width", "height", "steps", "cfg", "seed"] as const).map(
            (k, i) => (
              <Num
                key={k}
                label={
                  [
                    "Ширина изображения",
                    "Высота изображения",
                    "Шагов изображения",
                    "CFG изображения",
                    "Seed изображения",
                  ][i]
                }
                value={editor.request[k] || 0}
                onChange={(n) =>
                  edit((v) => {
                    v.request[k] = n;
                  })
                }
              />
            ),
          )}
          <button
            onClick={() =>
              edit((v) => {
                v.request.seed = crypto.getRandomValues(new Uint32Array(1))[0];
              })
            }
          >
            Новый seed изображения
          </button>
          <label>
            Sampler изображения
            <select
              value={editor.request.sampler}
              onChange={(e) =>
                edit((v) => {
                  v.request.sampler = e.target.value;
                })
              }
            >
              {!cap && (
                <option value={editor.request.sampler}>
                  {editor.request.sampler}
                </option>
              )}
              {cap?.samplers.map((x) => (
                <option key={x} value={x}>
                  {x}
                </option>
              ))}
            </select>
          </label>
          <label>
            Scheduler изображения
            <select
              value={editor.request.scheduler}
              onChange={(e) =>
                edit((v) => {
                  v.request.scheduler = e.target.value;
                })
              }
            >
              {!cap && (
                <option value={editor.request.scheduler}>
                  {editor.request.scheduler}
                </option>
              )}
              {cap?.schedulers.map((x) => (
                <option key={x} value={x}>
                  {x}
                </option>
              ))}
            </select>
          </label>
          <label>
            Категория результата изображения
            <select
              value={editor.request.category}
              onChange={(e) =>
                edit((v) => {
                  v.request.category = e.target
                    .value as ImageRequest["category"];
                })
              }
            >
              {[
                ["illustration", "Иллюстрация"],
                ["portrait", "Портрет"],
                ["expression", "Выражение"],
                ["costume", "Костюм"],
                ["background", "Фон"],
              ].map(([k, label]) => (
                <option key={k} value={k}>
                  {label}
                </option>
              ))}
            </select>
          </label>
          <label>
            История-источник изображения
            <select
              value={editor.request.story_id || ""}
              onChange={(e) => void action(() => source(e.target.value))}
            >
              <option value="">Без ссылки на историю</option>
              {stories.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.payload.name}
                </option>
              ))}
            </select>
          </label>
          {editor.request.story_id && (
            <>
              <p>
                Ревизия источника: {editor.request.expected_revision}; поздний
                результат не применяется к другой ветви.
              </p>
              <button
                onClick={() =>
                  void action(() => source(editor.request.story_id!))
                }
              >
                Обновить источник изображения
              </button>
            </>
          )}
          <button
            disabled={
              !editor.settings.enabled ||
              editor.dirty_settings ||
              !cap ||
              !editor.request.prompt.trim() ||
              !editor.request.checkpoint ||
              Boolean(activeJob) ||
              Boolean(lockedLease)
            }
            onClick={() => void action(generate)}
          >
            Сгенерировать изображение
          </button>
          <p>
            Данные формы:{" "}
            {dirty ? "есть неопубликованный черновик" : "форма без правок"};
            ревизия настроек {baseRevision}. Веса генератора и его runtime не
            поставляются автоматически.
          </p>
          {jobId && (
            <>
              <p>
                Задача изображения: {labels[jobState] || "Состояние неизвестно"}
                .
              </p>
              {jobState === "connection_error" && (
                <button onClick={() => setRetry((n) => n + 1)}>
                  Повторить проверку задачи изображения
                </button>
              )}
              <button
                disabled={terminal(jobState)}
                onClick={() =>
                  void action(async () => {
                    await request(`/generations/${jobId}/cancel`, "POST");
                    setStatus(
                      "Запрос отмены принят; ожидается подтверждение остановки внешнего UUID.",
                    );
                  })
                }
              >
                Остановить генерацию изображения
              </button>
            </>
          )}
          {resultAsset && (
            <article aria-label="Результат генерации изображения">
              <img
                className="media-preview"
                src={`/api/v1/media/${resultAsset.id}/file`}
                alt={resultAsset.payload.name}
                onError={() =>
                  setStatus(
                    "Файл результата недоступен или повреждён. Проверьте восстановление данных.",
                  )
                }
              />
              <p>{resultAsset.payload.name}</p>
              <button onClick={() => void action(() => onResult(resultAsset))}>
                Открыть результат в галерее
              </button>
            </article>
          )}
          <details>
            <summary>Задачи и внешний GPU-слот</summary>
            <button
              onClick={() =>
                void action(async () => {
                  const r = await request<{ lease: Lease | null }>(
                    "/images/settings",
                  );
                  setLease(r.lease);
                  await recent();
                })
              }
            >
              Обновить задачи изображений
            </button>
            <label>
              История задач изображения
              <select
                value={jobId}
                onChange={(e) => {
                  setResultAsset(null);
                  setJobId(e.target.value);
                }}
              >
                <option value="">Выберите задачу</option>
                {jobs.map((j) => (
                  <option key={j.id} value={j.id}>
                    {j.name} — {labels[j.state] || j.state}
                  </option>
                ))}
              </select>
            </label>
            {lease && (
              <>
                <p>
                  Внешний UUID: {lease.payload.prompt_id}; статус:{" "}
                  {(
                    {
                      completed: "завершена",
                      failed: "ошибка",
                      cancelled: "отменена",
                      pending: "в очереди",
                      in_progress: "выполняется",
                      running: "выполняется",
                      uncertain: "не подтверждена",
                      terminal: "завершена",
                      manually_released: "блокировка снята вручную",
                      rejected: "отклонена",
                      not_submitted: "не отправлена",
                    } as Record<string, string>
                  )[lease.payload.external_status || lease.payload.state] ||
                    "неизвестна"}
                  .{" "}
                  {lockedLease
                    ? "Новые GPU-запросы ожидают подтверждения завершения."
                    : "GPU-слот освобождён."}
                </p>
                <button
                  onClick={() =>
                    void action(async () => {
                      const r = await request<{ lease: Lease | null }>(
                        "/images/lease/reconcile",
                        "POST",
                        { cancel_external: false },
                      );
                      setLease(r.lease);
                      if (r.lease?.payload.result_asset_id) {
                        await loadAsset(r.lease.payload.result_asset_id);
                        await onGenerated();
                      }
                      setStatus(
                        r.lease?.payload.result_asset_id
                          ? "Внешнее состояние проверено; результат восстановлен в галерее."
                          : r.lease &&
                              ["running", "uncertain"].includes(
                                r.lease.payload.state,
                              )
                            ? "Внешняя задача ещё не завершена; GPU-слот сохраняется."
                            : "Внешняя задача завершена; нового изображения для восстановления нет.",
                      );
                    })
                  }
                >
                  Проверить внешнюю задачу
                </button>
                <button
                  onClick={() =>
                    void action(async () => {
                      const r = await request<{ lease: Lease | null }>(
                        "/images/lease/reconcile",
                        "POST",
                        { cancel_external: true, confirmed: true },
                      );
                      setLease(r.lease);
                      setStatus(
                        r.lease?.payload.external_status === "completed"
                          ? "Задача уже была завершена; повторная остановка не требовалась."
                          : "Адресная остановка проверена по внешнему статусу.",
                      );
                    })
                  }
                >
                  Остановить собственную внешнюю задачу
                </button>
                <details>
                  <summary>Сравнение загруженных экземпляров LM Studio</summary>
                  <p>
                    До:{" "}
                    {lease.payload.lm_before === null
                      ? "не удалось проверить"
                      : lease.payload.lm_before.length
                        ? lease.payload.lm_before.join(", ")
                        : "загруженных экземпляров нет"}
                    ; после:{" "}
                    {lease.payload.lm_after === null
                      ? "не удалось проверить"
                      : lease.payload.lm_after.length
                        ? lease.payload.lm_after.join(", ")
                        : "загруженных экземпляров нет"}
                    . Приложение не делает вывод о сохранении модели по
                    недоступному каталогу.
                  </p>
                </details>
                {lockedLease && (
                  <details>
                    <summary>
                      Ручное снятие блокировки после остановки движка
                    </summary>
                    <p>
                      Снятие блокировки не останавливает генератор. Используйте
                      после собственной проверки/остановки движка, если его API
                      не позволяет подтвердить результат. Неподтверждённая
                      работа GPU может конфликтовать с новым запросом.
                    </p>
                    <label>
                      <input
                        type="checkbox"
                        checked={manualConfirmed}
                        onChange={(e) => setManualConfirmed(e.target.checked)}
                      />
                      Подтверждаю ручную проверку и остановку внешней задачи
                    </label>
                    <label>
                      Основание снятия GPU-блокировки
                      <input
                        value={manualReason}
                        maxLength={2000}
                        onChange={(e) => setManualReason(e.target.value)}
                      />
                    </label>
                    <button
                      disabled={!manualConfirmed || !manualReason.trim()}
                      onClick={() =>
                        void action(async () => {
                          const r = await request<Lease>(
                            "/images/lease/manual-release",
                            "POST",
                            {
                              expected_revision: lease.revision,
                              reason: manualReason,
                              confirmed: true,
                            },
                          );
                          setLease(r);
                          setManualConfirmed(false);
                          setStatus(
                            "Блокировка снята вручную; техническое подтверждение остановки не подменялось.",
                          );
                        })
                      }
                    >
                      Снять GPU-блокировку вручную
                    </button>
                  </details>
                )}
              </>
            )}
          </details>
          <details>
            <summary>Черновики генерации изображений</summary>
            <button onClick={() => void action(draft.saveSeparate)}>
              Сохранить отдельный черновик изображения
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
                        true,
                      );
                      setCap(null);
                      setStatus(
                        "Черновик изображения восстановлен. Проверьте исходные настройки и возможности движка перед запуском.",
                      );
                    })
                  }
                >
                  Восстановить черновик изображения {d.id.slice(0, 8)}
                </button>
              </p>
            ))}
          </details>
        </fieldset>
      )}
    </details>
  );
}
