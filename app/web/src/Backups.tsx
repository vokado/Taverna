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

  async function saveDiagnostics(includeLogs: boolean) {
    if (
      includeLogs &&
      !window.confirm(
        "Журналы могут содержать личные сведения. Добавить их в архив для собственной проверки?",
      )
    ) return;
    setBusy(true);
    try {
      await downloadGet("/diagnostics/archive?include_logs=" + includeLogs);
      setMessage(
        includeLogs
          ? "Диагностика с журналами скачана. Проверьте архив перед передачей."
          : "Техническая диагностика скачана без историй и личных материалов.",
      );
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
        Автоматические проверенные копии создаются раз в сутки, пока приложение
        запущено. Хранятся семь последних и до четырёх недельных копий.
        Ручные архивы автоматически не удаляются. Восстановление пока
        допускается только в новый каталог после закрытия приложения.
      </p>
      <h3>Локальная диагностика</h3>
      <p>
        Технический архив по умолчанию не содержит историй, персонажей,
        промптов, пользовательских материалов, настроек или секретов.
        Никакие данные не отправляются автоматически.
      </p>
      <button
        disabled={busy}
        onClick={() => void saveDiagnostics(false)}
      >
        Скачать техническую диагностику
      </button>
      <button
        className="secondary"
        disabled={busy}
        onClick={() => void saveDiagnostics(true)}
      >
        Скачать диагностику с журналами
      </button>
      <p>
        При выборе журналов выполняется лишь частичное скрытие адресов и
        токенов. Перед передачей такого архива проверьте его самостоятельно.
      </p>
    </section>
  );
}
