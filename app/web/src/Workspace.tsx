import {
  useEffect,
  useRef,
  useState,
  type CSSProperties,
  type ReactNode,
} from "react";
import { request } from "./api";
import { GenerationPanel } from "./GenerationPanel";
import { GenerationHistory } from "./GenerationHistory";
import { Characters } from "./Characters";
import { Chat } from "./Chat";
import { Profiles } from "./Profiles";
import { Worlds } from "./Worlds";

type Colors = {
  background: string;
  surface: string;
  text: string;
  accent: string;
};
type Settings = {
  level: "simple" | "advanced" | "expert";
  theme: string;
  economy: boolean;
  font: string;
  font_size: number;
  colors: Colors;
  expert: Record<string, unknown>;
  active_workspace: string | null;
};
type Panel = {
  id: "models" | "history" | "storage";
  width: number;
  collapsed: boolean;
};
type Layout = { name: string; immersive: boolean; panels: Panel[] };
type WorkspaceRecord = { id: string; revision: number; payload: Layout };
type Summary = {
  character_count: number;
  scenario_count: number;
  recent_stories: Array<{ id: string; payload: { name?: string } }>;
};
const palettes: Record<string, Colors> = {
  night: {
    background: "#171a20",
    surface: "#22262e",
    text: "#e9e5df",
    accent: "#d6a25f",
  },
  warm: {
    background: "#221c1a",
    surface: "#322a25",
    text: "#f2e4d5",
    accent: "#d99562",
  },
  light: {
    background: "#eceef2",
    surface: "#ffffff",
    text: "#202632",
    accent: "#bd8545",
  },
};
const initial: Layout = {
  name: "Основное пространство",
  immersive: false,
  panels: [
    { id: "models", width: 800, collapsed: false },
    { id: "history", width: 800, collapsed: false },
    { id: "storage", width: 500, collapsed: false },
  ],
};
const titles: Record<string, string> = {
  models: "LM Studio",
  history: "Сохранённые ответы",
  storage: "Целостность данных",
};

function StoragePanel() {
  const [message, setMessage] = useState("Проверка ещё не выполнялась.");
  async function check() {
    try {
      const r = await request<{
        ok: boolean;
        documents: number;
        errors: unknown[];
      }>("/storage/integrity-check", "POST");
      setMessage(
        r.ok
          ? `Проверка пройдена. Документов: ${r.documents}.`
          : `Обнаружены ошибки: ${r.errors.length}. Откройте журнал диагностики.`,
      );
    } catch (e) {
      setMessage((e as Error).message);
    }
  }
  return (
    <div className="panel-body">
      <p>{message}</p>
      <button onClick={check}>Проверить JSON-хранилище</button>
    </div>
  );
}

