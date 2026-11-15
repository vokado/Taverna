import { useEffect, useState } from "react";
import { request } from "./api";

type Props = {
  storyId: string;
  branchId: string;
  revision: number;
  characterId: string;
  model: string;
  profileId: string;
  selectProfile: (id: string) => void;
  traces: string[];
  busy: boolean;
};
export function ContextInspector(props: Props) {
  const [profiles, setProfiles] = useState<
    Array<{ id: string; payload: { name: string } }>
  >([]);
  const [output, setOutput] = useState("");
  const [error, setError] = useState("");
  const [traceId, setTraceId] = useState("");
  const [jobs, setJobs] = useState<Array<{ id: string; state: string }>>([]);
  const [jobId, setJobId] = useState("");
  const [jobTraces, setJobTraces] = useState<string[]>([]);
  useEffect(() => {
    setOutput("");
    setJobTraces([]);
    setJobs([]);
    setJobId("");
    setTraceId("");
  }, [props.storyId, props.branchId]);
  async function loadJobs() {
    try {
      setJobs(
        (
          await request<{ jobs: typeof jobs }>(
            `/jobs?story_id=${props.storyId}`,
          )
        ).jobs,
      );
    } catch (e) {
      setError((e as Error).message);
    }
  }
  async function journal() {
    try {
      const events: Array<{
        sequence: number;
        type: string;
        data: { trace_id?: string };
      }> = [];
      let after = 0;
      while (true) {
        const page = await request<{ events: typeof events }>(
          `/generations/${jobId}/events?after=${after}`,
        );
        events.push(...page.events);
        if (page.events.length < 100) break;
        after = page.events.at(-1)!.sequence;
      }
      setJobTraces([
        ...new Set(
          events.flatMap((e) =>
            e.type === "generation.context" && e.data.trace_id
              ? [e.data.trace_id]
              : [],
          ),
        ),
      ]);
      setOutput(JSON.stringify(events, null, 2));
      setError("");
    } catch (e) {
      setError((e as Error).message);
    }
  }
  async function load() {
    try {
      setProfiles(
        (await request<{ profiles: typeof profiles }>("/profiles")).profiles,
      );
    } catch (e) {
      setError((e as Error).message);
    }
  }
  useEffect(() => {
    void load();
  }, []);
  async function preview() {
    try {
      setOutput(
        JSON.stringify(
          await request(`/stories/${props.storyId}/context-preview`, "POST", {
            branch_id: props.branchId,
            expected_revision: props.revision,
            character_id: props.characterId,
            ...(props.model ? { model_id: props.model } : {}),
            profile_id: props.profileId || null,
          }),
          null,
          2,
        ),
      );
      setError("");
    } catch (e) {
      setError((e as Error).message);
    }
  }
  async function actual() {
    try {
      setOutput(JSON.stringify(await request(`/contexts/${traceId}`), null, 2));
      setError("");
    } catch (e) {
      setError((e as Error).message);
    }
  }
  return (
    <details>
      <summary>Профиль и инспектор контекста</summary>
      <label>
        Профиль этой генерации
        <select
          disabled={props.busy}
          value={props.profileId}
          onChange={(e) => props.selectProfile(e.target.value)}
        >
          <option value="">По наследованию</option>
          {profiles.map((p) => (
            <option key={p.id} value={p.id}>
              {p.payload.name}
            </option>
          ))}
        </select>
      </label>
      <button disabled={props.busy} onClick={() => void load()}>
        Обновить список профилей
      </button>
      <p>
        Полный контекст содержит личные тексты. Он доступен только в локальном
        сеансе. Предпросмотр не проверяет текущее окно LM Studio; реальная
        отправка проверяет его повторно.
      </p>
      <button
        disabled={props.busy || !props.characterId}
        onClick={() => void preview()}
      >
        Предпросмотр контекста
      </button>
      <label>
        Контекст ответа
        <select value={traceId} onChange={(e) => setTraceId(e.target.value)}>
          <option value="">Выберите сохранённый ответ</option>
          {[...new Set([...props.traces, ...jobTraces])].map((id) => (
            <option value={id} key={id}>
              {id}
            </option>
          ))}
        </select>
      </label>
      <button onClick={() => void loadJobs()}>Обновить задачи истории</button>
      <label>
        Задача истории
        <select value={jobId} onChange={(e) => setJobId(e.target.value)}>
          <option value="">Выберите задачу</option>
          {jobs.map((j) => (
            <option key={j.id} value={j.id}>
              {j.id} · {j.state}
            </option>
          ))}
        </select>
      </label>
      <button disabled={!jobId} onClick={() => void journal()}>
        Журнал попыток и решений
      </button>
      <button disabled={!traceId} onClick={() => void actual()}>
        Показать отправленный контекст
      </button>
      {error && <p role="alert">{error}</p>}
      {output && <pre className="diagnostic">{output}</pre>}
    </details>
  );
}
