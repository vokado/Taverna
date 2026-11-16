# Состояние разработки

Допуск на 01–16: получен 2026-11-15. D-01–15: APPROVED.

Независимая ревизия: I/O/index/fork/journal исправлены и измерены до/после. Последний CI43864934556/4bb413b Ubuntu/Windows/frozen SUCCESS:22 command-kernel preflight,399 backend,20 browser. Два GUID launch FAIL сохранены и исправлены suspended-image вариантом. Шаблоны/генераторы tools.run и command kernel подтверждены native; command owner API/ledger ещё не подключён. Релиз1.0 не принят; обязательные14–16 остаются в работе.

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
| 14 | IN_PROGRESS: manager/grants/WASM/trusted host/shared queue/atomic plans, selected external reads, tools.run templates/names, native command kernel. CI4bb413b SUCCESS22 kernel/399 backend/20 browser/frozen. Owner external command API/ledger, write/network/rules/scheduler ещё не завершены |
| 15 | Полная упаковка/backup/manual update/documentation NOT_STARTED; базовый Windows portable pipeline с01 уже действует |
| 16 | Финальная приёмка NOT_STARTED; обязательные hardware/clean Windows11/live LM Studio критерии BLOCKED |

Первый официальный 1.0 не готов. Неполные внутренние сборки не являются релизом.

Аппаратные блокировки: локально доступен Linux, Windows/Wine/RTX5070/LM Studio не обнаружены. Доступен Windows GitHub runner; run 43636025382 успешно выполнил source tests/browser/build для a2b4d4e. Это не заменяет целевой Windows11/hardware/manual review. Новые CI результаты фиксируются после чтения фактического запуска. Реальная модель и генератор изображений не подменяются тестовыми doubles.

Независимая ревизия, повтор5a1859c: Windows343 backend PASS,16 browser PASS/3 FAIL, frozen NOT_RUN; trace/причины/исправление ожиданий — reports/REVISION_AUDIT.md. Локально после test-only исправления19 browser PASS2.4m; следующий native CI ожидается. Релиз не принят.

Этап14, исправление orphan host: Linux349 backend PASS/3 Windows SKIP81.69s, targeted49 PASS/1 Windows SKIP24.55s. Real parent-kill negative-before/positive-after и source hashes — reports/stage-14-progress.md; native frozen self-timeout ожидается.
