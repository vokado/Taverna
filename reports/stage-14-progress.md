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

### Windows CI43787077959: ZIP исправлен, browser synchronization failure

3cdd704: Ubuntu SUCCESS; Windows backend266 PASS (включая реальные junction/Job Object tests), browser16 PASS/1 FAIL3.6m; frozen NOT_RUN из-за browser failure. В manager тест ожидал уже показанный текст предыдущего сохранения и успел прочитать grants до завершения revoke PUT. В скачанной trace14191181561: GET01:44:34.153Z завершён до revoke PUT01:44:34.285Z (200); это не успешный отзыв старой записи. UI теперь очищает прошлый status при начале операции, test отдельно ждёт фактические grants/epoch. Не менялись ожидания безопасности: права должны реально исчезнуть. Новая проверка ожидает CI. Исходные доказательства: job133037010055, artifact14191181561; токены/headers из trace в документацию не копируются.


## Промежуточная контрольная точка: выполнение и внутренние эффекты

2026-11-16. Этап14 IN_PROGRESS, не финальная готовность plugins/automation. PLG-001–005/AUTO-004–005/SEC-006. Реальные shared queue production plugin_compute jobs исполняют declarative/isolated WASM/full-access Python, проверяя package identity/catalog revision/epoch/hash. Явные scoped reads own/note/book до гостя, fields/types/size checks; sealed Python helper snapshot; no eval/OS API для WASM. Типизированный PluginPlan v1 own.set/delete, note.save, book.save, отдельные preview/accept/reject и атомарные effects+receipt+manuscript versions. Новый API и визуальный runner/forms/read selection/stop/history; никакой код не запускается при импорте/включении. Внешние effects всё ещё не реализованы, unsupported actions отклоняются.

Проверки Linux: **283 backend PASS122.43s,2 Windows-only SKIP**; **18 browser PASS2.9m**;36 plugin-service/package target PASS12.80s;Ruff/types/build PASS,41 contracts. После Windows trace анализа изменено только очищение старого manager status/ожидание реальных revoke grants в test; plugin browser retest **2 PASS14.7s** приведён отдельно stage-14-effects-browser-final.txt. Полная browser регрессия относится к сборке до этой локальной правки; остальные сценарии после неё не повторялись, функциональные модули не менялись. Python warning Starlette/httpx сохраняется, chunk ~526kB отмечен для15.

Реальные проверки: subprocess WASM/Python без подмены runtime, HTTP per-run trust/revoke kills current production handler, actual os._exit88 в фазах prepared/committed effect transaction и recovery обеих записей/receipt, repeated accept/restart no duplicate manuscript version, read/target CAS conflict without partial writes, absent/unknown/wrong scopes/type/path, notes.write не открывает template grants, unique queue idempotency, one-pass template input, bounded expansion, ABA remove/reinstall одинакового ZIP и record revision/epoch, monotonic own tombstone. Никакого hardware/реального LM Studio подтверждения эти тесты не дают.

Миграции: новый plugin-plan-v1 контракт и виды JSON previews/plugin_data; существующие пользовательские документы не переписываются. ToolService.save использует выделенный plan без изменения прежнего API; регрессия tools входит в283/18. Восстановление JsonStore прежнее; receipts защищены той же транзакцией. I-14-03 — внутренняя деталь в рамках разрешения, не новая история согласования.

Frozen smoke дополнен настоящим WASM plugin через HTTP→общую очередь→Taverna-PluginHost.exe→pending→двукратный accept→ровно одна own revision. Новый native smoke NOT_RUN здесь, ожидает CI; предыдущие registry Windows failures сохранены выше. Windows runner266 backend PASS доказывает исходные OS guards, но не новую полную frozen интеграцию и не Windows11 hardware.

Осталось14: external filesystem/network/commands/tools broker и started/unknown reconciliation; rules/macros/scheduler/outbox/catch-up/loop budgets/maintenance; backup15. Затем15 и16 по PLANS, без сокращения181 требований. Parent matrix PLG остаётся IN_PROGRESS/NOT_RUN до всех согласованных подпунктов/нужных целевых проверок. release_ready=False.

### Подтверждение Windows для25be3b6

CI43788649808 SUCCESS Ubuntu/Windows. Windows job133041855584:285 backend PASS277.30s,18 browser PASS3.2m, scripts parse/build/frozen smoke PASS. Taverna.exe и Taverna-Diagnostics.exe после relocation/restricted PATH: настоящий isolated WASM/OS limits/trusted Python helpers/plugin registry/HTTP→shared queue→WASM→atomic own receipt/repeated accept PASS. Артефакт14191422651 (58 086 572байт). Это baseline выполнения плагинов; последующий PluginFiles в эту сборку ещё не входил. Clean Windows11/RTX5070/live LMStudio/подписанная приёмка не подтверждены.

## Промежуточная контрольная точка: внешний read primitive

