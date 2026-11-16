# Независимая ревизия: исправления и измерения

2026-11-16, рабочий отчёт. Релиз1.0 не принят и не объявляется готовым. Все181 требования и обязательные подпункты сохраняются. Основание: поручение владельца расследовать CI/I/O/index/history/growth, затем завершить14–16. Работа в пределах текущего JSON/FastAPI/React/общей очереди; изменение пользовательского объёма/модели безопасности не допускается.

## A-R01: Windows CI babfbe7

Run43815573469/job133126723969, artifact14202069348 (SHA256 d6ea32532057b472821145f2c3df7ae8fc3dfe8d3b565c5b29c344a4610cbb9c). Trace изучен, не просто увеличен timeout. File-preview POST вернул409 за22.723ms; UI status «Открытый файл не совпадает с проверенным объектом». Scope из Windows Temp `C:\Users\RUNNER~1\AppData\Local\Temp\...`; handle сообщает long canonical path. Сравнение с lexical root не признавало8.3 alias. 18 browser PASS/1FAIL; frozen этой версии NOT_RUN.

Исправление: до live validation получаются lexical/canonical имена и проверяется совпадение directory identity; после validation повторно проверяется root identity. File открывается по исходному grant path, его физический handle сравнивается с зафиксированным canonical root. Прямой доступ к app root/предкам дополнительно запрещён. Resolve после проверки не используется. Защита root-substitution, reparse/hardlink/regular-file/size сохранена; regression test Windows GetShortPathNameW добавлен (при отсутствии distinct8.3 на ФС — явный SKIP).

Linux317 backend PASS127.70s/3 Windows SKIP, полный browser отчёт audit-windows-fix-browser.txt,30 file/integration target PASS4.08s/1WindowsSKIP до полной регрессии. Ruff PASS. Новый Windows запуск ожидается; исходный FAIL сохранён. Linux/fixtures не подтверждают LM Studio/RTX5070/image engine/Windows11.

## Измерения до изменений производительности

Скрипт scripts/measure_revision_audit.py: synthetic UTF-8 corpus через реальный chat importer/storage/queue; ни одного запроса LLM/GPU. atomic_write_bytes — байты приложения, не физический I/O устройства/FTL. Counter instrumentation не подменяет запись/fsync. Каждый запуск выполнять без других локальных тестов; output содержит commit/platform/Python, исходные samples, размеры manifest/journal/objects и серии timings. SLA целевой конфигурации не назначается.

План:1000 фрагментов queue;2000 successive updates одного/2000 разных JSON keys;истории1k/5k/10k (_prefix/append/fork/PromptBuilder);fork1000 distinct state snapshots;20 обновлений индекса после10k imported messages. Fixtures удаляются после измерений, JSON evidence остаётся. Сначала baseline, затем ограниченные исправления подтверждённых bottlenecks и тот же workload повторно. Новая абстракция не вводится без доказанной необходимости.

### Повтор Windows e122473

Run43819051376: Ubuntu SUCCESS; Windows source tests и plugins.spec.ts PASS, новое падение editor.spec.ts при стандартном5s locator deadline. Trace artifact14204740279 SHA2564e4b4c0479afb64d6faa859267152e65228df417ca108e6e9bab2dcadac5e2ed изучен: job queued06:54:37.384Z → running2/3/4events → completed5events06:54:42.056Z, ошибок backend нет. UI ожидает отдельный сохранённый preview, не изменяет оригинал. Для этого конкретного асинхронного ожидания установлен20s deadline; успех по-прежнему требует настоящего completed/preview и последующих reject/export assertions. Это не SLA и не объявление реального LM Studio проверенным. Новый полный CI выполняется после фикса; исходный FAIL не скрыт. Производительные измерения и изменения JobQueue/MemoryIndex/fork ведутся отдельно и в этот test-only fix не включаются.

## Подтверждённые изменения A-R02–05 (контрольная точка, полная регрессия ещё ожидается)

