import { useState } from "react";
import { download, upload } from "./api";
type Preview = {
  story_id: string;
  sha256: string;
  name: string;
  document_count: number;
  asset_count: number;
  conflicts: string[];
  reused: string[];
  warnings: string[];
};
export function StoryPackagePanel({
  storyId,
  disabled,
  onImported,
}: {
  storyId: string | null;
  disabled: boolean;
  onImported: (id: string) => Promise<void>;
}) {
  const [file, setFile] = useState<File | null>(null),
    [preview, setPreview] = useState<Preview | null>(null),
    [status, setStatus] = useState(""),
    [busy, setBusy] = useState(false);
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
  return (
    <details aria-label="Пакет истории">
      <summary>Перенос всей истории с ветвями и состояниями</summary>
      <p>
        Пакет сохраняет ID, все ветви, снимки состояний, память, мир и
        PNG-портреты. Отличающиеся существующие данные не заменяются. Лимит
        ZIP16МиБ, распакованных данных64МиБ; другие типы медиа подключаются
        адаптером библиотеки.
      </p>
      <p role="status">{status}</p>
      <fieldset disabled={busy || disabled}>
        <button
          disabled={!storyId}
          onClick={() =>
            void action(() =>
              download(`/stories/${storyId}/export-package`, {}),
            )
          }
        >
          Экспорт всей истории ZIP
        </button>
        <label>
          ZIP-пакет истории
          <input
            type="file"
            accept=".zip"
            onChange={(e) => {
              const next = e.target.files?.[0] || null;
              setPreview(null);
              if (next && next.size > 16 * 1024 * 1024) {
                setStatus("ZIP превышает16МиБ.");
                setFile(null);
              } else {
                setFile(next);
                setStatus("");
              }
            }}
          />
        </label>
        <button
          disabled={!file}
          onClick={() =>
            void action(async () =>
              setPreview(
                await upload<Preview>("/story-packages/preview", file!),
              ),
            )
          }
        >
          Предпросмотр пакета истории
        </button>
        {preview && (
          <>
            <h4>{preview.name}</h4>
            <p>
              Документов: {preview.document_count}. Изображений:{" "}
              {preview.asset_count}. Совпадающих локальных документов:{" "}
              {preview.reused.length}.
            </p>
            {preview.warnings.map((w, i) => (
              <p key={i}>{w}</p>
            ))}
            {preview.conflicts.length > 0 && (
              <p>
                Конфликтующие ID: {preview.conflicts.join(", ")}. Изменения не
                будут опубликованы.
              </p>
            )}
            <button
              disabled={!!preview.conflicts.length || !file}
              onClick={() =>
                void action(async () => {
                  const r = await upload<{
                    story_id: string;
                    already_present: boolean;
                  }>("/story-packages/import", file!, {
                    "X-Confirm-Import": "true",
                    "X-Package-SHA256": preview.sha256,
                  });
                  await onImported(r.story_id);
                  setStatus(
                    r.already_present
                      ? "История уже присутствует без отличий."
                      : "История и все ветви импортированы.",
                  );
                  setPreview(null);
                })
              }
            >
              Подтвердить импорт всей истории
            </button>
          </>
        )}
      </fieldset>
    </details>
  );
}
