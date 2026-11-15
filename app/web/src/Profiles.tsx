import { useEffect, useState } from "react";
import { request } from "./api";

type Block = {
  id: string;
  name: string;
  source: string;
  enabled: boolean;
  role: string;
  priority: number;
  pinned: boolean;
  template: string;
  condition: { field: string; op: string; value: string | boolean } | null;
  max_tokens: number | null;
};
type Patch = {
  parameters?: Record<string, number | string[] | null>;
  blocks?: Block[];
  context_limit?: number;
  automatic_context?: boolean;
  estimator?: string;
  save_context?: boolean;
  quality?: Record<string, unknown>;
  [key: string]: unknown;
};
type Profile = {
  id: string;
  revision: number;
  payload: { name: string; patch: Patch; builtin: boolean };
};
type Binding = {
  revision: number;
  payload: { scope: string; scope_id: string; profile_id: string };
};
type Entity = {
  id: string;
  payload: { name?: string; fields?: { name?: string } };
};
const sources = [
  "system",
  "character",
  "scenario",
  "scene",
  "lore",
  "memory",
  "relationships",
  "history",
  "post_history",
  "custom",
];
const labels = [
  "Системные инструкции",
  "Карточка",
  "Сценарий",
  "Сцена",
  "Лорбук",
  "Память",
  "Отношения",
  "История",
  "После истории",
  "Свой текст",
];

