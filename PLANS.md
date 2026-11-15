# «Таверна» 1.0 — план выполнения и контроля

Дата: 15 ноября 2026. Версия подготовки: 1. Статус: **утверждён; автономная разработка 01–16 разрешена 15 ноября 2026**. Исходные 16 этапов сохранены в том же порядке. Функциональный код и проверки создаются последовательно с отчётами и коммитами.

## Порядок работы

Подготовка → согласование документов и необходимых D-ID → отдельное явное разрешение этапа 01 → последовательные этапы 01–16. Если разрешён только 01, после его отчёта остановиться. Если разрешена дальнейшая последовательная разработка, проходить контрольные точки без предположения о необходимости нового разрешения на каждый этап. Изменения архитектуры и функционального объёма согласовываются отдельно.

Каждый этап проходит цикл: входные зависимости/допуск → перечень REQ-ID и всех подпунктов → контракты/ошибки/миграции/план тестов → реализация → модульные и контрактные тесты → интеграция/негативные тесты → регрессия затронутых модулей → документация и матрица → отчёт контрольной точки. Непроверенные критические дефекты блокируют зависимую работу. Недоступные аппаратные проверки отмечаются BLOCKED и не блокируют независимые работы согласно уточнению владельца. Временный mock в тестах помечается; он не заменяет real integration.

JSON Schema/OpenAPI и TypeScript-типы создаются после разрешения в рамках соответствующих этапов. Полные схемы будущих модулей не объявляются завершёнными в 02: там фиксируются общие инварианты/связи и правила расширения. Исходный план с предлагаемыми файлами/классами сохранён в `docs/source/`; эти имена не утверждаются данным документом автоматически.

## Этапы

### 01 — фундамент, запуск и инфраструктура

Вход: отдельное разрешение владельца; решения D-01/03/13/15 и границы будущих D-04/07. Работы: структура проекта, Python/FastAPI и React/TS, loopback, конфигурация путей/порта, single-instance, безопасный bootstrap сеанса, `/api/v1` и единая русская ошибка, logging с redaction, старт/остановка/повторный запуск, базовые контракты очереди и шины. Данные истории здесь ещё не реализованы.

Проверки: чистая согласованная Windows без Python/Node, EXE без консоли и diagnostics EXE, занятый порт/недоступная папка/второй запуск, Host/Origin/CSRF/session, shutdown без orphan процессов, отсутствие вызовов управления LM Studio. Внутренний переносимый ZIP подтверждает упаковочную технологию; не весь REL-комплект. Выход: архитектура/API/security threat model, ранний build report. Контрольная точка: реально работающий защищённый launcher/shell, без утверждения готовности всех GEN/UI.

### 02 — transactional JSON storage

Вход: 01; D-02/07/08/12. Работы: общие схемы/UUID/revision/schema_version, переносимые каталоги, manager сериализации, expected_revision до commit, staging/manifest/журнал, согласованные чтения, восстановление и миграции, сегменты сообщений, link integrity, восстановимые производные индексы; контракт snapshots и контрольная копия до миграции. Определить поведение corrupt optional resource и повреждённого обязательного сегмента.

Проверки: kill в каждой фазе до/после commit/ACK, reader не видит смесь файлов, disk-full/denied/locked file, два клиента конфликтуют без перетирания, idempotent recovery, corrupt index rebuild, 10 000 сообщений без полного rewrite, неудачная миграция с восстановлением, baseline backup. Контрольная точка: данные согласованы в перечисленных моделях сбоя на выбранной Windows/ФС; гарантия внезапной потери питания отдельно оговаривается. Выход: storage ADR, схемы, recovery evidence, формат snapshot.

### 03 — LM Studio и поток

Вход: 01–02; D-05/14, ранняя оценка D-06. Работы: adapter/capabilities/loaded catalog, ручной адрес и поиск локального endpoint, profiles базового уровня, stream/partial/cancel/retry/idempotency, метрики, единая bounded priority queue и durable job state. Только already-loaded модели, никаких load/unload/download или скрытых probes с JIT.

Проверки: fake server для протокольных ошибок и реальный LM Studio + Dolphin на целевом ПК; TTFT/tokens/s/RAM/VRAM, обрыв/таймаут/отмена/restart partial, отсутствие дубликатов, неизвестный loaded status, отсутствие advanced channels. Story fixture только в harness, настоящий storage partial и adapter реализуются реально; production chat ещё не готов. Контрольная точка: реальная интеграция доказана; если целевая машина недоступна — BLOCKED. Выход: API compatibility matrix, baseline и отчёт.

### 04 — UI, настройки, рабочие пространства

