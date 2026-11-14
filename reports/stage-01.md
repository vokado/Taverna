# Этап 01 — фундамент

Дата: 2026-11-15. Допуск: явное разрешение владельца на 01–16; D-01–15 APPROVED.
Статус: доступные проверки PASS, Windows portable verification BLOCKED локально (Linux, нет Windows/Wine). Независимая работа продолжается по прямому разрешению владельца. Релиз 1.0 не готов.

Реализованы FastAPI/static/React/TS, loopback/port reservation, process lock, one-use bootstrap и повторный launcher, HttpOnly session/CSRF/Host/Origin/body bounds/CSP, локализованные ошибки, диагностический лог без payload, graceful shutdown. Session control-key хранится отдельно от логов и одноразовых ссылок; модель угроз описана в docs/ARCHITECTURE.md. Это не sandbox для процессов того же пользователя с доступом к данным.

REQ-ID (частичное покрытие групп, не полная готовность): ARCH-001–004/006, API-001/004/005, GEN-001/003/005/006, SEC-001/004/005. Очередь/шина ещё не реализованы; отсутствие генерации честно обозначено в capabilities.

Проверки на Linux/Python 3.12.14/Node 24.22.1/Chromium:

- `.venv/bin/pytest -q` — 5 PASS: session/replay/Host/Origin/CSRF/errors/limits/missing static, реальный subprocess launcher, повторный процесс, shutdown и port collision.
- `.venv/bin/ruff check app/backend tests` — PASS.
- `npm run build` в app/web — PASS: TypeScript strict + Vite production static.
- `TAVERNA_PYTHON=.../.venv/bin/python npm run test:e2e` — 1 PASS: реальный browser→backend, fragment removal, reload, closed-tab persistence, shutdown.
- `npm audit --omit=dev --json` — 0 runtime vulnerabilities по доступному registry на момент запуска.

Проверки Windows 11/Edge/Chrome, EXE без консоли, runtime без глобальных зависимостей: пока NOT_RUN/BLOCKED. `.github/workflows/checks.yml` готовит нативные Windows tests/build и внутренний artifact, запуск/результат отдельно отслеживается. Нельзя считать YAML доказательством успешной сборки. PyInstaller one-folder выбран, build script не объявлен проверенным на Windows. Полный лицензированный release ZIP остаётся этапом 15.

Замечание зависимостей: Starlette TestClient выдаёт deprecation warning о httpx; тесты проходят, warning не скрывается. Версии зафиксированы в requirements.lock/package-lock. Секреты/пользовательские данные не коммитятся.

Доступных критических дефектов не обнаружено. Следующий шаг: 02, transactional JSON. Архитектурный дефект проверки revision после replace устранить в контракте хранения до его кода.