export function Workspace({ shutdown }: { shutdown: () => void }) {
  const [settings, setSettings] = useState<Settings | null>(null);
  const revision = useRef(0);
  const [workspaces, setWorkspaces] = useState<WorkspaceRecord[]>([]);
  const record = useRef<WorkspaceRecord | null>(null);
  const [layout, setLayout] = useState<Layout>(initial);
  const [page, setPage] = useState("home");
  const [openStory, setOpenStory] = useState<{
    id: string;
    nonce: number;
  } | null>(null);
  const [saveState, setSaveState] = useState("Все изменения сохранены.");
  const [busy, setBusy] = useState(false);
  const [summary, setSummary] = useState<Summary | null>(null);
  const [name, setName] = useState("");
  const [expert, setExpert] = useState("");
  const drag = useRef<number | null>(null);
  async function load() {
    try {
      const [s, w, h] = await Promise.all([
        request<{ settings: Settings; revision: number }>("/settings"),
        request<{ workspaces: WorkspaceRecord[] }>("/workspaces"),
        request<Summary>("/home/summary"),
      ]);
      setSettings(s.settings);
      revision.current = s.revision;
      setWorkspaces(w.workspaces);
      setSummary(h);
      setExpert(JSON.stringify(s.settings.expert, null, 2));
      const active =
        w.workspaces.find((x) => x.id === s.settings.active_workspace) || null;
      record.current = active;
      setLayout(active?.payload || initial);
    } catch (e) {
      setSaveState((e as Error).message);
    }
  }
  useEffect(() => {
    void load();
  }, []);
  useEffect(() => {
    if (!settings) return;
    const colors = palettes[settings.theme] || settings.colors;
    for (const [key, value] of Object.entries(colors))
      document.documentElement.style.setProperty("--" + key, value);
    document.documentElement.style.fontFamily = settings.font;
    document.documentElement.style.fontSize = settings.font_size + "px";
    document.documentElement.dataset.economy = String(settings.economy);
  }, [settings]);
  async function patch(change: Partial<Settings>) {
    setSaveState("Есть несохранённые изменения. Сохраняем…");
    setBusy(true);
    try {
      const r = await request<{ settings: Settings; revision: number }>(
        "/settings",
        "PATCH",
        { expected_revision: revision.current, patch: change },
      );
      setSettings(r.settings);
      revision.current = r.revision;
      setSaveState("Все изменения сохранены.");
    } catch (e) {
      setSaveState(
        (e as Error).message +
          " Изменения не сохранены. Перечитайте данные и повторите.",
      );
    } finally {
      setBusy(false);
    }
  }
  async function saveLayout(next: Layout, createNew = false) {
    setSaveState("Сохраняем рабочее пространство…");
    setBusy(true);
    try {
      const old = createNew ? null : record.current;
      const saved = await request<WorkspaceRecord>(
        old ? `/workspaces/${old.id}` : "/workspaces",
        old ? "PATCH" : "POST",
        { expected_revision: old?.revision || 0, workspace: next },
      );
      record.current = saved;
      setLayout(saved.payload);
      setWorkspaces((w) => [...w.filter((x) => x.id !== saved.id), saved]);
      await patch({ active_workspace: saved.id });
    } catch (e) {
      setSaveState((e as Error).message + " Расположение не сохранено.");
    } finally {
      setBusy(false);
    }
  }
  async function selectWorkspace(id: string) {
    const selected = workspaces.find((w) => w.id === id);
    if (!selected) return;
    await patch({ active_workspace: id });
    record.current = selected;
    setLayout(selected.payload);
  }
  function changePanel(index: number, change: Partial<Panel>) {
    void saveLayout({
      ...layout,
      panels: layout.panels.map((p, i) =>
        i === index ? { ...p, ...change } : p,
      ),
    });
  }
  function move(index: number, target: number) {
    if (target < 0 || target >= layout.panels.length || busy) return;
    const panels = [...layout.panels];
    const [panel] = panels.splice(index, 1);
    panels.splice(target, 0, panel);
    void saveLayout({ ...layout, panels });
  }
  if (!settings)
    return (
      <section>
        <p role="status">
          {saveState === "Все изменения сохранены."
            ? "Загружаем настройки…"
            : saveState}
        </p>
        <button onClick={load}>Повторить загрузку</button>
      </section>
    );
  const content: Record<Panel["id"], ReactNode> = {
    models: <GenerationPanel />,
    history: <GenerationHistory />,
    storage: <StoragePanel />,
  };
  return (
    <div className={"workspace " + (layout.immersive ? "immersive" : "")}>
      <aside className="navigation">
        <div className="brand">
          Таверна <small>внутренняя сборка</small>
        </div>
        {[
          ["home", "Главная"],
          ["connection", "Рабочее пространство"],
          ["characters", "Персонажи"],
          ["chat", "Истории"],
          ["profiles", "Профили и промпты"],
          ["worlds", "Миры и сценарии"],
          ["settings", "Настройки"],
        ].map(([id, label]) => (
          <button
            key={id}
            className={page === id ? "active" : ""}
            onClick={() => {
              setPage(id);
              if (id === "home")
                void request<Summary>("/home/summary")
                  .then(setSummary)
                  .catch((e) => setSaveState(e.message));
            }}
          >
            {label}
          </button>
        ))}
        <button
          onClick={() =>
            void saveLayout({ ...layout, immersive: !layout.immersive })
          }
          disabled={busy}
        >
          {layout.immersive ? "Показать навигацию" : "Режим погружения"}
        </button>
        <button className="secondary" onClick={shutdown}>
          Завершить приложение
        </button>
      </aside>
      <div className="workspace-main">
        <header className="workspace-header">
          <div>
            <h1>
              {page === "home"
                ? "Ваша Таверна"
                : page === "settings"
                  ? "Настройки"
                  : page === "characters"
                    ? "Персонажи"
                    : page === "chat"
                      ? "Истории"
                      : page === "worlds"
                        ? "Миры и сценарии"
                        : page === "profiles"
                          ? "Профили и промпты"
                          : "Рабочее пространство"}
            </h1>
            <p>Локальные истории и персонажи</p>
          </div>
          <span className="badge">Разработка 1.0</span>
        </header>
        <div className="save-state" role="status">
          {saveState}
        </div>
        {page === "home" && (
          <>
            <section>
              <h2>Продолжить историю</h2>
              {summary?.recent_stories.length ? (
                <ul>
                  {summary.recent_stories.map((s) => (
                    <li key={s.id}>
                      <button
                        onClick={() => {
                          setOpenStory({ id: s.id, nonce: Date.now() });
                          setPage("chat");
                        }}
                      >
                        {s.payload.name || "История без названия"}
                      </button>
                    </li>
                  ))}
                </ul>
              ) : (
                <p>Историй пока нет. Создайте историю на странице «Истории».</p>
              )}
            </section>
            <div className="summary-grid">
              <section>
                <h2>Персонажи</h2>
                <p>{summary?.character_count || 0} в локальной библиотеке</p>
                <p>
                  Откройте «Персонажи», чтобы создать или импортировать
                  карточку.
                </p>
              </section>
              <section>
                <h2>Сценарии</h2>
                <p>{summary?.scenario_count || 0} сохранённых сценариев</p>
              </section>
            </div>
            <GenerationPanel />
          </>
        )}
        {page === "connection" && (
          <>
            <section className="workspace-controls">
              <label>
                Рабочее пространство
                <select
                  disabled={busy}
                  value={record.current?.id || ""}
                  onChange={(e) => void selectWorkspace(e.target.value)}
                >
                  <option value="">Основное пространство</option>
                  {workspaces.map((w) => (
                    <option key={w.id} value={w.id}>
                      {w.payload.name}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                Название нового пространства
                <input
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  maxLength={80}
                />
              </label>
              <button
                disabled={busy || !name.trim()}
                onClick={() =>
                  void saveLayout({ ...layout, name: name.trim() }, true)
                }
              >
                Сохранить как новое пространство
              </button>
            </section>
            <div className="panels">
              {layout.panels.map((panel, index) => (
                <article
                  key={panel.id}
                  className="managed-panel"
                  style={
                    { "--panel-width": panel.width + "px" } as CSSProperties
                  }
                  draggable={!busy}
                  onDragStart={() => {
                    drag.current = index;
                  }}
                  onDragOver={(e) => e.preventDefault()}
                  onDrop={(e) => {
                    e.preventDefault();
                    if (drag.current !== null) move(drag.current, index);
                    drag.current = null;
                  }}
                >
                  <div className="panel-toolbar">
                    <strong>{titles[panel.id]}</strong>
                    <button
                      aria-label={"Переместить вверх: " + titles[panel.id]}
                      disabled={busy || index === 0}
                      onClick={() => move(index, index - 1)}
                    >
                      ↑
                    </button>
                    <button
                      aria-label={"Переместить вниз: " + titles[panel.id]}
                      disabled={busy || index === layout.panels.length - 1}
                      onClick={() => move(index, index + 1)}
                    >
                      ↓
                    </button>
                    <button
                      disabled={busy}
                      onClick={() =>
                        changePanel(index, { collapsed: !panel.collapsed })
                      }
                    >
                      {panel.collapsed ? "Развернуть" : "Свернуть"}
                    </button>
                  </div>
                  {!panel.collapsed && (
                    <>
                      <label className="size-control">
                        Ширина панели
                        <input
                          type="range"
                          aria-label={"Ширина: " + titles[panel.id]}
                          min={300}
                          max={1200}
                          step={100}
                          defaultValue={panel.width}
                          onPointerUp={(e) =>
                            changePanel(index, {
                              width: Number(e.currentTarget.value),
                            })
                          }
                          onKeyUp={(e) =>
                            changePanel(index, {
                              width: Number(e.currentTarget.value),
                            })
                          }
                          disabled={busy}
                        />
                      </label>
                      {content[panel.id]}
                    </>
                  )}
                </article>
              ))}
            </div>
          </>
        )}
        {page === "settings" && (
          <section>
            <h2>Внешний вид и доступность параметров</h2>
            <label>
              Уровень настроек
              <select
                value={settings.level}
                disabled={busy}
                onChange={(e) =>
                  void patch({ level: e.target.value as Settings["level"] })
                }
              >
                <option value="simple">Простой</option>
                <option value="advanced">Расширенный</option>
                <option value="expert">Эксперт</option>
              </select>
            </label>
            <label>
              Тема
              <select
                value={settings.theme}
                disabled={busy}
                onChange={(e) => void patch({ theme: e.target.value })}
              >
                <option value="night">Ночная</option>
                <option value="warm">Тёплая</option>
                <option value="light">Светлая</option>
                <option value="custom">Своя палитра</option>
              </select>
            </label>
            <label>
              Шрифт
              <select
                value={settings.font}
                disabled={busy}
                onChange={(e) => void patch({ font: e.target.value })}
              >
                {["Segoe UI", "Arial", "Georgia", "Consolas"].map((f) => (
                  <option key={f}>{f}</option>
                ))}
              </select>
            </label>
            <label>
              Размер текста
              <input
                type="number"
                min={12}
                max={28}
                value={settings.font_size}
                disabled={busy}
                onChange={(e) => {
                  const n = Number(e.target.value);
                  if (n >= 12 && n <= 28) void patch({ font_size: n });
                }}
              />
            </label>
            <label className="inline">
              <input
                type="checkbox"
                checked={settings.economy}
                disabled={busy}
                onChange={(e) => void patch({ economy: e.target.checked })}
              />
              Экономичный режим: отключить декоративные эффекты
            </label>
            {settings.level !== "simple" && (
              <fieldset>
                <legend>Ручные цвета</legend>
                {Object.entries({
                  background: "Фон",
                  surface: "Панели",
                  text: "Текст",
                  accent: "Акцент",
                }).map(([key, label]) => (
                  <label key={key}>
                    {label}
                    <input
                      type="color"
                      value={settings.colors[key as keyof Colors]}
                      disabled={busy}
                      onChange={(e) =>
                        void patch({
                          theme: "custom",
                          colors: { ...settings.colors, [key]: e.target.value },
                        })
                      }
                    />
                  </label>
                ))}
              </fieldset>
            )}
            {settings.level === "expert" && (
              <>
                <label>
                  Дополнительные экспертные значения (JSON)
                  <textarea
                    rows={6}
                    value={expert}
                    onChange={(e) => {
                      setExpert(e.target.value);
                      setSaveState(
                        "Есть несохранённые изменения экспертных значений.",
                      );
                    }}
                  />
                </label>
                <button
                  disabled={busy}
                  onClick={() => {
                    try {
                      const value = JSON.parse(expert);
                      if (
                        !value ||
                        Array.isArray(value) ||
                        typeof value !== "object"
                      )
                        throw Error("Ожидается JSON-объект.");
                      void patch({ expert: value });
                    } catch (e) {
                      setSaveState(
                        "Неверный JSON. Проверьте скобки, кавычки и запятые.",
                      );
                    }
                  }}
                >
                  Сохранить экспертные значения
                </button>
              </>
            )}
            <p>
              Переключение уровня не удаляет сохранённые экспертные значения.
            </p>
            <button className="secondary" onClick={load} disabled={busy}>
              Перечитать сохранённые настройки
            </button>
          </section>
        )}
        <div hidden={page !== "characters"}>
          <Characters />
        </div>
        <div hidden={page !== "worlds"}>
          <Worlds />
        </div>
        <div hidden={page !== "chat"}>
          <Chat openStory={openStory} />
        </div>
        <div hidden={page !== "profiles"}>
          <Profiles />
        </div>
        <footer>
          Один пользователь · данные в переносимой папке · обновления только
          вручную
        </footer>
      </div>
    </div>
  );
}
