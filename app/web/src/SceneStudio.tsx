import { useEffect, useRef, useState } from "react";
import type { components } from "./api-types";
import { request } from "./api";
import { useTemplateDraft } from "./useTemplateDraft";
import {
  SceneStage,
  type StageData,
  type Composition,
  type SceneLayer,
} from "./SceneStage";
import type { MediaAsset } from "./MediaLibrary";
type MediaState = components["schemas"]["MediaState"];
type Editor = { media: MediaState; reason: string };
type Bundle = StageData & { media: MediaState };
const emotions = [
  "",
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
        step="any"
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
      />
    </label>
  );
}
export function SceneStudio({
  storyId,
  branchId,
  revision,
  actors,
  events,
  worldId,
  disabled,
  active = true,
  onChanged,
  registerFlush,
}: {
  storyId: string;
  branchId: string;
  revision: number;
  actors: { id: string; name: string }[];
  events: { id: string; kind: string }[];
  worldId: string | null;
  disabled: boolean;
  active?: boolean;
  onChanged: () => Promise<void>;
  registerFlush: (fn: () => Promise<void>) => void;
}) {
  const [opened, setOpened] = useState(false),
    [stage, setStage] = useState<StageData | null>(null),
    [bundle, setBundle] = useState<Bundle | null>(null),
    [editor, setEditor] = useState<Editor | null>(null),
    [dirty, setDirty] = useState(false),
    [baseRevision, setBaseRevision] = useState(revision),
    [raw, setRaw] = useState(""),
    [busy, setBusy] = useState(false),
    [status, setStatus] = useState("");
  const [assets, setAssets] = useState<MediaAsset[]>([]),
    [query, setQuery] = useState(""),
    [imageId, setImageId] = useState(""),
    [layerId, setLayerId] = useState(""),
    [preview, setPreview] = useState(false);
  const [variantActor, setVariantActor] = useState(actors[0]?.id || ""),
    [variantImage, setVariantImage] = useState(""),
    [costume, setCostume] = useState(""),
    [pose, setPose] = useState(""),
    [emotion, setEmotion] = useState(""),
    [priority, setPriority] = useState(0);
  const [bindingAsset, setBindingAsset] = useState(""),
    [bindingKind, setBindingKind] = useState<
      "character" | "scene" | "event" | "world"
    >("character"),
    [bindingTarget, setBindingTarget] = useState(actors[0]?.id || "");
  const latest = useRef({ editor, dirty, baseRevision, raw });
  latest.current = { editor, dirty, baseRevision, raw };
  const alive = useRef(true),
    epoch = useRef(0);
  const base = `/stories/${storyId}/media`,
    draftBase = base + `/drafts/${branchId}`;
  const draft = useTemplateDraft(
    "scene",
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
    latest.current = { editor: e, dirty: d, baseRevision: v, raw: text };
    setEditor(e);
    setBaseRevision(v);
    setRaw(text);
    setDirty(d);
  }
  function edit(fn: (m: MediaState) => void) {
    const old = latest.current;
    if (!old.editor) return;
    const e = copy(old.editor);
    fn(e.media);
    replace(e, old.baseRevision, old.raw, true);
  }
  async function load(reset = false) {
    const id = ++epoch.current;
    const r = await request<Bundle>(base + `?branch_id=${branchId}`);
    if (!alive.current || id !== epoch.current) return;
    setBundle(r);
    setStage(r);
    if (reset || !latest.current.editor || !latest.current.dirty)
      replace(
        { media: r.media, reason: "Ручная настройка сцены" },
        r.branch_revision,
        "",
        false,
      );
  }
  async function loadAssets() {
    const r = await request<{ items: MediaAsset[]; total: number }>(
      "/media?limit=500&query=" + encodeURIComponent(query),
    );
    if (!alive.current) return;
    setAssets(r.items);
    if (r.total > 500)
      setStatus("Показаны500 ресурсов; уточните поиск для остальных.");
  }
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
      epoch.current++;
    };
  }, []);
  useEffect(() => {
    let valid = true;
    if (active)
      void request<StageData>(base + `/stage?branch_id=${branchId}`)
        .then((r) => {
          if (valid) setStage(r);
        })
        .catch((e) => {
          if (valid) setStatus((e as Error).message);
        });
    return () => {
      valid = false;
    };
  }, [revision, active]);
  useEffect(() => {
    if (opened) {
      void load().catch((e) => setStatus((e as Error).message));
      void loadAssets().catch((e) => setStatus((e as Error).message));
    }
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
    const r = await request<{ branch_revision: number }>(base, "PUT", {
      branch_id: branchId,
      expected_revision: old.baseRevision,
      media: old.editor.media,
      reason: old.editor.reason,
    });
    if (!alive.current) return;
    replace(old.editor, r.branch_revision, old.raw, Boolean(old.raw.trim()));
    if (!old.raw.trim()) await draft.remove();
    await onChanged();
    await load();
    setPreview(false);
    setStatus("Композиция и привязки сцены сохранены в этой ветви.");
  }
  const sceneId = bundle?.scene_id || stage?.scene_id || "",
    composition = editor?.media.compositions?.[sceneId] || bundle?.composition;
  function compositionEdit(patch: Partial<Composition>) {
    if (!composition) return;
    edit((m) => {
      m.compositions = {
        ...m.compositions,
        [sceneId]: { ...composition, ...patch },
      };
    });
  }
  const layers = composition?.layers || [],
    layer = layers.find((l) => l.id === layerId) || layers[0],
    images = assets.filter((a) => a.payload.type.startsWith("image/")),
    audio = assets.filter((a) => a.payload.type.startsWith("audio/"));
  function layerEdit(patch: Partial<SceneLayer>) {
    if (!layer) return;
    compositionEdit({
      layers: layers.map((l) => (l.id === layer.id ? { ...l, ...patch } : l)),
    });
  }
  const options = (rows: MediaAsset[], current: string) => (
    <>
      <option value="">Выберите ресурс</option>
      {current && !rows.some((a) => a.id === current) && (
        <option value={current}>{current} (вне выборки)</option>
      )}
      {rows.map((a) => (
        <option key={a.id} value={a.id}>
          {a.payload.name}
        </option>
      ))}
    </>
  );
  const bindingTargets =
    bindingKind === "character"
      ? actors
      : bindingKind === "scene"
        ? [{ id: sceneId, name: "Текущая сцена" }]
        : bindingKind === "event"
          ? events.map((e) => ({
              id: e.id,
              name: e.kind + " — " + e.id.slice(0, 8),
            }))
          : worldId
            ? [{ id: worldId, name: "Мир истории" }]
            : [];
  return (
    <>
      <SceneStage data={stage} active={active} />
      <details
        aria-label="Студия сцены"
        onToggle={(e) => {
          if (e.target === e.currentTarget) setOpened(e.currentTarget.open);
        }}
      >
        <summary>Студия сцены и атмосфера</summary>
        <p role="status">{[status, draft.status].filter(Boolean).join(" ")}</p>
        {!editor || !bundle || !composition ? (
          <button onClick={() => void action(() => load())}>
            Загрузить студию сцены
          </button>
        ) : (
          <fieldset disabled={busy || disabled}>
            <p>
              {dirty
                ? "Есть неопубликованные настройки сцены."
                : "Настройки сцены опубликованы."}{" "}
              Исходная ревизия: {baseRevision}.
            </p>
            {dirty && baseRevision !== bundle.branch_revision && (
              <p>Ветвь изменилась; исходная ревизия черновика сохранена.</p>
            )}
            <button
              onClick={() =>
                void action(async () => {
                  await draft.flush();
                  await load(true);
                })
              }
            >
              Перечитать медиасцену
            </button>
            <label>
              Причина изменения сцены
              <input
                value={editor.reason}
                onChange={(e) =>
                  replace(
                    { ...editor, reason: e.target.value },
                    baseRevision,
                    raw,
                    true,
                  )
                }
              />
            </label>
            <label>
              <input
                type="checkbox"
                checked={composition.enabled}
                onChange={(e) => compositionEdit({ enabled: e.target.checked })}
              />
              Включить медиа сцены
            </label>
            <label>
              <input
                type="checkbox"
                checked={composition.visuals_enabled}
                onChange={(e) =>
                  compositionEdit({ visuals_enabled: e.target.checked })
                }
              />
              Показывать визуальные слои
            </label>
            <label>
              <input
                type="checkbox"
                checked={composition.effects_enabled}
                onChange={(e) =>
                  compositionEdit({ effects_enabled: e.target.checked })
                }
              />
              Включить свет и эффекты
            </label>
            <label>
              <input
                type="checkbox"
                checked={composition.low_effects}
                onChange={(e) =>
                  compositionEdit({ low_effects: e.target.checked })
                }
              />
              Уменьшить эффекты этой сцены
            </label>
            <label>
              Освещение сцены
              <select
                value={composition.lighting}
                onChange={(e) =>
                  compositionEdit({
                    lighting: e.target.value as Composition["lighting"],
                  })
                }
              >
                <option value="neutral">Нейтральное</option>
                <option value="warm">Тёплое</option>
                <option value="cold">Холодное</option>
                <option value="night">Ночь</option>
              </select>
            </label>
            <label>
              Эффект сцены
              <select
                value={composition.effect}
                onChange={(e) =>
                  compositionEdit({
                    effect: e.target.value as Composition["effect"],
                  })
                }
              >
                <option value="none">Без эффекта</option>
                <option value="rain">Дождь</option>
                <option value="fog">Туман</option>
                <option value="vignette">Виньетка</option>
              </select>
            </label>
            <Num
              label="Переход сцены в миллисекундах"
              value={composition.transition_ms || 0}
              onChange={(v) => compositionEdit({ transition_ms: v })}
            />
            <label>
              Поиск ресурсов студии
              <input value={query} onChange={(e) => setQuery(e.target.value)} />
            </label>
            <button onClick={() => void action(loadAssets)}>
              Найти ресурсы студии
            </button>
            <details>
              <summary>Визуальные слои</summary>
              <label>
                Изображение нового слоя
                <select
                  value={imageId}
                  onChange={(e) => setImageId(e.target.value)}
                >
                  {options(images, imageId)}
                </select>
              </label>
              <button
                disabled={!imageId}
                onClick={() => {
                  const id = crypto.randomUUID();
                  compositionEdit({
                    layers: [
                      ...layers,
                      {
                        id,
                        name: "Новый слой",
                        asset_id: imageId,
                        enabled: true,
                        x: 50,
                        y: 50,
                        width: 100,
                        height: 100,
                        opacity: 1,
                        z: layers.length,
                        fit: "contain",
                        costume: "",
                        pose: "",
                        emotion: "auto",
                        character_id: null,
                      },
                    ],
                  });
                  setLayerId(id);
                }}
              >
                Добавить слой сцены
              </button>
              <label>
                Редактируемый слой
                <select
                  value={layer?.id || ""}
                  onChange={(e) => setLayerId(e.target.value)}
                >
                  <option value="">Выберите слой</option>
                  {layers.map((l) => (
                    <option key={l.id} value={l.id}>
                      {l.name}
                    </option>
                  ))}
                </select>
              </label>
              {layer && (
                <>
                  <label>
                    Название слоя
                    <input
                      value={layer.name}
                      onChange={(e) => layerEdit({ name: e.target.value })}
                    />
                  </label>
                  <label>
                    Изображение слоя
                    <select
                      value={layer.asset_id}
                      onChange={(e) => layerEdit({ asset_id: e.target.value })}
                    >
                      {options(images, layer.asset_id)}
                    </select>
                  </label>
                  <label>
                    NPC слоя
                    <select
                      value={layer.character_id || ""}
                      onChange={(e) =>
                        layerEdit({ character_id: e.target.value || null })
                      }
                    >
                      <option value="">Фон/иллюстрация</option>
                      {actors.map((a) => (
                        <option key={a.id} value={a.id}>
                          {a.name}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label>
                    <input
                      type="checkbox"
                      checked={layer.enabled}
                      onChange={(e) => layerEdit({ enabled: e.target.checked })}
                    />
                    Показывать выбранный слой
                  </label>
                  {(["x", "y", "width", "height", "opacity", "z"] as const).map(
                    (k, i) => (
                      <Num
                        key={k}
                        label={
                          [
                            "X слоя, %",
                            "Y слоя, %",
                            "Ширина слоя, %",
                            "Высота слоя, %",
                            "Непрозрачность слоя",
                            "Порядок слоя",
                          ][i]
                        }
                        value={layer[k] ?? 0}
                        onChange={(v) => layerEdit({ [k]: v })}
                      />
                    ),
                  )}
                  <label>
                    Заполнение слоя
                    <select
                      value={layer.fit}
                      onChange={(e) =>
                        layerEdit({ fit: e.target.value as SceneLayer["fit"] })
                      }
                    >
                      <option value="contain">Вместить</option>
                      <option value="cover">Заполнить с обрезкой</option>
                      <option value="fill">Растянуть</option>
                    </select>
                  </label>
                  <label>
                    Костюм слоя
                    <input
                      value={layer.costume || ""}
                      onChange={(e) => layerEdit({ costume: e.target.value })}
                    />
                  </label>
                  <label>
                    Поза слоя
                    <input
                      value={layer.pose || ""}
                      onChange={(e) => layerEdit({ pose: e.target.value })}
                    />
                  </label>
                  <label>
                    Эмоция слоя
                    <select
                      value={layer.emotion}
                      onChange={(e) => layerEdit({ emotion: e.target.value })}
                    >
                      <option value="auto">По состоянию NPC</option>
                      {emotions.map((e) => (
                        <option key={e} value={e}>
                          {e || "Без условия эмоции"}
                        </option>
                      ))}
                    </select>
                  </label>
                  <button
                    onClick={() =>
                      compositionEdit({
                        layers: layers.filter((l) => l.id !== layer.id),
                      })
                    }
                  >
                    Удалить выбранный слой
                  </button>
                </>
              )}
            </details>
            <details>
              <summary>Варианты внешности NPC</summary>
              <label>
                NPC варианта
                <select
                  value={variantActor}
                  onChange={(e) => setVariantActor(e.target.value)}
                >
                  {actors.map((a) => (
                    <option key={a.id} value={a.id}>
                      {a.name}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                Изображение варианта
                <select
                  value={variantImage}
                  onChange={(e) => setVariantImage(e.target.value)}
                >
                  {options(images, variantImage)}
                </select>
              </label>
              <label>
                Костюм варианта
                <input
                  value={costume}
                  onChange={(e) => setCostume(e.target.value)}
                />
              </label>
              <label>
                Поза варианта
                <input value={pose} onChange={(e) => setPose(e.target.value)} />
              </label>
              <label>
                Эмоция варианта
                <select
                  value={emotion}
                  onChange={(e) => setEmotion(e.target.value)}
                >
                  {emotions.map((e) => (
                    <option key={e} value={e}>
                      {e || "Любая эмоция"}
                    </option>
                  ))}
                </select>
              </label>
              <Num
                label="Приоритет варианта"
                value={priority}
                onChange={setPriority}
              />
              <button
                disabled={!variantActor || !variantImage}
                onClick={() =>
                  edit((m) => {
                    m.variants = [
                      ...(m.variants || []),
                      {
                        id: crypto.randomUUID(),
                        character_id: variantActor,
                        asset_id: variantImage,
                        costume,
                        pose,
                        emotion,
                        priority,
                      },
                    ];
                  })
                }
              >
                Добавить вариант внешности
              </button>
              <p>
                Выбирается самый конкретный подходящий вариант, затем приоритет.
                Пустые условия подходят любому состоянию. Отсутствующий в сцене
                NPC скрывается.
              </p>
              {editor.media.variants?.map((v) => (
                <p key={v.id}>
                  {actors.find((a) => a.id === v.character_id)?.name}:{" "}
                  {v.costume || "любой костюм"}; {v.pose || "любая поза"};{" "}
                  {v.emotion || "любая эмоция"}
                  <button
                    onClick={() =>
                      edit((m) => {
                        m.variants = m.variants?.filter((x) => x.id !== v.id);
                      })
                    }
                  >
                    Удалить вариант {v.id?.slice(0, 8)}
                  </button>
                </p>
              ))}
            </details>
            <details>
              <summary>Музыка и звуки окружения</summary>
              {(["music", "ambient"] as const).map((track, i) => (
                <fieldset key={track}>
                  <legend>{i === 0 ? "Музыка" : "Окружение"}</legend>
                  <label>
                    {i === 0 ? "Музыка сцены" : "Звук окружения"}
                    <select
                      value={composition[track]?.asset_id || ""}
                      onChange={(e) =>
                        compositionEdit({
                          [track]: {
                            ...composition[track],
                            asset_id: e.target.value || null,
                          },
                        })
                      }
                    >
                      {options(audio, composition[track]?.asset_id || "")}
                    </select>
                  </label>
                  <label>
                    <input
                      type="checkbox"
                      checked={composition[track]?.enabled}
                      onChange={(e) =>
                        compositionEdit({
                          [track]: {
                            ...composition[track],
                            enabled: e.target.checked,
                          },
                        })
                      }
                    />
                    {i === 0 ? "Включить музыку" : "Включить окружение"}
                  </label>
                  <Num
                    label={
                      i === 0
                        ? "Громкость музыки (0…1)"
                        : "Громкость окружения (0…1)"
                    }
                    value={composition[track]?.volume ?? 0.5}
                    onChange={(v) =>
                      compositionEdit({
                        [track]: { ...composition[track], volume: v },
                      })
                    }
                  />
                  <label>
                    <input
                      type="checkbox"
                      checked={composition[track]?.loop}
                      onChange={(e) =>
                        compositionEdit({
                          [track]: {
                            ...composition[track],
                            loop: e.target.checked,
                          },
                        })
                      }
                    />
                    {i === 0 ? "Повторять музыку" : "Повторять окружение"}
                  </label>
                </fieldset>
              ))}
            </details>
            <details>
              <summary>Привязки к истории</summary>
              <label>
                Ресурс привязки
                <select
                  value={bindingAsset}
                  onChange={(e) => setBindingAsset(e.target.value)}
                >
                  {options(assets, bindingAsset)}
                </select>
              </label>
              <label>
                Тип привязки
                <select
                  value={bindingKind}
                  onChange={(e) => {
                    setBindingKind(e.target.value as typeof bindingKind);
                    setBindingTarget("");
                  }}
                >
                  <option value="character">Персонаж</option>
                  <option value="scene">Сцена</option>
                  <option value="event">Событие</option>
                  <option value="world">Мир</option>
                </select>
              </label>
              <label>
                Цель привязки
                <select
                  value={bindingTarget}
                  onChange={(e) => setBindingTarget(e.target.value)}
                >
                  <option value="">Выберите цель</option>
                  {bindingTargets.map((t) => (
                    <option key={t.id} value={t.id}>
                      {t.name}
                    </option>
                  ))}
                </select>
              </label>
              <button
                disabled={!bindingAsset || !bindingTarget}
                onClick={() =>
                  edit((m) => {
                    m.bindings = [
                      ...(m.bindings || []),
                      {
                        id: crypto.randomUUID(),
                        asset_id: bindingAsset,
                        kind: bindingKind,
                        target_id: bindingTarget,
                      },
                    ];
                  })
                }
              >
                Добавить привязку медиа
              </button>
              {editor.media.bindings?.map((b) => (
                <p key={b.id}>
                  {b.kind}: {b.target_id}; ресурс {b.asset_id}
                  <button
                    onClick={() =>
                      edit((m) => {
                        m.bindings = m.bindings?.filter((x) => x.id !== b.id);
                      })
                    }
                  >
                    Удалить привязку {b.id?.slice(0, 8)}
                  </button>
                </p>
              ))}
            </details>
            <button onClick={() => setPreview((v) => !v)}>
              {preview
                ? "Скрыть неопубликованный просмотр"
                : "Предпросмотр формы сцены"}
            </button>
            {preview && (
              <>
                <p>
                  Неопубликованный просмотр: условия вариантов NPC применяются
                  после backend-проверки/публикации.
                </p>
                <SceneStage
                  active={active}
                  data={{
                    ...bundle,
                    composition,
                    resolved_layers: composition.layers || [],
                  }}
                />
              </>
            )}
            <button onClick={() => void action(save)}>
              Сохранить студию сцены
            </button>
            <details>
              <summary>JSON и черновики студии сцены</summary>
              <button
                onClick={() =>
                  replace(
                    editor,
                    baseRevision,
                    JSON.stringify(editor.media, null, 2),
                    true,
                  )
                }
              >
                Заполнить JSON студии сцены
              </button>
              <label>
                JSON студии сцены
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
                    const valid = await request<MediaState>(
                      draftBase + "/validate",
                      "POST",
                      JSON.parse(raw),
                    );
                    replace(
                      { ...editor, media: valid },
                      baseRevision,
                      raw,
                      true,
                    );
                    setStatus("JSON сцены проверен; публикация отдельно.");
                  })
                }
              >
                Проверить JSON сцены
              </button>
              <button onClick={() => void action(draft.saveSeparate)}>
                Сохранить отдельный черновик сцены
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
                      })
                    }
                  >
                    Восстановить черновик сцены {d.id.slice(0, 8)}
                  </button>
                </p>
              ))}
            </details>
          </fieldset>
        )}
      </details>
    </>
  );
}
