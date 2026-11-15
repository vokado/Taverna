import type { Row } from "./MemoryPanel";
type Display = {
  results?: Array<{
    id: string;
    kind: string;
    text: string;
    record: Row | null;
    message: { id: string; text: string } | null;
  }>;
  entries?: Row[];
  conflicts?: Array<{ claim_key: string; records: Row[] }>;
  sources?: Array<{ id: string; message: { text: string } | null }>;
  history?: Array<{
    reason: string;
    changed_at: string;
    previous: { text: string; version: number };
  }>;
  mode?: string;
  message_ru?: string;
  entry_count?: number;
  record_count?: number;
  proposal_id?: string;
  anchors?: Record<string, Array<{ name: string; message_id: string | null }>>;
};
export function MemoryResults({
  text,
  onChoose,
  onMessage,
  onResolve,
}: {
  text: string;
  onChoose: (row: Row) => void;
  onMessage: (id: string) => void;
  onResolve: (row: Row, decision: "keep_both" | "supersede") => void;
}) {
  let value: Display = {};
  try {
    value = JSON.parse(text);
  } catch {
    /* The raw diagnostic remains available. */
  }
  return (
    <section aria-label="Результат операции памяти">
      {value.message_ru && <p>{value.message_ru}</p>}
      {value.mode && (
        <p>
          {value.mode === "hybrid"
            ? "Смысловой и текстовый поиск"
            : "Текстовый поиск"}
        </p>
      )}
      {value.results && (
        <>
          <h4>Найдено в памяти и источниках: {value.results.length}</h4>
          {value.results.length === 0 && <p>Совпадений нет.</p>}
          {value.results.map((row) => (
            <article className="memory-result" key={row.id}>
              <p>{row.text}</p>
              {row.record ? (
                <button onClick={() => onChoose(row.record!)}>
                  Открыть найденную запись
                </button>
              ) : (
                row.message && (
                  <button onClick={() => onMessage(row.message!.id)}>
                    Перейти к исходному сообщению
                  </button>
                )
              )}
            </article>
          ))}
        </>
      )}
      {value.entries && (
        <>
          <h4>Хронология</h4>
          <ol>
            {value.entries.map((row) => (
              <li key={row.id}>
                <time>{row.story_time || "Дата записи в журнале"}</time>
                <p>{row.text}</p>
                <button onClick={() => onChoose(row)}>
                  Открыть запись хронологии
                </button>
              </li>
            ))}
          </ol>
          {Object.entries(value.anchors || {}).flatMap(([kind, items]) =>
            items.map((anchor, i) => (
              <p key={kind + i}>
                {anchor.name}
                {anchor.message_id && (
                  <button onClick={() => onMessage(anchor.message_id!)}>
                    Перейти к отметке
                  </button>
                )}
              </p>
            )),
          )}
        </>
      )}
      {value.conflicts && (
        <>
          <h4>Несогласованные утверждения: {value.conflicts.length}</h4>
          {value.conflicts.map((group, i) => (
            <article className="memory-result" key={i}>
              <strong>{group.claim_key}</strong>
              {group.records.map((row) => (
                <div key={row.id}>
                  <p>{row.text}</p>
                  <button onClick={() => onChoose(row)}>
                    Просмотреть версию
                  </button>
                  <button onClick={() => onResolve(row, "supersede")}>
                    Оставить это утверждение действующим
                  </button>
                </div>
              ))}
              <button onClick={() => onResolve(group.records[0], "keep_both")}>
                Сохранить обе версии утверждения
              </button>
            </article>
          ))}
        </>
      )}
      {value.sources && (
        <>
          <h4>Исходные сообщения</h4>
          {value.sources.map((source) => (
            <article key={source.id}>
              {source.message ? (
                <>
                  <p>{source.message.text}</p>
                  <button onClick={() => onMessage(source.id)}>
                    Открыть источник в чате
                  </button>
                </>
              ) : (
                <p>Текст источника недоступен в выбранной области.</p>
              )}
            </article>
          ))}
        </>
      )}
      {value.history && (
        <>
          <h4>Изменения записи</h4>
          <ol>
            {value.history.map((change, i) => (
              <li key={i}>
                <p>
                  {change.reason} ·{" "}
                  {new Date(change.changed_at).toLocaleString("ru-RU")}
                </p>
                <p>
                  Версия {change.previous.version}: {change.previous.text}
                </p>
              </li>
            ))}
          </ol>
        </>
      )}
      {value.entry_count !== undefined && (
        <p>Проиндексировано записей и сообщений: {value.entry_count}.</p>
      )}
      {value.record_count !== undefined && (
        <p>
          Предложено записей: {value.record_count}. Просмотрите предложение
          перед внесением в память.
        </p>
      )}
      <details>
        <summary>Технические подробности результата</summary>
        <pre className="diagnostic">{text}</pre>
      </details>
    </section>
  );
}