Вход: 01–03; UI-часть D-09. Работы: согласованные макеты и навигация, home, перенос/resize/collapse панелей, темы/шрифты/фон, simple/advanced/expert без потерь, workspace отдельно от истории, immersion/economy; библиотечные представления и контракты панелей будущих функций. Недоступные ещё функции не выдавать за реализованные.

Проверки: 1280×720/1920×1080 и уменьшенное окно, масштаб, перезапуск настроек, offline/empty/loading/error/cancel states без зависания, управление мышью через кнопки, сохранение экспертных полей. Финальное наполнение home/panels — после соответствующих доменов. Выход: UI guide, скриншоты/браузерные traces. Контрольная точка: базовая навигация и настройки работают; интеграционные части UI остаются открытыми в матрице.

### 05 — персонажи и форматы

Вход: 02–04; D-10 и безопасность импорта. Работы: полный каталог полей/секций, wizard/expert editor, три режима характера с явными правилами, ИИ-draft/preview/review, альтернативные приветствия/пресеты, folders/tags/search/sort, базовые портреты, JSON/PNG V1/V2/V3 adapters, unknown metadata preservation/report, полный пакет персонажа. Подготовить состояние персонажа отдельно от карточки и snapshot policy.

Проверки: реальные законные fixtures разных версий + synthetic malformed, round-trip по каждому полю, Unicode/PNG chunk limits, XSS/path injection, смена имени без потерь ID, reject/редактирование draft, независимые snapshots. Эмоции, группы и медиа окончательно подтверждаются в 06/08/10/12. Выход: CHARACTER_COMPATIBILITY с точными версиями/ограничениями, test corpus manifest. Контрольная точка: редактор/обмен не теряют поддерживаемые данные молча.

### 06 — чат, группы и ветви

Вход: 02–05; D-08/09/10. Работы: реальная Story/Scene основа, сообщения с metadata/model/profile/sources, append-only versions, swipes/compare, stop/retry/continue/edit/delete consequences, частичная регенерация со специфицированной семантикой, дерево/главы/закладки/поиск/карта связей/checkpoints, presence и NPC turns, три представления одной истории. Добавить адаптер импорта чатов SillyTavern с preview и explicit event transfer с проверкой scope/ID.

Проверки: ветвление от предка/переключение/редактирование/удаление, stale generation и параллельные вкладки, partial restart, NPC↔NPC без реплик пользователя, 10k виртуализированных сообщений, перенос события только явно; полная изоляция памяти/мира повторно проверяется 08–10. Выход: story/branch semantics, import report, E2E. Контрольная точка: настоящий чат и граф истории сохраняются без разрушения альтернатив.

### 07 — промпты, профили и контроль ответа

Вход: 03/05/06; D-09. Работы: modular prompt builder, условия/порядок/шаблоны, profile inheritance включая группы/служебные задачи, token estimate/actual и reserve, pinned overflow policy, инспектор причин отбора и приватный diagnostic mode, supported reasoning filtering. Явно реализовать CHR-011: format/repetition/role warnings, bounded selective retry, decision log; смысловые проверки с честными ограничениями.

Проверки: детерминированный prompt для фиксированных источников, наследование/экспертные параметры, переполнение и несовместимые параметры, reasoning отдельно, adversarial templates без исполнения, все режимы quality checks и предел попыток. Выход: MEMORY_AND_CONTEXT часть prompts, benchmark/fixtures. Контрольная точка: каждый отправленный контекст объясним; оценка токенов не выдаётся за точный count.

### 08 — память, знания, отношения и поиск

Вход: 02/06/07 и adapter 03; D-08/09/10/11. Работы: шесть уровней, extraction proposals, provenance/confidence/version/scope, NPC truth/rumor/belief/secret, conflict resolution, pin/archive без удаления источников, chronology/summaries, relationships narrative/metrics/episodes/history. Полный editor и импорт/экспорт памяти, text+semantic search с фильтрами, JSON cache/index rebuild/model invalidation; fallback text при отсутствии embeddings.

Проверки: секрет не утекает через raw fact/summary/retrieval/relationship/director context, две истории и ветви изолированы, edit/delete source invalidation, ручной перенос/merge конфликтует явно, memory round-trip, rebuild 10k, pinned overflow. Выход: схемы scope/provenance, evidence graph, search benchmarks. Контрольная точка: все уровни и интерфейсы реально работают, semantic search подтверждён с реальной embedding-моделью, fallback проверен отдельно.

### 09 — миры, сценарии и события

