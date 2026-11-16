# Независимая ревизия: исправления и измерения

2026-11-16, рабочий отчёт. Релиз1.0 не принят и не объявляется готовым. Все181 требования и обязательные подпункты сохраняются. Основание: поручение владельца расследовать CI/I/O/index/history/growth, затем завершить14–16. Работа в пределах текущего JSON/FastAPI/React/общей очереди; изменение пользовательского объёма/модели безопасности не допускается.

## A-R01: Windows CI babfbe7

Run43815573469/job133126723969, artifact14202069348 (SHA256 d6ea32532057b472821145f2c3df7ae8fc3dfe8d3b565c5b29c344a4610cbb9c). Trace изучен, не просто увеличен timeout. File-preview POST вернул409 за22.723ms; UI status «Открытый файл не совпадает с проверенным объектом». Scope из Windows Temp `C:\Users\RUNNER~1\AppData\Local\Temp\...`; handle сообщает long canonical path. Сравнение с lexical root не признавало8.3 alias. 18 browser PASS/1FAIL; frozen этой версии NOT_RUN.

Исправление: до live validation получаются lexical/canonical имена и проверяется совпадение directory identity; после validation повторно проверяется root identity. File открывается по исходному grant path, его физический handle сравнивается с зафиксированным canonical root. Прямой доступ к app root/предкам дополнительно запрещён. Resolve после проверки не используется. Защита root-substitution, reparse/hardlink/regular-file/size сохранена; regression test Windows GetShortPathNameW добавлен (при отсутствии distinct8.3 на ФС — явный SKIP).

Linux317 backend PASS127.70s/3 Windows SKIP, полный browser отчёт audit-windows-fix-browser.txt,30 file/integration target PASS4.08s/1WindowsSKIP до полной регрессии. Ruff PASS. Новый Windows запуск ожидается; исходный FAIL сохранён. Linux/fixtures не подтверждают LM Studio/RTX5070/image engine/Windows11.

## Измерения до изменений производительности

Скрипт scripts/measure_revision_audit.py: synthetic UTF-8 corpus через реальный chat importer/storage/queue; ни одного запроса LLM/GPU. atomic_write_bytes — байты приложения, не физический I/O устройства/FTL. Counter instrumentation не подменяет запись/fsync. Каждый запуск выполнять без других локальных тестов; output содержит commit/platform/Python, исходные samples, размеры manifest/journal/objects и серии timings. SLA целевой конфигурации не назначается.

План:1000 фрагментов queue;2000 successive updates одного/2000 разных JSON keys;истории1k/5k/10k (_prefix/append/fork/PromptBuilder);fork1000 distinct state snapshots;20 обновлений индекса после10k imported messages. Fixtures удаляются после измерений, JSON evidence остаётся. Сначала baseline, затем ограниченные исправления подтверждённых bottlenecks и тот же workload повторно. Новая абстракция не вводится без доказанной необходимости.
