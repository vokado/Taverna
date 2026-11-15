import { useEffect, useRef, useState } from "react";
import { request } from "./api";
import { ChatImportPanel } from "./ChatImportPanel";
import { EventTransferPanel } from "./EventTransferPanel";
import { ContextInspector } from "./ContextInspector";
import { MemoryPanel } from "./MemoryPanel";
import { RpgPanel } from "./RpgPanel";
import { DirectorPanel } from "./DirectorPanel";
import { StoryWorld } from "./StoryWorld";
import { StoryPackagePanel } from "./StoryPackagePanel";

type Card = {
  id: string;
  payload: { fields: Record<string, unknown>; portrait_id: string | null };
};
type Story = {
  id: string;
  revision: number;
  payload: {
    name: string;
    active_branch: string;
    character_snapshots: Record<string, { card: Card["payload"] }>;
  };
};
type Anchor = {
  id: string;
  name: string;
  message_id: string | null;
  state_id: string;
};
type Branch = {
  id: string;
  revision: number;
  payload: {
    name: string;
    parent_id: string | null;
    fork_message_id: string | null;
    participants: string[];
    speaker_order: string[];
    view_mode: "messenger" | "literary" | "novel";
    chapters: Anchor[];
    bookmarks: Anchor[];
    checkpoints: Anchor[];
  };
};
type Message = {
  id: string;
  role: "user" | "assistant";
  author_id: string | null;
  text: string;
  created_at: string;
  version: number;
  source_message_ids: string[];
  partial?: boolean;
  context_trace_id?: string;
  quality_audit?: Array<{ flags: Array<{ code: string; message_ru: string }> }>;
};
type Bundle = {
  story: Story;
  branch: Branch;
  state: { payload: Record<string, unknown> };
};
type Event = {
  sequence: number;
  data: {
    text?: string;
    message_ru?: string;
    character_id?: string;
    branch_id?: string;
    show_reasoning?: boolean;
  };
};