Вход: 02/05/06/08; D-09/10. Работы: полный world/scenario constructor, география/фракции/правила/ресурсы/лор/связи, ручной ввод/шаблоны/ИИ preview/случайный старт, reusable templates и story snapshots, calendar/time/location/skips, JSON AST rules без eval, reducer/event provenance/conflict checks, импорт лорбуков SillyTavern, пакеты мира/истории.

Проверки: независимый запуск двух историй, snapshot vs изменение шаблона, time advance и off-scene события без real-time фоновой бесконечности, repeated event ровно один эффект, conflicts/manual correction, lore trigger scope/budget, malicious AST/import. Выход: WORLD_AND_DIRECTOR часть world, package/lore docs. Контрольная точка: миры и сценарии переиспользуемы и события воспроизводимы.

### 10 — режиссёр, NPC, эмоции и последствия

Вход: 03/05–09; D-08/09. Работы: bounded pre-scene preparation и post-response analytics, NPC initiative/off-scene actions, proposal→validate→apply/reject→audit, character modes/evolution, emotions/relationship changes с manual/off, субъективные знания, causal graph/unresolved arcs. Отказ пользователя от маршрута поддерживается; hidden planning отделён от прозы.

Проверки: сценарии отказа/секрета/ложного убеждения, запрет user impersonation, mode constraints, manual emotion/disable, повтор и stale job, failure director не ломает чат, реальные LLM examples плюс детерминированные rule assertions. Выход: NPC/director policy и human review cases. Контрольная точка: функции живого мира работают с видимыми ограничениями; смысловая безошибочность модели не заявляется.

### 11 — RPG

Вход: 09–10; D-09. Работы: optional module registry, inventory/stats/skills/dice/economy/conflicts/random events, JSON schemas и history, persisted seed/results, world policy checks, включение/отключение без потери литературного чата, применимые TOOLS-generators.

Проверки: каждая механика отдельно и вместе, deterministic seed replay, недопустимые действия, баланс инвентаря/экономики, rollback/ветви/рестарт, отключение убирает вмешательство в prompt и не уничтожает прошлые данные. Выход: RPG user/data docs. Контрольная точка: все перечисленные механики имеют реальные сценарии, а необязательность относится к использованию, не к включению в 1.0.

### 12 — мультимедиа и локальные изображения

Вход: 02–06/09, queue 03; D-06/10/13. Работы: supported media import/validation/checksum/tags/search/preview, portraits/expressions/costumes/animations/backgrounds, bindings to states/events, layered scene studio/transitions/effects, music/ambient controls, low-effects mode. Реальный image-engine adapter/capability check/job cancel/resource checks, без управления моделью LM Studio.

Проверки: успешная локальная генерация при уже загруженной LLM на согласованной конфигурации, engine-offline/OOM/cancel, damaged/oversized media/active SVG, browser autoplay restrictions, перемещение папки, отдельное отключение музыки/эффектов без изменения истории. Выход: IMAGE_ADAPTER, resource report и supported media matrix. Контрольная точка: импорт и сцена независимы от engine; engine-функция не заменена статусом «недоступно».

### 13 — литературный редактор, материалы и инструменты

Вход: 02/04/06/09/12, AI 03/07; D-09/10. Работы: manuscript chapters/structure/search/history, chat→prose copy, AI rewrite/style/shorten/expand/coherence с diff preview/reject, export TXT/MD/HTML/EPUB chapters/links/media, materials library/documents, notes/dictionary/name generators/bookmarks/event editor/templates и визуальный запуск tools. Права сложных tools завершаются в 14.

Проверки: исходный чат неизменен, reject сохраняет manuscript, ошибки rewrite/export не теряют оригинал, UTF-8 и файлы с кириллицей, EPUB validation + выбранная reader, unsafe HTML/links/attachments, полный перечень TOOLS. Выход: export compatibility matrix, samples и user guide. Контрольная точка: четыре реальных формата и все инструменты проверены.

### 14 — plugins, macros, scheduler

Вход: 01–13 с устойчивыми event/queue contracts; D-04/07/09/13. Работы: versioned manifest/dependencies/compatibility, enable/disable/delete confirmations, separate grants/revoke, declarative runtime/broker, выбранная защита executable host, user rules trigger/condition/action/limit/state/history, visual macros/templates/tools preview, scheduled maintenance memory/index/backup, catch-up и external effect reconciliation.

Проверки: hostile manifest/code/path/network/command, revoke во время jobs, crash/timeout/resource limits, no server/storage corruption, no duplicate effects после restart, endless rule loop budget, подтверждение external filesystem/commands, документация точных гарантий. Выход: PLUGIN_SECURITY/API, permission matrix, automation guide. Контрольная точка: достигнут согласованный уровень изоляции; warning или отдельный процесс сами не подтверждают запрет доступа.

