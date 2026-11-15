export type OwnerReference = {
  world_id: string | null;
  story_id: string | null;
  branch_id: string | null;
  scene_id: string | null;
  event_id: string | null;
  message_id: string | null;
};
const fields = {
  world_id: "ID мира",
  story_id: "ID истории",
  branch_id: "ID ветви",
  scene_id: "ID сцены",
  event_id: "ID события",
  message_id: "ID сообщения",
};
export function OwnerReferences({
  value,
  onChange,
}: {
  value: OwnerReference[];
  onChange: (v: OwnerReference[]) => void;
}) {
  return (
    <details>
      <summary>Привязки к мирам, сценам и сообщениям</summary>
      <p>
        Эти записи доступны владельцу. Привязка не передаёт материал в знания
        NPC автоматически.
      </p>
      {value.map((r, i) => (
        <fieldset key={i}>
          <legend>Привязка {i + 1}</legend>
          {Object.entries(fields).map(([key, label]) => (
            <label key={key}>
              {label}
              <input
                value={r[key as keyof OwnerReference] || ""}
                onChange={(e) =>
                  onChange(
                    value.map((v, n) =>
                      n === i ? { ...v, [key]: e.target.value || null } : v,
                    ),
                  )
                }
              />
            </label>
          ))}
          <button onClick={() => onChange(value.filter((_, n) => n !== i))}>
            Удалить привязку
          </button>
        </fieldset>
      ))}
      <button
        onClick={() =>
          onChange([
            ...value,
            {
              world_id: null,
              story_id: null,
              branch_id: null,
              scene_id: null,
              event_id: null,
              message_id: null,
            },
          ])
        }
      >
        Добавить привязку
      </button>
    </details>
  );
}