PLG-003/SEC-006/AUTO-004,14 IN_PROGRESS. PluginFiles.read реализует реальное bounded чтение только явно granted локального каталога вне app/data: относительные имена с пробелами/кириллицей, отдельное filesystem.read, проверка текущей identity до/после, links/reparse/hardlinks/FIFO/устройства/ADS/traversal,128КиБ, SHA256/base64, проверка фактического открытого OS handle до bytes read. Корень сохраняется как granted lexical path: повторное resolve после подмены корня иначе авторизует новую цель. Проверка подмены на app root действительно отвергает доступ. Ошибки missing/growing/disappearing root возвращаются как AppError, без выдачи содержимого. Windows handle path использует GetFinalPathNameByHandleW; его новые проверки ещё NOT_RUN на Windows.

Дополнительно storage.portable_parts отвергает Windows DOS device names COM¹/LPT²/³; это не меняет формат JSON. Linux312 backend PASS97.60s/2 Windows SKIP до последней локальной защиты root substitution; после неё51 file/storage target PASS5.00s (новый regression case включён). UI в этой точке не менялся, прежние18 browser относятся к25be3b6, новый запуск не приписывается. Ruff PASS. Новый read primitive ещё не подключён к production API/guest input; это проверенный building block, а не заявление завершения внешнего broker. Следом integration выбранных файлов/owner preview, write/network/commands/effect ledger/rules/scheduler остаются обязательными. No production stubs. Миграций нет. Native proof ожидает нового CI, parent PLG/AUTO NOT_RUN.


### Windows-подтверждение read primitive31abb4a

CI43813841312 SUCCESS Ubuntu/Windows. Windows job133121263662:315 backend PASS394.42s,18 browser PASS3.3m, build/frozen main/diagnostic smoke PASS. Artifact14202217288 (58 086 528байт). Новый primitive проходит реальные Windows handle/Unicode/hardlink/reparse/root-substitution checks в source tests. В frozen этой точки file-selection API ещё не был подключён; не приписывается end-to-end проверка новой функции.

## Промежуточная контрольная точка: выбранные файлы во входе расширения

2026-11-16. PLG-003/SEC-002/006/AUTO-004,14 IN_PROGRESS. Подключён настоящий PluginFiles к production PluginService. Pinned file-preview API возвращает размер/hash/безопасный UTF-8 preview≤8000символов, бинарные сведения без исполнения. Run.files≤8 требует подтверждения каждого scope/name/SHA256. Чтение повторно проверяет grants/identity/физический handle, несовпадение hash отклоняет job до compute/effects. Совокупный input≤256КиБ, file≤128КиБ, ранний aggregate guard для внутренних данных. Guest получает bytes/base64 snapshot; metadata preview содержит source scope/path/hash/bytes без автоматического добавления в NPC-память. Owner confirm apply меняет лишь внутренние JSON по существующему atomic receipt.

UI: каталог выбирается только из опубликованных filesystem.read scopes, относительное имя/preview/hash/checkbox/добавление/удаление, конфликт изменившегося файла, provenance в плане, остановка; основные операции без ручного JSON/терминала. Добавлены явные accessible имена selectors после обнаруженного browser locator failure; повтор сценария PASS7.4s, итоговая полная регрессия ниже. States отображаются по-русски; required checkbox имеет явное default=false, сброс optional select убирает поле, обязательная пустая строка backend отклоняется. Отдельное руководство docs/PLUGIN_GUIDE.md.

**317 backend PASS109.62s/2 Windows-only SKIP;19 browser PASS2.8m;23 integration/service target PASS20.40s; Ruff/types/build PASS;41 contracts.** Доказательства stage-14-file-input-*.txt/json. Настоящий WASM выполняет scan входного буфера и trap при отсутствии base64 bytes выбранного файла — runtime/данные не подменены fake worker. Проверены changed file/no confirm/aggregate size/UTF8 truncation/binary preview/CSRF/no guest execution при preview, атомарный own effect и provenance исходного snapshot. Последующее изменение внешнего файла не меняет уже согласованный snapshot и не приводит к внешней записи.

Миграций пользовательских данных нет: новый files optional input default[], старые pending plans без external_sources читаются. OpenAPI дополнен DTO/route. Источник основного хранилища/очередь/модель LM Studio не менялись. Native smoke расширен выбранным внешним файлом из отдельной папки вне portable root и проверкой provenance; новая frozen integration здесь NOT_RUN, будет подтверждена отдельным CI. Базовый native proof31abb4a выше не выдаётся за этот запуск.

Осталось14: filesystem.write/network/commands/tools broker, внешний started/unknown/reconciliation ledger, rules/macros/scheduler/durable outbox/loop budgets/catch-up/maintenance; backup15. Все181 требования сохранены; составные PLG/AUTO/SEC006 остаются IN_PROGRESS/NOT_RUN. Доступные подтверждения подфункций не заменяют недоступные Windows11/RTX5070/live LM Studio/финальную подписанную приёмку. release_ready=False.
