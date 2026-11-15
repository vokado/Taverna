import { useEffect, useRef, useState } from "react";
import type { components } from "./api-types";
export type Composition = components["schemas"]["Composition"];
export type SceneLayer = components["schemas"]["Layer"];
export type StageData = {
  composition: Composition;
  resolved_layers: SceneLayer[];
  scene_id: string;
  branch_revision: number;
};
export function SceneStage({
  data,
  active = true,
}: {
  data: StageData | null;
  active?: boolean;
}) {
  const music = useRef<HTMLAudioElement>(null),
    ambient = useRef<HTMLAudioElement>(null),
    [status, setStatus] = useState("");
  const [economy, setEconomy] = useState(
    document.documentElement.dataset.economy === "true",
  );
  const [reduced, setReduced] = useState(
    matchMedia("(prefers-reduced-motion: reduce)").matches,
  );
  useEffect(() => {
    const observer = new MutationObserver(() =>
      setEconomy(document.documentElement.dataset.economy === "true"),
    );
    observer.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ["data-economy"],
    });
    const media = matchMedia("(prefers-reduced-motion: reduce)");
    const changed = () => setReduced(media.matches);
    media.addEventListener("change", changed);
    return () => {
      observer.disconnect();
      media.removeEventListener("change", changed);
    };
  }, []);
  const c = data?.composition,
    quiet = Boolean(c?.low_effects || economy || reduced),
    enabled = Boolean(c?.enabled && active);
  useEffect(() => {
    if (music.current) music.current.volume = c?.music?.volume ?? 0.5;
    if (ambient.current) ambient.current.volume = c?.ambient?.volume ?? 0.5;
  }, [
    c?.music?.volume,
    c?.ambient?.volume,
    enabled,
    c?.music?.asset_id,
    c?.ambient?.asset_id,
  ]);
  useEffect(() => {
    const musicElement = music.current,
      ambientElement = ambient.current;
    return () => {
      musicElement?.pause();
      ambientElement?.pause();
    };
  }, [
    enabled,
    c?.music?.asset_id,
    c?.music?.enabled,
    c?.ambient?.asset_id,
    c?.ambient?.enabled,
  ]);
  useEffect(() => {
    if (!enabled) {
      music.current?.pause();
      ambient.current?.pause();
    }
  }, [enabled]);
  useEffect(() => {
    const pause = () => {
      if (document.hidden) {
        music.current?.pause();
        ambient.current?.pause();
      }
    };
    document.addEventListener("visibilitychange", pause);
    return () => {
      document.removeEventListener("visibilitychange", pause);
    };
  }, []);
  if (!data || !c || !enabled) return null;
  const layers = c.visuals_enabled
    ? data.resolved_layers.filter((l) => l.enabled)
    : [];
  const hasMusic = c.music?.enabled && c.music.asset_id,
    hasAmbient = c.ambient?.enabled && c.ambient.asset_id;
  if (!layers.length && !hasMusic && !hasAmbient) return null;
  return (
    <section aria-label="Медиасцена">
      <p role="status">{status}</p>
      {!!layers.length && (
        <div
          className="scene-stage"
          aria-label={
            quiet ? "Сцена с уменьшенными эффектами" : "Слоистая сцена"
          }
        >
          {layers
            .slice()
            .sort((a, b) => (a.z || 0) - (b.z || 0))
            .map((l) => (
              <img
                key={l.id}
                className="scene-layer"
                alt={l.name}
                src={`/api/v1/media/${l.asset_id}/${quiet ? "thumbnail" : "file"}`}
                onError={() =>
                  setStatus(
                    "Ресурс слоя не удалось показать. Текст истории сохранён.",
                  )
                }
                style={{
                  left: `${l.x ?? 50}%`,
                  top: `${l.y ?? 50}%`,
                  width: `${l.width ?? 50}%`,
                  height: `${l.height ?? 100}%`,
                  opacity: l.opacity ?? 1,
                  zIndex: l.z ?? 0,
                  objectFit: l.fit || "contain",
                  transition: quiet
                    ? "none"
                    : `opacity ${c.transition_ms || 0}ms, left ${c.transition_ms || 0}ms, top ${c.transition_ms || 0}ms`,
                }}
              />
            ))}
          {!quiet && c.effects_enabled && (
            <>
              <div className={`scene-light ${c.lighting || "neutral"}`} />
              <div className={`scene-effect ${c.effect || "none"}`} />
            </>
          )}
        </div>
      )}
      {hasMusic && (
        <div>
          <p>Музыка сцены</p>
          <audio
            ref={music}
            controls
            preload="none"
            loop={c.music?.loop}
            src={`/api/v1/media/${c.music!.asset_id}/file`}
            onError={() =>
              setStatus(
                "Музыка недоступна или формат не декодируется браузером.",
              )
            }
          />
          <button
            onClick={() => {
              void music.current
                ?.play()
                .catch(() =>
                  setStatus(
                    "Браузер не разрешил воспроизведение. Нажмите Play на аудиопанели.",
                  ),
                );
            }}
          >
            Воспроизвести музыку
          </button>
          <button onClick={() => music.current?.pause()}>
            Остановить музыку
          </button>
        </div>
      )}
      {hasAmbient && (
        <div>
          <p>Окружение сцены</p>
          <audio
            ref={ambient}
            controls
            preload="none"
            loop={c.ambient?.loop}
            src={`/api/v1/media/${c.ambient!.asset_id}/file`}
            onError={() =>
              setStatus(
                "Звук окружения недоступен или не декодируется браузером.",
              )
            }
          />
          <button
            onClick={() => {
              void ambient.current
                ?.play()
                .catch(() => setStatus("Нажмите Play для звука окружения."));
            }}
          >
            Воспроизвести окружение
          </button>
          <button onClick={() => ambient.current?.pause()}>
            Остановить окружение
          </button>
        </div>
      )}
    </section>
  );
}
