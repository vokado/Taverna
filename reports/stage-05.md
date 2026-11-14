# Этап 05 — карточки / библиотека / обмен

2026-11-15. Реализованы полные редактируемые карточки, базовые и собственные подробные секции, два редактора, три сохраняемых режима характера, альтернативные приветствия, metadata/extensions, папки/теги/поиск/сортировка/плитки/список, PNG/JSON V1–V3 adapters, preview import/export, own lossless package для текущих ресурсов, портреты, durable draft autosave/recovery и AI draft/validation/review через настоящий adapter.

Источник доказательств: tests/test_cards.py, tests/test_card_autosave.py, app/web/e2e/bootstrap.spec.ts; синтетический corpus помечен как синтетический. Пройдены JSON/PNG round-trip, unknown root/data/extensions preservation, V3→V2 loss reporting, package/portrait remap, rename/revision conflict, malformed JSON/PNG CRC/zip-slip, script-as-data, raw invalid draft restart без публикации. Browser проверяет create/edit/save/reload, скачивание .tavcharacter, автоматический draft save, reload и explicit recovery, плюс предыдущие workspace/session scenarios.

Первый запуск нового autosave unit test нашёл ошибку test harness: httpx Client.delete не принимает json; исправлено на request('DELETE',...). Это не ошибка runtime. E2E locator имени textarea исправлен на accessible textbox role (implicit label text включал текущее содержимое). Исправлены реальные риски UI: поля блокируются при сохранении, неприменённый raw JSON не может быть заменён старым draft, неопубликованные правки autosave в отдельный JSON и восстановимы. Итоговые результаты последнего прогона указаны ниже после завершения.

Блокировки/неполнота: реальный независимый corpus карт с проверенными правами ещё не получен; live AI wizard/качество модели не проверены; состояния/группы/quality checks/перенос событий требуют 06–10; полный media package/выражения/анимации — 12/15. COMP-006 и весь CHR-001–012 не получают ложный PASS. CHARX не заявлен поддержанным; ТЗ требует JSON/PNG, поддержка внешнего кода/assets URI не добавлялась.

CI этапа 04 (7b60db0), run 43637734145 — Ubuntu SUCCESS, Windows SUCCESS, включая frozen runtime smoke. Нативные проверки этого нового этапа запускаются после коммита; прежний PASS не подменяет новый код.

Контракты экспортируются из действующего API через scripts/export_contracts.py, TypeScript-типы генерируются openapi-typescript и используются для записи карточки. Поля динамических extensions дополнительно проверяются JSON Schema, типы не заменяют runtime валидацию. Formatter Prettier выбран и зафиксирован; Python Ruff/pytest и frontend strict compiler обязательны.

Последний фактический результат: 45 backend PASS за 9.25s, Ruff PASS; TypeScript/Vite build PASS; browser E2E 1 PASS за 9.9s с assertions всех описанных этапов. Аппаратные/реальные corpus проверки остаются BLOCKED.
