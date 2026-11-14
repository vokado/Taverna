import {useEffect, useState} from 'react';
import {createRoot} from 'react-dom/client';
import {authenticate, request} from './api';
import './style.css';

function App() {
  const [message, setMessage] = useState('Подключаемся к локальному серверу…');
  const [ready, setReady] = useState(false);
  const [generation, setGeneration] = useState('');
  async function connect() {
    setMessage('Подключаемся…');
    try {
      await authenticate();
      const status = await request<{message_ru: string}>('/status');
      const capabilities = await request<{generation: {reason_ru: string}}>('/capabilities');
      setMessage(status.message_ru); setGeneration(capabilities.generation.reason_ru); setReady(true);
    } catch(e) {setMessage(e instanceof Error ? e.message : 'Ошибка подключения.'); setReady(false);}
  }
  useEffect(() => {void connect();}, []);
  async function shutdown() {
    if (!confirm('Завершить работу Таверны? Другие вкладки также отключатся.')) return;
    try {await request('/system/shutdown', 'POST'); setReady(false); setMessage('Таверна завершает работу. Можно закрыть вкладку.');}
    catch(e) {setMessage((e as Error).message);}
  }
  return <main><header><span className="mark">Т</span><div><h1>Таверна</h1><p>Локальные истории и персонажи</p></div><span className="badge">Внутренняя сборка</span></header>
    <section><h2>Рабочее пространство</h2><p role="status">{message}</p>{ready ? <><p>{generation}</p><p>Функции добавляются последовательно. Эта сборка не является релизом 1.0.</p><button onClick={shutdown}>Завершить приложение</button></> : <button onClick={connect}>Повторить подключение</button>}</section>
    <footer>Данные остаются на вашем компьютере. Закрытие вкладки не завершает сервер.</footer></main>;
}
createRoot(document.getElementById('root')!).render(<App/>);
