# Промежуточная контрольная точка14 — изолированное вычислительное ядро

2026-11-16. Этап14 IN_PROGRESS; plugin manager/broker/automation ещё не готовы. Первое проверенное ядро, не завершение PLG-004/005. Допуск01–16/D-04/07/13 действует; модель доверия не меняется. Перед API выполнен контракт docs/PLUGIN_SECURITY.md.

Реализованы настоящий отдельный subprocess и ABI v1 Wasmtime50.0.1: memory/alloc/run, bounded UTF-8 JSON, отсутствие любых imports/WASI, stack/fuel/linear-memory/table/source/input/output limits, deadline/cancel/kill, bounded OS stdout/stderr, диагностика без source/inputs/paths. Ошибка/аварийный выход отдельного host не падает в серверный процесс. Это именно runtime isolation без предоставленных syscalls, не утверждение «процесс сам является sandbox». Конструктор Python/Windows команды host фиксированы; production-подмены worker на произвольную команду нет.

Trusted Python — отдельный явно full-access режим, запрещён без отдельного флага доверия; это НЕ доказательство выдачи прав пользователем: manager должен привязать его к предупреждению и hash, пока такого API нет. Python может напрямую обращаться к ОС, создавать потомков, менять среду и обходить broker; остановка управляемого процесса не отнимает уже использованные права и не обещает убить потомков. Untrusted Python не поддерживается/не исполняется. Вход ≤256КиБ; модуль ≤256КиБ/256функций, memory16МиБ, stack512КиБ, table1000элементов, fuel default1M (10k–10M), deadline default5s (0.1–30s), host envelope1МиБ. Точные численные пределы — инженерные ограничения, не аппаратный SLA; stack/table и compiler resource exhaustion требуют дополнительных adversarial испытаний при интеграции. Не обещается безопасность от дефекта Wasmtime/ОС.

**236 backend PASS81.87s**, включая15 новых kernel cases; отдельно **15 target PASS2.67s**. Ruff PASS. Источник9c4a672 + module snapshot stage-14-compute-build.json. Команды `.venv/bin/pytest -q`, `.venv/bin/pytest -q tests/test_plugin_compute.py`, `.venv/bin/ruff check app/backend tests scripts packaging`. Raw stage-14-compute-backend.txt / stage-14-compute-target.txt. Старые16browser PASS2.6m относятся к9c4a672, frontend в этой точке не менялся. Повтор UI не выдаётся за новый запуск. Starlette/httpx deprecation warning остаётся.

Cases: actual separate process/JSON/non-ASCII/fuel, denied fd_write/path_open/system/fetch/write_file, malformed/truncated/oversize WASM, loop/trap/pointer/ABI/memory initial/grow rejection, Python explicit trust, bounded1M-print log, real os._exit(12), timeout/signal cancel, os.write stdout flood и работоспособный следующий host, input bounds до запуска. Test doubles не подменяют runtime: модуль действительно компилируется/выполняется Wasmtime. Нет GPU/LLM и нет production-синтетических результатов вместо пользовательского кода.

Wasmtime50.0.1 wheel WindowsAMD64 существует; PyPI SHA256 a8999581230471f4ff62073b1111eff3a01d45ccec0a908da3f2ae3a499d459c. Установленный LICENSE/metadata Apache-2.0 WITH LLVM-exception проверены, collector49Linux entries. Полный аудит native transitive notices остаётся частью15/16, не заменяется числом packages. requirements.lock/pyproject закреплены. SQLite/новых очередей нет; engine cache не включается.

Добавлен третий отдельный Taverna-PluginHost.exe в portable build, --collect-all wasmtime для native DLL/data, общий включённый runtime. Frozen smoke исполняет настоящий JSON/WASM модуль и отвергает WASI в перенесённой папке с кириллицей/restricted PATH, помимо прежних main/diagnostic/media/docs/book checks. На Linux EXE не собирался; новый Windows build ещё NOT_RUN. Эти изменения не подтверждают clean Windows11 или sandbox всего trusted Python.

