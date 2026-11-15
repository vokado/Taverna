# Состояние разработки

Допуск на 01–16: получен 2026-11-15. D-01–15: APPROVED.

| Этап | Реализация / проверки |
|---|---|
| 01 | Linux и Windows-runner source/browser/build PASS; frozen EXE smoke подтверждён CI 43639176196 для adc8193; целевая Windows11/Edge/Chrome пока не подтверждена |
| 02 | Foundation реализован; Linux/Windows-runner source tests PASS после исправлений; power-loss/целевой NTFS отдельно не подтверждены |
| 03 | 34 backend tests с регрессией PASS, UI build/E2E PASS; live LM Studio/Dolphin/RTX5070 BLOCKED |
| 04 | Основа settings/workspace/layout реализована; Linux backend/build/browser проверки PASS; доменные панели проверяются по мере модулей |
| 05 | Карточки/обмен/draft autosave реализованы; 45 backend PASS, browser/build PASS; real corpus/live AI BLOCKED |
| 06 | Реализованы чаты/ветви/сцена/группы/import/transfers; 61 backend PASS, 3 E2E PASS (включая 10k), build PASS; прежний Windows FAIL поиска исправлен; CI 43675486779 для b93e896 SUCCESS на Ubuntu/Windows, включая frozen EXE smoke |
| 07 | Реализована основа профилей/контекста/качества/инспектора; 75 backend PASS, 4 E2E PASS, build/Ruff PASS; CI43676823740 для8e2e180 Ubuntu/Windows/frozen SUCCESS; live LM Studio/RTX5070 BLOCKED; продолжение memory/retrieval в 08–10 |
| 08 | Память/знания/отношения/поиск/редактор/обмен реализованы; 98 backend PASS, 7 E2E PASS, build/Ruff PASS; live LLM/embeddings/RTX5070 BLOCKED; календарь/режиссёр в09–10 |
| 09–16 | NOT_STARTED |

Первый официальный 1.0 не готов. Неполные внутренние сборки не являются релизом.

Аппаратные блокировки: локально доступен Linux, Windows/Wine/RTX5070/LM Studio не обнаружены. Доступен Windows GitHub runner; run 43636025382 успешно выполнил source tests/browser/build для a2b4d4e. Это не заменяет целевой Windows11/hardware/manual review. Новые CI результаты фиксируются после чтения фактического запуска. Реальная модель и генератор изображений не подменяются тестовыми doubles.