* JobQueue: первый фрагмент сохраняется сразу; соседние delta/reasoning накапливаются до50ms/8КиБ/64fragments, не смешиваются между видами. Semantic events/terminal/cancel/close сбрасывают буфер в той же транзакции. SSE выдаёт только сохранённые события, page читается одним consistent view. Убийство процесса может потерять ещё не показанный буфер, но не уже выданный текст. Отмена await во время реального commit дожидается фиксации bookkeeping; тесты не допускают дублирования текста/перезаписи terminal state.
* MemoryIndex: JSON format2, стабильные256-entry records/messages shards, text hashes и reuse токенов; обновляются только изменившиеся chunks. Старые fingerprint generations удаляются из live manifest вместе с заменой head. Head/shards читаются одним snapshot, corrupted/legacy cache пересоздаётся или доступен source fallback. Физические версии immutable объектов сохраняются; это не объявляется GC всех данных.
* Fork: source states читаются одним view, source revisions проверяются при publication; UUID/source mapping, история/состояния/knowers и состояние на выбранной точке сохраняются. Пустая ветвь не читает ненужный полный prefix. PromptBuilder не переписан: baseline показывает линейное приемлемое время на данной синтетической нагрузке, не даёт аппаратный SLA.
* JsonStore: primary JSON envelope/manifest и CAS/immutable objects/commit semantics сохранены. Physical path validation остаётся на I/O; manifest validation больше не выполняет lstat всех объектов при каждом get. Completed receipts архивируются в UUID buckets, active recovery не сканирует всю историю. Intent v2 — entry delta с checksum и JSON checkpoint каждые128 поколений; smaller full v1 сохраняется, когда он занимает меньше места (у v2-only был подтверждён лишний размер для tiny manifest). Старый журнал поддерживается; повтор txid возвращает исторический manifest без повторного эффекта через checkpoint/delta chain. Никакие source versions не удаляются. Reserved Windows device set перенесён из hot loop в immutable constant после cProfile:20k вызовов parser ≈0.366→0.080s, правила имён не менялись.

Target:89 исходных/новых core tests PASS22.56s; после compact-choice/negative-shape hardening47 targeted PASS7.54s, Ruff PASS. Реальные kill checks всех6фаз, backward v1→v2, historical replay update/delete/guard, corrupt checkpoint/receipt, cancellation during actual storage publication, legacy index cleanup/source fallback, scope/source-state conflicts. Полные backend/browser/native checks ещё не приписываются этой версии.

### Методика и raw baseline

`audit-baseline.json` (events) запущен на e122473; рост — отдельный `audit-baseline-growth.json` с тем же storage до изменений. Первоначальный growth fixture ошибочно создавал новый entity UUID при обновлении одного ключа; storage правильно отказал identity_change, этот незавершённый growth результат не принят за измерение. После исправления постоянного UUID оба growth case выполнены полностью. History/fork/index повторены последовательно с runtime из `git archive e122473 app/backend` через PYTHONPATH, скрипт исправленной нагрузки тот же; source SHA256 записаны. Все measurement workloads выполнялись без параллельных локальных tests. Профили parser — scripts/measure_path_validation.py. Raw `audit-after-v2-only.json` сохранён как промежуточный эксперимент, не подменяет финальный снимок. Финальный прогон после всех изменений должен быть выполнен на зафиксированном commit и отмечен отдельно.

Baseline:1000 burst fragments/15kB →1001 measured transactions/6006 atomic writes/307830689 application bytes/340.488s;2000 unique keys →943549546 application bytes/471683773 journal bytes/607.675s;2000 edits of one key →2871355 application bytes/1339786 journal bytes/65.415s.10k history _prefix p50=.1254s, PromptBuilder=.0219s, fork=4.2599s;1000 distinct state fork=203.335s.20 index updates after10k messages →841 live index keys,57.94MB data tree,102.76s rebuild-only sum. These are synthetic local measurements, not native inference/GPU results; atomic bytes are not physical device I/O. No throughput/SLA is promised for RTX5070/Windows11.

Ограничение: flat primary manifest всё ещё сериализуется целиком при publication; его размер линейно зависит от live entries, cumulative bytes при вставке множества ключей не стали O(1). Устраняются доказанные лишние journal snapshots/сканирование/повторные state reads/index generations, а не объявляется бесконечная масштабируемость. Source version storage растёт с сохраняемой историей; retention не вводится без утверждённого правила удаления. Hardware/реальные движки/подписанная приёмка BLOCKED. После аудита обязательные14–16 продолжаются, не исключаются из объёма.

## Финальный измеренный срез bfc6c3c

Raw audit-final.json содержит commit и SHA256 исходников; все workload выполнены последовательно без локальной регрессии параллельно. Паузы в потоке — audit-final-paced.json. Это синтетические данные, не тест реальной модели/видеокарты; время включает реальную запись/fsync в данном Linux overlay окружении. CSV audit-comparison.csv сохраняет исходные числа/отношения, без округления в доказательствах.

