# Этап 02 — JSON storage

Дата 2026-11-15. Доступные проверки PASS, native Windows/NTFS ещё проверяется CI. Объём storage foundation выполнен; схемы будущих доменов и финальный BACK не объявлены готовыми.

Реализованы versioned JSON envelope, immutable objects и generation manifest, сериализация чтений/записей, optimistic revisions до commit, intent/commit/complete журнал и roll-forward recovery, transaction fingerprint/idempotency, reference checks, переносимые safe paths, corruption isolation, bounded message segments, derived JSON index rebuild, JSON checkpoint/checksum/предмиграционный backup и transaction migration log. Добавлены защищённые storage diagnostics API. Ни SQLite, ни отдельная СУБД не используются.

Проверки: `.venv/bin/pytest -q` — 22 PASS (5 foundation regression + 17 storage/index cases); `.venv/bin/ruff check app/backend tests` PASS; browser foundation regression — 1 PASS. 10k messages сохраняются в 79 сегментов; следующий append меняет только tail+head, неизменяемые сообщения других сегментов не переписываются. Kill процесса проверен после каждой из 5 фаз; обе записи остаются старой парой до commit или новой парой после commit. Тест disk full — инъекция ENOSPC на commit, не реальное заполнение системного диска. Миграция неверного payload не меняет опубликованную старую версию. Согласованный reader не видит промежуточную смесь файлов.

Найденный ранее Windows-дефект: тест читал UTF-8 журнал в системной cp1252; исправлено явным encoding=utf-8. Первый CI run 43635295500: Windows 4 PASS/1 FAIL именно по декодированию тестового журнала, Ubuntu job cancelled из-за fail-fast. Ошибка не скрыта; повторный CI запускается с этим коммитом. Статус Windows до повторного результата не повышается.

REQ-ID частично: STOR-001–011, SEC-002/008, BACK-001/003/007, QA-004/005. Финальные полные payload schemas, branch semantics, медиа backup, restore UI, performance targets и clean-Windows durability остаются открытыми по плану. Orphan immutable objects не удаляются автоматически; безопасный GC проектируется отдельно, источники сохраняются. Нет обещания устойчивости к любому отключению питания/аппаратному отказу.

Доступных критических дефектов после исправлений не обнаружено. Следующий шаг — 03: настоящий протокольный adapter LM Studio и queue; реальные испытания Dolphin/RTX5070 зависят от оборудования и будут отмечены отдельно.
