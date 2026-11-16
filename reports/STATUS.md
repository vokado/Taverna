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
| 08 | Память/знания/отношения/поиск/редактор/обмен реализованы; 98 backend PASS, 7 E2E PASS, build/Ruff PASS; CI43698689482 для31a403a Ubuntu/Windows/frozen SUCCESS; live LLM/embeddings/RTX5070 BLOCKED; календарь/режиссёр в09–10 |
| 09 | Миры/сценарии/snapshots/calendar/events/AST/lore/пакеты/editor реализованы;119 backend PASS,8 E2E PASS, build/Ruff PASS; CI43702010767 дляa12e6ee Ubuntu/Windows/frozen SUCCESS; live AI/corpus/целевая среда BLOCKED; NPC/director в10 |
| 10 | Реализованы NPC/director/API/UI/drafts/источники/ручной отказ;139 backend и9 E2E PASS, Ruff/types/build PASS; CI43739668246/ad0e711 Ubuntu/Windows/frozen SUCCESS после исправления чтений и поиска; Windows139 backend и9 browser PASS; live/target BLOCKED |
| 11 | RPG7 modules/API/UI/drafts/replay/ZIP реализованы;159 backend и10 browser Linux PASS,Ruff/build PASS;CI43740639796/2ab1475 Ubuntu/Windows/frozen SUCCESS,целевая среда BLOCKED |
| 12 | Библиотека/слои/варианты/аудио/drafts/ZIP и Comfy image adapter/lease/cancel реализованы;190 backend/12 browser Linux PASS;CI43750969808/e67c4cd Ubuntu/Windows/frozen SUCCESS; окончательный выбор движка/RTX5070/LM Studio BLOCKED |
| 13 | Рукописи/AI previews/4формата экспорта/materials/tools реализованы; после исправлений CI43777643165/9c4a672 Ubuntu/Windows/frozen SUCCESS,221 backend/16 browser; target/live AI BLOCKED |
| 14 | IN_PROGRESS: Wasmtime/no-WASI host, registry/grants/manager, shared-queue compute и атомарные own/note/book plans; Linux283 backend PASS/2 Windows SKIP; 18 browser PASS2.9m +2 plugin retest PASS14.7s. CI08f99d4 host baseline SUCCESS; новые registry CI выявили ZIP/test synchronization defects, исправления проверяются. External broker/rules/macros/scheduler ещё не завершены |
| 15 | Полная упаковка/backup/manual update/documentation NOT_STARTED; базовый Windows portable pipeline с01 уже действует |
| 16 | Финальная приёмка NOT_STARTED; обязательные hardware/clean Windows11/live LM Studio критерии BLOCKED |

Первый официальный 1.0 не готов. Неполные внутренние сборки не являются релизом.

Аппаратные блокировки: локально доступен Linux, Windows/Wine/RTX5070/LM Studio не обнаружены. Доступен Windows GitHub runner; run 43636025382 успешно выполнил source tests/browser/build для a2b4d4e. Это не заменяет целевой Windows11/hardware/manual review. Новые CI результаты фиксируются после чтения фактического запуска. Реальная модель и генератор изображений не подменяются тестовыми doubles.