| Нагрузка | До | После |
|---|---:|---:|
| 1000 burst fragments (seconds) | 340.488 | 0.144 |
| 1000 burst fragments atomic application writes (bytes) | 307830689.000 | 103472.000 |
| growth-unique-False elapsed (seconds) | 65.415 | 11.904 |
| growth-unique-True elapsed (seconds) | 607.675 | 47.856 |
| growth-unique-True journal_bytes (bytes) | 471683773.000 | 5316571.000 |
| growth-unique-True manifest_bytes (bytes) | 471878.000 | 471878.000 |
| 1000 distinct state fork (seconds) | 203.335 | 2.498 |
| 20 index rebuilds (seconds) | 102.759 | 2.572 |
| index live entries (entries) | 841.000 | 41.000 |
| index data tree bytes (bytes) | 57940440.000 | 17410963.000 |

PromptBuilder не изменён; отличия его небольших timings являются измеренным разбросом среды, а не объявленным ускорением алгоритма. Объём одного-key journal сохранён точно1 339 786байт; у предварительного v2-only варианта он был больше, меньший full v1 устранил эту регрессию. Основной manifest остался прежнего размера; cumulative pointer writes всё ещё зависят от live key count.

Windows CI43825606858 дляbfc6c3c SUCCESS Ubuntu/Windows:342 backend PASS248.50s,19 browser PASS2.6m, оба frozen EXE после переноса/restricted PATH PASS, включая selected external file/queue/WASM/atomic receipt. Artifact14206424028 (58 139 820байт). Это native software proof, не clean Windows11/RTX5070/real LM Studio.

После этого обнаружен дополнительный отрицательный случай: escaped lone surrogate от модели мог попасть в pending buffer до UTF-8 проверки. Проверка перенесена до изменения buffer; реальная queue/storage проверка подтверждает clean failed job, сохранение корректного хвоста и успешную следующую задачу. Полная локальная регрессия этой версии:340 backend PASS100.56s/3 Windows-only SKIP,19 browser PASS2.9m. Новая Unicode guard не выдаётся за часть ранее проведённого Windows запуска; следующий CI подтверждает её отдельно.

## Повтор Windows 5a1859c: backend PASS, browser FAIL

Run43827967641/job133166176801:343 backend PASS315.71s, Ruff PASS,16 browser PASS/3 FAIL; frozen NOT_RUN из-за browser gate. Artifact14208216046 SHA256 `5afeeaf36cbed6ead59933ad8cd7d43f4b27ff31e708a5875563a56f6aaef84a`. Очищенные времена/HTTP states: evidence/audit-5a1859c-windows-failure.json; сырые headers/cookies/bootstrap secrets в репозиторий не переносятся.

images.spec.ts: первый POST08:09:19.647Z, completed GET08:09:25.502Z; второй POST08:09:25.866Z получил202 и оставался running без backend error до истечения5s predicate (HTTP polling сам занимал до1.47s). Cleanup затем прервал задачу. До prompt submit выполняются capabilities и ограниченная3s read-only проверка loaded LM catalog; отсутствие LM Studio допустимо и не означает отсутствие задержки. Проверка адресной отмены теперь ожидает фактический второй prompt до20s, затем по-прежнему требует ровно одну адресную отмену, отсутствие global interrupt и второго media asset. Production deadline/поведение генерации не менялись; HTTP fixture не является генератором на GPU.

long-chat/media: истёк общий5s fixture readiness, health ещё не200. Старый fixture игнорировал stderr и не сохранял startup state, поэтому конкретная причина задержки/возможной аварии не установлена; это не объявляется доказанным дефектом приложения или доказанным медленным стартом. Новый fixture даёт cold startup30s, ограничивает каждую health попытку1s, немедленно падает при exit/spawn error и прикладывает metadata без секретов (elapsed/attempts/health/exit/signal). Fixture имеет отдельный45s budget с shutdown; retries не добавлены. Следующий Windows CI должен подтвердить результат, иначе исследуется новая диагностика. Три затронутых browser tests локально PASS30.6s; полная локальная browser регрессия выполняется отдельно.

Полная локальная browser регрессия test-only исправления:19 PASS2.4m, evidence/audit-ci-deadlines-browser.txt. Backend не изменён (предыдущий Windows343 PASS); следующий CI повторяет backend/browser/frozen полностью.
