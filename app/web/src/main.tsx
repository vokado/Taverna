import { useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { authenticate, request } from "./api";
import { Workspace } from "./Workspace";
import "./style.css";
function App() {
  const [message, setMessage] = useState("Подключаемся к локальному серверу…");
  const [ready, setReady] = useState(false);
  async function connect() {
    setMessage("Подключаемся…");
    try {
      await authenticate();
      await request("/status");
      setReady(true);
    } catch (e) {
      setMessage((e as Error).message);
      setReady(false);
    }
  }
  useEffect(() => {
    void connect();
  }, []);
  async function shutdown() {
    if (
      !confirm(
        (document.documentElement.dataset.characterDirty === "true" ||
        document.documentElement.dataset.chatDirty === "true"
          ? "Есть несохранённые правки или неотправленное сообщение. "
          : "") + "Завершить работу Таверны? Другие вкладки также отключатся.",
      )
    )
      return;
    try {
      await request("/system/shutdown", "POST");
      setReady(false);
      setMessage("Таверна завершает работу. Можно закрыть вкладку.");
    } catch (e) {
      setMessage((e as Error).message);
    }
  }
  return ready ? (
    <Workspace shutdown={shutdown} />
  ) : (
    <main className="startup">
      <h1>Таверна</h1>
      <p role="status">{message}</p>
      <button onClick={connect}>Повторить подключение</button>
    </main>
  );
}
createRoot(document.getElementById("root")!).render(<App />);
