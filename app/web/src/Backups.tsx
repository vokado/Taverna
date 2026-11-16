import { useEffect, useState } from "react";
import { downloadGet, request } from "./api";

type BackupItem = { name: string; bytes: number; modified_at: number };
type BackupList = { items: BackupItem[] };
type BackupVerification = {
  name: string;
  verified: boolean;
  files: number;
  generation: number;
};

function size(bytes: number) {
  return (
    new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 1 }).format(
      bytes / (1024 * 1024),
    ) + " МиБ"
  );
}

export function Backups({ visible }: { visible: boolean }) {
  const [items, setItems] = useState<BackupItem[]>([]);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("Список резервных копий не загружен.");

  async function refresh() {
    const result = await request<BackupList>("/backups");
    setItems(result.items);
  }

  useEffect(() => {
    if (!visible) return;
    void refresh().catch((error) => setMessage((error as Error).message));
  }, [visible]);

  async function createBackup() {
    setBusy(true);
    setMessage("Создаём и проверяем архив…");
    try {
      const result = await request<{ name: string; bytes: number }>(
        "/backups",
        "POST",
        { reason: "manual" },
      );
      await refresh();
      setMessage(
        "Резервная копия создана и проверена: " +
          result.name +
          " (" +
          size(result.bytes) +
          ").",
      );
    } catch (error) {
      setMessage((error as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function verifyBackup(name: string) {
    setBusy(true);
    setMessage("Проверяем целостность архива…");
    try {
      const result = await request<BackupVerification>(
        "/backups/" + encodeURIComponent(name) + "/verify",
        "POST",
      );
      setMessage(
        result.verified
          ? "Архив проверен. Поколение " +
              result.generation +
              ", файлов: " +
              result.files +
              "."
          : "Архив не прошёл проверку.",
      );
    } catch (error) {
      setMessage((error as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function saveBackup(name: string) {
    setBusy(true);
    try {
      await downloadGet("/backups/" + encodeURIComponent(name) + "/download");
      setMessage("Скачивание архива запрошено. Сохраните его также на другом диске.");
    } catch (error) {
      setMessage((error as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <section aria-label="Резервные копии">
      <h2>Локальные резервные копии</h2>
      <p>
        Архив содержит опубликованные JSON-документы, связанные изображения,
        библиотечные материалы и файлы установленных расширений. Архив
        проверяется до выдачи.
      </p>
      <button disabled={busy} onClick={() => void createBackup()}>
        Создать резервную копию
      </button>
      <button
        className="secondary"
        disabled={busy}
        onClick={() => void refresh().catch((e) => setMessage(e.message))}
      >
        Обновить список
      </button>
      <p role="status">{message}</p>
      {items.length === 0 ? (
        <p>Локальных резервных копий пока нет.</p>
      ) : (
        <ul>
          {items.map((item) => (
            <li key={item.name}>
              <strong>{item.name}</strong> · {size(item.bytes)}
              <div>
                <button
                  disabled={busy}
                  onClick={() => void verifyBackup(item.name)}
                >
                  Проверить
                </button>
                <button
                  className="secondary"
                  disabled={busy}
                  onClick={() => void saveBackup(item.name)}
                >
                  Скачать
                </button>
              </div>
            </li>
          ))}
        </ul>
      )}
      <p>
        Автоматическое расписание и мастер восстановления в текущую работающую
        установку ещё разрабатываются. Для безопасности восстановление пока
        предусмотрено только в новый каталог при закрытом приложении.
      </p>
    </section>
  );
}
