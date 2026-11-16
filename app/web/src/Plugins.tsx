import { useEffect, useRef, useState } from "react";
import { request, upload } from "./api";
import type { components } from "./api-types";
import { useTemplateDraft, type TemplateDraft } from "./useTemplateDraft";
type Grant = components["schemas"]["PluginGrant"];
type Editor = {
  enabled: boolean;
  grants: Grant[];
  trusted_full_access_ack: boolean;
  catalog_revision: number;
  approved_sha256: string;
};
type Manifest = {
  id: string;
  name: string;
  version: string;
  description: string;
  license: string;
  runtime: "declarative" | "wasm" | "trusted_python";
  entry: string;
  requests: {
    capability: Grant["capability"];
    reason: string;
    scopes: string[];
  }[];
  dependencies: { plugin_id: string; version: string }[];
};
type Row = {
  id: string;
  revision: number;
  payload: {
    manifest: Manifest;
    package_sha256: string;
    enabled: boolean;
    grants: Grant[];
    trusted_full_access_ack: boolean;
    permission_epoch: number;
  };
};
type Preview = {
  manifest: Manifest;
  sha256: string;
  files: Record<string, string>;
  unpacked_bytes: number;
  application_compatible: boolean;
  expected_revision: number;
  catalog_revision: number;
  affected_dependents: string[];
  will_reset_grants: boolean;
  warning_ru: string | null;
};
const capabilityNames: Record<string, string> = {
  "own.read": "Чтение собственных данных расширения",
  "own.write": "Запись собственных данных расширения",
  "notes.read": "Чтение заметок",
  "notes.write": "Изменение заметок",
  "books.read": "Чтение рукописей",
  "books.write": "Изменение рукописей",
  "filesystem.read": "Чтение внешних локальных каталогов",
  "filesystem.write": "Запись во внешние локальные каталоги",
  "network.request": "Сетевые обращения",
  "tools.run": "Запуск выбранных инструментов",
  "commands.exec": "Выполнение внешних программ",
};
const runtimes = {
  declarative: "Декларативный пакет",
  wasm: "Изолированный WebAssembly",
  trusted_python: "Python с полным доступом",
};
const empty = (): Editor => ({
  enabled: false,
  grants: [],
  trusted_full_access_ack: false,
  catalog_revision: 0,
  approved_sha256: "",
});
export function Plugins({ visible }: { visible: boolean }) {
  const [rows, setRows] = useState<Row[]>([]),
    [catalog, setCatalog] = useState(0),
    [selected, setSelected] = useState<Row | null>(null),
    [sourceRevision, setSourceRevision] = useState(0),
    [editor, setEditor] = useState<Editor>(empty),
    [raw, setRaw] = useState(JSON.stringify(empty(), null, 2)),
    [dirty, setDirty] = useState(false),
    [status, setStatus] = useState(""),
    [busy, setBusy] = useState(false);
  const [file, setFile] = useState<File | null>(null),
    [preview, setPreview] = useState<Preview | null>(null),
    [confirmImport, setConfirmImport] = useState(false),
    [confirmState, setConfirmState] = useState(false),
    [confirmDelete, setConfirmDelete] = useState(false),
    [code, setCode] = useState<{
      filename: string;
      text: string | null;
      sha256: string;
      bytes: number;
    } | null>(null);
  const drafts = useTemplateDraft(
    "plugin",
    {
      template_id: selected?.id || null,
      source_revision: sourceRevision,
      raw,
      payload: editor as unknown as Record<string, unknown>,
    },
    dirty,
    busy,
    "/owner-drafts/plugin",
  );
  const working = useRef(false);
  async function act(fn: () => Promise<void>) {
    if (working.current) return;
    working.current = true;
    setBusy(true);
    try {
      await fn();
    } catch (e) {
      setStatus((e as Error).message);
    } finally {
      working.current = false;
      setBusy(false);
    }
  }
  async function refresh() {
    const r = await request<{
      items: Row[];
      catalog_revision: number;
      errors: string[];
    }>("/plugins");
    setRows(r.items);
    setCatalog(r.catalog_revision);
    if (r.errors.length)
      setStatus(
        "Некоторые записи каталога повреждены; установка и изменение прав заблокированы до восстановления.",
      );
    return r;
  }
  useEffect(() => {
    if (visible) void refresh().catch((e) => setStatus(e.message));
  }, [visible]);
  function edit(patch: Partial<Editor>) {
    const next = { ...editor, ...patch };
    setEditor(next);
    setRaw(JSON.stringify(next, null, 2));
    setDirty(true);
    setConfirmState(false);
  }
  function load(row: Row, revision: number) {
    const next = {
      enabled: row.payload.enabled,
      grants: row.payload.grants,
      trusted_full_access_ack: row.payload.trusted_full_access_ack,
      catalog_revision: revision,
      approved_sha256: row.payload.package_sha256,
    };
    setSelected(row);
    setSourceRevision(row.revision);
    setEditor(next);
    setRaw(JSON.stringify(next, null, 2));
    setDirty(false);
    setCode(null);
    setConfirmState(false);
    setConfirmDelete(false);
    drafts.create();
  }
  async function restore(d: TemplateDraft) {
    await drafts.flush();
    if (!d.payload.template_id) throw Error("В черновике не указан пакет.");
    const row = await request<Row>("/plugins/" + d.payload.template_id);
    setSelected(row);
    setSourceRevision(d.payload.source_revision);
    setEditor(d.payload.payload as unknown as Editor);
    setRaw(d.payload.raw);
    setDirty(true);
    setConfirmState(false);
    setConfirmDelete(false);
    setCode(null);
    drafts.adopt(d);
    setStatus(
      "Восстановлен черновик с исходной ревизией каталога, пакета и прав.",
    );
  }
  function toggle(cap: Grant["capability"], checked: boolean) {
    edit({
      grants: checked
        ? [
            ...editor.grants,
            { capability: cap, scopes: [""], full_access_warning_ack: false },
          ]
        : editor.grants.filter((g) => g.capability !== cap),
    });
  }
  function changeGrant(cap: Grant["capability"], patch: Partial<Grant>) {
    edit({
      grants: editor.grants.map((g) =>
        g.capability === cap ? { ...g, ...patch } : g,
      ),
    });
  }
  const affected = selected
    ? rows.filter(
        (row) =>
          row.payload.enabled &&
          row.payload.manifest.dependencies.some(
            (dep) => dep.plugin_id === selected.id,
          ),
      )
    : [];
  return (
    <section aria-label="Управление расширениями">
      <h2>Пакеты и разрешения расширений</h2>
      <p>
        Импорт и включение не исполняют код. Права выдаются отдельно; обновление
        кода сбрасывает доверие и разрешения. Запуск через интерфейс и
        автоматизация ещё в разработке и не объявлены готовыми в этой внутренней
        сборке.
      </p>
      <p role="status">{status}</p>
      <p>{drafts.status}</p>
      <button
        disabled={busy}
        onClick={() =>
          void act(async () => {
            await refresh();
            setStatus("Каталог перечитан. Открытый черновик не перебазирован.");
          })
        }
      >
        Обновить каталог расширений
      </button>
      <ul>
        {rows.map((row) => (
          <li key={row.id}>
            <button
              disabled={busy}
              onClick={() =>
                void act(async () => {
                  await drafts.flush();
                  load(row, catalog);
                  setStatus("Открыта опубликованная запись расширения.");
                })
              }
            >
              {row.payload.manifest.name} · {row.payload.manifest.version}
            </button>
            <span>{row.payload.enabled ? "Разрешён запуск" : "Выключено"}</span>
          </li>
        ))}
      </ul>
      <fieldset disabled={busy}>
        <legend>Импорт локального ZIP</legend>
        <label>
          Пакет расширения
          <input
            type="file"
            accept=".zip"
            onChange={(e) => {
              setFile(e.target.files?.[0] || null);
              setPreview(null);
              setConfirmImport(false);
            }}
          />
        </label>
        <button
          disabled={!file}
          onClick={() =>
            void act(async () => {
              await drafts.flush();
              setPreview(
                await upload<Preview>("/plugins/import-preview", file!),
              );
              setConfirmImport(false);
              setStatus(
                "Пакет проверен без исполнения кода. Проверьте состав и запрашиваемые права.",
              );
            })
          }
        >
          Проверить пакет без запуска
        </button>
        {preview && (
          <article aria-label="Предпросмотр пакета расширения">
            <h3>
              {preview.manifest.name} · {preview.manifest.version}
            </h3>
            <p>{preview.manifest.description}</p>
            <p>
              {runtimes[preview.manifest.runtime]}; совместимость с этой
              версией:{" "}
              {preview.application_compatible
                ? "да"
                : "нет, включение будет запрещено"}
              .
            </p>
            {preview.warning_ru && (
              <p className="warning">{preview.warning_ru}</p>
            )}
            <p>
              Файлов: {Object.keys(preview.files).length}; распакованных байт:{" "}
              {preview.unpacked_bytes}.
            </p>
            <p>SHA256: {preview.sha256}</p>
            <ul>
              {preview.manifest.requests.map((r) => (
                <li key={r.capability}>
                  {capabilityNames[r.capability]}: {r.reason}. Запрошенные
                  области: {r.scopes.join(", ") || "выбирает владелец"}. Пока не
                  выданы.
                </li>
              ))}
            </ul>
            {preview.will_reset_grants && (
              <p>
                Обновление сбросит текущие права и доверие. Зависимые пакеты
                будут отключены:{" "}
                {preview.affected_dependents
                  .map(
                    (id) =>
                      rows.find((r) => r.id === id)?.payload.manifest.name ||
                      id,
                  )
                  .join(", ") || "нет"}
                .
              </p>
            )}
            <details>
              <summary>Файлы и контрольные суммы пакета</summary>
              <pre>{JSON.stringify(preview.files, null, 2)}</pre>
            </details>
            <label>
              <input
                type="checkbox"
                checked={confirmImport}
                onChange={(e) => setConfirmImport(e.target.checked)}
              />
              Подтверждаю установку просмотренного пакета без выдачи прав и без
              запуска
            </label>
            <button
              disabled={!confirmImport}
              onClick={() =>
                void act(async () => {
                  const params = new URLSearchParams({
                    expected_revision: String(preview.expected_revision),
                    catalog_revision: String(preview.catalog_revision),
                    approved_sha256: preview.sha256,
                    confirmed: "true",
                  });
                  const row = await upload<Row>(
                    "/plugins/import?" + params,
                    file!,
                  );
                  const current = await refresh();
                  load(row, current.catalog_revision);
                  setPreview(null);
                  setConfirmImport(false);
                  setStatus(
                    "Пакет установлен. Код не запускался; права выдаются отдельно.",
                  );
                })
              }
            >
              Подтвердить установку расширения
            </button>
          </article>
        )}
      </fieldset>
      {!!drafts.items.length && (
        <details>
          <summary>Черновики разрешений расширений</summary>
          {drafts.items.map((d) => (
            <div key={d.id}>
              <button
                disabled={busy}
                onClick={() => void act(() => restore(d))}
              >
                Восстановить права {d.id.slice(0, 8)}
              </button>
              <details>
                <summary>Текст черновика {d.id.slice(0, 8)}</summary>
                <pre>{d.payload.raw}</pre>
              </details>
            </div>
          ))}
        </details>
      )}
      {selected && (
        <fieldset disabled={busy}>
          <legend>{selected.payload.manifest.name} · настройки пакета</legend>
          <p>
            {runtimes[selected.payload.manifest.runtime]}. Ревизия открытого
            редактора: {sourceRevision}; ревизия каталога:{" "}
            {editor.catalog_revision}.
          </p>
          <p>Подтверждаемый SHA256: {editor.approved_sha256}</p>
          <p>{selected.payload.manifest.description}</p>
          <p>
            Зависимости:{" "}
            {selected.payload.manifest.dependencies
              .map(
                (d) =>
                  (rows.find((r) => r.id === d.plugin_id)?.payload.manifest
                    .name || d.plugin_id) +
                  " " +
                  d.version,
              )
              .join(", ") || "нет"}
          </p>
          <label>
            <input
              type="checkbox"
              checked={editor.enabled}
              onChange={(e) => edit({ enabled: e.target.checked })}
            />
            Разрешить последующий явный запуск расширения
          </label>
          {selected.payload.manifest.runtime === "trusted_python" && (
            <div>
              <p className="warning">
                Доверенный Python получает полный доступ к файлам, сети,
                командам и данным пользователя. Отдельный процесс и список
                broker-разрешений не являются для Python песочницей. Ограничения
                ресурсов не заменяют доверие к коду.
              </p>
              <label>
                <input
                  type="checkbox"
                  checked={editor.trusted_full_access_ack}
                  onChange={(e) =>
                    edit({ trusted_full_access_ack: e.target.checked })
                  }
                />
                Я доверяю именно этому SHA256 и понимаю полный доступ Python
              </label>
            </div>
          )}
          <h3>Отдельные разрешения</h3>
          {selected.payload.manifest.requests.length === 0 && (
            <p>Пакет не запросил broker-разрешений.</p>
          )}
          {selected.payload.manifest.requests.map((r) => {
            const grant = editor.grants.find(
              (g) => g.capability === r.capability,
            );
            return (
              <fieldset key={r.capability}>
                <legend>{capabilityNames[r.capability]}</legend>
                <p>{r.reason}</p>
                <p>
                  Запрошено: {r.scopes.join(", ") || "границы задаёт владелец"}.
                </p>
                <label>
                  <input
                    type="checkbox"
                    checked={!!grant}
                    onChange={(e) => toggle(r.capability, e.target.checked)}
                  />
                  Выдать: {capabilityNames[r.capability]}
                </label>
                {grant && (
                  <div>
                    <label>
                      Разрешённые области: {capabilityNames[r.capability]}
                      <textarea
                        value={grant.scopes.join("\n")}
                        onChange={(e) =>
                          changeGrant(r.capability, {
                            scopes: e.target.value.split("\n"),
                          })
                        }
                      />
                    </label>
                    <p>
                      Каждая область с новой строки. Для данных — UUID, new или
                      явно разрешённая *. Для файлов — абсолютный локальный
                      каталог вне папки программы; UNC/сетевые
                      диски/перенаправления не допускаются. Для команд — точный
                      executable.
                    </p>
                    {r.capability === "commands.exec" && (
                      <label>
                        <input
                          type="checkbox"
                          checked={!!grant.full_access_warning_ack}
                          onChange={(e) =>
                            changeGrant(r.capability, {
                              full_access_warning_ack: e.target.checked,
                            })
                          }
                        />
                        Понимаю, что внешняя программа работает с полными
                        правами пользователя; область команд не ограничивает её
                        остальными правами расширения
                      </label>
                    )}
                  </div>
                )}
              </fieldset>
            );
          })}
          {affected.length > 0 && (
            <p>
              Отключение также остановит зависимые пакеты:{" "}
              {affected.map((r) => r.payload.manifest.name).join(", ")}. Для
              глубоких зависимостей действует такое же правило.
            </p>
          )}
          <button
            onClick={() =>
              void act(async () => {
                const r = await request<Row>("/plugins/" + selected.id);
                const current = await refresh();
                await drafts.flush();
                load(r, current.catalog_revision);
                setStatus(
                  "Открыто сохранённое состояние; предыдущий черновик сохранён отдельно.",
                );
              })
            }
          >
            Перечитать опубликованные права
          </button>
          <details>
            <summary>JSON разрешений расширения</summary>
            <textarea
              aria-label="JSON разрешений расширения"
              rows={12}
              value={raw}
              onChange={(e) => {
                setRaw(e.target.value);
                setDirty(true);
                setConfirmState(false);
              }}
            />
          </details>
          <label>
            <input
              type="checkbox"
              checked={confirmState}
              onChange={(e) => setConfirmState(e.target.checked)}
            />
            Подтверждаю изменение состояния и перечисленных прав
          </label>
          <button
            disabled={!confirmState}
            onClick={() =>
              void act(async () => {
                await drafts.flush();
                const parsed = JSON.parse(raw);
                const row = await request<Row>(
                  "/plugins/" + selected.id,
                  "PUT",
                  {
                    ...parsed,
                    expected_revision: sourceRevision,
                    confirmed: true,
                  },
                );
                await drafts.remove();
                const current = await refresh();
                load(row, current.catalog_revision);
                setStatus(
                  "Состояние и отдельные права сохранены. Устаревшие управляемые задачи отменены.",
                );
              })
            }
          >
            Сохранить состояние и права
          </button>
          <button
            onClick={() =>
              void act(async () => {
                setCode(await request("/plugins/" + selected.id + "/entry"));
              })
            }
          >
            Просмотреть исходник entry
          </button>
          {code && (
            <article aria-label="Исходник расширения">
              <p>
                {code.filename}; {code.bytes} байт; SHA256: {code.sha256}
              </p>
              <pre>
                {code.text ||
                  "Бинарный WebAssembly: текстового исходника в entry нет."}
              </pre>
            </article>
          )}
          <details>
            <summary>Удаление пакета</summary>
            <p>
              Удаляется код пакета из реестра и с диска; зависимые пакеты
              отключаются. История запусков и пользовательские данные не
              выполняются и не удаляются автоматически.
            </p>
            <label>
              <input
                type="checkbox"
                checked={confirmDelete}
                onChange={(e) => setConfirmDelete(e.target.checked)}
              />
              Подтверждаю удаление выбранного расширения и отключение зависимых
            </label>
            <button
              disabled={!confirmDelete}
              onClick={() =>
                void act(async () => {
                  await drafts.flush();
                  const r = await request<{
                    deleted: boolean;
                    files_removed: boolean;
                  }>("/plugins/" + selected.id, "DELETE", {
                    expected_revision: sourceRevision,
                    catalog_revision: editor.catalog_revision,
                    approved_sha256: editor.approved_sha256,
                    confirmed: true,
                  });
                  setSelected(null);
                  setDirty(false);
                  drafts.create();
                  await refresh();
                  setStatus(
                    r.files_removed
                      ? "Расширение удалено, файлы пакета убраны."
                      : "Расширение удалено из реестра, но часть файлов занята. Проверьте остатки в папке plugins.",
                  );
                })
              }
            >
              Удалить расширение
            </button>
          </details>
        </fieldset>
      )}
    </section>
  );
}
