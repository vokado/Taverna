from __future__ import annotations

import argparse
import json
import os
import secrets
import socket
import sys
import threading
import time
import webbrowser

import httpx
import portalocker
import uvicorn

from .core import Config, atomic_runtime_info, portable_root, setup_logging
from .main import create_app


def bind(port: int) -> socket.socket:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        # No SO_REUSEADDR: Windows must not share an active listener.
        sock.bind(("127.0.0.1", port))
        sock.listen(128)
        return sock
    except BaseException:
        sock.close()
        raise


def main(diagnostic: bool = False):
    parser = argparse.ArgumentParser(description="Таверна — локальный запуск")
    parser.add_argument("--root", type=str)
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--diagnostics", action="store_true")
    args = parser.parse_args()
    from pathlib import Path

    root = Path(args.root).resolve() if args.root else portable_root()
    config = Config(root, args.port)
    try:
        config.prepare()
        logger = setup_logging(root, diagnostic or args.diagnostics)
        with portalocker.Lock(root / "data/server.lock", timeout=0):
            with bind(args.port) as sock:
                config = Config(root, sock.getsockname()[1])
                if not (config.static / "index.html").is_file():
                    raise RuntimeError("Файлы интерфейса отсутствуют. Выполните npm run build в app/web.")
                server = None

                def shutdown():
                    server.should_exit = True

                control_key = secrets.token_urlsafe(32)
                key_path = root / "data/config/launcher.key"
                fd = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
                with os.fdopen(fd, "w", encoding="utf-8") as stream:
                    stream.write(control_key)
                app = create_app(config, shutdown, control_key)
                launcher_token = app.state.sessions.bootstrap()
                # Bootstrap URLs never enter logs or runtime metadata.
                server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=config.port,
                                                       log_config=None, access_log=False))
                atomic_runtime_info(root / "data/config/instance.json",
                                    {"port": config.port})
                def open_when_ready():
                    for _ in range(200):
                        if server.should_exit:
                            return
                        if server.started:
                            webbrowser.open(config.origin + "/#launch=" + launcher_token)
                            return
                        time.sleep(0.05)
                if not args.no_browser:
                    threading.Thread(target=open_when_ready, daemon=True).start()
                logger.info("Сервер запускается, порт %d", config.port)
                try:
                    server.run(sockets=[sock])
                finally:
                    (root / "data/config/instance.json").unlink(missing_ok=True)
                    key_path.unlink(missing_ok=True)
                    logger.info("Сервер остановлен")
    except portalocker.exceptions.LockException:
        try:
            port = json.loads((root / "data/config/instance.json").read_text())["port"]
            with httpx.Client(trust_env=False, timeout=3) as client:
                response = client.get(f"http://127.0.0.1:{port}/api/v1/health")
                response.raise_for_status()
                key = (root / "data/config/launcher.key").read_text(encoding="utf-8")
                response = client.post(f"http://127.0.0.1:{port}/api/v1/launcher/open",
                                       headers={"X-Taverna-Launcher": key})
                response.raise_for_status()
            if not args.no_browser:
                webbrowser.open(response.json()["url"])
        except Exception:
            _show_error("Другой экземпляр уже запущен, но пока недоступен. Повторите через несколько секунд.")
            return 1
    except Exception as exc:
        _show_error(str(exc))
        return 1
    return 0


def _show_error(message: str):
    if sys.platform == "win32":
        import ctypes
        ctypes.windll.user32.MessageBoxW(0, message, "Таверна: ошибка запуска", 0x10)
    else:
        print(f"Таверна: {message}", file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