export function Chat({
  openStory,
}: {
  openStory?: { id: string; nonce: number } | null;
}) {
  const [stories, setStories] = useState<Story[]>([]);
  const [cards, setCards] = useState<Card[]>([]);
  const [name, setName] = useState("");
  const [selected, setSelected] = useState<string[]>([]);
  const [greeting, setGreeting] = useState(0);
  const [current, setCurrent] = useState<Bundle | null>(null);
  const [branches, setBranches] = useState<Branch[]>([]);
  const [messages, setMessages] = useState<Message[]>([]);
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  const [search, setSearch] = useState("");
  const [status, setStatus] = useState("");
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(false);
  const [models, setModels] = useState<Array<{ id: string; name: string }>>([]);
  const [profileId, setProfileId] = useState("");
  const [model, setModel] = useState("");
  const [speaker, setSpeaker] = useState("");
  const [turns, setTurns] = useState(2);
  const [job, setJob] = useState("");
  const [partial, setPartial] = useState("");
  const [reasoning, setReasoning] = useState("");
  const [showReasoning, setShowReasoning] = useState(false);
  const [partialAvailable, setPartialAvailable] = useState(false);
  const [partialSpeaker, setPartialSpeaker] = useState("");
  const [edit, setEdit] = useState<{ message: Message; text: string } | null>(
    null,
  );
  const [anchorName, setAnchorName] = useState("");
  const [anchorKind, setAnchorKind] = useState("bookmark");
  const [comparison, setComparison] = useState<Message[] | null>(null);
  const [novelIndex, setNovelIndex] = useState(0);
  const stream = useRef<EventSource | null>(null);
  const loadSequence = useRef(0);
  const memoryFlush = useRef<(() => Promise<void>) | null>(null);
  const rpgFlush = useRef<(() => Promise<void>) | null>(null);
  const directorFlush = useRef<(() => Promise<void>) | null>(null);
  async function refresh() {
    try {
      const [s, c] = await Promise.all([
        request<{ stories: Story[] }>("/stories"),
        request<{ characters: Card[] }>("/characters"),
      ]);
      setStories(s.stories);
      setCards(c.characters);
    } catch (e) {
      setStatus((e as Error).message);
    }
  }
  useEffect(() => {
    void refresh();
    return () => stream.current?.close();
  }, []);
  useEffect(() => {
    if (openStory) void load(openStory.id);
  }, [openStory?.nonce]);
  useEffect(() => {
    const listener = (event: BeforeUnloadEvent) => {
      if (input) {
        event.preventDefault();
        event.returnValue = "";
      }
    };
    document.documentElement.dataset.chatDirty = String(Boolean(input));
    window.addEventListener("beforeunload", listener);
    return () => {
      window.removeEventListener("beforeunload", listener);
      delete document.documentElement.dataset.chatDirty;
    };
  }, [input]);
  async function load(
    id: string,
    pageOffset?: number,
    query = search,
    preservePosition = false,
  ) {
    const sequence = ++loadSequence.current;
    setLoading(true);
    try {
      await Promise.all([
        memoryFlush.current?.(),
        directorFlush.current?.(),
        rpgFlush.current?.(),
      ]);
      const bundle = await request<Bundle>(`/stories/${id}`);
      const base = `/stories/${id}/messages?branch_id=${bundle.branch.id}&query=${encodeURIComponent(query)}`;
      const first = await request<{ items: Message[]; total: number }>(
        base + "&limit=1",
      );
      const at =
        pageOffset === undefined
          ? Math.max(0, first.total - 100)
          : Math.max(0, pageOffset);
      const [page, tree] = await Promise.all([
        request<{ items: Message[]; total: number }>(
          base + `&offset=${at}&limit=100`,
        ),
        request<{ branches: Branch[] }>(`/stories/${id}/branches`),
      ]);
      if (sequence !== loadSequence.current) return;
      setCurrent(bundle);
      setBranches(tree.branches);
      setMessages(page.items);
      setTotal(page.total);
      setOffset(at);
      setNovelIndex((old) =>
        preservePosition
          ? Math.max(0, Math.min(old, page.items.length - 1))
          : Math.max(0, page.items.length - 1),
      );
      setSpeaker((s) =>
        bundle.branch.payload.participants.includes(s)
          ? s
          : bundle.branch.payload.speaker_order[0] || "",
      );
    } catch (e) {
      setStatus((e as Error).message);
    } finally {
      if (sequence === loadSequence.current) setLoading(false);
    }
  }
  useEffect(() => {
    if (!current || busy || loading) return;
    let active = true,
      inFlight = false;
    const storyId = current.story.id,
      branchId = current.branch.id,
      known = current.branch.revision;
    const timer = setInterval(() => {
      if (inFlight || !active) return;
      inFlight = true;
      void request<{ revision: number; active_branch: string | null }>(
        `/stories/${storyId}/branches/${branchId}/revision?known_revision=${known}`,
      )
        .then(async (result) => {
          if (!active || result.revision === known) return;
          if (result.active_branch !== branchId) {
            active = false;
            setStatus(
              "Активная ветвь изменена в другой вкладке. Черновики сохранены; перечитайте историю.",
            );
            return;
          }
          const follow =
            offset + messages.length >= total &&
            (current.branch.payload.view_mode !== "novel" ||
              novelIndex >= messages.length - 1);
          await load(storyId, follow ? undefined : offset, search, !follow);
        })
        .catch((error) => {
          if (active) setStatus((error as Error).message);
        })
        .finally(() => {
          inFlight = false;
        });
    }, 1500);
    return () => {
      active = false;
      clearInterval(timer);
    };
  }, [
    current?.story.id,
    current?.branch.id,
    current?.branch.revision,
    busy,
    loading,
    offset,
    messages.length,
    total,
    novelIndex,
    search,
  ]);
  async function create() {
    setBusy(true);
    try {
      const story = await request<Story>("/stories", "POST", {
        name,
        character_ids: selected,
        greeting_index: greeting,
      });
      setInput("");
      setSearch("");
      await load(story.id, undefined, "");
      await refresh();
      setStatus("Независимая история создана.");
    } catch (e) {
      setStatus((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  function author(message: Message) {
    return message.role === "user"
      ? "Вы"
      : String(
          current?.story.payload.character_snapshots[message.author_id || ""]
            ?.card.fields.name || "NPC",
        );
  }
  function revision() {
    return {
      expected_revision: current!.branch.revision,
      branch_id: current!.branch.id,
    };
  }
  async function send() {
    if (!current) return;
    setBusy(true);
    try {
      await request(`/stories/${current.story.id}/messages`, "POST", {
        ...revision(),
        text: input,
      });
      setInput("");
      await load(current.story.id);
      await refresh();
      setStatus(
        "Сообщение сохранено. Нажмите «Ответ NPC», чтобы запросить реплику выбранного персонажа.",
      );
    } catch (e) {
      setStatus((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function loadModels() {
    try {
      const response = await request<{
        models: Array<{ id: string; name: string; type: string }>;
      }>("/models");
      const list = response.models.filter((m) => m.type === "llm");
      setModels(list);
      setModel(list[0]?.id || "");
      if (!list.length) setStatus("Загрузите модель в LM Studio вручную.");
    } catch (e) {
      setStatus((e as Error).message);
    }
  }
  function follow(jobId: string, storyId: string) {
    stream.current?.close();
    setJob(jobId);
    setBusy(true);
    setPartial("");
    setPartialAvailable(false);
    setStatus("Генерация в очереди…");
    const source = new EventSource(`/api/v1/generations/${jobId}/stream`);
    stream.current = source;
    let sequence = 0,
      failures = 0;
    for (const kind of [
      "started",
      "attempt_started",
      "context",
      "reasoning",
      "delta",
      "speaker",
      "turn_completed",
      "branch_created",
      "completed",
      "failed",
      "cancelled",
      "interrupted",
    ]) {
      source.addEventListener("generation." + kind, (raw) => {
        const event = JSON.parse((raw as MessageEvent).data) as Event;
        if (event.sequence <= sequence) return;
        sequence = event.sequence;
        failures = 0;
        if (kind === "started") setStatus("Модель отвечает…");
        if (kind === "attempt_started") {
          setPartial("");
          setReasoning("");
        }
        if (kind === "context")
          setShowReasoning(Boolean(event.data.show_reasoning));
        if (kind === "reasoning")
          setReasoning((t) => t + (event.data.text || ""));
        if (kind === "delta") setPartial((t) => t + (event.data.text || ""));
        if (kind === "speaker") {
          setPartial("");
          setPartialSpeaker(event.data.character_id || "");
        }
        if (kind === "turn_completed") {
          setPartial("");
          void load(storyId);
        }
        if (kind === "branch_created") void load(storyId);
        if (
          ["completed", "failed", "cancelled", "interrupted"].includes(kind)
        ) {
          source.close();
          setBusy(false);
          setPartialAvailable(kind !== "completed");
          setStatus(
            kind === "completed"
              ? "Ответ сохранён в истории."
              : event.data.message_ru ||
                  "Генерация остановлена; частичный ответ сохранён отдельно.",
          );
          void load(storyId);
          void refresh();
        }
      });
    }
    source.onerror = () => {
      failures++;
      if (failures >= 3) {
        source.close();
        setBusy(false);
        setPartialAvailable(true);
        setStatus(
          "Поток недоступен. Сохранённые части остаются в задачах; откройте приложение повторно.",
        );
      } else setStatus("Переподключаем поток без повторной генерации…");
    };
  }
  async function generate(group = false) {
    if (!current) return;
    setBusy(true);
    try {
      const response = await request<{ generation_id: string }>(
        `/stories/${current.story.id}/${group ? "group-generations" : "generations"}`,
        "POST",
        {
          ...revision(),
          ...(model ? { model_id: model } : {}),
          profile_id: profileId || null,
          character_id: speaker,
          ...(group ? { turns } : {}),
        },
        { "Idempotency-Key": crypto.randomUUID() },
      );
      setPartialSpeaker(speaker);
      follow(response.generation_id, current.story.id);
    } catch (e) {
      setStatus((e as Error).message);
      setBusy(false);
    }
  }
  async function regenerate(
    message: Message,
    action: "regenerate" | "continue" | "fragment",
  ) {
    if (!current) return;
    let fragment: string | undefined;
    if (action === "fragment") {
      const selected = window.getSelection()?.toString() || "";
      fragment =
        prompt(
          "Укажите точный неповторяющийся фрагмент для замены:",
          selected,
        ) || undefined;
      if (!fragment) return;
    }
    setBusy(true);
    try {
      const result = await request<{ generation_id: string }>(
        `/stories/${current.story.id}/regenerate`,
        "POST",
        {
          ...revision(),
          message_id: message.id,
          ...(model ? { model_id: model } : {}),
          profile_id: profileId || null,
          character_id: message.author_id,
          action,
          ...(fragment ? { fragment } : {}),
        },
        { "Idempotency-Key": crypto.randomUUID() },
      );
      setPartialSpeaker(message.author_id || "");
      follow(result.generation_id, current.story.id);
    } catch (e) {
      setStatus((e as Error).message);
      setBusy(false);
    }
  }
  async function switchBranch(branchId: string) {
    if (!current) return;
    if (
      input &&
      !confirm(
        "Текст сообщения ещё не отправлен. Переключить ветвь, сохранив текст в форме?",
      )
    )
      return;
    setBusy(true);
    try {
      await request(
        `/stories/${current.story.id}/branches/${branchId}/activate`,
        "POST",
        {
          expected_revision: current.story.revision,
          branch_id: current.branch.id,
        },
      );
      await load(current.story.id);
      setComparison(null);
    } catch (e) {
      setStatus((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function fork(message?: Message) {
    if (!current) return;
    setBusy(true);
    try {
      await request(`/stories/${current.story.id}/branches`, "POST", {
        ...revision(),
        name: "Альтернативная ветвь",
        through_id: message?.id || null,
      });
      await load(current.story.id);
      setStatus("Ветвь создана. Оригинал сохранён.");
    } catch (e) {
      setStatus((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function saveEdit() {
    if (!current || !edit) return;
    setBusy(true);
    try {
      await request(
        `/stories/${current.story.id}/messages/${edit.message.id}`,
        "PATCH",
        { ...revision(), text: edit.text },
      );
      setEdit(null);
      await load(current.story.id);
      setStatus(
        "Правка сохранена в новой ветви. Последующие сообщения остались в оригинале.",
      );
    } catch (e) {
      setStatus((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function remove(message: Message) {
    if (
      !current ||
      !confirm(
        "Создать ветвь до этого сообщения? Оно и все последующие сообщения останутся в оригинальной ветви. Производные состояния откатятся к выбранной границе.",
      )
    )
      return;
    setBusy(true);
    try {
      await request(
        `/stories/${current.story.id}/messages/${message.id}`,
        "DELETE",
        { ...revision(), confirmed: true },
      );
      await load(current.story.id);
      setStatus("Создана ветвь до удалённого сообщения. Оригинал доступен.");
    } catch (e) {
      setStatus((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function branchSettings(patch: Record<string, unknown>) {
    if (!current) return;
    setBusy(true);
    try {
      await request(`/stories/${current.story.id}/branch`, "PATCH", {
        ...revision(),
        ...patch,
      });
      await load(current.story.id, offset);
    } catch (e) {
      setStatus((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function anchor() {
    if (!current) return;
    setBusy(true);
    try {
      await request(`/stories/${current.story.id}/anchors`, "POST", {
        ...revision(),
        name: anchorName,
        kind: anchorKind,
      });
      setAnchorName("");
      await load(current.story.id, offset);
    } catch (e) {
      setStatus((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function compare(branchId: string) {
    if (!current) return;
    try {
      const result = await request<{ items: Message[] }>(
        `/stories/${current.story.id}/messages?branch_id=${branchId}&offset=${offset}&limit=100`,
      );
      setComparison(result.items);
    } catch (e) {
      setStatus((e as Error).message);
    }
  }
  async function savePartial() {
    if (!current) return;
    try {
      await request(
        `/stories/${current.story.id}/partials/${job}/apply`,
        "POST",
        revision(),
      );
      setPartialAvailable(false);
      setPartial("");
      await load(current.story.id);
      setStatus("Частичный ответ явно добавлен в историю.");
    } catch (e) {
      setStatus((e as Error).message);
    }
  }
  async function jump(anchor: Anchor) {
    if (anchor.message_id) await jumpMessage(anchor.message_id);
  }
  async function jumpMessage(messageId: string) {
    if (!current) return;
    try {
      const position = await request<{ index: number }>(
        `/stories/${current.story.id}/messages/${messageId}/position?branch_id=${current.branch.id}`,
      );
      setSearch("");
      await load(current.story.id, Math.floor(position.index / 100) * 100, "");
      setNovelIndex(position.index % 100);
    } catch (e) {
      setStatus((e as Error).message);
    }
  }
  async function restore(anchor: Anchor) {
    if (
      !current ||
      !confirm(
        "Восстановить контрольную точку в новой ветви? Текущая линия сохранится.",
      )
    )
      return;
    setBusy(true);
    try {
      await request(
        `/stories/${current.story.id}/anchors/${anchor.id}/restore`,
        "POST",
        revision(),
      );
      await load(current.story.id);
      setStatus("Контрольная точка восстановлена в новой ветви.");
    } catch (e) {
      setStatus((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  const visible =
    current?.branch.payload.view_mode === "novel"
      ? messages[novelIndex]
        ? [messages[novelIndex]]
        : []
      : messages;
  return (
    <div className="chat-feature">
      <section>
        <h2>Истории и чаты</h2>
        <button onClick={refresh} disabled={busy}>
          Обновить истории
        </button>
        <label>
          Сохранённая история
          <select
            disabled={busy}
            value={current?.story.id || ""}
            onChange={(e) => {
              if (e.target.value) void load(e.target.value);
            }}
          >
            <option value="">Выберите историю</option>
            {stories.map((s) => (
              <option key={s.id} value={s.id}>
                {s.payload.name}
              </option>
            ))}
          </select>
        </label>
        <details>
          <summary>Новая независимая история</summary>
          <label>
            Название истории
            <input
              value={name}
              onChange={(e) => setName(e.target.value)}
              maxLength={200}
              disabled={busy}
            />
          </label>
          <fieldset disabled={busy}>
            <legend>Участники из локальной библиотеки</legend>
            {cards.length ? (
              cards.map((card) => (
                <label className="inline" key={card.id}>
                  <input
                    type="checkbox"
                    checked={selected.includes(card.id)}
                    onChange={(e) => {
                      setSelected((ids) =>
                        e.target.checked
                          ? [...ids, card.id]
                          : ids.filter((id) => id !== card.id),
                      );
                      setGreeting(0);
                    }}
                  />
                  {String(card.payload.fields.name)}
                </label>
              ))
            ) : (
              <p>Сначала создайте персонажа.</p>
            )}
          </fieldset>
          {selected[0] && (
            <label>
              Стартовое приветствие
              <select
                value={greeting}
                onChange={(e) => setGreeting(Number(e.target.value))}
                disabled={busy}
              >
                {[
                  String(
                    cards.find((c) => c.id === selected[0])?.payload.fields
                      .first_mes || "Без приветствия",
                  ),
                  ...((cards.find((c) => c.id === selected[0])?.payload.fields
                    .alternate_greetings as string[]) || []),
                ].map((g, i) => (
                  <option key={i} value={i}>
                    {g.slice(0, 100) || "Без приветствия"}
                  </option>
                ))}
              </select>
            </label>
          )}
          <button
            onClick={create}
            disabled={busy || !name.trim() || !selected.length}
          >
            Создать историю
          </button>
        </details>
        <p role="status">
          {status ||
            "Без LM Studio доступны сохранённые истории и редактирование."}
        </p>
        <StoryPackagePanel
          storyId={current?.story.id || null}
          disabled={busy || loading}
          onImported={async (id) => {
            setSearch("");
            await load(id, undefined, "");
            await refresh();
          }}
        />
        <ChatImportPanel
          cards={cards}
          onImported={async (id) => {
            setSearch("");
            await load(id, undefined, "");
            await refresh();
          }}
        />
      </section>
      {current && (
        <>
          <section className="chat-toolbar">
            <EventTransferPanel
              stories={stories}
              target={{
                storyId: current.story.id,
                branchId: current.branch.id,
                revision: current.branch.revision,
              }}
              disabled={busy}
              onApplied={() => load(current.story.id)}
            />
            <StoryWorld
              key={"world/" + current.story.id + "/" + current.branch.id}
              storyId={current.story.id}
              branchId={current.branch.id}
              revision={current.branch.revision}
              disabled={busy || loading}
              onChanged={() => load(current.story.id)}
            />
            <DirectorPanel
              key={"director/" + current.story.id + "/" + current.branch.id}
              storyId={current.story.id}
              branchId={current.branch.id}
              revision={current.branch.revision}
              actors={[
                ...current.branch.payload.speaker_order,
                ...Object.keys(
                  current.story.payload.character_snapshots,
                ).filter(
                  (id) => !current.branch.payload.speaker_order.includes(id),
                ),
              ].map((id) => ({
                id,
                name: String(
                  current.story.payload.character_snapshots[id].card.fields
                    .name,
                ),
              }))}
              disabled={busy || loading}
              onChanged={() => load(current.story.id)}
              registerFlush={(flush) => {
                directorFlush.current = flush;
              }}
            />
            <RpgPanel
              key={"rpg/" + current.story.id + "/" + current.branch.id}
              storyId={current.story.id}
              branchId={current.branch.id}
              revision={current.branch.revision}
              disabled={busy || loading}
              onChanged={() => load(current.story.id)}
              registerFlush={(flush) => {
                rpgFlush.current = flush;
              }}
            />
            <MemoryPanel
              key={current.story.id + "/" + current.branch.id}
              storyId={current.story.id}
              branchId={current.branch.id}
              revision={current.branch.revision}
              actors={[
                ...current.branch.payload.speaker_order,
                ...Object.keys(
                  current.story.payload.character_snapshots,
                ).filter(
                  (id) => !current.branch.payload.speaker_order.includes(id),
                ),
              ].map((id) => ({
                id,
                name: String(
                  current.story.payload.character_snapshots[id].card.fields
                    .name,
                ),
              }))}
              messages={messages}
              disabled={busy || loading}
              onChanged={() => load(current.story.id)}
              onMessage={jumpMessage}
              registerFlush={(flush) => {
                memoryFlush.current = flush;
              }}
            />
            <h2>{current.story.payload.name}</h2>
            <label>
              Активная ветвь
              <select
                value={current.branch.id}
                disabled={busy}
                onChange={(e) => void switchBranch(e.target.value)}
              >
                {branches.map((b) => (
                  <option key={b.id} value={b.id}>
                    {b.payload.name}
                  </option>
                ))}
              </select>
            </label>
            <button onClick={() => void fork()} disabled={busy}>
              Новая ветвь от текущей точки
            </button>
            <button onClick={() => void load(current.story.id)} disabled={busy}>
              Перечитать историю
            </button>
            <label>
              Вид чата
              <select
                value={current.branch.payload.view_mode}
                disabled={busy}
                onChange={(e) =>
                  void branchSettings({ view_mode: e.target.value })
                }
              >
                <option value="messenger">Мессенджер</option>
                <option value="literary">Литературный</option>
                <option value="novel">Визуальная новелла</option>
              </select>
            </label>
            <label>
              Поиск по истории
              <input
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                disabled={busy}
              />
            </label>
            <button
              disabled={busy}
              onClick={() => void load(current.story.id, 0)}
            >
              Найти в истории
            </button>
            <details>
              <summary>Сцена и очередность NPC</summary>
              {Object.entries(current.story.payload.character_snapshots).map(
                ([id, entry]) => (
                  <label className="inline" key={id}>
                    <input
                      type="checkbox"
                      checked={current.branch.payload.participants.includes(id)}
                      disabled={busy}
                      onChange={(e) =>
                        void branchSettings({
                          participants: e.target.checked
                            ? [...current.branch.payload.participants, id]
                            : current.branch.payload.participants.filter(
                                (cid) => cid !== id,
                              ),
                        })
                      }
                    />
                    {String(entry.card.fields.name)}
                  </label>
                ),
              )}
              <ol>
                {current.branch.payload.speaker_order.map((id, i) => (
                  <li key={id}>
                    {String(
                      current.story.payload.character_snapshots[id]?.card.fields
                        .name,
                    )}{" "}
                    <button
                      disabled={busy || i === 0}
                      onClick={() => {
                        const order = [...current.branch.payload.speaker_order];
                        [order[i - 1], order[i]] = [order[i], order[i - 1]];
                        void branchSettings({ speaker_order: order });
                      }}
                    >
                      Выше в очереди
                    </button>
                  </li>
                ))}
              </ol>
            </details>
            <details>
              <summary>Ветви и сюжетные связи</summary>
              <ul>
                {branches.map((b) => (
                  <li key={b.id}>
                    {b.payload.parent_id
                      ? `${branches.find((p) => p.id === b.payload.parent_id)?.payload.name || "Родительская ветвь"} → `
                      : ""}
                    {b.payload.name}{" "}
                    <button
                      disabled={busy || b.id === current.branch.id}
                      onClick={() => void compare(b.id)}
                    >
                      Сравнить сообщения
                    </button>
                  </li>
                ))}
              </ul>
              {comparison && (
                <div className="comparison">
                  <div>
                    <h3>Активная ветвь</h3>
                    {messages.map((m) => (
                      <pre key={m.id} className="reply">
                        {m.text}
                      </pre>
                    ))}
                  </div>
                  <div>
                    <h3>Сравниваемая ветвь</h3>
                    {comparison.map((m) => (
                      <pre key={m.id} className="reply">
                        {m.text}
                      </pre>
                    ))}
                  </div>
                </div>
              )}
            </details>
            <details>
              <summary>Главы, закладки и контрольные точки</summary>
              <label>
                Название отметки
                <input
                  value={anchorName}
                  onChange={(e) => setAnchorName(e.target.value)}
                  maxLength={200}
                />
              </label>
              <label>
                Тип отметки
                <select
                  value={anchorKind}
                  onChange={(e) => setAnchorKind(e.target.value)}
                >
                  <option value="bookmark">Закладка</option>
                  <option value="chapter">Глава</option>
                  <option value="checkpoint">Контрольная точка</option>
                </select>
              </label>
              <button disabled={busy || !anchorName.trim()} onClick={anchor}>
                Сохранить отметку текущей точки
              </button>
              {[
                ...current.branch.payload.chapters,
                ...current.branch.payload.bookmarks,
                ...current.branch.payload.checkpoints,
              ].map((a) => (
                <p key={a.id}>
                  {a.name}{" "}
                  <button
                    disabled={busy || !a.message_id}
                    onClick={() => void jump(a)}
                  >
                    Перейти к сообщению
                  </button>
                  {current.branch.payload.checkpoints.some(
                    (p) => p.id === a.id,
                  ) && (
                    <button disabled={busy} onClick={() => void restore(a)}>
                      Восстановить контрольную точку
                    </button>
                  )}
                </p>
              ))}
            </details>
          </section>
          <section>
            <div className="pagination">
              <button
                disabled={busy || offset === 0}
                onClick={() =>
                  void load(current.story.id, Math.max(0, offset - 100))
                }
              >
                Предыдущие сообщения
              </button>
              <span>
                {total
                  ? `${offset + 1}–${Math.min(offset + 100, total)} из ${total}`
                  : "Сообщений нет"}
              </span>
              <button
                disabled={busy || offset + 100 >= total}
                onClick={() => void load(current.story.id, offset + 100)}
              >
                Следующие сообщения
              </button>
            </div>
            <div
              className={"chat-messages " + current.branch.payload.view_mode}
            >
              {visible.map((message) => (
                <article
                  className={"chat-message " + message.role}
                  key={message.id}
                >
                  <header>
                    <strong>{author(message)}</strong>
                    <time>
                      {new Date(message.created_at).toLocaleString("ru-RU")}
                    </time>
                  </header>
                  {current.branch.payload.view_mode === "novel" &&
                    message.author_id &&
                    current.story.payload.character_snapshots[message.author_id]
                      ?.card.portrait_id && (
                      <img
                        className="novel-portrait"
                        alt={author(message)}
                        src={`/api/v1/stories/${current.story.id}/portraits/${message.author_id}`}
                        onError={(e) => {
                          e.currentTarget.hidden = true;
                        }}
                      />
                    )}
                  <pre className="reply">{message.text}</pre>
                  {message.partial && <p>Сохранённый частичный ответ</p>}
                  {message.quality_audit?.at(-1)?.flags.map((flag, i) => (
                    <p key={i}>Проверка ответа: {flag.message_ru}</p>
                  ))}
                  <div className="message-actions">
                    <button
                      disabled={busy}
                      onClick={() => {
                        setEdit({ message, text: message.text });
                      }}
                    >
                      Редактировать
                    </button>
                    <button
                      disabled={busy}
                      onClick={() => void remove(message)}
                    >
                      Удалить в новой ветви
                    </button>
                    <button disabled={busy} onClick={() => void fork(message)}>
                      Ветвь отсюда
                    </button>
                    {message.role === "assistant" && (
                      <>
                        <button
                          disabled={busy}
                          onClick={() => void regenerate(message, "regenerate")}
                        >
                          Другой вариант
                        </button>
                        <button
                          disabled={busy}
                          onClick={() => void regenerate(message, "continue")}
                        >
                          Продолжить ответ
                        </button>
                        <button
                          disabled={busy}
                          onClick={() => void regenerate(message, "fragment")}
                        >
                          Заменить фрагмент
                        </button>
                      </>
                    )}
                  </div>
                </article>
              ))}
            </div>
            {current.branch.payload.view_mode === "novel" && (
              <div className="pagination">
                <button
                  disabled={novelIndex === 0}
                  onClick={() => setNovelIndex((i) => i - 1)}
                >
                  Предыдущая реплика
                </button>
                <button
                  disabled={novelIndex >= messages.length - 1}
                  onClick={() => setNovelIndex((i) => i + 1)}
                >
                  Следующая реплика
                </button>
              </div>
            )}
            {partial && (
              <article className="partial-answer">
                <h3>
                  {String(
                    current.story.payload.character_snapshots[partialSpeaker]
                      ?.card.fields.name || "Ответ модели",
                  )}{" "}
                  — {busy ? "поток" : "частичный ответ"}
                </h3>
                <pre className="reply">{partial}</pre>
              </article>
            )}
            {partialAvailable && (
              <button onClick={savePartial} disabled={busy || !partial}>
                Явно сохранить частичный ответ в историю
              </button>
            )}
            <label>
              Ваше сообщение
              <textarea
                rows={4}
                value={input}
                onChange={(e) => setInput(e.target.value)}
                disabled={busy}
              />
            </label>
            <button onClick={send} disabled={busy || !input.trim()}>
              Отправить сообщение
            </button>
            <fieldset>
              <legend>Ответы персонажей</legend>
              <label>
                <input
                  type="checkbox"
                  checked={showReasoning}
                  onChange={(e) => setShowReasoning(e.target.checked)}
                />
                Показывать отдельный канал рассуждений
              </label>
              {showReasoning && reasoning && (
                <pre className="diagnostic">{reasoning}</pre>
              )}
              <ContextInspector
                storyId={current.story.id}
                branchId={current.branch.id}
                revision={current.branch.revision}
                characterId={speaker}
                model={model}
                profileId={profileId}
                selectProfile={setProfileId}
                traces={messages.flatMap((m) =>
                  m.context_trace_id ? [m.context_trace_id] : [],
                )}
                busy={busy}
              />
              <button onClick={loadModels} disabled={busy}>
                Проверить модели для чата
              </button>
              <label>
                Модель чата
                <select
                  value={model}
                  onChange={(e) => setModel(e.target.value)}
                  disabled={busy}
                >
                  <option value="">Из профиля (если назначена)</option>
                  {models.map((m) => (
                    <option key={m.id} value={m.id}>
                      {m.name}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                Говорящий NPC
                <select
                  value={speaker}
                  onChange={(e) => setSpeaker(e.target.value)}
                  disabled={busy}
                >
                  {current.branch.payload.speaker_order.map((id) => (
                    <option key={id} value={id}>
                      {String(
                        current.story.payload.character_snapshots[id]?.card
                          .fields.name,
                      )}
                    </option>
                  ))}
                </select>
              </label>
              <button
                onClick={() => void generate()}
                disabled={busy || !speaker}
              >
                Ответ NPC
              </button>
              <label>
                Количество реплик в групповой сцене
                <input
                  type="number"
                  min={1}
                  max={16}
                  value={turns}
                  onChange={(e) => setTurns(Number(e.target.value))}
                  disabled={busy}
                />
              </label>
              <button
                onClick={() => void generate(true)}
                disabled={busy || !speaker || turns < 1 || turns > 16}
              >
                Диалог NPC друг с другом
              </button>
              {busy && job && (
                <button
                  onClick={() =>
                    void request(`/generations/${job}/cancel`, "POST").catch(
                      (e) => setStatus(e.message),
                    )
                  }
                >
                  Остановить ответ
                </button>
              )}
            </fieldset>
          </section>
        </>
      )}
      {edit && (
        <div className="dialog-backdrop">
          <section
            role="dialog"
            aria-modal="true"
            aria-label="Редактирование сообщения"
          >
            <h2>Изменить сообщение в новой ветви</h2>
            <p>
              Оригинальная ветвь и последующие сообщения сохранятся. Производные
              состояния будут пересчитаны от выбранной точки.
            </p>
            <label>
              Новый текст сообщения
              <textarea
                rows={8}
                value={edit.text}
                onChange={(e) => setEdit({ ...edit, text: e.target.value })}
              />
            </label>
            <button onClick={saveEdit} disabled={busy || !edit.text.trim()}>
              Сохранить правку в новой ветви
            </button>
            <button disabled={busy} onClick={() => setEdit(null)}>
              Отмена
            </button>
          </section>
        </div>
      )}
    </div>
  );
}