### 15 — полная упаковка, backup, update, документация

Вход: 01–14; D-12/15, финальные решения форматов. Работы: согласованные авто-snapshots JSON/media/config/library и утверждённого состава, verify manifest/checksums, retention и внешний каталог, restore wizard preview/version/pre-restore copy/no plugin autorun, manual update validation/staging/migrate/rollback. Полный ZIP runtime/static/exe/help/docs/licenses/CHANGELOG/manifest/test report; никаких автопроверок обновлений.

Проверки: backup при активной записи/диск full, испорченный/неполный archive, restore с проверкой каждого файла и ссылок, crash в update/restore/migration, перенос диска и пути с пробелами/кириллицей, clean Windows без dev tools/глобальных runtime, no accidental private data. Выход: релиз-кандидат, USER_GUIDE/BACKUP_RESTORE/API/SCHEMAS/licenses. Контрольная точка: весь комплект автономен, обновление и восстановление реальны.

### 16 — комплексная приёмка 1.0

Вход: все 01–15 и D-ID закрыты в зависимых областях; целевая Windows/движки/fixtures доступны. Работы: все 181 REQ-ID с подслучаями, UX-01–09, AT-01–16, supplemental obligations; unit/contract/integration/E2E/recovery/security/compatibility/performance regression, три профиля на целевой машине, чистая Windows и другой диск, русификация и документация. Новые функции — только через change control; дефекты исправляются с повтором затронутой регрессии.

Контрольная точка: 100% обязательного объёма реализовано и проверено, нет FAIL/NOT_RUN/BLOCKED обязательных проверок и критических/блокирующих дефектов, все доказательства относятся к версии/commit/хешу release candidate. Есть подписанный отчёт владельца, лицензии и доверенная процедура проверки ZIP. Выпуск только после этой приёмки; финальная сборка после исправлений проверяется вновь в затронутом объёме.

## Тестирование и доказательства

Unit — доменные инварианты; contract — JSON Schema/OpenAPI/SSE; integration — настоящий storage+API, fake/live adapters раздельно; browser — реальные пользовательские действия и разные окна; recovery — kill/disk full/corruption/restore; security — соседние сайты/пути/импорт/plugins; performance — 10k+ messages, import/index/backup/VRAM; release — clean Windows/LM Studio/image engine/EPUB reader. Полная комплексная приёмка в 16 не отменяет интеграцию и регрессию на каждом этапе.

Числовые SLA, процент unit coverage, сроки в днях и гарантии VRAM не выдумывать. Условия benchmark фиксировать до замера; baseline не равен согласованному SLA. Исходные 8192/16384 и reserve 1024–2048 — параметры эксперимента, не обещание работоспособности. Ограничения реальной машины отмечать в report.

Каждый planned test `T-REQ-ID` до реализации разбивается на положительные/негативные/аварийные подслучаи. Назначение ID в матрице не означает существующий тест. Для PASS обязательны actual_test_path, command, execution_id, build/commit/hash, дата, среда, результат и evidence. Для UX/AT также указывать связанные REQ-ID и конкретные assertions, а не считать один happy path доказательством всех требований.

## Отчёт этапа

Файл будущего отчёта: `reports/stage-NN.md`. Поля: допуск/решения; выполненные работы и файлы; REQ-ID полностью/частично выполненные; схемы/API/миграции; команды и точные результаты; среда и версии; evidence; дефекты (severity/impact/repro), ограничения/непроверенное; восстановление; статус контрольной точки и допустимый следующий шаг. Статус проекта: NOT_STARTED/IN_PROGRESS/IMPLEMENTED_UNVERIFIED/VERIFIED/BLOCKED; статус теста: NOT_RUN/PASS/FAIL/BLOCKED. Скриншот не доказывает транзакционность, fake adapter не доказывает реальную модель.

Промежуточные обновления пользователю сообщают проверенный результат, неопределённость и следующий шаг; отчёт не подменяет доказательства. Решения D/CR включают влияние на матрицу, миграции и тесты. Исходный этап согласования 1–8 неизвестен без протоколов, поэтому не заполняется вымышленным значением.

## Состояние сейчас

Подготовительные документы сохранены; этапы01–09 реализованы с доступными проверками; следующий этап10. Подробные результаты и блокировки: reports/STATUS.md и отчёты этапов. Разрешение на01–16 действует; следующий этап определяется фактическими проверками, отдельный допуск не нужен. Полный1.0 не готов.
