# JSON Storage: контракт и границы гарантий

Схема envelope: `schemas/document-v1.json`. Все authoritative записи — UTF-8 JSON с UUID, kind, schema_version, revision, UTC timestamps и payload. Реальные payload schemas регистрируются соответствующими модулями. Незнакомая версия зарегистрированного kind отклоняется. API не даёт произвольного доступа к storage paths.

## Транзакция

Однопроцессный launcher lock и межпроцессный manager.lock защищают запись/чтение. Для всех изменений сначала сверяется expected_revision под lock. Новые immutable JSON-объекты получают SHA-256 в имени в своём каталоге. Манифест `data/config/store-manifest.json` отображает логические относительные ключи на объекты/ревизии; категории остаются characters/chats/worlds/scenarios/memory/relationships/config/indexes и другие согласованные домены.

Порядок: объекты → fsync → intent (before checksum/after manifest/changes/fingerprint) → fsync commit marker → атомарная публикация manifest → complete → ACK. До commit pointer не меняется; после commit recovery доводит до публикации. Повтор txid должен иметь тот же fingerprint; другой запрос с тем же ID даёт conflict. Читать согласованный набор следует через store.view; последовательные независимые get не обещают один snapshot.

После аварии incomplete committed intent проверяется и применяется один раз; uncommitted intent не применяется. Потерянный/повреждённый manifest не заменяется молча пустой библиотекой. Orphan immutable объекты/журналы сохраняются до будущего безопасного GC; автоматическое удаление первоисточников не выполняется. При disk full/denied выдаются русские специализированные ошибки. На POSIX fsync выполняется для файла и каталога, на Windows fsync файла и os.replace; гарантии потери питания/физического носителя не заявляются без отдельного испытания. Тест kill процесса подтверждает только соответствующую модель сбоя.

SHA-256 обнаруживает порчу; это не криптографическая защита от процесса, имеющего право переписать манифест и данные. Paths не допускают traversal/Windows reserved names/симлинки/reparse points. Приложение не делает защищённую песочницу из общей доступной для записи папки.

## Сегменты, индексы и миграции

Message segments ограничены 128 сообщениями/1 MiB (первоначальная измеренная конфигурация, можно пересмотреть без изменения функций). Append публикует изменённый bounded tail и metadata, предыдущие сегменты не переписываются. Page читает сегменты в согласованном view и отдаёт ограниченный результат; DOM virtualization относится к этапу 06. Дубликаты message UUID отклоняются. Типизированные branch/story данные будут добавлены в 06.

Производный JSON index пересоздаётся из authoritative документов; corrupt index можно удалить и построить заново без изменения source. Неавторитетные ресурсы можно сканировать с isolate/report, не блокируя остальную библиотеку. Повреждённый обязательный документ не подменяется пустым.

Миграция: согласованная контрольная ZIP-копия с checksum manifest → валидация преобразованных документов → одна транзакция всех изменений и migration_log. Ошибка transform/schema оставляет старую публикацию. Повтор уже завершённой миграции пропускается. Полный UI restore/media snapshot/retention остаётся этапом 15: checkpoint текущего этапа покрывает только JSON-объекты, что явно отражено в формате.

## Доказательства

tests/test_storage.py: kill после object/prepared/commit/publish/complete; conflict двух клиентов; запрет broken refs; idempotency; миграции; disk-full injection; corrupt-resource isolation; согласованность reader; 10k messages и ровно 2 изменённых logical keys на append. tests/test_index.py: rebuild повреждённого JSON индекса без изменения source. Linux PASS, native Windows проверяется CI отдельно. Эти тесты не доказывают качество будущей памяти/ветвей или весь STOR/BACK релиза.