export function Profiles() {
  const [profiles, setProfiles] = useState<Profile[]>([]);
  const [defaults, setDefaults] = useState<Patch>({});
  const [selected, setSelected] = useState<Profile | null>(null);
  const [name, setName] = useState("Новый профиль");
  const [patch, setPatch] = useState<Patch>({});
  const [raw, setRaw] = useState("{}");
  const [applied, setApplied] = useState("{}");
  const [bindings, setBindings] = useState<Binding[]>([]);
  const [characters, setCharacters] = useState<Entity[]>([]);
  const [stories, setStories] = useState<Entity[]>([]);
  const [scope, setScope] = useState("global");
  const [scopeId, setScopeId] = useState("default");
  const [bindingProfile, setBindingProfile] = useState("");
  const [status, setStatus] = useState("");
  const [busy, setBusy] = useState(false);
  const [resolved, setResolved] = useState("");
  async function load() {
    try {
      const [p, b, c, s] = await Promise.all([
        request<{ profiles: Profile[]; defaults: Patch }>("/profiles"),
        request<{ bindings: Binding[] }>("/profiles/bindings"),
        request<{ characters: Entity[] }>("/characters"),
        request<{ stories: Entity[] }>("/stories"),
      ]);
      setProfiles(p.profiles);
      setDefaults(p.defaults);
      setBindings(b.bindings);
      setCharacters(c.characters);
      setStories(s.stories);
    } catch (e) {
      setStatus((e as Error).message);
    }
  }
  useEffect(() => {
    void load();
  }, []);
  function change(value: Patch) {
    setPatch(value);
    const text = JSON.stringify(value, null, 2);
    setRaw(text);
    setApplied(text);
  }
  function choose(profile: Profile | null) {
    setSelected(profile);
    setName(profile?.payload.name || "Новый профиль");
    change(profile?.payload.patch || {});
    setStatus("");
  }
  async function save() {
    setBusy(true);
    try {
      const result = await request<Profile>(
        selected ? `/profiles/${selected.id}` : "/profiles",
        selected ? "PATCH" : "POST",
        { name, patch, expected_revision: selected?.revision || 0 },
      );
      choose(result);
      await load();
      setStatus("Профиль сохранён.");
    } catch (e) {
      setStatus((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  const blocks = patch.blocks || defaults.blocks || [];
  const ready = raw === applied && !busy;
  function blockChange(index: number, value: Partial<Block>) {
    change({
      ...patch,
      blocks: blocks.map((b, i) => (i === index ? { ...b, ...value } : b)),
    });
  }
  async function bind() {
    setBusy(true);
    try {
      const previous = bindings.find(
        (b) => b.payload.scope === scope && b.payload.scope_id === scopeId,
      );
      await request("/profiles/bindings", "PUT", {
        scope,
        scope_id: scopeId,
        profile_id: bindingProfile || null,
        expected_revision: previous?.revision || 0,
      });
      await load();
      setStatus("Назначение сохранено.");
    } catch (e) {
      setStatus((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  const entities = scope === "character" ? characters : stories;
  return (
    <section className="panel" aria-label="Профили генерации">
      <h2>Профили и промпты</h2>
      <p>
        Профили хранят только явные переопределения. Порядок: базовый → общий →
        служебная роль → персонаж → история/группа → задача. Оценка токенов
        приблизительная; точное потребление показывается по ответу движка.
      </p>
      <label>
        Сохранённый профиль
        <select
          value={selected?.id || ""}
          onChange={(e) =>
            choose(profiles.find((p) => p.id === e.target.value) || null)
          }
        >
          <option value="">Новый профиль</option>
          {profiles.map((p) => (
            <option key={p.id} value={p.id}>
              {p.payload.name}
            </option>
          ))}
        </select>
      </label>
      <button onClick={() => choose(null)} disabled={busy}>
        Новый профиль
      </button>
      <label>
        Название профиля
        <input
          value={name}
          disabled={busy}
          onChange={(e) => setName(e.target.value)}
        />
      </label>
      <fieldset disabled={!ready}>
        <legend>Основные переопределения</legend>
        <label>
          Резерв ответа
          <input
            type="number"
            min={1}
            max={32768}
            value={(patch.parameters?.max_tokens as number) ?? ""}
            placeholder="Наследуется"
            onChange={(e) => {
              const p = { ...patch.parameters };
              if (e.target.value) p.max_tokens = Number(e.target.value);
              else delete p.max_tokens;
              change({ ...patch, parameters: p });
            }}
          />
        </label>
        <label>
          Температура
          <input
            type="number"
            min={0}
            max={2}
            step="0.05"
            value={(patch.parameters?.temperature as number) ?? ""}
            placeholder="Наследуется"
            onChange={(e) => {
              const p = { ...patch.parameters };
              if (e.target.value) p.temperature = Number(e.target.value);
              else delete p.temperature;
              change({ ...patch, parameters: p });
            }}
          />
        </label>
        <label>
          Окно контекста
          <input
            type="number"
            min={256}
            max={1048576}
            value={patch.context_limit ?? ""}
            placeholder="Наследуется"
            onChange={(e) => {
              const p = { ...patch };
              if (e.target.value) p.context_limit = Number(e.target.value);
              else delete p.context_limit;
              change(p);
            }}
          />
        </label>
        <label>
          Оценка токенов
          <select
            value={patch.estimator || ""}
            onChange={(e) => {
              const p = { ...patch };
              if (e.target.value) p.estimator = e.target.value;
              else delete p.estimator;
              change(p);
            }}
          >
            <option value="">Наследуется</option>
            <option value="balanced">
              Сбалансированная (UTF-8 / 2 + служебный запас)
            </option>
            <option value="conservative">
              Осторожная (байты UTF-8 + служебный запас)
            </option>
          </select>
        </label>
        <label>
          Сохранение полного контекста
          <select
            value={
              patch.save_context === undefined ? "" : String(patch.save_context)
            }
            onChange={(e) => {
              const p = { ...patch };
              if (e.target.value) p.save_context = e.target.value === "true";
              else delete p.save_context;
              change(p);
            }}
          >
            <option value="">Наследуется</option>
            <option value="true">Да, для локального инспектора</option>
            <option value="false">Только метаданные и хеш запроса</option>
          </select>
        </label>
        <label>
          Проверка ответа
          <select
            value={String(patch.quality?.mode || "")}
            onChange={(e) => {
              const q = { ...patch.quality };
              if (e.target.value) q.mode = e.target.value;
              else delete q.mode;
              change({ ...patch, quality: q });
            }}
          >
            <option value="">Наследуется</option>
            <option value="warn">Предупреждать</option>
            <option value="retry">Повторить ограниченно</option>
            <option value="reject">Отклонить</option>
          </select>
        </label>
      </fieldset>
      <details>
        <summary>Визуальный конструктор блоков</summary>
        <p>
          Порядок списка определяет порядок отправки. При нехватке окна
          исключаются незакреплённые блоки с меньшим приоритетом, затем более
          ранние. Последнее видимое сообщение защищено.
        </p>
        <fieldset disabled={!ready}>
          {blocks.map((block, index) => (
            <div className="panel" key={block.id}>
              <label>
                Имя блока
                <input
                  value={block.name}
                  onChange={(e) => blockChange(index, { name: e.target.value })}
                />
              </label>
              <label>
                Источник
                <select
                  value={block.source}
                  onChange={(e) =>
                    blockChange(index, { source: e.target.value })
                  }
                >
                  {sources.map((s, i) => (
                    <option key={s} value={s}>
                      {labels[i]}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                <input
                  type="checkbox"
                  checked={block.enabled !== false}
                  onChange={(e) =>
                    blockChange(index, { enabled: e.target.checked })
                  }
                />
                Включён
              </label>
              <label>
                <input
                  type="checkbox"
                  checked={block.pinned}
                  onChange={(e) =>
                    blockChange(index, { pinned: e.target.checked })
                  }
                />
                Закреплён
              </label>
              <label>
                Приоритет
                <input
                  type="number"
                  min={0}
                  max={100}
                  value={block.priority}
                  onChange={(e) =>
                    blockChange(index, { priority: Number(e.target.value) })
                  }
                />
              </label>
              <label>
                Шаблон
                <textarea
                  value={block.template || ""}
                  onChange={(e) =>
                    blockChange(index, { template: e.target.value })
                  }
                  placeholder="{{char}}, {{user}}, {{content}}, {{original}}"
                />
              </label>
              <label>
                Условие
                <select
                  value={block.condition?.field || ""}
                  onChange={(e) =>
                    blockChange(index, {
                      condition: e.target.value
                        ? {
                            field: e.target.value,
                            op: "equals",
                            value: [
                              "group",
                              "has_world",
                              "has_memory",
                            ].includes(e.target.value)
                              ? true
                              : e.target.value === "role"
                                ? "generation"
                                : "balanced",
                          }
                        : null,
                    })
                  }
                >
                  <option value="">Всегда</option>
                  {[
                    "group",
                    "has_world",
                    "has_memory",
                    "performance",
                    "role",
                  ].map((v) => (
                    <option key={v} value={v}>
                      {v}
                    </option>
                  ))}
                </select>
              </label>
              {block.condition && (
                <>
                  <label>
                    Сравнение
                    <select
                      value={block.condition.op}
                      onChange={(e) =>
                        blockChange(index, {
                          condition: {
                            ...block.condition!,
                            op: e.target.value,
                          },
                        })
                      }
                    >
                      <option value="equals">Равно</option>
                      <option value="not_equals">Не равно</option>
                    </select>
                  </label>
                  <label>
                    Значение условия
                    <input
                      value={String(block.condition.value)}
                      onChange={(e) =>
                        blockChange(index, {
                          condition: {
                            ...block.condition!,
                            value: [
                              "group",
                              "has_world",
                              "has_memory",
                            ].includes(block.condition!.field)
                              ? e.target.value === "true"
                              : e.target.value,
                          },
                        })
                      }
                    />
                  </label>
                </>
              )}
              <button
                disabled={index === 0}
                onClick={() => {
                  const next = [...blocks];
                  [next[index - 1], next[index]] = [
                    next[index],
                    next[index - 1],
                  ];
                  change({ ...patch, blocks: next });
                }}
              >
                Выше
              </button>
              <button
                disabled={index === blocks.length - 1}
                onClick={() => {
                  const next = [...blocks];
                  [next[index + 1], next[index]] = [
                    next[index],
                    next[index + 1],
                  ];
                  change({ ...patch, blocks: next });
                }}
              >
                Ниже
              </button>
              <button
                onClick={() =>
                  change({
                    ...patch,
                    blocks: blocks.filter((_, i) => i !== index),
                  })
                }
              >
                Удалить блок
              </button>
            </div>
          ))}
          <button
            onClick={() =>
              change({
                ...patch,
                blocks: [
                  ...blocks,
                  {
                    id: crypto.randomUUID(),
                    name: "Свой блок",
                    source: "custom",
                    enabled: true,
                    role: "system",
                    priority: 50,
                    pinned: false,
                    template: "",
                    condition: null,
                    max_tokens: null,
                  },
                ],
              })
            }
          >
            Добавить блок
          </button>
          <button
            onClick={() => {
              const p = { ...patch };
              delete p.blocks;
              change(p);
            }}
          >
            Наследовать блоки
          </button>
        </fieldset>
      </details>
      <details>
        <summary>Экспертные настройки JSON</summary>
        <label>
          Переопределения профиля
          <textarea
            rows={18}
            value={raw}
            disabled={busy}
            onChange={(e) => setRaw(e.target.value)}
          />
        </label>
        <button
          disabled={busy}
          onClick={() => {
            try {
              const value = JSON.parse(raw);
              if (!value || Array.isArray(value) || typeof value !== "object")
                throw Error("Нужен JSON-объект.");
              change(value);
              setStatus("JSON применён к форме; сохраните профиль.");
            } catch (e) {
              setStatus((e as Error).message);
            }
          }}
        >
          Применить JSON к форме
        </button>
      </details>
      {raw !== applied && (
        <p role="status">
          JSON изменён. Примените его к форме перед сохранением.
        </p>
      )}
      <button disabled={!ready || !name.trim()} onClick={() => void save()}>
        Сохранить профиль
      </button>
      <h3>Назначения</h3>
      <label>
        Область профиля
        <select
          value={scope}
          onChange={(e) => {
            const v = e.target.value;
            setScope(v);
            setScopeId(
              v === "global" ? "default" : v === "service" ? "generation" : "",
            );
          }}
        >
          <option value="global">Общая</option>
          <option value="service">Служебная роль</option>
          <option value="character">Персонаж</option>
          <option value="story">История / группа</option>
        </select>
      </label>
      {scope === "service" ? (
        <label>
          Служебная роль
          <select value={scopeId} onChange={(e) => setScopeId(e.target.value)}>
            {[
              "generation",
              "memory",
              "director",
              "editor",
              "character",
              "world",
              "quality",
            ].map((v) => (
              <option key={v} value={v}>
                {v}
              </option>
            ))}
          </select>
        </label>
      ) : (
        scope !== "global" && (
          <label>
            Объект назначения
            <select
              value={scopeId}
              onChange={(e) => setScopeId(e.target.value)}
            >
              <option value="">Выберите</option>
              {entities.map((e) => (
                <option key={e.id} value={e.id}>
                  {e.payload.name || e.payload.fields?.name || e.id}
                </option>
              ))}
            </select>
          </label>
        )
      )}
      <label>
        Назначаемый профиль
        <select
          value={bindingProfile}
          onChange={(e) => setBindingProfile(e.target.value)}
        >
          <option value="">Снять назначение</option>
          {profiles.map((p) => (
            <option key={p.id} value={p.id}>
              {p.payload.name}
            </option>
          ))}
        </select>
      </label>
      <button disabled={busy || !scopeId} onClick={() => void bind()}>
        Сохранить назначение
      </button>
      <ul>
        {bindings.map((b, i) => (
          <li key={i}>
            {b.payload.scope} / {b.payload.scope_id}:{" "}
            {profiles.find((p) => p.id === b.payload.profile_id)?.payload
              .name || b.payload.profile_id}
          </li>
        ))}
      </ul>
      <button onClick={() => void load()}>Обновить профили и назначения</button>
      <button
        onClick={async () => {
          try {
            setResolved(
              JSON.stringify(
                await request("/profiles/resolve", "POST", {
                  ...(scope === "character"
                    ? { character_id: scopeId }
                    : scope === "story"
                      ? { story_id: scopeId }
                      : scope === "service"
                        ? { role: scopeId }
                        : {}),
                  ...(selected ? { profile_id: selected.id } : {}),
                }),
                null,
                2,
              ),
            );
          } catch (e) {
            setStatus((e as Error).message);
          }
        }}
      >
        Проверить наследование
      </button>
      {resolved && <pre className="diagnostic">{resolved}</pre>}
      <p role="status">{status}</p>
    </section>
  );
}