Осталось14: versioned manifest/import/dependencies/compatibility/enable/disable/delete; scopes/grants/revoke/кодовые hashes и UI предупреждений; broker и previews/receipts внешних/внутренних действий; единая queue integration, правила/macros/планировщик, durable outbox/catch-up/idempotence, background memory/index/backup integration15; реальные Windows hostile tests и полный пользовательский UI. Никакого заглушечного manager/успешного placeholder backup нет. Соответствующие критерии PLG/AUTO не закрыты, matrix parent NOT_RUN.

Параллельно проверяется исправление13 9c4a672 в CI43777643165; предыдущий Windows13 failure и его причины/исправления в stage-13.md. Аппаратные LMStudio/RTX5070/image-engine/финальная приёмка остаются BLOCKED.

## Подтверждение первоначального host

CI43778582262 для08f99d4: SUCCESS Ubuntu/Windows,236 backend/16 browser, frozen smoke трёх EXE с настоящим WASM и отказом WASI. Артефакт14187382997 (48 878 025байт). Это подтверждение первоначального host; новый registry/UI/OS Job Object ниже в этот запуск НЕ входили.

## Промежуточная контрольная точка: registry и управление

Дата2026-11-16. Этап14 IN_PROGRESS. Реализованы versioned manifest, ZIP preview без исполнения, immutable checksum code, catalog CAS, зависимости/совместимость/циклы, выключение зависимых, отдельные grants/epoch/revoke, удаление с подтверждением и очисткой файлов. Обновление сбрасывает доверие и права. В UI есть просмотр кода, импорт, отдельное full-access подтверждение Python, настройка и отзыв прав, восстановление raw drafts. Включение не запускает код. API отменяет устаревшие plugin jobs в общей очереди; production compute/effect handlers ещё не подключены.

Linux:264 backend PASS107.14s,2 явных Windows-only SKIP (junction и завершение потомка Job Object);17 browser PASS2.8m; TypeScript/build/Ruff PASS,40 contracts. Доказательства reports/evidence/stage-14-registry-*.txt и stage-14-registry-build.json. Новые native/OS проверки ожидают следующего CI. Большой web chunk ~517kB отмечен для оптимизации15, численный SLA не приписывается.

Host получает OS resource ceiling512MiB: Linux RLIMIT_AS, Windows Job Object process+family commit и8 процессов/kill-on-close с QueryInformationJobObject проверкой. Это ограничение ресурсов, не sandbox для full-access Python. Его код способен обходить ограничения при полном доверии. Ошибка установки лимитов блокирует исполнение. Доверенный SDK поддерживает run(request), stdlib/core dependencies и bundled helpers; штатный CLI python.exe не обещается. Frozen smoke дополнен реальными импортами helper/math/statistics/httpx и проверкой лимитов.

Storage path проверки используют lstat для reparse points, ZIP syntax validation выделена в чистую portable_parts без обращения к live filesystem. Main JSON/CAS/recovery формат не меняется; schema plugin_registration v1 и40 contracts. Полная регрессия после этих изменений указана выше.

Осталось: production compute/effect broker с previews/receipts, отдельные внешние операции и проверка scopes, правила/macros/scheduler/durable events, maintenance/backup15. PLG-001–005 parent NOT_RUN до полной интеграции/целевых испытаний. Hardware и подписанная приёмка BLOCKED; релиз не готов.

### Windows CI43786462006: дефект ZIP-нормализации

Для f07ae08 Windows backend:265 PASS,1 FAIL248.52s, failure `test_windows_paths_and_case_collisions_rejected[folder\\evil]`; последующие browser/frozen NOT_RUN на Windows. ZipInfo на Windows заменяет обратный слеш, а test writer тоже менял его до записи: fixture не сохраняла опасное исходное имя. Parser теперь отвергает orig_filename != filename (включая усечение NUL), test writer сохраняет raw filename независимо от ОС. Linux целевая регрессия26PASS/1WindowsSKIP3.23s. Повтор Windows ожидается; исходная ошибка не скрывается. Новая compute/broker работа ведётся отдельно, в этот fix commit не включена.
