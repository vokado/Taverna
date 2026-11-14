import { useEffect, useRef, useState } from "react";
import { request } from "./api";
type Job = { id: string; state: string; kind: string };
type SavedEvent = {
  sequence: number;
  data: { text?: string; message_ru?: string };
  type: string;
};
const states: Record<string, string> = {
  queued: "В очереди",
  running: "Выполняется",
  completed: "Завершена",
  failed: "Ошибка; частичный ответ сохранён",
  cancelled: "Остановлена",
  interrupted: "Прервана перезапуском",
};
export function GenerationHistory() {
  const [jobs, setJobs] = useState<Job[]>([]);
  const [text, setText] = useState("");
  const [status, setStatus] = useState("");
  const stream = useRef<EventSource | null>(null);
  async function refresh() {
    try {
      setJobs((await request<{ jobs: Job[] }>("/jobs")).jobs);
    } catch (e) {
      setStatus((e as Error).message);
    }
  }
  useEffect(() => {
    void refresh();
    return () => stream.current?.close();
  }, []);
  function open(job: Job) {
    stream.current?.close();
    setText("");
    setStatus(states[job.state] || "Сохранённая задача");
    const source = new EventSource(`/api/v1/generations/${job.id}/stream`);
    stream.current = source;
    let sequence = 0;
    for (const kind of [
      "started",
      "delta",
      "completed",
      "failed",
      "cancelled",
      "interrupted",
    ]) {
      source.addEventListener("generation." + kind, (raw) => {
        const event = JSON.parse((raw as MessageEvent).data) as SavedEvent;
        if (event.sequence <= sequence) return;
        sequence = event.sequence;
        if (kind === "delta") setText((t) => t + (event.data.text || ""));
        else if (kind === "started") setStatus("Выполняется");
        else {
          setStatus(event.data.message_ru || states[kind]);
          source.close();
          void refresh();
        }
      });
    }
    source.onerror = () => {
      setStatus(
        "Не удалось прочитать поток. Откройте сохранённый ответ повторно.",
      );
      source.close();
    };
  }
  return (
    <section>
      <h2>Сохранённые ответы и задачи</h2>
      <button onClick={refresh}>Обновить список задач</button>
      {jobs.length === 0 ? (
        <p>Сохранённых задач пока нет.</p>
      ) : (
        <ul>
          {jobs
            .filter((j) => j.kind === "llm")
            .map((j) => (
              <li key={j.id}>
                {states[j.state] || "Неизвестное состояние"}{" "}
                <button onClick={() => open(j)}>
                  Открыть сохранённый ответ
                </button>
              </li>
            ))}
        </ul>
      )}
      {status && <p role="status">{status}</p>}
      {text && <pre className="reply">{text}</pre>}
    </section>
  );
}
