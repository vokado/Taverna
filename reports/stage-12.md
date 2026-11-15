# Контрольная точка12 — медиа и изображения

2026-11-15. Доступная реализация и регрессия завершены; аппаратные критерии BLOCKED. Официальный1.0 не готов. Допуск владельца01–16 сохраняется. Следующая независимая работа — редактор/материалы/инструменты13; реальный выбор движка D-06 остаётся открытым до аппаратных испытаний.

## Результат

Библиотека/слои/аудио/ветвление/пакеты/черновики описаны в stage-12-progress.md; это историческая промежуточная точка173/11, а не актуальный список отсутствующих функций. Добавлен реальный адаптер современного ComfyUI, фиксированный checkpoint workflow, настройки/capabilities/явный запуск, общая очередь, журнал и адресная отмена, durable lease до внешнего POST, консервативная обработка потерянного ACK, reconciliation без повторной генерации, идемпотентная публикация фактического PNG. Результат сохраняется в галерею; ветвь автоматически не изменяется. LM Studio lifecycle endpoints не используются. Защита ресурсов охватывает queue и вложенные вызовы GenerationService, не контролирует чужие процессы или кеш движка.

Исправлены гонки отмены до/после claim/ACK/terminal diagnostics; pending dequeue принимается за отмену только после наблюдённого pending и положительного scoped cancel. После неопределённого исхода GPU-задачи блокируются до проверки, CPU-поиск доступен. Ручное снятие требует подтверждения/причины и не объявляется доказанной остановкой. В production нет тестовых генераторов или фиктивных PNG.

UI: все параметры, выбор действительных checkpoint/sampler/scheduler, предупреждения о совместном использовании VRAM, реальные статусы, отмена, история, восстановление результата, отдельные raw JSON черновики с исходной ревизией. Сохранение невалидного дробного размера не запускает генерацию. Unknown/free-memory diagnostics не маскируются как гарантия.

## Требования и доказательства

MED-001–009; части CHR-006/UI-007/009/011/NFR-005/006/COMP/STOR. Родительские аппаратные/составные требования не объявлены PASS. Матрица181 исходных требований сохранена.

Linux6.18/Python3.12.14/Node24.22.1/Chromium: **190 backend PASS90.47s,12 browser PASS2.0m**. Ruff/types/build PASS,35 contracts экспортированы. Команды: `.venv/bin/pytest -q`; `.venv/bin/ruff check app/backend tests scripts packaging`; `npm run types`; `npm run build`; `TAVERNA_PYTHON=/workspace/work/Taverna/.venv/bin/python npm run test:e2e`. Raw evidence stage-12-final-backend.txt / stage-12-final-browser.txt; snapshot stage-12-final-build.json. Есть deprecation warning Starlette/httpx, без FAIL.

Tests/test_images.py:17 unit/integration/API случаев, включая реальный production HTTP adapter с MockTransport, ACK/cancel/pending removal/OOM/restart/idempotency/stale branch/no lifecycle/resource guard/черновики/CAS. Browser images.spec.ts использует отдельный test-only HTTP fixture с реальным PNG и проверяет UI→HTTP→gallery/cancel/reload; это не diffusion/GPU. Существующие media/RPG и полная регрессия пройдены.

Библиотечный коммит401c216: [CI43743550392](https://github.com/vokado/Taverna/actions/runs/43743550392) SUCCESS Ubuntu/Windows, native frozen main+diagnostic EXE и import/serve PNG/WebP/WAV/MP3/Ogg/FLAC/thumbnail. Artifact14173139303,37,061,181bytes. Эти результаты не приписываются новому image-adapter: его Windows CI выполняется после данного коммита.

## Форматы, восстановление и ограничения

Новые image-settings-v1/image-request-v1/image-lease-v1 schemas; основной JSON format неизменен. Lease переживает restart и блокирует повторный GPU-запуск до сверки своего UUID. Не replay внешних задач. Asset checksum UUID идемпотентен, ручные metadata edits не перезаписываются при reconciliation. Общий draft shape validator сохраняет дробные значения для последующей проверки полной схемой.

Добавлен воспроизводимый Windows PowerShell комплект docs/testing/Run-ImageTrial.ps1 с инструкцией IMAGE_TRIALS.md, включён в ZIP. Он проверяет реальную генерацию/адресную отмену, loaded instances до/после и короткий ответ той же LLM после изображения. Локально PowerShell отсутствует; parser check включён в Windows CI, аппаратный запуск не выполнялся.

BLOCKED: Windows11/RTX5070/реальный LM Studio/ComfyUI, сравнение CUDA/low-VRAM/CPU, VRAM/качество/скорость/OOM/реальная отмена и продолжение диалога; выбор окончательного engine/configuration D-06, аппаратная AT-09 и подписанная приёмка. Нет доступа к целевым сервисам/GPU. Linux/fake HTTP и Windows runner не заменяют эти испытания. Независимую работу13 можно продолжать без объявления аппаратных критериев выполненными.

## Последующая Windows-проверка адаптера

Коммитe67c4cd: [CI43750969808](https://github.com/vokado/Taverna/actions/runs/43750969808) SUCCESS Ubuntu/Windows, включая190 backend/12 browser, PowerShell parser и frozen smoke обоих EXE. Внутренний Windows ZIP: artifact14176527952,37,139,456bytes. Это подтверждает автоматические проверки на Windows runner, не реальную diffusion/RTX5070/LMStudio или запуск аппаратного PowerShell-сценария. Перечисленные выше аппаратные BLOCKED не закрыты.
