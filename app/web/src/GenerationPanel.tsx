import { useEffect, useRef, useState } from "react";
import { request } from "./api";
type Model = { id: string; name: string; type: string };
type StreamEvent = {
  sequence: number;
  data: {
    text?: string;
    message_ru?: string;
    duration_seconds?: number;
    tokens_per_second?: number;
  };
};
export function GenerationPanel() {
  const [endpoint, setEndpoint] = useState("http://127.0.0.1:1234");
  const [revision, setRevision] = useState(0);
  const [models, setModels] = useState<Model[]>([]);
  const [model, setModel] = useState("");
  const [status, setStatus] = useState("Нажмите «Проверить соединение».");
  const [prompt, setPrompt] = useState("");
  const [text, setText] = useState("");
  const [reasoning, setReasoning] = useState("");
  const [busy, setBusy] = useState(false);
  const [job, setJob] = useState("");
  const [metrics, setMetrics] = useState("");
  const stream = useRef<EventSource | null>(null);
  useEffect(() => {
    void request<{ endpoint: string; revision: number }>("/models/connection")
      .then((c) => {
        setEndpoint(c.endpoint);
        setRevision(c.revision);
      })
      .catch((e) => setStatus(e.message));
    return () => stream.current?.close();
  }, []);
  async function check() {
    try {
      const saved = await request<{ revision: number }>(
        "/models/connection",
        "PUT",
        { endpoint, expected_revision: revision },
      );
      setRevision(saved.revision);
      const result = await request<{ models: Model[]; message_ru: string }>(
        "/models/status",
      );
      const loaded = result.models.filter((m) => m.type === "llm");
      setModels(loaded);
      setModel(loaded[0]?.id || "");
      setStatus(result.message_ru);
    } catch (e) {
      setStatus((e as Error).message);
    }
  }
  async function generate() {
    setBusy(true);
    setText("");
    setReasoning("");
    setMetrics("");
    setStatus("Запрос поставлен в очередь…");
    try {
      const result = await request<{ generation_id: string }>(
        "/generations",
        "POST",
        {
          model_id: model,
          messages: [{ role: "user", content: prompt }],
          parameters: { max_tokens: 1024 },
        },
        { "Idempotency-Key": crypto.randomUUID() },
      );
      setJob(result.generation_id);
      const source = new EventSource(
        `/api/v1/generations/${result.generation_id}/stream`,
      );
      stream.current = source;
      let sequence = 0;
      for (const kind of [
        "started",
        "delta",
        "reasoning",
        "metrics",
        "completed",
        "failed",
        "cancelled",
        "interrupted",
      ]) {
        source.addEventListener("generation." + kind, (raw) => {
          const event = JSON.parse((raw as MessageEvent).data) as StreamEvent;
          if (event.sequence <= sequence) return;
          sequence = event.sequence;
          if (kind === "started") setStatus("Модель отвечает…");
          if (kind === "delta") setText((t) => t + (event.data.text || ""));
          if (kind === "reasoning")
            setReasoning((t) => t + (event.data.text || ""));
          if (kind === "metrics")
            setMetrics(
              `Время: ${(event.data.duration_seconds || 0).toFixed(1)} с` +
                (event.data.tokens_per_second
                  ? ` · ${event.data.tokens_per_second.toFixed(1)} токенов/с`
                  : " · точное число токенов не предоставлено"),
            );
          if (
            ["completed", "failed", "cancelled", "interrupted"].includes(kind)
          ) {
            setBusy(false);
            source.close();
            setStatus(
              kind === "completed"
                ? "Ответ сохранён."
                : event.data.message_ru ||
                    "Генерация остановлена. Частичный ответ сохранён.",
            );
          }
        });
      }
      source.onerror = () =>
        setStatus(
          "Поток отключён. Восстанавливаем соединение без повторной генерации.",
        );
    } catch (e) {
      setStatus((e as Error).message);
      setBusy(false);
    }
  }
  async function cancel() {
    try {
      await request(`/generations/${job}/cancel`, "POST");
    } catch (e) {
      setStatus((e as Error).message);
    }
  }
  return (
    <section>
      <h2>Подключение к LM Studio</h2>
      <p>
        Загрузите модель в LM Studio самостоятельно. Таверна не управляет
        загрузкой моделей.
      </p>
      <label>
        Адрес локального сервера
        <input
          value={endpoint}
          onChange={(e) => setEndpoint(e.target.value)}
          disabled={busy}
        />
      </label>
      <button onClick={check} disabled={busy}>
        Проверить соединение
      </button>
      <p role="status">{status}</p>
      {models.length > 0 && (
        <>
          <label>
            Загруженная модель
            <select
              value={model}
              onChange={(e) => setModel(e.target.value)}
              disabled={busy}
            >
              {models.map((m) => (
                <option key={m.id} value={m.id}>
                  {m.name}
                </option>
              ))}
            </select>
          </label>
          <label>
            Проверочный запрос
            <textarea
              rows={4}
              value={prompt}
              onChange={(e) => setPrompt(e.target.value)}
              disabled={busy}
            />
          </label>
          <p>
            Это проверка соединения. Истории и персонажи добавляются на
            следующих этапах.
          </p>
          <button
            onClick={generate}
            disabled={busy || !prompt.trim() || !model}
          >
            Отправить запрос
          </button>
          {busy && <button onClick={cancel}>Остановить</button>}
        </>
      )}
      {text && <pre className="reply">{text}</pre>}
      {reasoning && (
        <details>
          <summary>Служебные рассуждения модели</summary>
          <pre className="reply">{reasoning}</pre>
        </details>
      )}
      {metrics && <p>{metrics}</p>}
    </section>
  );
}
